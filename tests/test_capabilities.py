from __future__ import annotations

import unittest
from pathlib import Path

from mundsen_agent.paths import MundsenAgentPaths
from mundsen_agent.router.agents import ClaudeAdapter, CodexAdapter
from mundsen_agent.router.capabilities import (
    CapabilityError,
    claude_allowed_tools,
    codex_sandbox,
)
from mundsen_agent.router.process import CommandResult


def _recording_runner(recorded: list[list[str]]) -> object:
    def runner(command, **_: object) -> CommandResult:
        recorded.append(list(command))
        return CommandResult(
            status="completed",
            returncode=0,
            stdout="",
            stderr="",
            duration_ms=0,
        )

    return runner


class TestCapabilityBoundary(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = MundsenAgentPaths.from_root(Path("/tmp/mundsen-agent-capability-test"))

    def test_claude_command_grants_exactly_the_local_only_tools(self) -> None:
        recorded: list[list[str]] = []
        adapter = ClaudeAdapter(
            self.paths,
            executable="/nonexistent/claude",
            runner=_recording_runner(recorded),
        )
        adapter.call("hei")

        self.assertEqual(len(recorded), 1)
        command = recorded[0]
        self.assertIn("--allowedTools", command)
        granted = command[command.index("--allowedTools") + 1]
        self.assertEqual(granted, "Read,Edit,Write,Glob,Grep")
        # No capability that can reach another person, service, or account.
        for forbidden in ("Bash", "WebFetch", "WebSearch", "Task", "--dangerously"):
            self.assertNotIn(forbidden, granted)
            self.assertNotIn(forbidden, " ".join(command))

    def test_codex_command_uses_a_workspace_sandbox(self) -> None:
        recorded: list[list[str]] = []
        adapter = CodexAdapter(
            self.paths,
            executable="/nonexistent/codex",
            runner=_recording_runner(recorded),
        )
        adapter.call("hei")

        command = recorded[0]
        self.assertIn("--sandbox", command)
        self.assertEqual(command[command.index("--sandbox") + 1], "workspace-write")
        self.assertNotIn("danger-full-access", " ".join(command))

    def test_shell_and_network_tools_are_refused(self) -> None:
        for tool in ("Bash", "WebFetch", "WebSearch", "Task"):
            with self.subTest(tool=tool):
                with self.assertRaises(CapabilityError):
                    claude_allowed_tools(("Read", tool))

    def test_adapter_refuses_an_external_grant_at_construction(self) -> None:
        with self.assertRaises(CapabilityError):
            ClaudeAdapter(
                self.paths,
                executable="/nonexistent/claude",
                allowed_tools=("Read", "Bash"),
            )
        with self.assertRaises(CapabilityError):
            CodexAdapter(
                self.paths,
                executable="/nonexistent/codex",
                sandbox="danger-full-access",
            )

    def test_empty_or_duplicated_grants_are_refused(self) -> None:
        with self.assertRaises(CapabilityError):
            claude_allowed_tools(())
        with self.assertRaises(CapabilityError):
            claude_allowed_tools(("Read", "Read"))
        with self.assertRaises(CapabilityError):
            claude_allowed_tools(("",))

    def test_read_only_sandbox_is_permitted(self) -> None:
        self.assertEqual(codex_sandbox("read-only"), "read-only")


if __name__ == "__main__":
    unittest.main()
