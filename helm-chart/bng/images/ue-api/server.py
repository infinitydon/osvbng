import ipaddress
import json
import os
import re
import socket
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


SOCKET_PATH = os.getenv("BNGBLASTER_SOCKET", "/run/shared/bngblaster.sock")
SESSION_CAPACITY = int(os.getenv("UE_SESSION_CAPACITY", "100"))
SESSION_TIMEOUT = int(os.getenv("UE_SESSION_TIMEOUT", "30"))
LISTEN_PORT = int(os.getenv("UE_API_PORT", "8081"))
SESSION_PATH = re.compile(r"^/sessions/([1-9][0-9]*)$")
PING_PATH = re.compile(r"^/sessions/([1-9][0-9]*)/ping$")
CURL_PATH = re.compile(r"^/sessions/([1-9][0-9]*)/curl$")


def rpc(command: str, arguments: dict | None = None) -> dict:
    request = {"command": command}
    if arguments:
        request["arguments"] = arguments
    payload = json.dumps(request).encode()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(10)
        client.connect(SOCKET_PATH)
        client.sendall(payload)
        chunks = []
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    result = json.loads(b"".join(chunks))
    if result.get("code", 500) >= 400:
        raise RuntimeError(result.get("message", f"{command} failed"))
    return result


def validate_session_id(session_id: int) -> None:
    if session_id < 1 or session_id > SESSION_CAPACITY:
        raise ValueError(f"session id must be between 1 and {SESSION_CAPACITY}")


def session_info(session_id: int) -> dict:
    validate_session_id(session_id)
    return rpc("session-info", {"session-id": session_id})["session-info"]


def wait_for_state(session_id: int, active: bool) -> dict:
    deadline = time.monotonic() + SESSION_TIMEOUT
    last = {}
    while time.monotonic() < deadline:
        last = session_info(session_id)
        is_active = (
            last.get("session-state") == "Established"
            or last.get("dhcp-state") == "Bound"
        )
        is_inactive = (
            last.get("session-state") == "Terminated"
            and last.get("dhcp-state") == "Init"
        )
        if (active and is_active) or (not active and is_inactive):
            return last
        time.sleep(1)
    raise TimeoutError(f"session {session_id} did not reach requested state: {last}")


def configure_interface(session_id: int, info: dict) -> None:
    interface = f"bbl{session_id}"
    address = info.get("ipv4-address")
    subprocess.run(["ip", "link", "set", interface, "up"], check=True)
    table = str(100 + session_id)
    subprocess.run(
        ["ip", "route", "replace", "table", table, "default", "dev", interface],
        check=True,
    )
    if address:
        rule = subprocess.run(
            ["ip", "rule", "show"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        marker = f"from {address} lookup {table}"
        if marker not in rule:
            subprocess.run(
                ["ip", "rule", "add", "from", f"{address}/32", "table", table],
                check=True,
            )


def start_session(session_id: int) -> dict:
    validate_session_id(session_id)
    rpc("session-start", {"session-id": session_id})
    info = wait_for_state(session_id, True)
    configure_interface(session_id, info)
    return info


def stop_session(session_id: int) -> dict:
    validate_session_id(session_id)
    info = session_info(session_id)
    address = info.get("ipv4-address")
    try:
        rpc("dhcp-release", {"session-id": session_id})
    except RuntimeError:
        pass
    rpc("session-stop", {"session-id": session_id})
    stopped = wait_for_state(session_id, False)
    table = str(100 + session_id)
    if address:
        while (
            subprocess.run(
                ["ip", "rule", "del", "from", f"{address}/32", "table", table],
                capture_output=True,
            ).returncode
            == 0
        ):
            pass
    subprocess.run(["ip", "route", "flush", "table", table], check=False)
    return stopped


def run_ping(session_id: int, destination: str, count: int) -> dict:
    info = session_info(session_id)
    if info.get("dhcp-state") != "Bound":
        raise RuntimeError(f"session {session_id} is not DHCP Bound")
    if count < 1 or count > 10:
        raise ValueError("count must be between 1 and 10")
    try:
        ipaddress.ip_address(destination)
    except ValueError:
        if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", destination):
            raise ValueError("invalid ping destination")
    completed = subprocess.run(
        ["ping", "-I", f"bbl{session_id}", "-c", str(count), "-W", "3", destination],
        capture_output=True,
        text=True,
        timeout=(count * 4) + 2,
    )
    return {
        "session_id": session_id,
        "interface": f"bbl{session_id}",
        "destination": destination,
        "success": completed.returncode == 0,
        "output": (completed.stdout + completed.stderr)[-8000:],
    }


def run_curl(session_id: int, url: str, max_time: int) -> dict:
    info = session_info(session_id)
    if info.get("dhcp-state") != "Bound":
        raise RuntimeError(f"session {session_id} is not DHCP Bound")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("url must use http or https")
    if max_time < 1 or max_time > 30:
        raise ValueError("max_time must be between 1 and 30")
    completed = subprocess.run(
        [
            "curl",
            "--silent",
            "--show-error",
            "--location",
            "--interface",
            f"bbl{session_id}",
            "--max-time",
            str(max_time),
            "--write-out",
            "\nHTTP_STATUS:%{http_code}\n",
            url,
        ],
        capture_output=True,
        text=True,
        timeout=max_time + 2,
    )
    return {
        "session_id": session_id,
        "interface": f"bbl{session_id}",
        "url": url,
        "success": completed.returncode == 0,
        "output": (completed.stdout + completed.stderr)[-16000:],
    }


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status: int, payload: dict | list) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def handle_error(self, exc: Exception) -> None:
        status = 400 if isinstance(exc, (ValueError, RuntimeError)) else 500
        self.send_json(status, {"error": type(exc).__name__, "message": str(exc)})

    def do_GET(self) -> None:
        try:
            if self.path == "/health":
                self.send_json(
                    200,
                    {
                        "status": "ok",
                        "capacity": SESSION_CAPACITY,
                        "socket": os.path.exists(SOCKET_PATH),
                    },
                )
                return
            if self.path == "/sessions":
                result = []
                for session_id in range(1, SESSION_CAPACITY + 1):
                    try:
                        result.append(session_info(session_id))
                    except Exception as exc:
                        result.append({"session-id": session_id, "error": str(exc)})
                self.send_json(200, {"capacity": SESSION_CAPACITY, "sessions": result})
                return
            match = SESSION_PATH.fullmatch(self.path)
            if match:
                self.send_json(200, session_info(int(match.group(1))))
                return
            self.send_json(404, {"message": "not found"})
        except Exception as exc:
            self.handle_error(exc)

    def do_POST(self) -> None:
        try:
            match = SESSION_PATH.fullmatch(self.path)
            if match:
                self.send_json(200, start_session(int(match.group(1))))
                return
            match = PING_PATH.fullmatch(self.path)
            if match:
                body = self.read_json()
                self.send_json(
                    200,
                    run_ping(
                        int(match.group(1)),
                        body.get("destination", "1.1.1.1"),
                        int(body.get("count", 3)),
                    ),
                )
                return
            match = CURL_PATH.fullmatch(self.path)
            if match:
                body = self.read_json()
                self.send_json(
                    200,
                    run_curl(
                        int(match.group(1)),
                        body.get("url", "http://example.com"),
                        int(body.get("max_time", 10)),
                    ),
                )
                return
            self.send_json(404, {"message": "not found"})
        except Exception as exc:
            self.handle_error(exc)

    def do_DELETE(self) -> None:
        try:
            match = SESSION_PATH.fullmatch(self.path)
            if match:
                self.send_json(200, stop_session(int(match.group(1))))
                return
            self.send_json(404, {"message": "not found"})
        except Exception as exc:
            self.handle_error(exc)

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler).serve_forever()
