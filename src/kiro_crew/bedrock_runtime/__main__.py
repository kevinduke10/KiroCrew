"""Run the bundled ACP transport with the current Python interpreter."""

import asyncio

from kiro_crew.bedrock_runtime.server import Server

if __name__ == "__main__":
    asyncio.run(Server().run())
