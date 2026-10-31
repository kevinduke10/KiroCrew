"""Sanitized AI failures; errors never choose an alternate backend."""

from __future__ import annotations


class AISetupError(ValueError):
    """A missing native configuration with a safe, authored remedy."""


MODEL_SETUP_REQUIRED = (
    "Cannot reach AI: Bedrock is not configured. Select a Bedrock model ID or "
    "inference profile in agent.model before chatting. You can create mates without it."
)
AI_UNREACHABLE = (
    "Cannot reach AI. Check your connection, AWS Region, credentials and Bedrock "
    "model access, then try again. No alternate backend was started."
)


def ai_failure_message(error: Exception) -> str:
    return str(error) if isinstance(error, AISetupError) else AI_UNREACHABLE
