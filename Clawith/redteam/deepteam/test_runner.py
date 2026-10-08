"""Runner preflight checks before Oracle preparation."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from redteam.deepteam import run


class RunnerTest(unittest.TestCase):
    def test_default_zhipu_configuration(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(run, "env_value", return_value="key"):
            self.assertEqual(run.attacker_settings("glm-4.7"),
                             (run.ZHIPU_BASE, "key", "glm-4.7"))

    def test_missing_key_fails(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(run, "env_value", return_value=""):
            with self.assertRaisesRegex(ValueError, "DEEPTEAM_ATTACKER_API_KEY"):
                run.attacker_settings("glm-4.7")

    def test_custom_endpoint_never_uses_zhipu_key(self):
        with (patch.dict(os.environ, {"DEEPTEAM_ATTACKER_BASE_URL": "https://example.test/v1"},
                         clear=True), patch.object(run, "env_value", return_value="zhipu-key")):
            with self.assertRaisesRegex(ValueError, "DEEPTEAM_ATTACKER_API_KEY"):
                run.attacker_settings("custom-model")

    def test_custom_model_configuration(self):
        env = {"DEEPTEAM_ATTACKER_BASE_URL": "https://example.test/v1",
               "DEEPTEAM_ATTACKER_API_KEY": "custom"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(run.attacker_settings("custom-model"),
                             ("https://example.test/v1", "custom", "custom-model"))

    def test_insecure_endpoint_is_rejected(self):
        env = {"DEEPTEAM_ATTACKER_BASE_URL": "http://example.test/v1",
               "DEEPTEAM_ATTACKER_API_KEY": "custom"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                run.attacker_settings("custom-model")


if __name__ == "__main__":
    unittest.main()
