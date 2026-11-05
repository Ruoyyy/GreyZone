"""Runner preflight checks that must happen before Oracle preparation."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from redteam.pyrit import run


class RunnerTest(unittest.TestCase):
    def test_default_openai_configuration(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(run, "env_value", side_effect=lambda name: "key" if name == "OPENAI_API_KEY" else ""):
            self.assertEqual(run.attacker_settings("test-model"),
                             (run.OPENAI_DEFAULT_BASE, "key", "test-model"))

    def test_missing_model_key_fails_before_episode(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(run, "env_value", return_value=""):
            with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
                run.attacker_settings("test-model")

    def test_custom_model_configuration(self):
        env = {"OPENAI_BASE_URL": "https://example.test/v1",
               "OPENAI_API_KEY": "custom"}
        with patch.dict(os.environ, env, clear=True), patch.object(run, "env_value", side_effect=lambda name: env.get(name, "")):
            self.assertEqual(run.attacker_settings("custom-model"),
                             ("https://example.test/v1", "custom", "custom-model"))

    def test_insecure_endpoint_is_rejected(self):
        env = {"OPENAI_BASE_URL": "http://example.test/v1",
               "OPENAI_API_KEY": "custom"}
        with patch.dict(os.environ, env, clear=True), patch.object(run, "env_value", side_effect=lambda name: env.get(name, "")):
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                run.attacker_settings("custom-model")

    def test_custom_endpoint_uses_standard_key(self):
        with (patch.dict(os.environ, {"OPENAI_BASE_URL": "https://example.test/v1"},
                         clear=True), patch.object(run, "env_value", return_value="test-key")):
            self.assertEqual(run.attacker_settings("custom-model"),
                             ("https://example.test/v1", "test-key", "custom-model"))


if __name__ == "__main__":
    unittest.main()
