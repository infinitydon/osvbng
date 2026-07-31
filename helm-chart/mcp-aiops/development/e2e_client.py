import argparse
import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main(url: str, lifecycle: bool) -> None:
    async with streamable_http_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(tool.name for tool in tools.tools)
            required = {
                "osvbng_bng_health",
                "osvbng_bng_running_config",
                "osvbng_bng_running_configs",
                "osvbng_ha_status",
                "osvbng_ha_sync",
                "osvbng_subscriber_sessions",
                "osvbng_cgnat_pools",
                "osvbng_cgnat_mappings",
                "osvbng_cgnat_sessions",
                "osvbng_radius_servers",
                "osvbng_ha_switchover",
                "osvbng_ue_sessions",
                "osvbng_ue_session_status",
                "osvbng_ue_session_range",
                "osvbng_ue_session_create",
                "osvbng_ue_session_delete",
                "osvbng_ue_ping",
                "osvbng_ue_curl",
                "kubernetes_pods_list_in_namespace",
                "kubernetes_events_list",
            }
            missing = required.difference(names)
            if missing:
                raise RuntimeError(f"missing tools: {sorted(missing)}")

            health = await session.call_tool("osvbng_bng_health", {})
            running = await session.call_tool(
                "osvbng_bng_running_config",
                {
                    "member": 0,
                    "section": "plugins.subscriber.auth.radius",
                },
            )
            running_all = await session.call_tool(
                "osvbng_bng_running_configs",
                {"section": "ha"},
            )
            status = await session.call_tool("osvbng_ha_status", {"member": 0})
            pools = await session.call_tool("osvbng_cgnat_pools", {})
            mappings = await session.call_tool("osvbng_cgnat_mappings", {})
            ue_range = await session.call_tool(
                "osvbng_ue_session_range",
                {"start_session_id": 1, "end_session_id": 10},
            )
            blocked = await session.call_tool(
                "osvbng_ha_switchover", {"member": 0, "confirm": True}
            )
            if blocked.isError is not True:
                raise RuntimeError("mutating tool was not blocked")

            result = {
                "tools": names,
                "bng_health_error": health.isError,
                "bng_running_config_error": running.isError,
                "bng_running_configs_error": running_all.isError,
                "bng_running_config_redacted": (
                    "<redacted>" in json.dumps(running.structuredContent)
                ),
                "bng_running_config_diff_count": (
                    len(running_all.structuredContent.get("differences", []))
                    if running_all.structuredContent
                    else None
                ),
                "ha_status_error": status.isError,
                "cgnat_pools_error": pools.isError,
                "cgnat_mappings_error": mappings.isError,
                "cgnat_mapping_count": (
                    len(
                        mappings.structuredContent.get("result", {})
                        .get("data", [])
                    )
                    if mappings.structuredContent
                    else None
                ),
                "cgnat_active_member": (
                    pools.structuredContent.get("member")
                    if pools.structuredContent
                    else None
                ),
                "ue_session_range_error": ue_range.isError,
                "switchover_blocked": blocked.isError,
            }

            if lifecycle:
                created = await session.call_tool(
                    "osvbng_ue_session_create", {"session_id": 2, "confirm": True}
                )
                created_second = await session.call_tool(
                    "osvbng_ue_session_create", {"session_id": 3, "confirm": True}
                )
                status = await session.call_tool(
                    "osvbng_ue_session_status", {"session_id": 3}
                )
                ping = await session.call_tool(
                    "osvbng_ue_ping",
                    {"session_id": 3, "destination": "10.255.0.1", "count": 3},
                )
                curl = await session.call_tool(
                    "osvbng_ue_curl",
                    {
                        "session_id": 3,
                        "url": "https://example.com",
                        "max_time": 15,
                    },
                )
                deleted = await session.call_tool(
                    "osvbng_ue_session_delete", {"session_id": 2, "confirm": True}
                )
                deleted_second = await session.call_tool(
                    "osvbng_ue_session_delete", {"session_id": 3, "confirm": True}
                )
                result.update(
                    {
                        "ue_create_error": created.isError,
                        "ue_second_create_error": created_second.isError,
                        "ue_status_error": status.isError,
                        "ue_ping_error": ping.isError,
                        "ue_curl_error": curl.isError,
                        "ue_delete_error": deleted.isError,
                        "ue_second_delete_error": deleted_second.isError,
                    }
                )

            print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:18080/mcp")
    parser.add_argument(
        "--lifecycle",
        action="store_true",
        help="create UE sessions 2 and 3, test session 3, then delete both",
    )
    args = parser.parse_args()
    asyncio.run(main(args.url, args.lifecycle))
