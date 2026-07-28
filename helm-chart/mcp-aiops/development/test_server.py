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
