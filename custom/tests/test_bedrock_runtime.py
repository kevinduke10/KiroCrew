"""Native Bedrock turns, permissions and cancellation without AWS credentials."""

from __future__ import annotations

import asyncio
import copy
from contextlib import AsyncExitStack
from unittest.mock import AsyncMock

import pytest
from kirocrew_custom.bedrock.engine import Conversation, Tool
from kirocrew_custom.bedrock.server import Server


def reply(*content, stop="end_turn"):
    return {
        "output": {"message": {"role": "assistant", "content": list(content)}},
        "stopReason": stop,
        "usage": {"inputTokens": 10, "outputTokens": 5},
    }


def tool_call(name="lookup", tool_id="call-1"):
    return {"toolUse": {"name": name, "toolUseId": tool_id, "input": {"query": "test"}}}


def make_tool(call):
    return Tool(
        "lookup",
        "test-server",
        "native_lookup",
        "Look up a value",
        {"type": "object", "properties": {}},
        call,
    )


class Inference:
    def __init__(self, *replies):
        self.replies = iter(replies)
        self.requests = []

    async def converse(self, **request):
        self.requests.append(copy.deepcopy(request))
        response = next(self.replies)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.mark.asyncio
async def test_plain_chat_preserves_history_and_has_no_tool_config():
    inference = Inference(reply({"text": "First"}), reply({"text": "Second"}))
    conversation = Conversation(inference, "vendor.test-v1:0", [])
    emit = AsyncMock()
    await conversation.turn("Hello", AsyncMock(), emit)
    await conversation.turn("Follow up", AsyncMock(), emit)
    assert "toolConfig" not in inference.requests[0]
    assert inference.requests[0]["modelId"] == "vendor.test-v1:0"
    assert [m["role"] for m in inference.requests[1]["messages"]] == ["user", "assistant", "user"]
    assert emit.await_args_list[0].args == ("text", {"text": "First"})


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [True, False])
async def test_tool_execution_requires_explicit_approval(allowed):
    call = AsyncMock(return_value={"content": [{"type": "text", "text": "Found"}]})
    approve = AsyncMock(return_value=allowed)
    inference = Inference(reply(tool_call(), stop="tool_use"), reply({"text": "Done"}))
    conversation = Conversation(inference, "vendor.test", [make_tool(call)])
    await conversation.turn("Look it up", approve, AsyncMock())
    approve.assert_awaited_once_with(conversation.tools["lookup"], "call-1", {"query": "test"})
    assert call.await_count == int(allowed)
    result = inference.requests[1]["messages"][-1]["content"][0]["toolResult"]
    assert result["status"] == ("success" if allowed else "error")
    assert result["content"][0]["text"] == ("Found" if allowed else "Tool call denied")


@pytest.mark.asyncio
async def test_unknown_tool_cannot_select_a_server_or_executable():
    call = AsyncMock()
    approve = AsyncMock()
    inference = Inference(reply(tool_call("not-mounted"), stop="tool_use"), reply({"text": "Done"}))
    await Conversation(inference, "vendor.test", [make_tool(call)]).turn(
        "Run", approve, AsyncMock()
    )
    call.assert_not_awaited()
    approve.assert_not_awaited()
    assert inference.requests[1]["messages"][-1]["content"][0]["toolResult"]["status"] == "error"


@pytest.mark.asyncio
async def test_completed_tool_is_retained_after_upstream_failure():
    call = AsyncMock(return_value={"content": [{"type": "text", "text": "Written"}]})
    inference = Inference(
        reply(tool_call(), stop="tool_use"),
        RuntimeError("upstream failed"),
        reply({"text": "Recovered"}),
    )
    conversation = Conversation(inference, "vendor.test", [make_tool(call)])
    with pytest.raises(RuntimeError, match="upstream failed"):
        await conversation.turn("Write", AsyncMock(return_value=True), AsyncMock())
    await conversation.turn("Continue", AsyncMock(return_value=True), AsyncMock())
    call.assert_awaited_once()
    assert inference.requests[-1]["messages"][-1]["content"][0]["toolResult"]["status"] == "success"


@pytest.mark.asyncio
async def test_cancellation_at_permission_never_executes_and_closes_tool_exchange():
    requested = asyncio.Event()
    hold = asyncio.Event()
    call = AsyncMock()

    async def approve(*args):
        requested.set()
        await asyncio.wait_for(hold.wait(), 5)
        return True

    inference = Inference(reply(tool_call(), stop="tool_use"), reply({"text": "Continued"}))
    conversation = Conversation(inference, "vendor.test", [make_tool(call)])
    task = asyncio.create_task(conversation.turn("Run", approve, AsyncMock()))
    try:
        await asyncio.wait_for(requested.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    finally:
        hold.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    call.assert_not_awaited()
    assert conversation.messages[-1]["content"][0]["toolResult"]["status"] == "error"
    await conversation.turn("Continue", AsyncMock(return_value=True), AsyncMock())


@pytest.mark.asyncio
async def test_step_limit_leaves_complete_tool_results():
    call = AsyncMock(return_value={"content": []})
    inference = Inference(reply(tool_call(), stop="tool_use"))
    conversation = Conversation(inference, "vendor.test", [make_tool(call)])
    with pytest.raises(RuntimeError, match="step limit"):
        await conversation.turn("Run", AsyncMock(return_value=True), AsyncMock(), max_steps=1)
    assert conversation.messages[-1]["role"] == "user"
    assert conversation.messages[-1]["content"][0]["toolResult"]["status"] == "success"


@pytest.mark.asyncio
async def test_duplicate_call_ids_are_rejected_before_any_execution():
    call = AsyncMock()
    inference = Inference(reply(tool_call(), tool_call(), stop="tool_use"))
    with pytest.raises(ValueError, match="identities"):
        await Conversation(inference, "vendor.test", [make_tool(call)]).turn(
            "Run", AsyncMock(return_value=True), AsyncMock()
        )
    call.assert_not_awaited()


@pytest.mark.parametrize("model", ["", "auto"])
def test_no_hardcoded_model_substitution(model):
    with pytest.raises(ValueError, match="Select a Bedrock model"):
        Conversation(Inference(), model, [])


class RecordingServer(Server):
    def __init__(self):
        super().__init__()
        self.frames = []
        self.permission_sent = asyncio.Event()

    async def write(self, frame):
        self.frames.append(frame)
        if frame.get("method") == "session/request_permission":
            self.permission_sent.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("option,expected", [("allow", True), ("deny", False), ("other", False)])
async def test_acp_permission_result_and_trusted_identity(option, expected):
    from kiro_crew.acp._dispatch import classify_tool_call

    server = RecordingServer()
    server.session_id = "session"
    tool = make_tool(AsyncMock())
    async with AsyncExitStack() as stack:
        task = asyncio.create_task(server.approve(tool, "call-1", {"query": "test"}))
        try:
            await asyncio.wait_for(server.permission_sent.wait(), 5)
            frame = server.frames[-1]
            identity = classify_tool_call(frame["params"]["toolCall"])
            assert identity.identity_trusted
            assert (identity.mcp_server_name, identity.tool_name) == (
                "test-server",
                "native_lookup",
            )
            await server.handle(
                {
                    "id": frame["id"],
                    "result": {"outcome": {"outcome": "selected", "optionId": option}},
                },
                stack,
            )
            assert await asyncio.wait_for(task, 5) is expected
            assert server.pending == {}
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_acp_initialize_and_empty_tool_session_never_construct_aws_client(
    monkeypatch, tmp_path
):
    from kirocrew_custom.bedrock.inference import BedrockInference

    def forbidden():
        raise AssertionError("AWS client must be lazy")

    monkeypatch.setattr(BedrockInference, "create", forbidden)
    server = RecordingServer()
    async with AsyncExitStack() as stack:
        await server.handle({"id": 1, "method": "initialize"}, stack)
        await server.handle(
            {"id": 2, "method": "session/new", "params": {"cwd": str(tmp_path), "mcpServers": []}},
            stack,
        )
        assert server.frames[0]["result"]["protocolVersion"] == 1
        assert server.frames[1]["result"]["sessionId"]
        await server.handle(
            {
                "id": 3,
                "method": "session/set_model",
                "params": {"sessionId": server.session_id, "modelId": "vendor.test-v1:0"},
            },
            stack,
        )
        assert server.model == "vendor.test-v1:0"


@pytest.mark.asyncio
async def test_launch_uses_current_python_not_an_agent_cli(monkeypatch, tmp_path):
    from kiro_crew.acp.harness.base import SpawnContext
    from kiro_crew.acp.harness.bedrock import BedrockLaunch
    from kiro_crew.platform_compat import isolated_python_argv

    monkeypatch.setattr("kiro_crew.acp.harness.bedrock.find_spec", lambda name: object())
    launch = BedrockLaunch()
    ctx = SpawnContext(
        agent="kirocrew", work_dir=tmp_path, model="vendor.test-v1:0", environ={}, home=tmp_path
    )
    plan = await launch.resolve_spawn(ctx)
    assert plan.argv == isolated_python_argv("-P", "-m", "kirocrew_custom.bedrock")
    env = {}
    launch.apply_spawn_env(env)
    assert env == {"KIROCREW_BEDROCK_MODEL": "vendor.test-v1:0"}


@pytest.mark.parametrize(
    "model",
    [
        "vendor.test-v1:0",
        "us.vendor.test-v1:0",
        "arn:aws:bedrock:us-east-1:123456789012:inference-profile/example-profile",
        "arn:aws:bedrock:us-east-1::foundation-model/vendor.test-v1:0",
    ],
)
def test_model_ids_survive_validation_and_provider_factory(model):
    import re

    from kiro_crew.config.loader import KiroCrewConfig
    from kiro_crew.dashboard.handlers.core_config_schema import _EDITABLE_CONFIG
    from kiro_crew.validation import _MODEL_NAME_RE

    assert _MODEL_NAME_RE.fullmatch(model)
    schema = _EDITABLE_CONFIG["agent.model"]
    assert len(model) <= schema["max_len"]
    assert re.fullmatch(schema["pattern"], model)
    config = KiroCrewConfig()
    config.agent.acp_backend = "bedrock"
    config.agent.model = model
    provider = config.create_provider_factory()(session_key="test:bedrock", agent="")
    assert provider.client.backend == "bedrock"
    assert provider.client._model == model
    assert provider.client._uses_advertised_model_selection


@pytest.mark.parametrize(
    "model",
    [
        "vendor.test:0\n",
        "vendor.test:0;echo",
        "vendor.test:$(echo)",
        "arn:aws:bedrock:us-east-1:123456789012:inference-profile/../file",
        "arn:aws:s3:us-east-1:123456789012:inference-profile/profile",
    ],
)
def test_bedrock_identifiers_do_not_admit_commands_or_paths(model):
    from kiro_crew.validation import _MODEL_NAME_RE

    assert not _MODEL_NAME_RE.fullmatch(model)


@pytest.mark.asyncio
async def test_projected_tools_reach_the_session_new_wire(monkeypatch, tmp_path):
    from kiro_crew.acp.client import AcpClient

    client = AcpClient(work_dir=tmp_path, acp_backend="bedrock", model="vendor.test")
    element = {"name": "probe", "command": "probe-server", "args": [], "env": []}
    client._session_mcp_cache = [element]
    monkeypatch.setattr(client, "_send_request", AsyncMock(return_value=1))
    monkeypatch.setattr(client, "_wait_for_response", AsyncMock(return_value={"sessionId": "s"}))
    monkeypatch.setattr(client, "_begin_session_report", lambda *args: None)
    monkeypatch.setattr(client, "_guard_unresolved_mcp_refs", lambda *args: None)
    await client._new_session_following_substitution()
    request = client._send_request.await_args.args
    assert request[0] == "session/new"
    assert request[1]["mcpServers"] == [element]


@pytest.mark.asyncio
async def test_cancelled_sdk_request_remains_owned_until_shutdown(monkeypatch):
    from types import SimpleNamespace

    from kirocrew_custom.bedrock.inference import BedrockInference

    started, release = asyncio.Event(), asyncio.Event()
    closed = []

    async def threaded(function, **kwargs):
        if function == "converse":
            started.set()
            await asyncio.wait_for(release.wait(), 5)
            return reply({"text": "Late response"})
        closed.append(True)

    monkeypatch.setattr(asyncio, "to_thread", threaded)
    inference = BedrockInference(SimpleNamespace(converse="converse", close="close"))
    task = asyncio.create_task(inference.converse(modelId="vendor.test-v1:0"))
    try:
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        with pytest.raises(RuntimeError, match="still finishing"):
            await inference.converse(modelId="vendor.test-v1:0")
    finally:
        release.set()
        await asyncio.wait_for(inference.close(), 5)
        await asyncio.gather(task, return_exceptions=True)
    assert closed == [True]


@pytest.mark.asyncio
async def test_acp_prompt_completes_with_mock_inference(monkeypatch, tmp_path):
    from kirocrew_custom.bedrock.inference import BedrockInference

    inference = Inference(reply({"text": "Hello from Bedrock"}))
    monkeypatch.setattr(BedrockInference, "create", lambda: inference)
    server = RecordingServer()
    server.model = "vendor.test-v1:0"
    async with AsyncExitStack() as stack:
        await server.handle(
            {"id": 1, "method": "session/new", "params": {"cwd": str(tmp_path), "mcpServers": []}},
            stack,
        )
        await server.handle(
            {
                "id": 2,
                "method": "session/prompt",
                "params": {
                    "sessionId": server.session_id,
                    "prompt": [{"type": "text", "text": "Hi"}],
                },
            },
            stack,
        )
        await asyncio.wait_for(server.turn, 5)
    assert server.frames[-1] == {"id": 2, "result": {"stopReason": "end_turn"}}
    assert inference.requests[0]["modelId"] == server.model
    assert any(
        frame.get("params", {}).get("update", {}).get("content", {}).get("text")
        == "Hello from Bedrock"
        for frame in server.frames
    )
