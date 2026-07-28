import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse


NAMESPACE = os.getenv("OSVBNG_NAMESPACE", "osvbng-ha")
RELEASE = os.getenv("OSVBNG_RELEASE", "osvbng")
MEMBER_COUNT = int(os.getenv("OSVBNG_MEMBER_COUNT", "2"))
TIMEOUT = float(os.getenv("OSVBNG_TIMEOUT", "10"))
ALLOW_MUTATIONS = os.getenv("OSVBNG_ALLOW_MUTATIONS", "false").lower() == "true"
UE_API_URL = os.getenv(
    "OSVBNG_UE_API_URL",
    "http://ue-test-api.osvbng-ha.svc.cluster.local:8081",
).rstrip("/")
UE_TIMEOUT = float(os.getenv("OSVBNG_UE_TIMEOUT", "60"))
ALLOW_UE_MUTATIONS = (
    os.getenv("OSVBNG_ALLOW_UE_MUTATIONS", "false").lower() == "true"
)

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


@mcp.custom_route("/", methods=["GET"])
async def health(_request):
    """Health endpoint used by the ToolHive backend checks."""
    return JSONResponse({"status": "ok", "service": "osvbng-operations"})


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


async def _active_member() -> int:
    """Return the zero-based StatefulSet ordinal currently ACTIVE for the default SRG."""
    states = []
    for member in range(MEMBER_COUNT):
        try:
            status = await _request(member, "/api/show/ha/status")
            srgs = status["result"].get("data", {}).get("srgs", [])
            states.append(
                {
                    "member": member,
                    "states": [srg.get("state") for srg in srgs],
                }
            )
            if any(srg.get("state") == "ACTIVE" for srg in srgs):
                return member
        except Exception as exc:
            states.append({"member": member, "error": str(exc)})
    raise RuntimeError(f"no ACTIVE BNG member found: {states}")


async def _resolve_member(member: int | None) -> int:
    return await _active_member() if member is None else member


async def _ue_request(
    path: str, method: str = "GET", payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=UE_TIMEOUT) as client:
        response = await client.request(method, f"{UE_API_URL}{path}", json=payload)
        response.raise_for_status()
        return response.json()


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
async def cgnat_pools(member: int | None = None) -> dict[str, Any]:
    """Return current CGNAT pools. Omit member to query the ACTIVE HA member automatically; an explicit member is a zero-based StatefulSet ordinal."""
    return await _request(
        await _resolve_member(member), "/api/show/cgnat/pools"
    )


@mcp.tool()
async def cgnat_mappings(member: int | None = None) -> dict[str, Any]:
    """Return current PBA subscriber-to-outside-IP and port-block mappings from the ACTIVE HA member. Use this once for mapping, translation-address, or allocated-port-block questions."""
    return await _request(
        await _resolve_member(member), "/api/show/cgnat/mappings"
    )


@mcp.tool()
async def cgnat_sessions(
    inside_ip: str, member: int | None = None
) -> dict[str, Any]:
    """Return current transport-flow sessions for one exact subscriber IPv4 address. This is not the PBA mapping table; ICMP may work while this flow table is empty. Omit member to query ACTIVE."""
    return await _request(
        await _resolve_member(member),
        f"/api/show/cgnat/sessions?inside-ip={inside_ip}",
    )


@mcp.tool()
async def radius_servers(member: int = 0) -> dict[str, Any]:
    """Return osvbng RADIUS server health and request counters."""
    return await _request(member, "/api/show/aaa/radius/servers")


@mcp.tool()
async def ue_sessions(include_inactive: bool = False) -> dict[str, Any]:
    """Read authoritative current UE state. Preserve returned session IDs exactly and never infer current mappings from earlier create responses."""
    suffix = "?include_inactive=true" if include_inactive else ""
    return await _ue_request(f"/sessions{suffix}")


@mcp.tool()
async def ue_session_range(
    start_session_id: int, end_session_id: int
) -> dict[str, Any]:
    """Read authoritative current state for an inclusive UE session-ID range. Use for questions about multiple numbered sessions and preserve every returned session-id, IPv4 address, access interface, and linux-interface exactly."""
    return await _ue_request(
        "/sessions"
        f"?start_session_id={start_session_id}"
        f"&end_session_id={end_session_id}"
        "&include_inactive=true"
    )


@mcp.tool()
async def ue_session_status(session_id: int) -> dict[str, Any]:
    """Read authoritative current state, address, VLANs, and counters for exactly one interactive UE session."""
    return await _ue_request(f"/sessions/{session_id}")


@mcp.tool()
async def ue_session_create(
    session_id: int, confirm: bool = False
) -> dict[str, Any]:
    """Start a preallocated UE slot and wait for its DHCP session. This changes lab state and requires confirmation."""
    if not ALLOW_UE_MUTATIONS:
        raise PermissionError("UE session mutations are disabled")
    if not confirm:
        raise ValueError("confirm must be true")
    return await _ue_request(f"/sessions/{session_id}", method="POST")


@mcp.tool()
async def ue_session_delete(
    session_id: int, confirm: bool = False
) -> dict[str, Any]:
    """Release and stop an interactive UE session. This changes lab state and requires confirmation."""
    if not ALLOW_UE_MUTATIONS:
        raise PermissionError("UE session mutations are disabled")
    if not confirm:
        raise ValueError("confirm must be true")
    return await _ue_request(f"/sessions/{session_id}", method="DELETE")


@mcp.tool()
async def ue_ping(
    session_id: int, destination: str = "1.1.1.1", count: int = 3
) -> dict[str, Any]:
    """Send ping traffic through one established UE session and return its output."""
    return await _ue_request(
        f"/sessions/{session_id}/ping",
        method="POST",
        payload={"destination": destination, "count": count},
    )


@mcp.tool()
async def ue_curl(
    session_id: int, url: str = "http://example.com", max_time: int = 10
) -> dict[str, Any]:
    """Send an HTTP or HTTPS request through one established UE session."""
    return await _ue_request(
        f"/sessions/{session_id}/curl",
        method="POST",
        payload={"url": url, "max_time": max_time},
    )


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
