"""Offline transport checks for the benchmark's OpenAI configuration."""

import json
import os
from pathlib import Path
import sys
import subprocess
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ZerePy"))
from src.connections import openai_connection as connection_module


class OpenAIConfigurationTests(unittest.TestCase):
    def test_adapter_settings_use_the_selected_openai_configuration(self):
        root = Path(__file__).resolve().parents[1]
        code = '''
import importlib.util, pathlib, sys
path = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("settings", path)
module = importlib.util.module_from_spec(spec)
sys.modules["settings"] = module
spec.loader.exec_module(module)
if path.parts[-4] == "shopware-claude-commerce":
    settings = module.load_settings(path.with_name("config.yaml"), None, False)[0]
elif "require_credentials" in module.load_settings.__code__.co_varnames:
    settings = module.load_settings(path.with_name("config.yaml"), require_credentials=False)[0]
else:
    settings = module.load_settings(path.with_name("config.yaml"))[0]
models = settings["models"] if isinstance(settings, dict) else settings.models.model_dump()
for model in models.values():
    assert model["model"] == "selected-model", model["model"]
    assert model["base_url"] == "https://model.example.test/v1", model["base_url"]
    assert model["api_key_env"] == "OPENAI_API_KEY", model["api_key_env"]
    assert "thinking" not in model.get("extra_body", {})
'''
        env = {**os.environ, "OPENAI_API_KEY": "sk-fixture", "OPENAI_MODEL": "selected-model",
               "OPENAI_BASE_URL": "https://model.example.test/v1", "PYTHONDONTWRITEBYTECODE": "1"}
        for project in ["Letta_Issue_Agent", "Inbox_Zero_Mail_Agent", "shopware-claude-commerce"]:
            for adapter in ["deepteam", "pyrit"]:
                with self.subTest(project=project, adapter=adapter):
                    path = root / project / "redteam" / adapter / "settings.py"
                    result = subprocess.run([sys.executable, "-c", code, str(path)],
                                            cwd=root / project, env=env, capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_key_endpoint_and_model_reach_the_sdk_with_tool_calls(self):
        requests = []

        def respond(request):
            body = json.loads(request.content)
            requests.append((request, body))
            return httpx.Response(200, json={
                "id": "chatcmpl-fixture", "object": "chat.completion", "created": 1,
                "model": body["model"], "choices": [{"index": 0, "finish_reason": "tool_calls",
                    "message": {"role": "assistant", "content": None, "tool_calls": [{
                        "id": "call-fixture", "type": "function",
                        "function": {"name": "echo", "arguments": '{"value":"hello"}'},
                    }]} }],
            })

        transport = httpx.MockTransport(respond)
        env = {"OPENAI_API_KEY": "sk-fixture", "OPENAI_BASE_URL": "https://model.example.test/v1",
               "OPENAI_MODEL": "selected-model"}
        with patch.dict(os.environ, env), httpx.Client(transport=transport) as http:
            client = OpenAI(api_key=env["OPENAI_API_KEY"], http_client=http)
            with patch.object(connection_module, "OpenAI", return_value=client):
                connection = connection_module.OpenAIConnection({"name": "openai", "model": "old-model"})
                result = connection.chat([{"role": "user", "content": "hello"}], tools=[{
                    "type": "function", "function": {"name": "echo", "parameters": {
                        "type": "object", "properties": {"value": {"type": "string"}},
                    }},
                }])
        request, body = requests[0]
        self.assertEqual(str(request.url), "https://model.example.test/v1/chat/completions")
        self.assertEqual(request.headers["authorization"], "Bearer sk-fixture")
        self.assertEqual(body["model"], "selected-model")
        self.assertEqual(result.tool_calls[0].function.name, "echo")

    def test_missing_model_is_rejected_before_network_access(self):
        with patch.dict(os.environ, {"OPENAI_MODEL": ""}):
            with self.assertLogs(level="ERROR"), self.assertRaisesRegex(
                    connection_module.OpenAIConfigurationError, "OPENAI_MODEL"):
                connection_module.OpenAIConnection({"name": "openai", "model": ""})


if __name__ == "__main__":
    unittest.main()
