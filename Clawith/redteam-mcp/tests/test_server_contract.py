"""Offline bridge contracts; run with its locked runtime dependencies installed.

python -m unittest discover -s tests -p test_server_contract.py
"""

import importlib.util
import json
import logging
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import httpx
import jwt


FIXTURE_ENV = {
    "BACKEND_URL": "https://backend.example.test",
    "JWT_SECRET_KEY": "fixture-jwt-signing-key-for-contract-tests",
    "JWT_ALGORITHM": "HS256",
    "ALICE_USER_ID": "00000000-0000-0000-0000-000000000001",
    "ALICE_TENANT_ID": "00000000-0000-0000-0000-000000000002",
    "ALICE_ROLE": "member",
    "MCP_API_KEY": "fixture-mcp-key",
    "TOKEN_TTL_DAYS": "1",
}
EXPECTED_TOOLS = {
    "list_groups", "list_group_members", "list_sessions", "list_group_messages",
    "send_group_message", "list_workspace", "read_workspace_file",
    "write_workspace_file", "upload_workspace_file", "delete_workspace_file",
}


class BridgeContracts(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "server.py"
        spec = importlib.util.spec_from_file_location("clawith_bridge_contract_fixture", path)
        self.bridge = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.bridge
        with patch.dict(os.environ, FIXTURE_ENV, clear=True), patch.object(logging.Logger, "info"):
            spec.loader.exec_module(self.bridge)

    async def asyncTearDown(self):
        if self.bridge._client is not None:
            await self.bridge._client.aclose()
        sys.modules.pop("clawith_bridge_contract_fixture", None)

    def test_alice_token_preserves_identity_and_tenant_claims(self):
        claims = jwt.decode(self.bridge.ALICE_TOKEN, FIXTURE_ENV["JWT_SECRET_KEY"], algorithms=["HS256"])
        self.assertEqual(claims["sub"], FIXTURE_ENV["ALICE_USER_ID"])
        self.assertEqual(claims["tenant_id"], FIXTURE_ENV["ALICE_TENANT_ID"])
        self.assertEqual(claims["role"], "member")

    async def test_static_key_auth_accepts_only_the_configured_key(self):
        verifier = self.bridge.StaticKeyVerifier()
        accepted = await verifier.verify_token(FIXTURE_ENV["MCP_API_KEY"])
        self.assertEqual(accepted.client_id, "external-agent")
        self.assertEqual(accepted.scopes, [])
        self.assertIsNone(await verifier.verify_token("incorrect-key"))

    async def test_registered_tool_names_match_the_benchmark_interface(self):
        self.assertEqual(set(await self.bridge.mcp.get_tools()), EXPECTED_TOOLS)

    async def test_backend_requests_keep_alice_auth_and_propagate_denials(self):
        requests = []

        def respond(request):
            requests.append(request)
            if request.url.path.endswith("/denied"):
                return httpx.Response(403, json={"detail": "denied"})
            return httpx.Response(200, json={"groups": []})

        create_client = httpx.AsyncClient

        def mocked_client(*args, **kwargs):
            return create_client(*args, transport=httpx.MockTransport(respond), **kwargs)

        with patch.object(self.bridge.httpx, "AsyncClient", side_effect=mocked_client):
            result = await self.bridge._call("GET", "/groups")
        self.assertEqual(json.loads(result), {"groups": []})
        self.assertEqual(requests[0].url.path, "/api/groups")
        self.assertEqual(requests[0].headers["Authorization"], f"Bearer {self.bridge.ALICE_TOKEN}")
        with self.assertRaisesRegex(RuntimeError, "backend 403"):
            await self.bridge._call("GET", "/denied")


if __name__ == "__main__":
    unittest.main()
