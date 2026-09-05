from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from mundsen_agent.paths import MundsenAgentPaths
from mundsen_agent.router.agents import (
    ClaudeAdapter,
    CodexAdapter,
    provider_environment,
)
from mundsen_agent.router.process import CommandResult


class FakeRunner:
    def __init__(self, result: CommandResult) -> None:
        self.result = result
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, command: list[str], **kwargs: object) -> CommandResult:
        self.calls.append((command, kwargs))
        return self.result


class TestAgentAdapters(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.paths = MundsenAgentPaths.from_root(Path(self.tempdir.name) / "Mundsen Agent")
        self.paths.instance_dir.mkdir(parents=True)

    def test_provider_environment_removes_api_billing_variables(self) -> None:
        env = provider_environment(
            self.paths,
            {
                "PATH": "/live/bin",
                "ANTHROPIC_API_KEY": "secret-one",
                "OPENAI_API_KEY": "secret-two",
                "CODEX_ACCESS_TOKEN": "secret-three",
                "CODEX_API_KEY": "secret-four",
                "CLAUDE_CODE_USE_BEDROCK": "1",
                "OPENAI_BASE_URL": "https://usage-billed.example",
                "GH_TOKEN": "secret-five",
                "TELEGRAM_BOT_TOKEN": "secret-six",
                "UNRELATED_PRIVATE_VALUE": "secret-seven",
                "LANG": "nb_NO.UTF-8",
            },
        )

        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("CODEX_ACCESS_TOKEN", env)
        self.assertNotIn("CODEX_API_KEY", env)
        self.assertNotIn("CLAUDE_CODE_USE_BEDROCK", env)
        self.assertNotIn("OPENAI_BASE_URL", env)
        self.assertNotIn("GH_TOKEN", env)
        self.assertNotIn("TELEGRAM_BOT_TOKEN", env)
        self.assertNotIn("UNRELATED_PRIVATE_VALUE", env)
        self.assertNotIn("/live/bin", env["PATH"])
        self.assertEqual(env["HOME"], str(self.paths.provider_home))
        self.assertEqual(env["CODEX_HOME"], str(self.paths.codex_home))
        self.assertEqual(env["LANG"], "nb_NO.UTF-8")

    def test_claude_prompt_uses_stdin_and_parses_result(self) -> None:
        stdout = "\n".join(
            (
                json.dumps(
                    {
                        "type": "system",
                        "subtype": "init",
                        "session_id": "session-1",
                    }
                ),
                json.dumps(
                    {
                        "type": "result",
                        "session_id": "session-1",
                        "result": "Hei",
                        "usage": {"input_tokens": 3, "output_tokens": 2},
                    }
                ),
            )
        )
        runner = FakeRunner(CommandResult("completed", 0, stdout, "", 10))
        response = ClaudeAdapter(self.paths, runner=runner).call(
            "private message"
        )

        command, kwargs = runner.calls[0]
        self.assertNotIn("private message", command)
        self.assertEqual(kwargs["input_text"], "private message")
        self.assertIn("acceptEdits", command)
        self.assertTrue(response.success)
        self.assertEqual(response.text, "Hei")
        self.assertEqual(response.session_id, "session-1")

    def test_codex_uses_workspace_sandbox_and_parses_last_message(self) -> None:
        stdout = "\n".join(
            (
                json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "reasoning", "text": "not the answer"},
                    }
                ),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": "Svar"},
                    }
                ),
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 7,
                            "cached_input_tokens": 2,
                            "output_tokens": 4,
                        },
                    }
                ),
            )
        )
        runner = FakeRunner(CommandResult("completed", 0, stdout, "", 10))
        response = CodexAdapter(self.paths, runner=runner).call("secret text")

        command, kwargs = runner.calls[0]
        self.assertNotIn("secret text", command)
        self.assertEqual(kwargs["input_text"], "secret text")
        self.assertIn("workspace-write", command)
        self.assertNotIn("danger-full-access", command)
        self.assertLess(command.index("-C"), command.index("exec"))
        self.assertEqual(command[command.index("-C") + 1], str(self.paths.instance_dir))
        self.assertIn("never", command)
        self.assertTrue(response.success)
        self.assertEqual(response.session_id, "thread-1")
        self.assertEqual(response.cached_input_tokens, 2)
        self.assertEqual(response.text, "Svar")

    def test_provider_errors_are_categorized_without_returning_raw_text(self) -> None:
        runner = FakeRunner(
            CommandResult(
                "completed",
                1,
                "",
                "Bearer abcdefghijklmnopqrstuvwxyz authentication failed",
                10,
            )
        )
        response = CodexAdapter(self.paths, runner=runner).call("hello")

        self.assertFalse(response.success)
        self.assertEqual(response.text, "")
        self.assertEqual(response.error_category, "sensitive_error")

    def test_resume_commands_only_contain_opaque_session_id(self) -> None:
        claude_runner = FakeRunner(CommandResult("timeout", 1, "", "", 10))
        ClaudeAdapter(self.paths, runner=claude_runner).call(
            "message",
            session_id="session-2",
        )
        codex_runner = FakeRunner(CommandResult("timeout", 1, "", "", 10))
        CodexAdapter(self.paths, runner=codex_runner).call(
            "message",
            session_id="thread-2",
        )

        self.assertIn("--resume", claude_runner.calls[0][0])
        self.assertIn("session-2", claude_runner.calls[0][0])
        self.assertIn("resume", codex_runner.calls[0][0])
        self.assertIn("thread-2", codex_runner.calls[0][0])


if __name__ == "__main__":
    unittest.main()
