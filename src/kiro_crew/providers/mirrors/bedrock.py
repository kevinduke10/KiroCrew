"""Lazy integration bridge to the repository-local Bedrock mirror."""

from __future__ import annotations

from importlib import import_module
from importlib.util import find_spec
from typing import Any, Mapping, cast

from kiro_crew.agent_sdk.backends import ACP_BACKEND_BEDROCK
from kiro_crew.providers.mirrors.base import (
    AgentConfigMirror,
    Concern,
    Disposition,
    Ruling,
    SessionProjection,
)


class BedrockMirror(AgentConfigMirror):
    backend = ACP_BACKEND_BEDROCK

    @staticmethod
    def _implementation() -> AgentConfigMirror:
        try:
            module = import_module("kirocrew_custom.integration.mirror")
        except ModuleNotFoundError as exc:
            if exc.name not in {"kirocrew_custom", "kirocrew_custom.integration"}:
                raise
            raise RuntimeError(
                "Install the custom runtime: pip install -e './custom[bedrock]'"
            ) from exc
        return cast(AgentConfigMirror, module.BedrockMirror())

    def rulings(self) -> Mapping[Concern, Ruling]:
        if find_spec("kirocrew_custom") is None:
            return {
                concern: Ruling(
                    Disposition.WITHHELD, "Install the custom package to enable Bedrock."
                )
                for concern in Concern
            }
        return self._implementation().rulings()

    def session_params(self, agent: str | None, **kwargs: Any) -> dict[str, Any]:
        return self.session_projection(agent, **kwargs).params

    def session_projection(self, agent: str | None, **kwargs: Any) -> SessionProjection:
        return self._implementation().session_projection(agent, **kwargs)
