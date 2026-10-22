"""Local .env loading and legacy secret migration."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import configure_deepseek
from config_env import load_env


class EnvConfigurationTests(unittest.TestCase):
    def test_env_file_does_not_override_process_environment(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            path = Path(directory) / ".env"
            path.write_text("DEEPSEEK_API_KEY=file-key\nDEEPSEEK_MODEL=deepseek-chat\nUNKNOWN_KEY=ignored\n", encoding="utf-8")
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "process-key"}, clear=True):
                load_env(path)
                self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "process-key")
                self.assertEqual(os.environ["DEEPSEEK_MODEL"], "deepseek-chat")
                self.assertNotIn("UNKNOWN_KEY", os.environ)

    def test_migration_moves_legacy_key_without_changing_other_settings(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            root = Path(directory)
            env_path = root / ".env"
            legacy_path = root / "deepseek.key"
            env_path.write_text("BOJI_PORT=5000\n", encoding="utf-8")
            legacy_path.write_text("sk-example-legacy-key-123456", encoding="utf-8")
            with patch.object(configure_deepseek, "ENV_PATH", env_path), patch.object(configure_deepseek, "LEGACY_KEY_PATH", legacy_path):
                configure_deepseek.save_key(legacy_path.read_text(encoding="utf-8"))
            self.assertFalse(legacy_path.exists())
            self.assertIn("BOJI_PORT=5000", env_path.read_text(encoding="utf-8"))
            self.assertIn("DEEPSEEK_API_KEY=sk-example-legacy-key-123456", env_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
