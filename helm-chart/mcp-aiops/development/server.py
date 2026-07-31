import ast
import asyncio
import ipaddress
import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import httpx
from kubernetes import client, config
from kubernetes.stream import stream
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
FRR_RELEASE = os.getenv("OSVBNG_FRR_RELEASE", "osvbng-frr-isp")
ROUTING_CONTAINER = os.getenv("OSVBNG_ROUTING_CONTAINER", "frr")
_core_api = None

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


SENSITIVE_CONFIG_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "key",
    "password",
    "private_key",
    "secret",
    "token",
}


def _redact_config(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "<redacted>"
                if key.lower().replace("-", "_") in SENSITIVE_CONFIG_KEYS
                else _redact_config(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_config(item) for item in value]
    return value


def _config_section(config: dict[str, Any], section: str | None) -> Any:
    if not section:
        return config
    current: Any = config
    components = section.split(".")
    while components:
        if not isinstance(current, dict):
            raise ValueError(f"running-config section not found: {section}")
        matched = None
        for count in range(len(components), 0, -1):
            candidate = ".".join(components[:count])
            if candidate in current:
                matched = candidate
                components = components[count:]
                break
        if matched is None:
            raise ValueError(f"running-config section not found: {section}")
        current = current[matched]
    return current


def _flatten_config(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        flattened = {}
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else key
            flattened.update(_flatten_config(item, path))
        return flattened
    if isinstance(value, list):
        flattened = {}
        for index, item in enumerate(value):
            flattened.update(_flatten_config(item, f"{prefix}[{index}]"))
        return flattened
    return {prefix: value}


async def _running_config(member: int, section: str | None = None) -> dict[str, Any]:
    response = await _request(member, "/api/show/running-config")
    config = deepcopy(response["result"].get("data", {}))
    return {
        "member": member,
        "source": "/api/show/running-config",
        "section": section or "all",
        "config": _config_section(_redact_config(config), section),
    }


async def _ue_request(
    path: str, method: str = "GET", payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=UE_TIMEOUT) as client:
        response = await client.request(method, f"{UE_API_URL}{path}", json=payload)
        response.raise_for_status()
        return response.json()


def _api() -> client.CoreV1Api:
    global _core_api
    if _core_api is None:
        config.load_incluster_config()
        configuration = client.Configuration.get_default_copy()
        # This MicroK8s lab CA predates strict X.509 key-usage validation.
        # Authentication still uses the projected, namespace-scoped SA token.
        configuration.verify_ssl = False
        _core_api = client.CoreV1Api(client.ApiClient(configuration))
    return _core_api


def _validate_prefix(prefix: str | None) -> str | None:
    if prefix is None:
        return None
    return str(ipaddress.ip_network(prefix, strict=False))


def _validate_neighbor(neighbor: str) -> str:
    return str(ipaddress.ip_address(neighbor))


def _frr_pod(router: str) -> str:
    if router not in {"a", "b"}:
        raise ValueError("router must be 'a' or 'b'")
    pods = _api().list_namespaced_pod(
        NAMESPACE,
        label_selector=(
            f"app.kubernetes.io/name={FRR_RELEASE},"
            f"app.kubernetes.io/component=router,"
            f"app.kubernetes.io/instance={router}"
        ),
    ).items
    running = [pod for pod in pods if pod.status.phase == "Running"]
    if len(running) != 1:
        raise RuntimeError(
            f"expected one running FRR-{router} pod, found "
            f"{[pod.metadata.name for pod in running]}"
        )
    return running[0].metadata.name


def _pod_exec(pod: str, container: str, command: list[str]) -> Any:
    return stream(
        _api().connect_get_namespaced_pod_exec,
        pod,
        NAMESPACE,
        container=container,
        command=command,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
    )


async def _exec_text(
    pod: str, container: str, command: list[str]
) -> Any:
    return await asyncio.to_thread(_pod_exec, pod, container, command)


async def _exec_json(
    pod: str, container: str, command: list[str]
) -> Any:
    output = await _exec_text(pod, container, command)
    # The Kubernetes stream client may deserialize a top-level JSON object
    # before returning it, depending on the exec response content.
    if isinstance(output, (dict, list)):
        return output
    decoder = json.JSONDecoder()
    for index, character in enumerate(output):
        if character not in "{[":
            continue
        try:
            return decoder.raw_decode(output[index:])[0]
        except json.JSONDecodeError:
            continue
    # websocket-client converts some JSON objects to their Python literal
    # representation before kubernetes.stream returns stdout.
    try:
        literal = ast.literal_eval(output)
        if isinstance(literal, (dict, list)):
            return literal
    except (SyntaxError, ValueError):
        pass
    raise RuntimeError(f"routing command returned no JSON: {output[:300]}")


async def _bng_vtysh(member: int, command: str) -> Any:
    _member_url(member, "/")
    return await _exec_json(
        f"{RELEASE}-{member}",
        RELEASE,
        ["ip", "netns", "exec", "dataplane", "vtysh", "-c", command],
    )


async def _frr_vtysh(router: str, command: str) -> tuple[str, Any]:
    pod = await asyncio.to_thread(_frr_pod, router)
    result = await _exec_json(
        pod, ROUTING_CONTAINER, ["vtysh", "-c", command]
    )
    return pod, result


def _routing_result(source: str, result: Any, **identity: Any) -> dict[str, Any]:
    return {
        **identity,
        "source": source,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "result": result,
    }


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
async def bng_running_config(
    member: int, section: str | None = None
) -> dict[str, Any]:
    """Dump the authoritative live configuration for one zero-based BNG StatefulSet member. Secrets are always redacted server-side. Optionally select a dotted section such as cgnat, ha, interfaces.core, aaa, or plugins.subscriber.auth.radius."""
    result = await _running_config(member, section)
    result["observed_at"] = datetime.now(timezone.utc).isoformat()
    return result


@mcp.tool()
async def bng_running_configs(
    section: str | None = None,
) -> dict[str, Any]:
    """Dump redacted live configurations for every BNG member and return exact leaf-level differences. Optionally select a dotted section."""
    members = []
    for member in range(MEMBER_COUNT):
        try:
            members.append(await _running_config(member, section))
        except Exception as exc:
            members.append({"member": member, "error": str(exc)})

    successful = [item for item in members if "config" in item]
    differences = []
    if len(successful) >= 2:
        baseline = _flatten_config(successful[0]["config"])
        for compared in successful[1:]:
            candidate = _flatten_config(compared["config"])
            for path in sorted(set(baseline) | set(candidate)):
                if baseline.get(path) != candidate.get(path):
                    differences.append(
                        {
                            "path": path,
                            f"member_{successful[0]['member']}": baseline.get(path),
                            f"member_{compared['member']}": candidate.get(path),
                        }
                    )
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "section": section or "all",
        "members": members,
        "differences": differences,
    }


@mcp.tool()
async def bng_bgp_status(member: int) -> dict[str, Any]:
    """Return structured FRR BGP summary and peer state for one zero-based BNG member."""
    result = await _bng_vtysh(member, "show bgp ipv4 unicast summary json")
    return _routing_result(
        "vtysh: show bgp ipv4 unicast summary json", result, member=member
    )


@mcp.tool()
async def bng_routes(
    member: int, prefix: str | None = None
) -> dict[str, Any]:
    """Return the structured FRR RIB for one BNG member, optionally restricted to one validated IPv4 prefix."""
    prefix = _validate_prefix(prefix)
    command = "show ip route" + (f" {prefix}" if prefix else "") + " json"
    result = await _bng_vtysh(member, command)
    return _routing_result(f"vtysh: {command}", result, member=member)


@mcp.tool()
async def bng_bgp_routes(
    member: int, prefix: str | None = None
) -> dict[str, Any]:
    """Return the structured BGP RIB for one BNG member, optionally restricted to one validated IPv4 prefix."""
    prefix = _validate_prefix(prefix)
    command = "show bgp ipv4 unicast" + (f" {prefix}" if prefix else "") + " json"
    result = await _bng_vtysh(member, command)
    return _routing_result(f"vtysh: {command}", result, member=member)


@mcp.tool()
async def bng_vpp_routes(member: int, prefix: str) -> dict[str, Any]:
    """Return the authoritative VPP FIB detail for one validated IPv4 prefix on a BNG member."""
    _member_url(member, "/")
    prefix = _validate_prefix(prefix)
    command = [
        "vppctl", "-s", "/run/osvbng/cli.sock", "show", "ip", "fib", prefix
    ]
    result = await _exec_text(f"{RELEASE}-{member}", RELEASE, command)
    return _routing_result(
        f"vppctl: show ip fib {prefix}", {"output": result}, member=member
    )


@mcp.tool()
async def frr_bgp_status(router: str) -> dict[str, Any]:
    """Return structured BGP summary and peer state for ISP FRR router 'a' or 'b'."""
    pod, result = await _frr_vtysh(
        router, "show bgp ipv4 unicast summary json"
    )
    return _routing_result(
        "vtysh: show bgp ipv4 unicast summary json",
        result,
        router=router,
        pod=pod,
    )


@mcp.tool()
async def frr_routes(
    router: str, prefix: str | None = None
) -> dict[str, Any]:
    """Return the structured FRR RIB for ISP router 'a' or 'b', optionally restricted to one validated IPv4 prefix."""
    prefix = _validate_prefix(prefix)
    command = "show ip route" + (f" {prefix}" if prefix else "") + " json"
    pod, result = await _frr_vtysh(router, command)
    return _routing_result(
        f"vtysh: {command}", result, router=router, pod=pod
    )


@mcp.tool()
async def frr_bgp_routes(
    router: str, prefix: str | None = None
) -> dict[str, Any]:
    """Return the structured BGP RIB for ISP router 'a' or 'b', optionally restricted to one validated IPv4 prefix."""
    prefix = _validate_prefix(prefix)
    command = "show bgp ipv4 unicast" + (f" {prefix}" if prefix else "") + " json"
    pod, result = await _frr_vtysh(router, command)
    return _routing_result(
        f"vtysh: {command}", result, router=router, pod=pod
    )


@mcp.tool()
async def frr_neighbor_routes(
    router: str, neighbor: str, direction: str
) -> dict[str, Any]:
    """Return structured advertised or received BGP routes for one validated FRR neighbor. Direction must be 'advertised' or 'received'."""
    neighbor = _validate_neighbor(neighbor)
    if direction not in {"advertised", "received"}:
        raise ValueError("direction must be 'advertised' or 'received'")
    command = (
        f"show bgp ipv4 unicast neighbors {neighbor} "
        f"{direction}-routes json"
    )
    pod, result = await _frr_vtysh(router, command)
    return _routing_result(
        f"vtysh: {command}",
        result,
        router=router,
        pod=pod,
        neighbor=neighbor,
        direction=direction,
    )


@mcp.tool()
async def routing_overview(
    cgnat_prefix: str = "100.64.100.0/24",
) -> dict[str, Any]:
    """Return one live routing overview across every BNG and both ISP FRRs: BGP summaries, default routes, CGNAT routes, and BNG VPP FIB state."""
    cgnat_prefix = _validate_prefix(cgnat_prefix)

    async def capture(name: str, operation) -> dict[str, Any]:
        try:
            return {"name": name, "data": await operation}
        except Exception as exc:
            return {"name": name, "error": str(exc)}

    operations = []
    for member in range(MEMBER_COUNT):
        operations.extend(
            [
                capture(f"bng-{member}-bgp", bng_bgp_status(member)),
                capture(f"bng-{member}-default-rib", bng_routes(member, "0.0.0.0/0")),
                capture(f"bng-{member}-cgnat-rib", bng_routes(member, cgnat_prefix)),
                capture(f"bng-{member}-default-fib", bng_vpp_routes(member, "0.0.0.0/0")),
                capture(f"bng-{member}-cgnat-fib", bng_vpp_routes(member, cgnat_prefix)),
            ]
        )
    for router in ("a", "b"):
        operations.extend(
            [
                capture(f"frr-{router}-bgp", frr_bgp_status(router)),
                capture(f"frr-{router}-default", frr_routes(router, "0.0.0.0/0")),
                capture(f"frr-{router}-cgnat", frr_routes(router, cgnat_prefix)),
            ]
        )
    # kubernetes.stream mutates its ApiClient while upgrading to WebSocket and
    # is not thread-safe. Run the bounded set of read-only checks sequentially.
    checks = [await operation for operation in operations]
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "healthy": all("error" not in check for check in checks),
        "cgnat_prefix": cgnat_prefix,
        "checks": checks,
    }


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
