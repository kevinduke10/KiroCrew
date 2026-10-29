"""Bedrock conversation and tool loop, independent of the ACP transport.

The transport supplies an approval callback and a finite, session-owned tool
catalog. Model output never supplies a server address or executable.
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol


class Inference(Protocol):
    async def converse(self, **request: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class Tool:
    name: str
    server: str
    native_name: str
    description: str
    schema: dict[str, Any]
    call: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

    def specification(self) -> dict[str, Any]:
        return {
            "toolSpec": {
                "name": self.name,
                "description": self.description or self.native_name,
                "inputSchema": {"json": self.schema},
            }
        }


class Conversation:
    """One conversation; history commits after each complete model/tool exchange.

    Completed tool results remain in history after cancellation or a subsequent
    inference failure, so a later turn does not replay side effects. Pending tool
    calls get an explicit error result before the turn unwinds.
    """

    def __init__(self, inference: Inference, model: str, tools: list[Tool]) -> None:
        if not model or model == "auto":
            raise ValueError(
                "Select a Bedrock model ID or inference profile before starting a chat."
            )
        self.inference = inference
        self.model = model
        self.tools = {tool.name: tool for tool in tools}
        if len(self.tools) != len(tools):
            raise ValueError("Duplicate Bedrock tool names")
        self.messages: list[dict[str, Any]] = []
        self._running = False

    async def turn(
        self,
        text: str,
        approve: Callable[[Tool, str, dict[str, Any]], Awaitable[bool]],
        emit: Callable[[str, dict[str, Any]], Awaitable[None]],
        *,
        max_steps: int = 64,
    ) -> str:
        if self._running:
            raise RuntimeError("A Bedrock turn is already running")
        self._running = True
        try:
            return await self._turn(text, approve, emit, max_steps)
        finally:
            self._running = False

    async def _turn(self, text: str, approve: Any, emit: Any, max_steps: int) -> str:
        # Merge consecutive user input after an interrupted inference. Bedrock
        # models that require alternating roles receive a valid conversation.
        user = {"text": text}
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages[-1]["content"].append(user)
        else:
            self.messages.append({"role": "user", "content": [user]})
        for _ in range(max_steps):
            request: dict[str, Any] = {
                "modelId": self.model,
                "messages": copy.deepcopy(self.messages),
            }
            if self.tools:
                request["toolConfig"] = {
                    "tools": [tool.specification() for tool in self.tools.values()]
                }
            reply = await self.inference.converse(**request)
            message = reply.get("output", {}).get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                raise ValueError("Bedrock returned no assistant message")
            content = message.get("content")
            if not isinstance(content, list) or not content:
                raise ValueError("Bedrock returned empty assistant content")
            calls = [block["toolUse"] for block in content if "toolUse" in block]
            ids = [call.get("toolUseId") for call in calls]
            if any(not isinstance(value, str) or not value for value in ids) or len(
                set(ids)
            ) != len(ids):
                raise ValueError("Bedrock returned invalid tool call identities")
            self.messages.append(copy.deepcopy(message))
            results: list[dict[str, Any]] = []
            try:
                for block in content:
                    if "text" in block:
                        await emit("text", {"text": block["text"]})
                await emit("usage", reply.get("usage", {}))
                for call in calls:
                    result = await self._tool(call, approve)
                    results.append({"toolResult": result})
                    await emit("tool_result", {"toolUseId": call["toolUseId"], "result": result})
            finally:
                if calls:
                    # No call is retried automatically, including one interrupted
                    # while the remote server may already have executed it.
                    for call in calls[len(results) :]:
                        results.append(
                            {
                                "toolResult": {
                                    "toolUseId": call["toolUseId"],
                                    "status": "error",
                                    "content": [
                                        {
                                            "text": "Turn interrupted; execution status is unknown. Do not retry without checking."
                                        }
                                    ],
                                }
                            }
                        )
                    self.messages.append({"role": "user", "content": results})
            if not calls:
                return str(reply.get("stopReason", "end_turn"))
        raise RuntimeError("Bedrock reached the tool-step limit for this turn")

    async def _tool(self, call: dict[str, Any], approve: Any) -> dict[str, Any]:
        tool_id = call["toolUseId"]
        tool = self.tools.get(call.get("name", ""))
        arguments = call.get("input")
        result: dict[str, Any]
        if tool is None or not isinstance(arguments, dict):
            result = {
                "isError": True,
                "content": [{"type": "text", "text": "Unknown tool or invalid arguments"}],
            }
        elif not await approve(tool, tool_id, arguments):
            result = {"isError": True, "content": [{"type": "text", "text": "Tool call denied"}]}
        else:
            try:
                result = await tool.call(copy.deepcopy(arguments))
            except asyncio.CancelledError:
                raise
            except Exception:
                # Server exceptions can contain credentials; only the server's
                # explicit MCP result content is forwarded to the model.
                result = {
                    "isError": True,
                    "content": [{"type": "text", "text": "Tool execution failed"}],
                }
        blocks = [
            {"text": item["text"]}
            for item in result.get("content", [])
            if item.get("type") == "text"
        ]
        if "structuredContent" in result:
            blocks.append({"json": result["structuredContent"]})
        return {
            "toolUseId": tool_id,
            "status": "error" if result.get("isError") else "success",
            "content": blocks or [{"text": "Tool returned no text content"}],
        }
