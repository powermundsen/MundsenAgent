from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from threading import Event, Thread

from mundsen_agent.audit import AuditLogger
from mundsen_agent.router.models import AgentResponse, RouterMode
from mundsen_agent.router.router import AgentRouter
from mundsen_agent.router.state import RouterStateStore


class FakeAdapter:
    def __init__(self, name: str, responses: list[AgentResponse]) -> None:
        self.name = name
        self.responses = responses
        self.calls: list[tuple[str, str, Event | None]] = []
        self.model = ""
        self.reasoning_effort = ""

    def set_model(self, model: str) -> None:
        self.model = model

    def set_reasoning_effort(self, effort: str) -> None:
        self.reasoning_effort = effort

    def call(
        self,
        message: str,
        *,
        session_id: str = "",
        cancel_event: Event | None = None,
    ) -> AgentResponse:
        self.calls.append((message, session_id, cancel_event))
        return self.responses.pop(0)


class BlockingAdapter:
    def __init__(self, name: str) -> None:
        self.name = name
        self.started = Event()

    def call(
        self,
        message: str,
        *,
        session_id: str = "",
        cancel_event: Event | None = None,
    ) -> AgentResponse:
        del message, session_id
        self.started.set()
        if cancel_event is None or not cancel_event.wait(timeout=1):
            return AgentResponse("", self.name, False, error_category="timeout")
        return AgentResponse(
            "",
            self.name,
            False,
            error_category="cancelled",
            session_id="late-session",
        )


class TestAgentRouter(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        self.store = RouterStateStore(root / "state" / "router.json")
        self.audit = AuditLogger(root / "logs" / "audit.jsonl")

    def router(
        self,
        claude_responses: list[AgentResponse],
        codex_responses: list[AgentResponse],
        *,
        preferred: str = "auto",
    ) -> tuple[AgentRouter, FakeAdapter, FakeAdapter]:
        claude = FakeAdapter("claude", claude_responses)
        codex = FakeAdapter("codex", codex_responses)
        router = AgentRouter(
            claude=claude,
            codex=codex,
            state_store=self.store,
            audit=self.audit,
            preferred_agent=preferred,
        )
        return router, claude, codex

    def test_auto_falls_back_and_saves_successful_session(self) -> None:
        router, claude, codex = self.router(
            [
                AgentResponse(
                    "", "claude", False, error_category="limit"
                )
            ],
            [
                AgentResponse(
                    "Svar", "codex", True, session_id="thread-1"
                )
            ],
        )

        response = router.route("Hei")

        self.assertTrue(response.success)
        self.assertEqual(response.agent, "codex")
        self.assertEqual(len(claude.calls), 1)
        self.assertEqual(len(codex.calls), 1)
        self.assertEqual(self.store.load().codex_session_id, "thread-1")

    def test_forced_agent_does_not_fallback(self) -> None:
        router, claude, codex = self.router(
            [
                AgentResponse(
                    "", "claude", False, error_category="auth"
                )
            ],
            [],
        )
        self.assertIn("claude", router.route("/claude").text)

        response = router.route("Hei")

        self.assertFalse(response.success)
        self.assertEqual(response.error_category, "auth")
        self.assertEqual(len(codex.calls), 0)

    def test_new_clears_both_provider_sessions(self) -> None:
        router, _, _ = self.router([], [])
        router.state.claude_session_id = "session-1"
        router.state.codex_session_id = "thread-1"
        self.store.save(router.state)

        response = router.route("/new")

        self.assertTrue(response.success)
        state = self.store.load()
        self.assertEqual(state.claude_session_id, "")
        self.assertEqual(state.codex_session_id, "")

    def test_stop_closes_both_provider_sessions(self) -> None:
        router, claude, codex = self.router([], [])
        router.state.claude_session_id = "session-1"
        router.state.codex_session_id = "thread-1"
        self.store.save(router.state)

        response = router.route("/stop")

        self.assertTrue(response.success)
        self.assertIn("lukket", response.text)
        state = self.store.load()
        self.assertEqual(state.claude_session_id, "")
        self.assertEqual(state.codex_session_id, "")
        self.assertEqual(claude.calls, [])
        self.assertEqual(codex.calls, [])

    def test_stop_waits_for_active_route_before_closing_sessions(self) -> None:
        claude = BlockingAdapter("claude")
        codex = FakeAdapter("codex", [])
        router = AgentRouter(
            claude=claude,
            codex=codex,
            state_store=self.store,
            audit=self.audit,
        )
        router.state.claude_session_id = "session-1"
        router.state.codex_session_id = "thread-1"
        self.store.save(router.state)
        cancel = Event()
        worker = Thread(target=router.route, args=("lang jobb",), kwargs={"cancel_event": cancel})
        worker.start()
        self.assertTrue(claude.started.wait(timeout=0.5))

        cancel.set()
        response = router.route("/stop")
        worker.join(timeout=1)

        self.assertFalse(worker.is_alive())
        self.assertTrue(response.success)
        state = self.store.load()
        self.assertEqual(state.claude_session_id, "")
        self.assertEqual(state.codex_session_id, "")

    def test_explicit_modes_survive_restart(self) -> None:
        router, _, _ = self.router([], [])
        router.route("/codex")
        reloaded, _, _ = self.router([], [])

        self.assertEqual(reloaded.state.mode, RouterMode.CODEX)

    def test_cancelled_primary_does_not_start_fallback(self) -> None:
        event = Event()
        router, _, codex = self.router(
            [
                AgentResponse(
                    "", "claude", False, error_category="cancelled"
                )
            ],
            [],
        )

        response = router.route("Hei", cancel_event=event)

        self.assertEqual(response.error_category, "cancelled")
        self.assertEqual(len(codex.calls), 0)

    def test_configured_preference_survives_forced_mode_restart(self) -> None:
        router, _, _ = self.router([], [], preferred="codex")
        router.route("/claude")
        reloaded, _, codex = self.router(
            [],
            [AgentResponse("Svar", "codex", True)],
            preferred="codex",
        )

        reloaded.route("/auto")
        response = reloaded.route("Hei")

        self.assertTrue(response.success)
        self.assertEqual(response.agent, "codex")
        self.assertEqual(len(codex.calls), 1)

    def test_durable_context_is_injected_for_primary_and_fallback(self) -> None:
        claude = FakeAdapter(
            "claude",
            [AgentResponse("", "claude", False, error_category="limit")],
        )
        codex = FakeAdapter(
            "codex",
            [AgentResponse("Svar", "codex", True)],
        )
        router = AgentRouter(
            claude=claude,
            codex=codex,
            state_store=self.store,
            audit=self.audit,
            context_provider=lambda: "profile context",
        )

        router.route("Hei")

        for adapter in (claude, codex):
            self.assertIn("profile context", adapter.calls[0][0])
            self.assertIn("<owner_message>\nHei", adapter.calls[0][0])

    def test_extended_commands_are_explicit_and_persist_model_choices(self) -> None:
        claude = FakeAdapter("claude", [])
        codex = FakeAdapter("codex", [])
        router = AgentRouter(
            claude=claude,
            codex=codex,
            state_store=self.store,
            audit=self.audit,
            extended_commands=True,
            version_text="Mundsen Agent 0.4.0",
            claude_model_aliases={"opus": "example-opus"},
        )

        self.assertIn("0.4.0", router.route("/version").text)
        self.assertIn("opus", router.route("/opus").text.lower())
        self.assertEqual(claude.model, "example-opus")
        self.assertEqual(self.store.load().claude_model, "example-opus")
        self.assertIn("ikke", router.route("/rollback 0.3.0").text.lower())

    def test_reasoning_command_and_gpt_alias_select_codex(self) -> None:
        router, _, codex = self.router([], [])
        router.extended_commands = True

        router.route("/gpt")
        response = router.route("/think xhigh")

        self.assertIn("xhigh", response.text)
        self.assertEqual(codex.reasoning_effort, "xhigh")
        self.assertEqual(self.store.load().mode, RouterMode.CODEX)

    def test_similar_command_name_is_not_treated_as_rollback(self) -> None:
        router, claude, _ = self.router(
            [AgentResponse("provider response", "claude", True)],
            [],
        )
        router.extended_commands = True

        response = router.route("/rollback-now")

        self.assertEqual(response.text, "provider response")
        self.assertEqual(len(claude.calls), 1)

    def test_circuit_breaker_skips_repeatedly_failing_primary(self) -> None:
        now = [100.0]
        claude = FakeAdapter(
            "claude",
            [
                AgentResponse("", "claude", False, error_category="capacity"),
                AgentResponse("Recovered", "claude", True),
            ],
        )
        codex = FakeAdapter(
            "codex",
            [AgentResponse("First", "codex", True), AgentResponse("Second", "codex", True)],
        )
        router = AgentRouter(
            claude=claude,
            codex=codex,
            state_store=self.store,
            audit=self.audit,
            circuit_breaker_threshold=1,
            circuit_breaker_cooldown_seconds=30,
            monotonic=lambda: now[0],
        )

        self.assertEqual(router.route("one").agent, "codex")
        self.assertEqual(router.route("two").agent, "codex")
        self.assertEqual(len(claude.calls), 1)
        now[0] = 131.0
        self.assertEqual(router.route("three").agent, "claude")


if __name__ == "__main__":
    unittest.main()
