"""Launch Crew's bundled Bedrock runtime without an external agent executable."""

from __future__ import annotations

from importlib.util import find_spec

from kiro_crew.acp.harness.base import ProcessAdapter, SpawnContext, SpawnPlan
from kiro_crew.agent_sdk.backends import ACP_BACKEND_BEDROCK
from kiro_crew.platform_compat import isolated_python_argv


class BedrockLaunch(ProcessAdapter):
    backend = ACP_BACKEND_BEDROCK

    def __init__(self) -> None:
        self.model = "auto"

    async def resolve_spawn(self, ctx: SpawnContext) -> SpawnPlan:
        missing = [name for name in ("boto3", "mcp") if find_spec(name) is None]
        if missing:
            raise RuntimeError("Install the Bedrock runtime with pip install 'kirocrew[bedrock]'")
        self.model = ctx.model or "auto"
        if ctx.session is not None:
            await ctx.session._prepare_session_mcp()
        return SpawnPlan(
            argv=isolated_python_argv("-P", "-m", "kiro_crew.bedrock_runtime"),
            spawn_label="Bedrock",
            stderr_label="Bedrock",
        )

    def apply_spawn_env(self, env: dict[str, str], *, spawned_binary: str | None = None) -> None:
        env["KIROCREW_BEDROCK_MODEL"] = self.model
