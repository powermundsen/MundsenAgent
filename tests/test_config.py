from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from mundsen_agent.config import (
    MAX_CONFIG_BYTES,
    ConfigurationError,
    load_runtime_settings,
    parse_env_file,
)
from mundsen_agent.paths import MundsenAgentPaths, ensure_private_directories


class TestRuntimeConfiguration(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.home = Path(self.tempdir.name) / "home"
        self.home.mkdir()
        self.paths = MundsenAgentPaths.from_environ({"HOME": str(self.home)})
        ensure_private_directories((self.paths.config_dir,))

    def write_runtime(self, text: str) -> None:
        self.paths.runtime_config_file.write_text(text, encoding="utf-8")

    def write_secrets(self, text: str, mode: int = 0o600) -> None:
        self.paths.secrets_file.write_text(text, encoding="utf-8")
        self.paths.secrets_file.chmod(mode)

    def test_parser_does_not_expand_shell_syntax(self) -> None:
        self.write_runtime(
            "# comment\nMODE=auto\nLITERAL=\"$HOME $(touch forbidden)\"\n"
        )

        parsed = parse_env_file(self.paths.runtime_config_file)

        self.assertEqual(parsed["MODE"], "auto")
        self.assertEqual(parsed["LITERAL"], "$HOME $(touch forbidden)")
        self.assertFalse((self.home / "forbidden").exists())

    def test_duplicate_and_invalid_keys_are_rejected(self) -> None:
        self.write_runtime("MODE=auto\nMODE=manual\n")
        with self.assertRaises(ConfigurationError):
            parse_env_file(self.paths.runtime_config_file)

        self.write_runtime("lowercase=value\n")
        with self.assertRaises(ConfigurationError):
            parse_env_file(self.paths.runtime_config_file)

    def test_unmatched_quote_is_rejected(self) -> None:
        self.write_runtime('MODE="auto\n')
        with self.assertRaises(ConfigurationError):
            parse_env_file(self.paths.runtime_config_file)

    def test_null_byte_is_rejected(self) -> None:
        self.paths.runtime_config_file.write_bytes(b"MODE=auto\x00unsafe\n")
        with self.assertRaises(ConfigurationError):
            parse_env_file(self.paths.runtime_config_file)

    def test_oversized_file_is_rejected(self) -> None:
        self.paths.runtime_config_file.write_text(
            "A=" + ("x" * MAX_CONFIG_BYTES), encoding="utf-8"
        )
        with self.assertRaises(ConfigurationError):
            parse_env_file(self.paths.runtime_config_file)

    def test_secret_file_requires_mode_0600(self) -> None:
        self.write_secrets("SERVICE_TOKEN=fake-token\n", mode=0o644)
        with self.assertRaises(ConfigurationError):
            load_runtime_settings(self.paths)

        self.write_secrets("SERVICE_TOKEN=fake-token\n", mode=0o400)
        with self.assertRaises(ConfigurationError):
            load_runtime_settings(self.paths)

    def test_secret_like_value_is_rejected_from_runtime_file(self) -> None:
        self.write_runtime("SERVICE_TOKEN=not-allowed-here\n")
        with self.assertRaises(ConfigurationError):
            load_runtime_settings(self.paths)

    def test_non_secret_session_timeout_is_allowed(self) -> None:
        self.write_runtime("SESSION_TIMEOUT_SECONDS=300\n")
        settings = load_runtime_settings(self.paths)
        self.assertEqual(settings.get("SESSION_TIMEOUT_SECONDS"), "300")

    def test_precedence_redaction_and_no_environment_mutation(self) -> None:
        self.write_runtime("MODE=auto\n")
        self.write_secrets(
            "SERVICE_TOKEN=fake-secret-value\nTELEGRAM_CHAT_ID=100200300\n"
        )
        marker_name = "MUNDSEN_TEST_MARKER"
        before = os.environ.get(marker_name)
        settings = load_runtime_settings(
            self.paths,
            environ={
                "MODE": "codex",
                marker_name: "enabled",
            },
            required_names=("MODE", "SERVICE_TOKEN"),
        )

        self.assertEqual(settings.require("MODE"), "codex")
        self.assertEqual(settings.require("SERVICE_TOKEN"), "fake-secret-value")
        self.assertEqual(settings.get(marker_name), "enabled")
        self.assertEqual(settings.redacted()["SERVICE_TOKEN"], "[redacted]")
        self.assertEqual(settings.redacted()["TELEGRAM_CHAT_ID"], "[redacted]")
        self.assertNotIn("fake-secret-value", repr(settings))
        self.assertEqual(os.environ.get(marker_name), before)
        self.assertEqual(
            settings.as_environment(base={"PATH": "/test/bin"})["PATH"],
            "/test/bin",
        )

    def test_missing_required_setting_names_key_not_value(self) -> None:
        with self.assertRaisesRegex(
            ConfigurationError, "MISSING_SETTING"
        ):
            load_runtime_settings(
                self.paths,
                environ={},
                required_names=("MISSING_SETTING",),
            )

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_symlinked_secret_file_is_rejected(self) -> None:
        target = self.paths.config_dir / "actual.env"
        target.write_text("SERVICE_TOKEN=fake\n", encoding="utf-8")
        target.chmod(0o600)
        self.paths.secrets_file.symlink_to(target)

        with self.assertRaises(ConfigurationError):
            load_runtime_settings(self.paths)


if __name__ == "__main__":
    unittest.main()
