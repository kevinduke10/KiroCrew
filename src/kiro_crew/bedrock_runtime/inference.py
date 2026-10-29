"""AWS SDK ownership and bounded network calls for the Bedrock runtime."""

from __future__ import annotations

import asyncio
from typing import Any


class BedrockInference:
    """Resolve AWS credentials using the standard SDK chain, never a CLI.

    Converse works for models whose tool calls cannot be streamed. Text is
    emitted to ACP when each inference response arrives. SDK retries are off:
    the caller owns retry policy and never repeats a tool execution here.
    """

    def __init__(self, client: Any) -> None:
        self.client = client
        self._request: asyncio.Task[dict[str, Any]] | None = None

    @classmethod
    def create(cls) -> BedrockInference:
        import boto3
        from botocore.config import Config

        return cls(
            boto3.Session().client(
                "bedrock-runtime",
                config=Config(
                    connect_timeout=10, read_timeout=120, retries={"total_max_attempts": 1}
                ),
            )
        )

    async def converse(self, **request: Any) -> dict[str, Any]:
        if self._request is not None:
            if not self._request.done():
                raise RuntimeError(
                    "The cancelled Bedrock request is still finishing; try again shortly"
                )
            # Retrieve a cancelled turn's SDK exception before releasing ownership.
            if not self._request.cancelled():
                self._request.exception()
        self._request = asyncio.create_task(asyncio.to_thread(self.client.converse, **request))
        return await asyncio.shield(self._request)

    async def close(self) -> None:
        if self._request is not None:
            await asyncio.gather(self._request, return_exceptions=True)
        await asyncio.to_thread(self.client.close)
