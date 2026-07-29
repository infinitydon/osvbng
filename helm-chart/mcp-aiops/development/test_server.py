import importlib
import os
import unittest
from unittest.mock import AsyncMock, patch


os.environ["OSVBNG_MEMBER_COUNT"] = "2"
server = importlib.import_module("server")


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_member_bounds(self):
        with self.assertRaises(ValueError):
            server._member_url(2, "/api/show/ha/status")

    async def test_health_reports_all_members(self):
        with patch.object(server, "_request", new=AsyncMock(return_value={"ok": True})):
            result = await server.bng_health()
        self.assertTrue(result["healthy"])
        self.assertEqual(2, len(result["members"]))

    async def test_active_member_selection(self):
        active = {
            "result": {
                "data": {
                    "srgs": [{"name": "default", "state": "ACTIVE"}]
                }
            }
        }
        standby = {
            "result": {
                "data": {
                    "srgs": [{"name": "default", "state": "STANDBY"}]
                }
            }
        }
        with patch.object(
            server,
            "_request",
            new=AsyncMock(side_effect=[standby, active]),
        ):
            self.assertEqual(1, await server._active_member())

    async def test_cgnat_pool_uses_active_member(self):
        with (
            patch.object(
                server, "_active_member", new=AsyncMock(return_value=1)
            ),
            patch.object(
                server,
                "_request",
                new=AsyncMock(return_value={"member": 1}),
            ) as request,
        ):
            result = await server.cgnat_pools()
        self.assertEqual(1, result["member"])
        request.assert_awaited_once_with(1, "/api/show/cgnat/pools")

    async def test_running_config_redacts_and_selects_dotted_key(self):
        response = {
            "result": {
                "data": {
                    "plugins": {
                        "subscriber.auth.radius": {
                            "servers": [
                                {"host": "radius", "secret": "do-not-leak"}
                            ]
                        }
                    }
                }
            }
        }
        with patch.object(
            server, "_request", new=AsyncMock(return_value=response)
        ):
            result = await server.bng_running_config(
                0, "plugins.subscriber.auth.radius"
            )
        self.assertEqual(
            "<redacted>", result["config"]["servers"][0]["secret"]
        )
        self.assertNotIn("do-not-leak", str(result))

    async def test_running_configs_returns_leaf_diff(self):
        first = {
            "result": {
                "data": {
                    "ha": {"node_id": "bng-a", "priority": 100},
                    "password": "first",
                }
            }
        }
        second = {
            "result": {
                "data": {
                    "ha": {"node_id": "bng-b", "priority": 90},
                    "password": "second",
                }
            }
        }
        with patch.object(
            server,
            "_request",
            new=AsyncMock(side_effect=[first, second]),
        ):
            result = await server.bng_running_configs()
        paths = {item["path"] for item in result["differences"]}
        self.assertEqual({"ha.node_id", "ha.priority"}, paths)
        self.assertNotIn("first", str(result))
        self.assertNotIn("second", str(result))

    async def test_mutation_disabled(self):
        with self.assertRaises(PermissionError):
            await server.ha_switchover(confirm=True)

    async def test_ue_mutation_disabled(self):
        with self.assertRaises(PermissionError):
            await server.ue_session_create(1, confirm=True)

    async def test_ue_status(self):
        with patch.object(
            server,
            "_ue_request",
            new=AsyncMock(return_value={"session-id": 1}),
        ) as request:
            result = await server.ue_session_status(1)
        self.assertEqual(1, result["session-id"])
        request.assert_awaited_once_with("/sessions/1")

    async def test_ue_session_range(self):
        with patch.object(
            server,
            "_ue_request",
            new=AsyncMock(
                return_value={
                    "requested_range": {
                        "start_session_id": 1,
                        "end_session_id": 10,
                    }
                }
            ),
        ) as request:
            result = await server.ue_session_range(1, 10)
        self.assertEqual(10, result["requested_range"]["end_session_id"])
        request.assert_awaited_once_with(
            "/sessions?start_session_id=1&end_session_id=10&include_inactive=true"
        )


if __name__ == "__main__":
    unittest.main()
