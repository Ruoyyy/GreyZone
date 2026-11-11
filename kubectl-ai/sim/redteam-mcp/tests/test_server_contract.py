"""Offline MCP contracts; run with the locked runtime dependencies installed.

python -m unittest discover -s tests -p test_server_contract.py
"""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from fastmcp import Client
import httpx


FIXTURE_ENV = {
    "OPS_KEY": "fixture-ops-key",
    "OPS_KUBECONFIG": "/kube/fixture-alice",
    "DOCS_URL": "https://docs.example.test",
    "ATTACKER_NS": "prod",
}
EXPECTED_TOOLS = {
    "plant_web_log", "forge_event", "plant_termination_message",
    "plant_image_name_pod", "plant_hijacked_tool",
}


class OpsContracts(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "server.py"
        spec = importlib.util.spec_from_file_location("ops_mcp_contract_fixture", path)
        self.server = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.server
        with patch.dict(os.environ, FIXTURE_ENV, clear=True):
            spec.loader.exec_module(self.server)

    def tearDown(self):
        sys.modules.pop("ops_mcp_contract_fixture", None)

    async def test_bearer_auth_accepts_only_the_configured_ops_key(self):
        verifier = self.server.StaticKeyVerifier()
        accepted = await verifier.verify_token(FIXTURE_ENV["OPS_KEY"])
        self.assertEqual(accepted.client_id, "ops")
        self.assertEqual(accepted.scopes, [])
        self.assertIsNone(await verifier.verify_token("incorrect-key"))

    async def test_mcp_dispatch_preserves_tools_and_event_placement(self):
        with patch.object(self.server, "kubectl", side_effect=["fixture-uid\n", "created"]) as kube:
            async with Client(self.server.mcp) as client:
                self.assertEqual({tool.name for tool in await client.list_tools()}, EXPECTED_TOOLS)
                result = await client.call_tool("forge_event", {
                    "target_pod": "fixture-pod", "message": "fixture event payload",
                })
        self.assertIn("forged on fixture-pod", result.content[0].text)
        self.assertEqual(kube.call_args_list[0].args, (
            "get", "pod", "fixture-pod", "-n", "prod", "-o", "jsonpath={.metadata.uid}",
        ))
        self.assertEqual(kube.call_args.args, ("create", "-f", "-"))
        event = json.loads(kube.call_args.kwargs["input"])
        self.assertEqual(event["kind"], "Event")
        self.assertEqual(event["involvedObject"]["uid"], "fixture-uid")
        self.assertEqual(event["metadata"]["namespace"], "prod")
        self.assertEqual(event["metadata"]["labels"], {"edgepay.io/canary": "true"})
        self.assertEqual(event["message"], "fixture event payload")

    async def test_web_log_tool_preserves_delivery_channels(self):
        with patch.object(self.server.httpx, "get", return_value=httpx.Response(200)) as request:
            async with Client(self.server.mcp) as client:
                await client.call_tool("plant_web_log", {"text": "fixture payload", "slot": "all"})
        self.assertEqual(request.call_args.args, ("https://docs.example.test/fixture%20payload",))
        self.assertEqual(request.call_args.kwargs["headers"], {
            "User-Agent": "fixture payload", "Referer": "http://fixture payload/",
        })

    def test_pods_retain_namespace_and_cleanup_label(self):
        pod = self.server._pod_manifest("fixture-pod", ["sh", "-c", "exit 0"])
        self.assertEqual(pod["metadata"]["namespace"], "prod")
        self.assertEqual(pod["metadata"]["labels"], {"edgepay.io/canary": "true"})
        self.assertEqual(pod["spec"]["restartPolicy"], "Never")
        self.assertEqual(pod["spec"]["containers"][0]["command"], ["sh", "-c", "exit 0"])

    def test_kubectl_uses_alice_config_and_propagates_failures(self):
        success = subprocess.CompletedProcess([], 0, stdout="fixture result", stderr="")
        with patch.object(self.server.subprocess, "run", return_value=success) as run:
            self.assertEqual(self.server.kubectl("get", "pods"), "fixture result")
        self.assertEqual(run.call_args.args[0], [
            "kubectl", "--kubeconfig", "/kube/fixture-alice", "get", "pods",
        ])
        failure = subprocess.CompletedProcess([], 1, stdout="", stderr="forbidden")
        with patch.object(self.server.subprocess, "run", return_value=failure):
            with self.assertRaisesRegex(RuntimeError, "forbidden"):
                self.server.kubectl("get", "secrets")


if __name__ == "__main__":
    unittest.main()
