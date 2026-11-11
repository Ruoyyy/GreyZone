"""Static public-name contracts; no server startup or MCP requests."""

import ast
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
PYTHON_SERVERS = (
    "Clawith/redteam-mcp/server.py",
    "Clawith/redteam/dreadnode/attacker_mcp.py",
    "ZerePy/redteam-mcp/server.py",
    "alpaca-mcp-server/sim/redteam-mcp/server.py",
    "kubectl-ai/sim/redteam-mcp/server.py",
    "shopware-claude-commerce/storefront/api/seller_mcp.py",
    "smart-home/sim/redteam-mcp/server.py",
)
APPLICATIONS = (
    "Clawith", "Inbox_Zero_Mail_Agent", "Letta_Issue_Agent", "ZerePy",
    "agent-openai-java-banking-assistant", "alpaca-mcp-server", "commonly",
    "kubectl-ai", "shopware-claude-commerce", "smart-home",
)


class RedteamMcpNames(unittest.TestCase):
    def test_python_advertised_names(self):
        for relative in PYTHON_SERVERS:
            with self.subTest(path=relative):
                names = []
                for node in ast.walk(ast.parse((ROOT / relative).read_text())):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"FastMCP", "MCPServer"}:
                        value = next((item.value for item in node.keywords if item.arg == "name"),
                                     node.args[0] if node.args else None)
                        names.append(ast.literal_eval(value))
                self.assertEqual(names, ["redteam-mcp"])

    def test_typescript_javascript_and_java_names(self):
        for relative in ("Inbox_Zero_Mail_Agent/apps/web/scripts/redteam-mcp.ts",
                         "commonly/commonly-mcp/src/user-index.js"):
            self.assertRegex((ROOT / relative).read_text(), r"name:\s*['\"]redteam-mcp['\"]")
        properties = ROOT / "agent-openai-java-banking-assistant/app/business-api/redteam-mcp/src/main/resources/application.properties"
        self.assertIn("spring.ai.mcp.server.name=redteam-mcp", properties.read_text())

    def test_pinned_gitea_name_patch_is_applied(self):
        directory = ROOT / "Letta_Issue_Agent/redteam-mcp"
        self.assertRegex((directory / "server-name.patch").read_text(), r'\+\s+Name:\s+"redteam-mcp"')
        recipe = (directory / "Dockerfile").read_text()
        self.assertIn("COPY server-name.patch /tmp/server-name.patch", recipe)
        self.assertIn("patch --fuzz=0 -p1 < /tmp/server-name.patch", recipe)

    def test_documented_client_names(self):
        for application in APPLICATIONS:
            text = (ROOT / application / "README.md").read_text()
            blocks = [json.loads(body) for body in re.findall(r"```json\n(.*?)\n```", text, re.S)]
            connections = [block["mcpServers"] for block in blocks if "mcpServers" in block]
            with self.subTest(application=application):
                self.assertTrue(connections)
                for connection in connections:
                    self.assertEqual(set(connection), {"redteam-mcp"})
        for application in ("Clawith", "Inbox_Zero_Mail_Agent", "Letta_Issue_Agent", "shopware-claude-commerce"):
            capability = ROOT / application / "redteam/dreadnode/capability.yaml"
            self.assertRegex(capability.read_text(), r"mcp:\n\s+servers:\n\s+redteam-mcp:")


if __name__ == "__main__":
    unittest.main()
