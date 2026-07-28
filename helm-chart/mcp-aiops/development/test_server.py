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


if __name__ == "__main__":
    unittest.main()
