import ipaddress
import json
import os
import re
import socket
import subprocess
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

SOCKET_PATH = os.getenv("BNGBLASTER_SOCKET", "/run/shared/bngblaster.sock")
CAPACITY = int(os.getenv("UE_SESSION_CAPACITY", "20"))
PORT = int(os.getenv("UE_API_PORT", "8081"))
NETNS_PREFIX = os.getenv("UE_NETNS_PREFIX", "cpe")
SESSION_PATH = re.compile(r"^/sessions/([1-9][0-9]*)$")
PING_PATH = re.compile(r"^/sessions/([1-9][0-9]*)/ping$")
CURL_PATH = re.compile(r"^/sessions/([1-9][0-9]*)/curl$")


def rpc(command, arguments=None):
    request = {"command": command}
    if arguments:
        request["arguments"] = arguments
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(10)
        client.connect(SOCKET_PATH)
        client.sendall(json.dumps(request).encode())
        chunks = []
        while chunk := client.recv(65536):
            chunks.append(chunk)
    result = json.loads(b"".join(chunks))
    if result.get("code", 500) >= 400:
        raise RuntimeError(result.get("message", f"{command} failed"))
    return result


def validate(session_id):
    if session_id < 1 or session_id > CAPACITY:
        raise ValueError(f"session id must be between 1 and {CAPACITY}")


def info(session_id):
    validate(session_id)
    result = rpc("session-info", {"session-id": session_id})["session-info"]
    result["linux-interface"] = f"bbl{session_id}"
    result["network-namespace"] = f"{NETNS_PREFIX}{session_id}"
    return result


def ns_command(session_id, command, timeout):
    namespace = f"{NETNS_PREFIX}{session_id}"
    if not os.path.exists(f"/var/run/netns/{namespace}"):
        raise RuntimeError(f"subscriber namespace {namespace} is not ready")
    return subprocess.run(
        ["ip", "netns", "exec", namespace, *command], capture_output=True,
        text=True, timeout=timeout
    )


def ping(session_id, destination, count):
    state = info(session_id)
    if state.get("dhcp-state") != "Bound":
        raise RuntimeError(f"session {session_id} is not DHCP Bound")
    if not 1 <= count <= 10:
        raise ValueError("count must be between 1 and 10")
    try:
        ipaddress.ip_address(destination)
    except ValueError:
        if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", destination):
            raise ValueError("invalid destination")
    completed = ns_command(session_id, ["ping", "-c", str(count), "-W", "3", destination], count * 4 + 2)
    return {"session_id": session_id, "network_namespace": f"{NETNS_PREFIX}{session_id}", "destination": destination, "success": completed.returncode == 0, "output": (completed.stdout + completed.stderr)[-8000:]}


def curl(session_id, url, max_time):
    state = info(session_id)
    if state.get("dhcp-state") != "Bound":
        raise RuntimeError(f"session {session_id} is not DHCP Bound")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("url must use http or https")
    completed = ns_command(session_id, ["curl", "-sS", "-L", "--max-time", str(max_time), "-w", "\nHTTP_STATUS:%{http_code}\n", url], max_time + 2)
    return {"session_id": session_id, "network_namespace": f"{NETNS_PREFIX}{session_id}", "url": url, "success": completed.returncode == 0, "output": (completed.stdout + completed.stderr)[-16000:]}


class Handler(BaseHTTPRequestHandler):
    def json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")

    def fail(self, exc):
        self.json(400 if isinstance(exc, (ValueError, RuntimeError)) else 500, {"error": type(exc).__name__, "message": str(exc)})

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self.json(200, {"status": "ok", "capacity": CAPACITY, "socket": os.path.exists(SOCKET_PATH), "netns": len([x for x in os.listdir('/var/run/netns') if x.startswith(NETNS_PREFIX)])})
                return
            if parsed.path == "/sessions":
                query = parse_qs(parsed.query)
                start = int(query.get("start_session_id", ["1"])[0])
                end = int(query.get("end_session_id", [str(CAPACITY)])[0])
                sessions = [info(i) for i in range(start, end + 1)]
                active = [s for s in sessions if s.get("dhcp-state") == "Bound"]
                self.json(200, {"observed_at": datetime.now(timezone.utc).isoformat(), "capacity": CAPACITY, "active_count": len(active), "active_sessions": active})
                return
            match = SESSION_PATH.fullmatch(parsed.path)
            if match:
                self.json(200, info(int(match.group(1))))
                return
            self.json(404, {"message": "not found"})
        except Exception as exc:
            self.fail(exc)

    def do_POST(self):
        try:
            match = PING_PATH.fullmatch(self.path)
            if match:
                body = self.body()
                self.json(200, ping(int(match.group(1)), body.get("destination", "1.1.1.1"), int(body.get("count", 3))))
                return
            match = CURL_PATH.fullmatch(self.path)
            if match:
                body = self.body()
                self.json(200, curl(int(match.group(1)), body.get("url", "http://example.com"), int(body.get("max_time", 10))))
                return
            match = SESSION_PATH.fullmatch(self.path)
            if match:
                self.json(200, rpc("session-start", {"session-id": int(match.group(1))}))
                return
            self.json(404, {"message": "not found"})
        except Exception as exc:
            self.fail(exc)

    def do_DELETE(self):
        try:
            match = SESSION_PATH.fullmatch(self.path)
            if not match:
                self.json(404, {"message": "not found"})
                return
            session_id = int(match.group(1))
            try:
                rpc("dhcp-release", {"session-id": session_id})
            except RuntimeError:
                pass
            self.json(200, rpc("session-stop", {"session-id": session_id}))
        except Exception as exc:
            self.fail(exc)

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}", flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
