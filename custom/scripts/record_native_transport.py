"""Capture the running native ACP/MCP transport with explicitly mocked inference.

No AWS client is created. This proves local transport and permission wiring only,
not AWS access or model behavior. Run with the core and custom packages installed.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

RUNTIME = """
import asyncio
from kirocrew_custom.bedrock.inference import BedrockInference
from kirocrew_custom.bedrock.server import Server
class ProbeInference:
    async def converse(self, **request):
        if len(request["messages"]) == 1:
            assert "worker-probe-context" in request["messages"][0]["content"][0]["text"]
            name = request["toolConfig"]["tools"][0]["toolSpec"]["name"]
            content = [{"toolUse": {"toolUseId": "probe-call", "name": name, "input": {}}}]
        else:
            assert "native-worker-probe" in str(request["messages"][-1])
            content = [{"text": "native-worker-probe completed"}]
        return {"output": {"message": {"role": "assistant", "content": content}}}
    async def close(self):
        pass
BedrockInference.create = staticmethod(lambda: ProbeInference())
asyncio.run(Server().run())
"""
MCP = """
from mcp.server.fastmcp import FastMCP
server = FastMCP("native-probe")
@server.tool()
def worker_probe() -> str:
    return "native-worker-probe"
server.run()
"""


async def capture(home: str) -> list[dict]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-P",
        "-u",
        "-c",
        RUNTIME,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={
            "HOME": home,
            "PATH": home,
            "KIROCREW_HOME": home,
            "KIROCREW_TELEMETRY": "0",
            "KIROCREW_BEDROCK_MODEL": "vendor.test-v1:0",
        },
    )
    frames: list[dict] = []

    async def send(frame):
        process.stdin.write((json.dumps({"jsonrpc": "2.0", **frame}) + "\n").encode())
        await process.stdin.drain()

    async def receive():
        line = await asyncio.wait_for(process.stdout.readline(), 30)
        if not line:
            raise RuntimeError("Native transport exited before replying")
        frame = json.loads(line)
        frames.append(frame)
        return frame

    try:
        await send({"id": 1, "method": "initialize", "params": {"protocolVersion": 1}})
        assert (await receive())["result"]["agentInfo"]["name"] == "kirocrew-bedrock"
        await send(
            {
                "id": 2,
                "method": "session/new",
                "params": {
                    "cwd": home,
                    "mcpServers": [
                        {
                            "name": "native-probe",
                            "command": sys.executable,
                            "args": ["-P", "-u", "-c", MCP],
                            "env": [],
                        }
                    ],
                },
            }
        )
        session = (await receive())["result"]["sessionId"]
        await send(
            {
                "id": 3,
                "method": "session/prompt",
                "params": {
                    "sessionId": session,
                    "prompt": [{"type": "text", "text": "worker-probe-context"}],
                },
            }
        )
        while True:
            frame = await receive()
            if frame.get("method") == "session/request_permission":
                await send(
                    {
                        "id": frame["id"],
                        "result": {
                            "outcome": {"outcome": "selected", "optionId": "allow"},
                        },
                    }
                )
            if frame.get("id") == 3:
                assert frame["result"]["stopReason"] == "end_turn", frame
                break
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 30)
        assert process.returncode == 0
        return frames
    finally:
        if process.returncode is None:
            process.kill()
            await asyncio.wait_for(process.wait(), 10)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="crew-native-capture-") as home:
        frames = asyncio.run(capture(home))
    # Normalize only random protocol identifiers, preserving the captured payloads.
    session = frames[1]["result"]["sessionId"]
    permission = next(f["id"] for f in frames if f.get("method") == "session/request_permission")
    payload = "\n".join(json.dumps(frame) for frame in frames)
    payload = payload.replace(session, "probe-session").replace(permission, "probe-permission")
    root = Path(__file__).resolve().parents[2]
    target = root / "test/fixtures/acp_frames/bedrock/native-mcp-transport-live.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "backend": "bedrock",
        "recorded": "live",
        "date": date.today().isoformat(),
        "agent_version": frames[0]["result"]["agentInfo"]["version"],
        "agent_name": "kirocrew-bedrock",
        "inference": "mocked",
        "note": "Captured from real native ACP and MCP stdio child processes. Only inference is mocked; no AWS calls. Proves local permission/MCP transport, not model behavior. Random protocol IDs normalized. Reproduce with custom/scripts/record_native_transport.py.",
    }
    target.write_text(json.dumps({"_meta": meta}) + "\n" + payload + "\n", encoding="utf-8")
    os.environ["KIROCREW_TELEMETRY"] = "0"
    sys.path.insert(0, str(root / "test"))
    from acp_frame_replay_harness import read_fixture, replay_frames, snapshot_json

    _, captured = read_fixture(target)
    target.with_suffix(".expected.json").write_text(
        snapshot_json(replay_frames(captured)), encoding="utf-8"
    )
    print(target)


if __name__ == "__main__":
    main()
