"""Offline checks for attacker model preflight."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from redteam.dreadnode import run


class ModelEnvironmentTest(unittest.TestCase):
    def test_default_glm_uses_existing_zhipu_key(self):
        with patch.object(run, "env_value", return_value="zhipu-test-key"):
            env = run.model_environment("openai/glm-4.7", {})
        self.assertEqual(env["OPENAI_API_KEY"], "zhipu-test-key")
        self.assertEqual(env["OPENAI_BASE_URL"], run.ZHIPU_OPENAI_BASE)

    def test_glm_fails_before_episode_without_key(self):
        with patch.object(run, "env_value", return_value=""):
            with self.assertRaisesRegex(ValueError, "ZHIPU_API_KEY"):
                run.model_environment("openai/glm-4.7", {})

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
