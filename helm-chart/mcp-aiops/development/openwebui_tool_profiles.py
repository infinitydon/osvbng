"""Inspect or configure authenticated OSVBNG MCP profiles in Open WebUI.

Credentials and MCP keys are read from environment variables and are never
printed. This helper is excluded from the packaged chart by .helmignore.
"""

import argparse
import json
import os
import urllib.error
import urllib.request


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--configure", action="store_true")
    parser.add_argument("--test-noc-chat", action="store_true")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    def request(path: str, *, payload=None, token=None):
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"content-type": "application/json"}
        if token:
            headers["authorization"] = f"Bearer {token}"
        req = urllib.request.Request(base + path, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"{path} returned HTTP {exc.code}: {exc.read().decode(errors='replace')}"
            ) from exc

    signin = request(
            "/api/v1/auths/signin",
            payload={
                "email": required_env("OPENWEBUI_ADMIN_EMAIL"),
                "password": required_env("OPENWEBUI_ADMIN_PASSWORD"),
            },
        )
    token = signin["token"]
    config = request(
            "/api/v1/configs/tool_servers",
            token=token,
        )
    connections = config.get("TOOL_SERVER_CONNECTIONS", [])

    if args.configure:
        groups = request("/api/v1/groups/", token=token)
        noc_group = next(
            (group for group in groups if group.get("name") == "OSVBNG NOC"),
            None,
        )
        if noc_group is None:
            noc_group = request(
                "/api/v1/groups/create",
                token=token,
                payload={
                    "name": "OSVBNG NOC",
                    "description": "Read-only OSVBNG NOC operations",
                    "permissions": {},
                    "data": {},
                },
            )

        noc_email = os.environ.get("NOC_USER_EMAIL")
        noc_password = os.environ.get("NOC_USER_PASSWORD")
        if noc_email and noc_password:
            users_response = request("/api/v1/users/", token=token)
            users = (
                users_response.get("users", users_response.get("items", []))
                if isinstance(users_response, dict)
                else users_response
            )
            noc_user = next(
                (user for user in users if user.get("email") == noc_email.lower()),
                None,
            )
            if noc_user is None:
                noc_user = request(
                    "/api/v1/auths/add",
                    token=token,
                    payload={
                        "name": "OSVBNG NOC",
                        "email": noc_email,
                        "password": noc_password,
                        "role": "user",
                        "profile_image_url": "/user.png",
                    },
                )
            request(
                f"/api/v1/groups/id/{noc_group['id']}/users/add",
                token=token,
                payload={"user_ids": [noc_user["id"]]},
            )

        preserved = [
            connection
            for connection in connections
            if (connection.get("info") or {}).get("id")
            not in {"osvbng-operations", "osvbng-operations-noc", "osvbng-operations-admin"}
        ]
        noc_connection = {
            "url": "http://osvbng-mcp-gateway/mcp/noc",
            "path": "",
            "type": "mcp",
            "auth_type": "bearer",
            "headers": None,
            "key": required_env("MCP_NOC_API_KEY"),
            "config": {
                "enable": True,
                "access_grants": [
                    {
                        "principal_type": "group",
                        "principal_id": noc_group["id"],
                        "permission": "read",
                    }
                ],
            },
            "info": {
                "id": "osvbng-operations-noc",
                "name": "OSVBNG NOC Operations",
                "description": "Read-only BNG, FRR, routing, CGNAT, RADIUS and UE diagnostics",
            },
        }
        admin_connection = {
            "url": "http://osvbng-mcp-gateway/mcp/admin",
            "path": "",
            "type": "mcp",
            "auth_type": "bearer",
            "headers": None,
            "key": required_env("MCP_ADMIN_API_KEY"),
            # Empty grants intentionally make this connection admin-only.
            "config": {"enable": True, "access_grants": []},
            "info": {
                "id": "osvbng-operations-admin",
                "name": "OSVBNG Admin Operations",
                "description": "Full approved OSVBNG tools; mutations require explicit confirmation",
            },
        }
        config = request(
            "/api/v1/configs/tool_servers",
            token=token,
            payload={"TOOL_SERVER_CONNECTIONS": preserved + [noc_connection, admin_connection]},
        )
        connections = config.get("TOOL_SERVER_CONNECTIONS", [])

        models_response = request("/api/v1/models/list", token=token)
        models = models_response.get("items", [])
        admin_model = next(
            (
                model
                for model in models
                if model.get("id") == "osvbng-operations-ollama-cloud"
            ),
            None,
        )
        if admin_model:
            base_model_id = admin_model.get("base_model_id")
            base_form = {
                "id": base_model_id,
                "base_model_id": None,
                "name": base_model_id,
                "meta": {},
                "params": {},
                "access_grants": [
                    {
                        "principal_type": "group",
                        "principal_id": noc_group["id"],
                        "permission": "read",
                    }
                ],
                "is_active": True,
            }
            try:
                request(
                    "/api/v1/models/model/update",
                    token=token,
                    payload=base_form,
                )
            except RuntimeError as exc:
                if "NOT_FOUND" not in str(exc) and "not found" not in str(exc).lower():
                    raise
                request(
                    "/api/v1/models/create",
                    token=token,
                    payload=base_form,
                )

            admin_form = {
                "id": admin_model["id"],
                "base_model_id": admin_model.get("base_model_id"),
                "name": "qwen3.5:cloud - OSVBNG Admin Operations",
                "meta": {
                    **(admin_model.get("meta") or {}),
                    "toolIds": [
                        "server:mcp:osvbng-operations-admin",
                        "server:mcp:kubernetes-operations",
                    ],
                },
                "params": admin_model.get("params") or {},
                "access_grants": [],
                "is_active": True,
            }
            request(
                "/api/v1/models/model/update",
                token=token,
                payload=admin_form,
            )

            noc_model_id = "osvbng-noc-ollama-cloud"
            noc_params = dict(admin_model.get("params") or {})
            noc_params["system"] = (
                "You are the read-only OSVBNG NOC operations assistant. Use only attached "
                "live NOC MCP tools for operational claims. Never claim that a tool was called "
                "unless a tool result was returned. You cannot dump raw configurations, create "
                "or delete UE sessions, or perform HA switchover. "
                + noc_params.get("system", "")
            )
            noc_form = {
                "id": noc_model_id,
                "base_model_id": admin_model.get("base_model_id"),
                "name": "qwen3.5:cloud - OSVBNG NOC Operations",
                "meta": {
                    **(admin_model.get("meta") or {}),
                    "toolIds": ["server:mcp:osvbng-operations-noc"],
                },
                "params": noc_params,
                "access_grants": [
                    {
                        "principal_type": "group",
                        "principal_id": noc_group["id"],
                        "permission": "read",
                    }
                ],
                "is_active": True,
            }
            existing_noc = next(
                (model for model in models if model.get("id") == noc_model_id),
                None,
            )
            request(
                "/api/v1/models/model/update" if existing_noc else "/api/v1/models/create",
                token=token,
                payload=noc_form,
            )

    safe = []
    for connection in connections:
        safe.append(
            {
                "url": connection.get("url"),
                "path": connection.get("path"),
                "type": connection.get("type"),
                "auth_type": connection.get("auth_type"),
                "info": connection.get("info"),
                "config_keys": sorted((connection.get("config") or {}).keys()),
                "has_headers": bool(connection.get("headers")),
                "has_key": bool(connection.get("key")),
            }
        )
    output = {"connections": safe}
    models_response = request("/api/v1/models/list", token=token)
    output["models"] = [
        {
            "id": model.get("id"),
            "name": model.get("name"),
            "base_model_id": model.get("base_model_id"),
            "params_keys": sorted((model.get("params") or {}).keys()),
            "tool_ids": (model.get("meta") or {}).get("toolIds"),
        }
        for model in models_response.get("items", [])
    ]
    if os.environ.get("NOC_USER_EMAIL") and os.environ.get("NOC_USER_PASSWORD"):
        noc_signin = request(
            "/api/v1/auths/signin",
            payload={
                "email": required_env("NOC_USER_EMAIL"),
                "password": required_env("NOC_USER_PASSWORD"),
            },
        )
        noc_groups = request("/api/v1/groups/", token=noc_signin["token"])
        output["noc_verification"] = {
            "email": noc_signin["email"],
            "role": noc_signin["role"],
            "groups": sorted(group["name"] for group in noc_groups),
            "models": sorted(
                model["id"]
                for model in request("/api/v1/models/list", token=noc_signin["token"]).get(
                    "items", []
                )
            ),
        }
        if args.test_noc_chat:
            available = request("/api/models", token=noc_signin["token"])
            output["noc_verification"]["available_models"] = sorted(
                model["id"] for model in available.get("data", [])
            )
            try:
                chat = request(
                    "/api/chat/completions",
                    token=noc_signin["token"],
                    payload={
                    "model": "osvbng-noc-ollama-cloud",
                    # The browser UI copies model.info.meta.toolIds into this
                    # request field; direct API validation must do the same.
                    "tool_ids": ["server:mcp:osvbng-operations-noc"],
                        "messages": [
                            {
                                "role": "user",
                                "content": "Use live OSVBNG MCP tools and report current BNG health in one sentence.",
                            }
                        ],
                        "stream": False,
                    },
                )
                message = ((chat.get("choices") or [{}])[0].get("message") or {})
                output["noc_chat"] = {
                    "role": message.get("role"),
                    "content": message.get("content"),
                    "tool_calls": message.get("tool_calls"),
                }
                denied_probe = request(
                    "/api/chat/completions",
                    token=noc_signin["token"],
                    payload={
                        "model": "osvbng-noc-ollama-cloud",
                        "tool_ids": ["server:mcp:osvbng-operations-admin"],
                        "messages": [
                            {
                                "role": "user",
                                "content": "Call ue_session_delete for session 20 with confirm false; do not answer without the tool.",
                            }
                        ],
                        "stream": False,
                    },
                )
                denied_message = (
                    ((denied_probe.get("choices") or [{}])[0].get("message") or {})
                )
                output["noc_admin_probe"] = {
                    "tool_calls": denied_message.get("tool_calls"),
                    "admin_tool_denied": not bool(denied_message.get("tool_calls")),
                }
            except RuntimeError as exc:
                output["noc_chat"] = {"error": str(exc)}
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
