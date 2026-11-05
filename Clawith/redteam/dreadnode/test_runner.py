"""Offline checks for attacker model preflight."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from redteam.dreadnode import run


class ModelEnvironmentTest(unittest.TestCase):
    def test_default_openai_uses_standard_key(self):
        with patch.object(run, "env_value", side_effect=lambda name: "test-key" if name == "OPENAI_API_KEY" else ""):
            env = run.model_environment("openai/test-model", {})
        self.assertEqual(env["OPENAI_API_KEY"], "test-key")
        self.assertEqual(env["OPENAI_BASE_URL"], run.OPENAI_DEFAULT_BASE)

    def test_missing_key_fails_before_episode(self):
        with patch.object(run, "env_value", return_value=""):
            with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
                run.model_environment("openai/test-model", {})

    def test_dn_model_requires_both_proxy_settings(self):
        with patch.object(run, "env_value", return_value=""):
            with self.assertRaisesRegex(ValueError, "DREADNODE_LLM_BASE"):
                run.model_environment("dn/example", {})
        env = run.model_environment("dn/example", {
            "DREADNODE_LLM_BASE": "https://proxy.example/v1",
            "DREADNODE_LLM_API_KEY": "proxy-test-key",
        })
        self.assertEqual(env["DREADNODE_LLM_API_KEY"], "proxy-test-key")


if __name__ == "__main__":
    unittest.main()
