"""Session-scoped MCP connections for the native Bedrock agent."""

from __future__ import annotations

import hashlib
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Any

from kirocrew_custom.bedrock.engine import Tool


async def connect_tools(
    stack: AsyncExitStack, servers: list[dict[str, Any]], cwd: str
) -> list[Tool]:
    """Mount exactly the projected session array; never discover ambient servers.

    The runtime process is already under Crew's OS sandbox. Stdio servers
    inherit that sandbox and receive only the SDK's minimal environment plus
    their explicitly projected env, not the runtime's AWS credentials.
    """
    if not servers:
        return []

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.sse import sse_client
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamablehttp_client

    tools: list[Tool] = []
    names: set[str] = set()
    for server in servers:
        name = server["name"]
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("Invalid or duplicate MCP server name")
        names.add(name)
        kind = server.get("type", "stdio")
        if kind == "stdio":
            transport = stdio_client(
                StdioServerParameters(
                    command=server["command"],
                    args=server.get("args", []),
                    env={item["name"]: item["value"] for item in server.get("env", [])},
                    cwd=cwd,
                )
            )
        else:
            headers = {item["name"]: item["value"] for item in server.get("headers", [])}
            if kind == "http":
                transport = streamablehttp_client(server["url"], headers=headers)
            elif kind == "sse":
                transport = sse_client(server["url"], headers=headers)
            else:
                raise ValueError("Unsupported MCP transport")
        streams = await stack.enter_async_context(transport)
        session = await stack.enter_async_context(
            ClientSession(streams[0], streams[1], read_timeout_seconds=timedelta(seconds=120))
        )
        await session.initialize()
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            page = await session.list_tools(cursor=cursor)
            for entry in page.tools:
                # Bedrock tool names have a smaller alphabet and length bound
                # than MCP. A digest keeps arbitrary server/tool names distinct.
                wire_name = (
                    "tool_"
                    + hashlib.sha256((name + "\0" + entry.name).encode("utf-8")).hexdigest()[:48]
                )

                async def call(
                    arguments: dict[str, Any], client: Any = session, tool_name: str = entry.name
                ) -> dict[str, Any]:
                    result = await client.call_tool(tool_name, arguments=arguments)
                    return result.model_dump(by_alias=True, exclude_none=True)

                tools.append(
                    Tool(
                        wire_name,
                        name,
                        entry.name,
                        entry.description or entry.name,
                        entry.inputSchema,
                        call,
                    )
                )
            cursor = page.nextCursor
            if not cursor:
                break
            if cursor in seen_cursors:
                raise ValueError("MCP tools/list repeated its pagination cursor")
            seen_cursors.add(cursor)
    return tools
