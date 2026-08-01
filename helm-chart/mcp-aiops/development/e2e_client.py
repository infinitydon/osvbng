import argparse
import asyncio
import json
import os

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main(
    url: str,
    lifecycle: bool,
    profile: str,
    list_only: bool = False,
    expected_tools: set[str] | None = None,
    denied_tools: set[str] | None = None,
) -> None:
    token = os.environ.get("MCP_API_KEY")
    headers = {"Authorization": f"Bearer {token}"} if token else None
    async with httpx.AsyncClient(headers=headers) as http_client:
      async with streamable_http_client(url, http_client=http_client) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(tool.name for tool in tools.tools)
            if list_only:
                print(json.dumps({"profile": profile, "tool_count": len(names), "tools": names}, indent=2))
                return
            missing = (expected_tools or set()).difference(names)
            exposed_denied = (denied_tools or set()).intersection(names)
            if missing:
                raise RuntimeError(f"missing expected tools: {sorted(missing)}")
            if exposed_denied:
                raise RuntimeError(f"denied tools are visible: {sorted(exposed_denied)}")

            health = await session.call_tool("bng_health", {})
            running = None
            running_all = None
            if profile == "admin":
                running = await session.call_tool(
                    "bng_running_config",
                    {
                        "member": 0,
                        "section": "plugins.subscriber.auth.radius",
                    },
                )
                running_all = await session.call_tool(
                    "bng_running_configs",
                    {"section": "ha"},
                )
            status = await session.call_tool("ha_status", {"member": 0})
            pools = await session.call_tool("cgnat_pools", {})
            mappings = await session.call_tool("cgnat_mappings", {})
            ue_range = await session.call_tool(
                "ue_session_range",
                {"start_session_id": 1, "end_session_id": 10},
            )
            routing = await session.call_tool("routing_overview", {})
            admin_ue_confirmation_blocked = None
            admin_ha_confirmation_blocked = None
            if profile == "admin":
                guarded_ue = await session.call_tool(
                    "ue_session_delete", {"session_id": 20, "confirm": False}
                )
                guarded_ha = await session.call_tool(
                    "ha_switchover", {"member": 0, "confirm": False}
                )
                admin_ue_confirmation_blocked = guarded_ue.isError
                admin_ha_confirmation_blocked = guarded_ha.isError
                if not admin_ue_confirmation_blocked or not admin_ha_confirmation_blocked:
                    raise RuntimeError("admin mutation confirmation guard failed")
            result = {
                "profile": profile,
                "tools": names,
                "bng_health_error": health.isError,
                "bng_running_config_error": running.isError if running else None,
                "bng_running_configs_error": running_all.isError if running_all else None,
                "bng_running_config_redacted": (
                    "<redacted>" in json.dumps(running.structuredContent)
                    if running else None
                ),
                "bng_running_config_diff_count": (
                    len(running_all.structuredContent.get("differences", []))
                    if running_all and running_all.structuredContent
                    else None
                ),
                "ha_status_error": status.isError,
                "cgnat_pools_error": pools.isError,
                "cgnat_mappings_error": mappings.isError,
                "cgnat_mapping_count": (
                    len(
                        mappings.structuredContent.get("result", {})
                        .get("data") or []
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
                "routing_overview_error": routing.isError,
                "denied_tools_present": sorted((denied_tools or set()).intersection(names)),
                "admin_ue_confirmation_blocked": admin_ue_confirmation_blocked,
                "admin_ha_confirmation_blocked": admin_ha_confirmation_blocked,
            }

            if lifecycle:
                created = await session.call_tool(
                    "ue_session_create", {"session_id": 2, "confirm": True}
                )
                created_second = await session.call_tool(
                    "ue_session_create", {"session_id": 3, "confirm": True}
                )
                status = await session.call_tool(
                    "ue_session_status", {"session_id": 3}
                )
                ping = await session.call_tool(
                    "ue_ping",
                    {"session_id": 3, "destination": "10.255.0.1", "count": 3},
                )
                curl = await session.call_tool(
                    "ue_curl",
                    {
                        "session_id": 3,
                        "url": "https://example.com",
                        "max_time": 15,
                    },
                )
                await asyncio.sleep(1)
                active_mappings = await session.call_tool("cgnat_mappings", {})
                active_mapping_data = (
                    active_mappings.structuredContent.get("result", {}).get("data") or []
                    if active_mappings.structuredContent
                    else []
                )
                deleted = await session.call_tool(
                    "ue_session_delete", {"session_id": 2, "confirm": True}
                )
                deleted_second = await session.call_tool(
                    "ue_session_delete", {"session_id": 3, "confirm": True}
                )
                result.update(
                    {
                        "ue_create_error": created.isError,
                        "ue_second_create_error": created_second.isError,
                        "ue_status_error": status.isError,
                        "ue_ping_error": ping.isError,
                        "ue_curl_error": curl.isError,
                        "active_cgnat_mappings_error": active_mappings.isError,
                        "active_cgnat_mapping_count": len(active_mapping_data),
                        "ue_delete_error": deleted.isError,
                        "ue_second_delete_error": deleted_second.isError,
                    }
                )

            print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:18080/mcp")
    parser.add_argument("--profile", choices=("noc", "admin"), default="admin")
    parser.add_argument(
        "--lifecycle",
        action="store_true",
        help="create UE sessions 2 and 3, test session 3, then delete both",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="list the tools visible through the selected gateway profile",
    )
    parser.add_argument(
        "--expected-tools",
        default=os.environ.get("EXPECTED_MCP_TOOLS", ""),
        help="comma-separated expected tools supplied by deployment validation",
    )
    parser.add_argument(
        "--denied-tools",
        default=os.environ.get("DENIED_MCP_TOOLS", ""),
        help="comma-separated tools that must not be visible",
    )
    args = parser.parse_args()
    parse_tools = lambda value: {item.strip() for item in value.split(",") if item.strip()}
    asyncio.run(
        main(
            args.url,
            args.lifecycle,
            args.profile,
            args.list_only,
            parse_tools(args.expected_tools),
            parse_tools(args.denied_tools),
        )
    )
