from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from mundsen_agent.auth import authentication_status, interactive_login
from mundsen_agent.paths import MundsenAgentPaths


class FakeRunner:
    def __init__(self, results: list[tuple[int, str]]) -> None:
        self.results = results
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(
        self, command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, kwargs))
        returncode, stdout = self.results.pop(0)
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr="")


class TestProviderAuthentication(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.paths = MundsenAgentPaths.from_root(Path(self.tempdir.name) / "Mundsen Agent")
        self.paths.instance_dir.mkdir(parents=True)
        claude = self.paths.provider_home / ".local" / "bin" / "claude"
        codex = self.paths.provider_bin_dir / "codex"
        claude.parent.mkdir(parents=True)
        codex.parent.mkdir(parents=True)
        claude.write_text("", encoding="utf-8")
        codex.write_text("", encoding="utf-8")

    def test_status_uses_provider_subscription_commands(self) -> None:
        runner = FakeRunner(
            [
                (
                    0,
                    '{"loggedIn":true,"authMethod":"claude.ai",'
                    '"subscriptionType":"pro"}',
                ),
                (0, "Logged in using an API key"),
            ]
        )

        claude = authentication_status(self.paths, "claude", runner=runner)
        codex = authentication_status(self.paths, "codex", runner=runner)

        self.assertTrue(claude.authenticated)
        self.assertFalse(codex.authenticated)
        self.assertEqual(runner.calls[0][0][-2:], ["auth", "status"])
        self.assertEqual(runner.calls[1][0][-2:], ["login", "status"])
        for _, kwargs in runner.calls:
            environment = kwargs["env"]
            self.assertNotIn("OPENAI_API_KEY", environment)
            self.assertNotIn("ANTHROPIC_API_KEY", environment)
            self.assertNotIn("CODEX_API_KEY", environment)
            self.assertNotIn("CLAUDE_CODE_USE_BEDROCK", environment)

    def test_interactive_login_verifies_status_after_login(self) -> None:
        runner = FakeRunner(
            [(0, ""), (0, "Logged in using ChatGPT")]
        )

        self.assertTrue(interactive_login(self.paths, "codex", runner=runner))
        self.assertEqual(runner.calls[0][0][-1], "login")
        self.assertEqual(runner.calls[1][0][-2:], ["login", "status"])

    def test_console_and_api_key_authentication_are_rejected(self) -> None:
        runner = FakeRunner(
            [
                (
                    0,
                    '{"loggedIn":true,"authMethod":"console",'
                    '"subscriptionType":""}',
                ),
                (0, "Logged in using an API key"),
            ]
        )

        self.assertFalse(
            authentication_status(self.paths, "claude", runner=runner).authenticated
        )
        self.assertFalse(
            authentication_status(self.paths, "codex", runner=runner).authenticated
        )


if __name__ == "__main__":
    unittest.main()
