import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP


NAMESPACE = os.getenv("OSVBNG_NAMESPACE", "osvbng-ha")
RELEASE = os.getenv("OSVBNG_RELEASE", "osvbng")
MEMBER_COUNT = int(os.getenv("OSVBNG_MEMBER_COUNT", "2"))
TIMEOUT = float(os.getenv("OSVBNG_TIMEOUT", "10"))
ALLOW_MUTATIONS = os.getenv("OSVBNG_ALLOW_MUTATIONS", "false").lower() == "true"

mcp = FastMCP(
    "osvbng-operations",
    instructions=(
        "Read operational state from osvbng. Mutating operations are disabled "
        "unless explicitly enabled by the platform administrator."
    ),
    host="0.0.0.0",
    port=8000,
    stateless_http=True,
)


def _member_url(member: int, path: str) -> str:
    if member < 0 or member >= MEMBER_COUNT:
        raise ValueError(f"member must be between 0 and {MEMBER_COUNT - 1}")
    host = f"{RELEASE}-{member}.{RELEASE}-headless.{NAMESPACE}.svc.cluster.local"
    return f"http://{host}:8080{path}"


async def _request(member: int, path: str, method: str = "GET") -> dict[str, Any]:
    url = _member_url(member, path)
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.request(method, url)
        response.raise_for_status()
        payload = response.json()
    return {"member": member, "endpoint": path, "result": payload}


@mcp.tool()
async def bng_health() -> dict[str, Any]:
    """Return reachability and HA state for every configured BNG member."""
    members = []
    for member in range(MEMBER_COUNT):
        try:
            members.append(await _request(member, "/api/show/ha/status"))
        except Exception as exc:
            members.append({"member": member, "error": str(exc)})
    return {"healthy": all("error" not in item for item in members), "members": members}


@mcp.tool()
async def ha_status(member: int = 0) -> dict[str, Any]:
    """Return active/standby HA state for one StatefulSet member."""
    return await _request(member, "/api/show/ha/status")


@mcp.tool()
async def ha_sync(member: int = 0) -> dict[str, Any]:
    """Return HA synchronization state and counters."""
    return await _request(member, "/api/show/ha/sync")


@mcp.tool()
async def subscriber_sessions(member: int = 0) -> dict[str, Any]:
    """Return subscriber sessions known by one BNG member."""
    return await _request(member, "/api/show/subscriber/sessions")


@mcp.tool()
async def cgnat_pools(member: int = 0) -> dict[str, Any]:
    """Return configured CGNAT pools."""
    return await _request(member, "/api/show/cgnat/pools")


@mcp.tool()
async def cgnat_mappings(member: int = 0) -> dict[str, Any]:
    """Return current CGNAT subscriber mappings."""
    return await _request(member, "/api/show/cgnat/mappings")


@mcp.tool()
async def cgnat_sessions(inside_ip: str, member: int = 0) -> dict[str, Any]:
    """Return CGNAT sessions for an exact subscriber IPv4 address."""
    return await _request(
        member, f"/api/show/cgnat/sessions?inside-ip={inside_ip}"
    )


@mcp.tool()
async def radius_servers(member: int = 0) -> dict[str, Any]:
    """Return osvbng RADIUS server health and request counters."""
    return await _request(member, "/api/show/aaa/radius/servers")


@mcp.tool()
async def ha_switchover(member: int = 0, confirm: bool = False) -> dict[str, Any]:
    """Gracefully switch the active SRG away from a member. Requires explicit enablement and confirmation."""
    if not ALLOW_MUTATIONS:
        raise PermissionError("mutating MCP tools are disabled")
    if not confirm:
        raise ValueError("confirm must be true")
    return await _request(member, "/api/exec/ha/switchover", method="POST")


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
