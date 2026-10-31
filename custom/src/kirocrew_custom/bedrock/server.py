"""ACP v1 service shipped with Crew; inference goes directly to Bedrock."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from contextlib import AsyncExitStack
from typing import Any

from kirocrew_custom.bedrock.engine import Conversation, Tool
from kirocrew_custom.bedrock.errors import MODEL_SETUP_REQUIRED, AISetupError, ai_failure_message
from kirocrew_custom.bedrock.inference import BedrockInference
from kirocrew_custom.bedrock.mcp import connect_tools

PROTOCOL_VERSION_BEDROCK = 1
MAX_FRAME_BYTES = 16 * 1024 * 1024


class Server:
    def __init__(self) -> None:
        self.session_id = ""
        self.model = os.environ.get("KIROCREW_BEDROCK_MODEL", "auto")
        self.conversation: Conversation | None = None
        self.inference: BedrockInference | None = None
        self.tools: list[Tool] = []
        self.turn: asyncio.Task[None] | None = None
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.tool_frames: dict[str, dict[str, Any]] = {}

    async def write(self, frame: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", **frame}) + "\n")
        sys.stdout.flush()

    async def update(self, update: dict[str, Any]) -> None:
        await self.write(
            {
                "method": "session/update",
                "params": {
                    "sessionId": self.session_id,
                    "update": update,
                },
            }
        )

    async def approve(self, tool: Tool, tool_id: str, arguments: dict[str, Any]) -> bool:
        frame = {
            "toolCallId": tool_id,
            "title": f"{tool.server}: {tool.native_name}",
            "kind": "other",
            "status": "pending",
            "rawInput": arguments,
            "_meta": {"bedrock": {"serverName": tool.server, "toolName": tool.native_name}},
        }
        self.tool_frames[tool_id] = frame
        await self.update({"sessionUpdate": "tool_call", **frame})
        request_id = "permission-" + uuid.uuid4().hex
        answer: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.pending[request_id] = answer
        try:
            await self.write(
                {
                    "id": request_id,
                    "method": "session/request_permission",
                    "params": {
                        "sessionId": self.session_id,
                        "toolCall": frame,
                        "options": [
                            {"optionId": "allow", "name": "Allow once", "kind": "allow_once"},
                            {"optionId": "deny", "name": "Deny", "kind": "reject_once"},
                        ],
                    },
                }
            )
            response = await answer
            outcome = response.get("outcome", {})
            return outcome.get("outcome") == "selected" and outcome.get("optionId") == "allow"
        finally:
            self.pending.pop(request_id, None)

    async def emit(self, kind: str, data: dict[str, Any]) -> None:
        if kind == "text":
            await self.update(
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {
                        "type": "text",
                        "text": data["text"],
                    },
                }
            )
        elif kind == "tool_result":
            tool_id = data["toolUseId"]
            frame = self.tool_frames.pop(tool_id, None)
            if frame is not None:
                result = data["result"]
                await self.update(
                    {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": tool_id,
                        "status": "failed" if result.get("status") == "error" else "completed",
                        "rawOutput": result,
                        "content": [
                            {"type": "content", "content": {"type": "text", "text": item["text"]}}
                            for item in result.get("content", [])
                            if "text" in item
                        ],
                    }
                )

    async def prompt(self, request: dict[str, Any]) -> None:
        try:
            blocks = request["params"].get("prompt", [])
            if not blocks or any(block.get("type") != "text" for block in blocks):
                raise ValueError("The Bedrock runtime currently accepts text prompts only")
            if self.conversation is None:
                if self.model == "auto" or not self.model:
                    raise AISetupError(MODEL_SETUP_REQUIRED)
                creation = asyncio.create_task(asyncio.to_thread(BedrockInference.create))
                try:
                    self.inference = await asyncio.shield(creation)
                except asyncio.CancelledError:
                    self.inference = await creation
                    raise
                self.conversation = Conversation(self.inference, self.model, self.tools)
            reason = await self.conversation.turn(
                "\n".join(block["text"] for block in blocks), self.approve, self.emit
            )
            stop = "max_tokens" if reason == "max_tokens" else "end_turn"
            await self.write({"id": request["id"], "result": {"stopReason": stop}})
        except asyncio.CancelledError:
            await self.write({"id": request["id"], "result": {"stopReason": "cancelled"}})
        except Exception as exc:
            # The provider's redactor remains the outer boundary; avoid echoing
            # SDK request parameters or a server's credential-bearing exception.
            message = ai_failure_message(exc)
            await self.write({"id": request["id"], "error": {"code": -32603, "message": message}})
        finally:
            self.tool_frames.clear()

    async def handle(self, request: dict[str, Any], stack: AsyncExitStack) -> None:
        method = request.get("method")
        request_id = request.get("id")
        if method is None:
            pending = self.pending.get(request_id) if isinstance(request_id, str) else None
            if pending is not None and not pending.done():
                pending.set_result(request.get("result", {}))
            return
        params = request.get("params", {})
        try:
            if method == "initialize":
                result: dict[str, Any] = {
                    "protocolVersion": PROTOCOL_VERSION_BEDROCK,
                    "agentInfo": {"name": "kirocrew-bedrock", "version": "1.0.0"},
                    "agentCapabilities": {
                        "loadSession": False,
                        "mcpCapabilities": {"http": True, "sse": True},
                    },
                    "authMethods": [],
                }
            elif method == "session/new":
                if self.session_id:
                    raise ValueError("This runtime serves one session per process")
                self.tools = await connect_tools(stack, params.get("mcpServers", []), params["cwd"])
                self.session_id = uuid.uuid4().hex
                result = {
                    "sessionId": self.session_id,
                    "models": {"currentModelId": self.model, "availableModels": []},
                }
            elif method.startswith("session/"):
                if not self.session_id or params.get("sessionId") != self.session_id:
                    raise ValueError("Unknown Bedrock session")
                if method == "session/prompt":
                    if self.turn is not None and not self.turn.done():
                        raise ValueError("A Bedrock turn is already running")
                    self.turn = asyncio.create_task(self.prompt(request))
                    return
                if method == "session/cancel":
                    if self.turn is not None and not self.turn.done():
                        self.turn.cancel()
                        await self.turn
                    result = {}
                elif method == "session/set_model":
                    if self.turn is not None and not self.turn.done():
                        raise ValueError("Cannot switch models during a turn")
                    model = params.get("modelId")
                    if not isinstance(model, str) or not model.strip() or model == "auto":
                        raise ValueError(
                            "A concrete Bedrock model ID or inference profile is required"
                        )
                    self.model = model
                    if self.conversation is not None:
                        self.conversation.model = model
                    result = {}
                else:
                    await self.write(
                        {
                            "id": request_id,
                            "error": {"code": -32601, "message": "Unsupported ACP method"},
                        }
                    )
                    return
            else:
                if request_id is not None:
                    await self.write(
                        {
                            "id": request_id,
                            "error": {"code": -32601, "message": "Unsupported ACP method"},
                        }
                    )
                return
            if request_id is not None:
                await self.write({"id": request_id, "result": result})
        except Exception:
            if request_id is not None:
                await self.write(
                    {
                        "id": request_id,
                        "error": {
                            "code": -32602,
                            "message": "Bedrock session setup or request failed",
                        },
                    }
                )

    async def run(self) -> None:
        async with AsyncExitStack() as stack:
            try:
                while True:
                    line = await asyncio.to_thread(sys.stdin.buffer.readline, MAX_FRAME_BYTES + 1)
                    if not line:
                        return
                    if len(line) > MAX_FRAME_BYTES:
                        raise ValueError("ACP frame exceeds the size limit")
                    request = json.loads(line)
                    if not isinstance(request, dict):
                        raise ValueError("ACP frame must be an object")
                    await self.handle(request, stack)
            finally:
                if self.turn is not None and not self.turn.done():
                    self.turn.cancel()
                    await self.turn
                if self.inference is not None:
                    await self.inference.close()
