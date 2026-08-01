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
        noc_group_name = required_env("NOC_GROUP_NAME")
        noc_connection_id = required_env("NOC_CONNECTION_ID")
        admin_connection_id = required_env("ADMIN_CONNECTION_ID")
        kubernetes_connection_id = required_env("KUBERNETES_CONNECTION_ID")
        noc_model_id = required_env("NOC_MODEL_ID")
        admin_model_id = required_env("ADMIN_MODEL_ID")
        groups = request("/api/v1/groups/", token=token)
        noc_group = next(
            (group for group in groups if group.get("name") == noc_group_name),
            None,
        )
        if noc_group is None:
            noc_group = request(
                "/api/v1/groups/create",
                token=token,
                payload={
                    "name": noc_group_name,
                    "description": required_env("NOC_GROUP_DESCRIPTION"),
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
                        "name": noc_group_name,
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
            not in {noc_connection_id, admin_connection_id}
        ]
        noc_connection = {
            "url": required_env("NOC_MCP_URL"),
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
                "id": noc_connection_id,
                "name": required_env("NOC_CONNECTION_NAME"),
                "description": required_env("NOC_CONNECTION_DESCRIPTION"),
            },
        }
        admin_connection = {
            "url": required_env("ADMIN_MCP_URL"),
            "path": "",
            "type": "mcp",
            "auth_type": "bearer",
            "headers": None,
            "key": required_env("MCP_ADMIN_API_KEY"),
            # Empty grants intentionally make this connection admin-only.
            "config": {"enable": True, "access_grants": []},
            "info": {
                "id": admin_connection_id,
                "name": required_env("ADMIN_CONNECTION_NAME"),
                "description": required_env("ADMIN_CONNECTION_DESCRIPTION"),
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
                if model.get("id") == admin_model_id
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
                "name": required_env("ADMIN_MODEL_NAME"),
                "meta": {
                    **(admin_model.get("meta") or {}),
                    "toolIds": [
                        f"server:mcp:{admin_connection_id}",
                        f"server:mcp:{kubernetes_connection_id}",
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

            noc_params = dict(admin_model.get("params") or {})
            noc_params["system"] = required_env("NOC_SYSTEM_PROMPT")
            noc_form = {
                "id": noc_model_id,
                "base_model_id": admin_model.get("base_model_id"),
                "name": required_env("NOC_MODEL_NAME"),
                "meta": {
                    **(admin_model.get("meta") or {}),
                    "toolIds": [f"server:mcp:{noc_connection_id}"],
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
                    "model": required_env("NOC_MODEL_ID"),
                    # The browser UI copies model.info.meta.toolIds into this
                    # request field; direct API validation must do the same.
                    "tool_ids": [f"server:mcp:{required_env('NOC_CONNECTION_ID')}"],
                        "messages": [
                            {
                                "role": "user",
                                "content": required_env("NOC_ALLOWED_TEST_PROMPT"),
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
                denial_chat = request(
                    "/api/chat/completions",
                    token=noc_signin["token"],
                    payload={
                        "model": required_env("NOC_MODEL_ID"),
                        "tool_ids": [f"server:mcp:{required_env('NOC_CONNECTION_ID')}"],
                        "messages": [
                            {
                                "role": "user",
                                "content": required_env("NOC_DENIED_TEST_PROMPT"),
                            }
                        ],
                        "stream": False,
                    },
                )
                denial_chat_message = (
                    ((denial_chat.get("choices") or [{}])[0].get("message") or {})
                )
                output["noc_denial_wording"] = {
                    "content": denial_chat_message.get("content"),
                    "tool_calls": denial_chat_message.get("tool_calls"),
                }
                denied_probe = request(
                    "/api/chat/completions",
                    token=noc_signin["token"],
                    payload={
                        "model": required_env("NOC_MODEL_ID"),
                        "tool_ids": [f"server:mcp:{required_env('ADMIN_CONNECTION_ID')}"],
                        "messages": [
                            {
                                "role": "user",
                                "content": required_env("NOC_ADMIN_PROBE_PROMPT"),
                            }
                        ],
                        "stream": False,
                    },
                )
                denied_message = (
                    ((denied_probe.get("choices") or [{}])[0].get("message") or {})
                )
                output["noc_admin_probe"] = {
                    "content": denied_message.get("content"),
                    "tool_calls": denied_message.get("tool_calls"),
                    "admin_tool_denied": not bool(denied_message.get("tool_calls")),
                }
            except RuntimeError as exc:
                output["noc_chat"] = {"error": str(exc)}
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
