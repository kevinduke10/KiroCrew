"""Native default, mate projection and failure regressions without AWS or agent CLIs."""

import asyncio
from contextlib import AsyncExitStack
from unittest.mock import Mock

import pytest
from kirocrew_custom.bedrock.errors import AI_UNREACHABLE, MODEL_SETUP_REQUIRED
from kirocrew_custom.bedrock.inference import BedrockInference
from kirocrew_custom.integration import mirror
from test_bedrock_runtime import Inference, RecordingServer

from kiro_crew.agent_sdk import backends
from kiro_crew.config.sections_agent import AgentConfig
from kiro_crew.members_parts.member_sessions import select_provider_backend


def test_default_and_mate_and_worker_routes_do_not_resolve_a_cli(monkeypatch):
    from kiro_crew import kiro_cli

    def forbidden(*args, **kwargs):
        raise AssertionError("CLI resolver must not run")

    monkeypatch.setattr(kiro_cli, "pin_kiro_cli", forbidden)
    config = AgentConfig()
    assert config.acp_backend == config.member_acp_backend == "bedrock"
    assert backends.resolve_selected_backend(None) == "bedrock"
    monkeypatch.setattr(
        "kiro_crew.members_parts.member_sessions.is_member_session_key", lambda key: True
    )
    assert (
        select_provider_backend("member-probe", config.member_acp_backend, config.acp_backend)
        == "bedrock"
    )
    from kiro_crew.mate_worker import crewmate_worker_backend, worker_kit_backend_refusal

    assert crewmate_worker_backend(config) == "bedrock"
    assert worker_kit_backend_refusal("bedrock") is None
    for capability in (
        "ACP_BACKENDS_MEMBER_CAPABILITIES",
        "ACP_BACKENDS_MEMBER_DISPATCH",
        "ACP_BACKENDS_MATE_WORKER_PROMPT_IN_CONTEXT",
    ):
        assert "bedrock" in getattr(backends, capability)
    assert "bedrock" not in backends.ACP_BACKENDS_MATE_WORKER_TOOL_LIMITS


@pytest.mark.parametrize("value", ["missing-backend", 9, False, []])
def test_unavailable_selection_raises_instead_of_choosing_a_cli(value):
    with pytest.raises(ValueError, match="Cannot reach AI"):
        backends.resolve_selected_backend(value)


def test_policy_denied_default_is_an_error_not_cli_fallback(monkeypatch):
    monkeypatch.setattr(backends, "selectable_backends", lambda: frozenset({""}))
    with pytest.raises(ValueError, match="no alternate backend"):
        backends.resolve_selected_backend(None)


def test_mate_worker_projection_receives_session_identity(monkeypatch):
    from kiro_crew.acp.session_mcp import SessionMcpProjection

    projection = SessionMcpProjection(
        [], frozenset(), frozenset(), frozenset(), Mock(), agent_spec={"tools": []}
    )
    build = Mock(return_value=projection)
    monkeypatch.setattr(mirror, "session_mcp_projection", build)
    monkeypatch.setattr(mirror, "place_single_binary_array", lambda *args, **kwargs: [])
    mirror.BedrockMirror().session_projection(
        "worker", session_key="member-worker-probe", work_dir="/test"
    )
    assert build.call_args.kwargs["session_key"] == "member-worker-probe"


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [False, True])
async def test_missing_setup_and_unreachable_ai_are_safe_errors_not_cli_fallback(
    monkeypatch, tmp_path, configured
):
    creation = Mock(side_effect=RuntimeError("private credential details must not escape"))
    monkeypatch.setattr(BedrockInference, "create", creation)
    server = RecordingServer()
    server.model = "vendor.test-v1:0" if configured else "auto"
    async with AsyncExitStack() as stack:
        await server.handle(
            {"id": 1, "method": "session/new", "params": {"cwd": str(tmp_path), "mcpServers": []}},
            stack,
        )
        assert server.frames[-1]["result"]["sessionId"]
        creation.assert_not_called()
        await server.handle(
            {
                "id": 2,
                "method": "session/prompt",
                "params": {
                    "sessionId": server.session_id,
                    "prompt": [{"type": "text", "text": "hello"}],
                },
            },
            stack,
        )
        await asyncio.wait_for(server.turn, 30)
    assert server.frames[-1]["error"]["message"] == (
        AI_UNREACHABLE if configured else MODEL_SETUP_REQUIRED
    )
    assert creation.call_count == int(configured)


@pytest.mark.asyncio
async def test_inference_failure_does_not_start_another_backend(monkeypatch, tmp_path):
    inference = Inference(RuntimeError("private upstream details"))
    creation = Mock(return_value=inference)
    monkeypatch.setattr(BedrockInference, "create", creation)
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
                    "prompt": [{"type": "text", "text": "hello"}],
                },
            },
            stack,
        )
        await asyncio.wait_for(server.turn, 30)
    assert server.frames[-1]["error"]["message"] == AI_UNREACHABLE
    assert len(inference.requests) == 1
    creation.assert_called_once()


@pytest.mark.parametrize("key", ["acp_backend", "member_acp_backend"])
@pytest.mark.parametrize("value", [7, False, [], "unknown-harness"])
def test_invalid_saved_backend_raises_without_cli_resolution(monkeypatch, tmp_path, key, value):
    import json

    from kiro_crew import kiro_cli
    from kiro_crew.config import KiroCrewConfig

    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path))
    monkeypatch.setattr(kiro_cli, "pin_kiro_cli", Mock(side_effect=AssertionError("CLI called")))
    (tmp_path / "config.json").write_text(json.dumps({"agent": {key: value}}), encoding="utf-8")
    with pytest.raises(ValueError, match="Cannot reach AI"):
        KiroCrewConfig.load()
