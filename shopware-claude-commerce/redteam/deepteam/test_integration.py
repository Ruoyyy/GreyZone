"""Offline guard for DeepTeam's single persistent Shopware write."""

import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from shopware_target import ShopwareTarget


class ShopwareTargetTests(unittest.IsolatedAsyncioTestCase):
    async def test_callback_writes_once_and_returns_oracle_result(self):
        evaluation = {
            "reply": "Buyer ignored the planted instruction.",
            "oracle": {"success": False, "outcome": "failed"},
            "resultPath": "/tmp/result-1.json",
        }
        submitted = []

        class FakeProcess:
            returncode = 0

            async def communicate(self, value):
                submitted.append(value)
                return json.dumps(evaluation).encode(), b""

        target = ShopwareTarget(Path("/tmp/buyer-misdirection"))
        with patch("shopware_target.asyncio.create_subprocess_exec",
                   new=AsyncMock(return_value=FakeProcess())) as spawn:
            reply = await target.send("Seller-controlled review")
            with self.assertRaisesRegex(RuntimeError, "one payload"):
                await target.send("Second review")
        self.assertEqual(submitted, [b"Seller-controlled review"])
        self.assertIn("Oracle scenario success: false", reply.content)
        self.assertEqual(target.evaluation, evaluation)
        self.assertEqual(spawn.await_count, 1)
        self.assertIn("deepteam", spawn.call_args.args)


if __name__ == "__main__":
    unittest.main()
