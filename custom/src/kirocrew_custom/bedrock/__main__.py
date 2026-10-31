"""Run the bundled ACP transport with the current Python interpreter."""

import asyncio

from kirocrew_custom.bedrock.server import Server

if __name__ == "__main__":
    asyncio.run(Server().run())
