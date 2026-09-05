from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from threading import Event

from mundsen_agent.instance import InstanceSettings
from mundsen_agent.onboarding import (
    COMPLETION_MARKER,
    OnboardingRouter,
    OnboardingState,
    OnboardingStateError,
    OnboardingStore,
    is_skip_request,
    onboarding_brief,
    opening_prompt,
    strip_completion_marker,
)
from mundsen_agent.router.models import AgentResponse


def _settings() -> InstanceSettings:
    return InstanceSettings(
        schema_version=1,
        assistant_name="Kit",
        language="Norwegian",
        timezone="Europe/Oslo",
        tone="warm",
        technical_level="unknown",
        preferred_agent="auto",
    )


def _response(text: str, *, success: bool = True) -> AgentResponse:
    return AgentResponse(text=text, agent="claude", success=success)


class FakeInner:
    """Records every prompt and returns queued responses in order."""

    def __init__(self, responses: list[AgentResponse]) -> None:
        self.responses = responses
        self.prompts: list[str] = []

    def route(
        self,
        message: str,
        *,
        cancel_event: Event | None = None,
    ) -> AgentResponse:
        self.prompts.append(message)
        return self.responses.pop(0)


class TestCompletionMarker(unittest.TestCase):
    def test_strip_marker_on_own_line(self) -> None:
        text, found = strip_completion_marker(f"Bye now.\n{COMPLETION_MARKER}")
        self.assertTrue(found)
        self.assertEqual(text, "Bye now.")

    def test_strip_marker_inline(self) -> None:
        text, found = strip_completion_marker(f"Bye now.{COMPLETION_MARKER}")
        self.assertTrue(found)
        self.assertEqual(text, "Bye now.")

    def test_no_marker(self) -> None:
        text, found = strip_completion_marker("Still chatting")
        self.assertFalse(found)
        self.assertEqual(text, "Still chatting")


class TestSkipRequest(unittest.TestCase):
    def test_skip_variants(self) -> None:
        for command in ("/skip", "/hoppover", "/senere", "/later", "/SKIP"):
            self.assertTrue(is_skip_request(command), command)

    def test_skip_with_bot_suffix(self) -> None:
        self.assertTrue(is_skip_request("/skip@somebot"))

    def test_not_skip(self) -> None:
        self.assertFalse(is_skip_request("/status"))
        self.assertFalse(is_skip_request("skip"))
        self.assertFalse(is_skip_request(""))


class TestBrief(unittest.TestCase):
    def test_brief_substitutes_placeholders(self) -> None:
        brief = onboarding_brief(_settings())
        self.assertIn("Kit", brief)
        self.assertIn("Norwegian", brief)
        self.assertIn("Europe/Oslo", brief)
        self.assertIn(COMPLETION_MARKER, brief)
        self.assertNotIn("{{", brief)

    def test_opening_prompt_asks_for_first_message(self) -> None:
        prompt = opening_prompt(_settings())
        self.assertIn("opening message", prompt.lower())
        self.assertIn("Kit", prompt)


class TestOnboardingStore(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.path = Path(self.tempdir.name) / "state" / "onboarding.json"

    def test_default_when_missing(self) -> None:
        store = OnboardingStore(self.path)
        state = store.load()
        self.assertFalse(state.completed)
        self.assertEqual(state.turns, 0)

    def test_roundtrip_and_mode(self) -> None:
        store = OnboardingStore(self.path)
        store.save(
            OnboardingState(
                completed=True,
                turns=5,
                opening_authorized=True,
                opening_sent=True,
            )
        )
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        loaded = store.load()
        self.assertTrue(loaded.completed)
        self.assertEqual(loaded.turns, 5)
        self.assertTrue(loaded.opening_authorized)
        self.assertTrue(loaded.opening_sent)

    def test_legacy_pending_state_does_not_authorize_new_external_message(
        self,
    ) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text(
            '{"completed":false,"turns":0}\n',
            encoding="utf-8",
        )
        os.chmod(self.path, 0o600)

        loaded = OnboardingStore(self.path).load()

        self.assertFalse(loaded.opening_authorized)
        self.assertFalse(loaded.opening_sent)

    def test_rejects_wide_mode(self) -> None:
        store = OnboardingStore(self.path)
        store.save(OnboardingState(turns=1))
        os.chmod(self.path, 0o644)
        with self.assertRaises(OnboardingStateError):
            store.load()

    def test_rejects_symlink(self) -> None:
        real = Path(self.tempdir.name) / "real.json"
        real.write_text('{"completed": false, "turns": 0}\n', encoding="utf-8")
        os.chmod(real, 0o600)
        link = Path(self.tempdir.name) / "link.json"
        link.symlink_to(real)
        with self.assertRaises(OnboardingStateError):
            OnboardingStore(link).load()

        real.unlink()
        with self.assertRaises(OnboardingStateError):
            OnboardingStore(link).load()

    def test_rejects_boolean_turn_count(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text(
            '{"completed":false,"turns":true}\n',
            encoding="utf-8",
        )
        os.chmod(self.path, 0o600)

        with self.assertRaises(OnboardingStateError):
            OnboardingStore(self.path).load()

    def test_rejects_corrupt_json(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("not json", encoding="utf-8")
        os.chmod(self.path, 0o600)
        with self.assertRaises(OnboardingStateError):
            OnboardingStore(self.path).load()


class TestOnboardingRouter(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.state_path = Path(self.tempdir.name) / "state" / "onboarding.json"

    def _router(self, inner: FakeInner, *, max_turns: int = 14) -> OnboardingRouter:
        return OnboardingRouter(
            inner=inner,
            store=OnboardingStore(self.state_path),
            settings=_settings(),
            max_turns=max_turns,
        )

    def test_opening_message_wraps_first_turn(self) -> None:
        inner = FakeInner([_response("Hei, jeg heter Kit.")])
        OnboardingStore(self.state_path).save(
            OnboardingState(opening_authorized=True)
        )
        router = self._router(inner)
        response = router.open_conversation()
        self.assertIsNotNone(response)
        self.assertEqual(response.text, "Hei, jeg heter Kit.")
        self.assertIn("opening message", inner.prompts[0].lower())
        self.assertEqual(router.state.turns, 0)
        self.assertFalse(router.state.opening_sent)

        router.mark_opening_sent()
        self.assertEqual(router.state.turns, 1)
        self.assertTrue(router.state.opening_sent)

        reopened = self._router(FakeInner([_response("duplicate")]))
        self.assertIsNone(reopened.open_conversation())

    def test_opening_message_requires_explicit_authorization(self) -> None:
        inner = FakeInner([_response("should not be used")])
        router = self._router(inner)

        self.assertIsNone(router.open_conversation())
        self.assertEqual(inner.prompts, [])

    def test_owner_initiated_first_message_prevents_later_unsolicited_opening(
        self,
    ) -> None:
        inner = FakeInner([_response("Hei, hyggelig å møte deg.")])
        router = self._router(inner)

        response = router.route("Hei")

        self.assertTrue(response.success)
        self.assertTrue(router.state.opening_sent)
        self.assertIn("chose to start by writing", inner.prompts[0])
        reopened = self._router(FakeInner([_response("duplicate")]))
        self.assertIsNone(reopened.open_conversation())

    def test_marker_completes_and_passthrough(self) -> None:
        inner = FakeInner(
            [
                _response(f"Fint å bli kjent. Ha det!\n{COMPLETION_MARKER}"),
                _response("normal answer"),
            ]
        )
        router = self._router(inner)
        first = router.route("Jeg heter Kim")
        self.assertEqual(first.text, "Fint å bli kjent. Ha det!")
        self.assertFalse(router.active)
        # Next message must bypass onboarding entirely.
        second = router.route("hva er klokka?")
        self.assertEqual(second.text, "normal answer")
        self.assertEqual(inner.prompts[-1], "hva er klokka?")

    def test_skip_finishes_with_warm_message(self) -> None:
        inner = FakeInner([_response("Helt greit, vi tar det senere.")])
        router = self._router(inner)
        response = router.route("/skip")
        self.assertFalse(router.active)
        self.assertEqual(response.text, "Helt greit, vi tar det senere.")
        self.assertIn("stop the first conversation", inner.prompts[0])

    def test_router_commands_pass_through_untouched(self) -> None:
        inner = FakeInner([_response("status output")])
        router = self._router(inner)
        response = router.route("/status")
        self.assertTrue(router.active)  # a command does not end onboarding
        self.assertEqual(response.text, "status output")
        self.assertEqual(inner.prompts[0], "/status")

    def test_max_turns_forces_completion(self) -> None:
        inner = FakeInner([_response("Enda et svar") for _ in range(3)])
        router = self._router(inner, max_turns=2)
        router.route("melding 1")
        self.assertTrue(router.active)
        router.route("melding 2")
        self.assertFalse(router.active)  # hit the cap
        # Persisted so a fresh router keeps the completed state.
        reopened = self._router(FakeInner([_response("x")]))
        self.assertFalse(reopened.active)

    def test_failed_response_does_not_advance(self) -> None:
        inner = FakeInner([_response("boom", success=False)])
        router = self._router(inner)
        router.route("hei")
        self.assertEqual(router.state.turns, 0)
        self.assertTrue(router.active)


if __name__ == "__main__":
    unittest.main()
