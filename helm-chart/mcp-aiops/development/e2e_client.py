import argparse
import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main(url: str) -> None:
    async with streamable_http_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(tool.name for tool in tools.tools)
            required = {
                "bng_health",
                "ha_status",
                "ha_sync",
                "subscriber_sessions",
                "cgnat_pools",
                "cgnat_mappings",
                "cgnat_sessions",
                "radius_servers",
                "ha_switchover",
            }
            missing = required.difference(names)
            if missing:
                raise RuntimeError(f"missing tools: {sorted(missing)}")

            health = await session.call_tool("bng_health", {})
            status = await session.call_tool("ha_status", {"member": 0})
            pools = await session.call_tool("cgnat_pools", {"member": 0})
            blocked = await session.call_tool(
                "ha_switchover", {"member": 0, "confirm": True}
            )
            if blocked.isError is not True:
                raise RuntimeError("mutating tool was not blocked")

            print(json.dumps({
                "tools": names,
                "bng_health_error": health.isError,
                "ha_status_error": status.isError,
                "cgnat_pools_error": pools.isError,
                "switchover_blocked": blocked.isError,
            }, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:18080/mcp")
    args = parser.parse_args()
    asyncio.run(main(args.url))
