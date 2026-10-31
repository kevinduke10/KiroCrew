"""Project the agent's MCP grants onto the bundled Bedrock runtime."""

from __future__ import annotations

from typing import Any, Mapping

from kiro_crew.acp.session_mcp import session_mcp_projection
from kiro_crew.agent_sdk.backends import ACP_BACKEND_BEDROCK
from kiro_crew.providers.mirrors.base import (
    AgentConfigMirror,
    Concern,
    Disposition,
    Ruling,
    SessionProjection,
)
from kiro_crew.providers.mirrors.opencode import place_single_binary_array


class BedrockMirror(AgentConfigMirror):
    backend = ACP_BACKEND_BEDROCK

    def rulings(self) -> Mapping[Concern, Ruling]:
        return {
            Concern.MCP_SERVERS: Ruling(
                Disposition.TRANSLATED, "Mounted through session/new.mcpServers."
            ),
            Concern.TOOL_ALLOWLIST: Ruling(
                Disposition.TRANSLATED, "The shared projection narrows the mounted servers."
            ),
            Concern.DENIED_TOOLS: Ruling(
                Disposition.TRANSLATED, "A per-tool restriction withholds its whole server."
            ),
            Concern.MODEL: Ruling(
                Disposition.DELIVERED,
                "Selected through session/set_model; passed unchanged to Bedrock.",
            ),
            Concern.MODEL_ALLOWLIST: Ruling(
                Disposition.WITHHELD, "AWS authorizes the requested model at invocation."
            ),
            Concern.AUTO_APPROVE: Ruling(
                Disposition.WITHHELD, "Every invocation asks Crew for permission."
            ),
            Concern.PERMISSION_MODE: Ruling(
                Disposition.WITHHELD, "The runtime has no permission bypass mode."
            ),
            Concern.PROMPT: Ruling(
                Disposition.WITHHELD, "Crew supplies context in each turn's prompt."
            ),
            Concern.RESOURCES: Ruling(
                Disposition.WITHHELD, "Resources are not read implicitly by the runtime."
            ),
            Concern.HOOKS: Ruling(
                Disposition.WITHHELD, "Native script hooks are not supported by this runtime."
            ),
        }

    def session_params(self, agent: str | None, **kwargs: Any) -> dict[str, Any]:
        return self.session_projection(agent, **kwargs).params

    def session_projection(self, agent: str | None, **kwargs: Any) -> SessionProjection:
        projection = session_mcp_projection(
            agent,
            stub_server_names=kwargs.get("stub_server_names", ()),
            work_dir=kwargs.get("work_dir"),
            session_key=kwargs.get("session_key", ""),
        )
        narrowed = frozenset(server for server, _ in projection.disabled_tools)
        elements = place_single_binary_array(
            projection,
            label="Bedrock",
            unhonoured=narrowed,
            stub_elements=kwargs.get("stub_elements", ()),
            session_key=kwargs.get("session_key", ""),
            channel_id=kwargs.get("channel_id", ""),
            session_token=kwargs.get("session_token", ""),
        )
        return SessionProjection(
            params={"mcpServers": elements},
            disabled_servers=projection.disabled_servers,
            restricted_servers=narrowed,
            unhonoured_servers=narrowed,
            zero_tools=projection.zero_tools,
            derived_spec_snapshot=projection.derived_spec_snapshot,
        )
