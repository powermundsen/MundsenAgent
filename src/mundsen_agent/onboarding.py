"""First-conversation onboarding: a guided interview instead of a form.

A new instance knows nothing about its owner. Rather than asking them to fill
in a profile, the assistant runs the first conversation itself: it introduces
itself, asks about the person, and writes what it learns into the personal
instance. The brief that steers that conversation lives in
``templates/onboarding.md.tmpl`` so it upgrades with the core and stays
readable by the owner.

This module owns the durable state, the injected brief, and the wrapper that
applies both. It never calls an agent directly.
"""

from __future__ import annotations

import importlib.resources
import json
import os
import stat
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event
from typing import Protocol

from mundsen_agent.instance import InstanceSettings
from mundsen_agent.paths import ensure_private_directories
from mundsen_agent.router.models import AgentResponse

COMPLETION_MARKER = "[[mundsen:first-conversation-complete]]"
"""Sentinel the assistant appends to its closing message. Never sent onward."""

MAX_TURNS = 14
"""Hard stop so a stalled interview cannot follow the owner forever."""

_SKIP_COMMANDS = frozenset(
    {
        "/skip",
        "/hoppover",
        "/senere",
        "/later",
    }
)


class OnboardingStateError(ValueError):
    """Raised when persisted onboarding state is unsafe or invalid."""


class InnerRouter(Protocol):
    def route(
        self,
        message: str,
        *,
        cancel_event: Event | None = None,
    ) -> AgentResponse: ...


@dataclass(frozen=True, slots=True)
class OnboardingState:
    completed: bool = False
    turns: int = 0
    opening_authorized: bool = False
    opening_sent: bool = False


class OnboardingStore:
    """Private, atomic persistence for first-conversation progress."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> OnboardingState:
        if self.path.is_symlink():
            raise OnboardingStateError("onboarding state path is unsafe")
        if not self.path.exists():
            return OnboardingState()
        if not self.path.is_absolute():
            raise OnboardingStateError("onboarding state path is unsafe")
        file_stat = self.path.stat()
        if not stat.S_ISREG(file_stat.st_mode):
            raise OnboardingStateError("onboarding state is not a regular file")
        if stat.S_IMODE(file_stat.st_mode) != 0o600:
            raise OnboardingStateError("onboarding state must use mode 0600")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            completed = data["completed"]
            turns = data["turns"]
            opening_authorized = data.get("opening_authorized", False)
            opening_sent = data.get("opening_sent", turns > 0)
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ) as exc:
            raise OnboardingStateError("onboarding state is invalid") from exc
        if (
            not isinstance(completed, bool)
            or isinstance(turns, bool)
            or not isinstance(turns, int)
            or not isinstance(opening_authorized, bool)
            or not isinstance(opening_sent, bool)
            or turns < 0
            or turns > 10_000
        ):
            raise OnboardingStateError("onboarding state is invalid")
        return OnboardingState(
            completed=completed,
            turns=turns,
            opening_authorized=opening_authorized,
            opening_sent=opening_sent,
        )

    def save(self, state: OnboardingState) -> None:
        if not self.path.is_absolute() or self.path.is_symlink():
            raise OnboardingStateError("onboarding state path is unsafe")
        ensure_private_directories((self.path.parent,))
        payload = {
            "completed": state.completed,
            "turns": state.turns,
            "opening_authorized": state.opening_authorized,
            "opening_sent": state.opening_sent,
        }
        descriptor, temporary = tempfile.mkstemp(
            prefix=".onboarding-state-",
            dir=self.path.parent,
        )
        temp_path = Path(temporary)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def onboarding_brief(settings: InstanceSettings) -> str:
    """Render the interview brief for one instance."""

    source = (
        importlib.resources.files("mundsen_agent")
        .joinpath("templates")
        .joinpath("onboarding.md.tmpl")
        .read_text(encoding="utf-8")
    )
    return (
        source.replace("{{ASSISTANT_NAME}}", settings.assistant_name)
        .replace("{{LANGUAGE}}", settings.language)
        .replace("{{TIMEZONE}}", settings.timezone)
        .replace("{{COMPLETION_MARKER}}", COMPLETION_MARKER)
    )


def opening_prompt(settings: InstanceSettings) -> str:
    """The instruction that produces the assistant's very first message."""

    return (
        f"{onboarding_brief(settings)}\n\n"
        "---\n\n"
        "The owner has not written anything yet. Send your opening message now, "
        "following the section above on how to open the conversation. Output "
        "only that message."
    )


def strip_completion_marker(text: str) -> tuple[str, bool]:
    """Remove the sentinel anywhere in the reply and report whether it was there."""

    if COMPLETION_MARKER not in text:
        return text, False
    cleaned = "\n".join(
        line
        for line in text.splitlines()
        if line.strip() != COMPLETION_MARKER
    )
    cleaned = cleaned.replace(COMPLETION_MARKER, "")
    return cleaned.strip(), True


def is_skip_request(message: str) -> bool:
    parts = message.strip().split(maxsplit=1)
    if not parts:
        return False
    return parts[0].lower().split("@", 1)[0] in _SKIP_COMMANDS


class OnboardingRouter:
    """Wrap the agent router so the first conversation is an interview.

    Once onboarding is complete this adds nothing: messages pass straight
    through to the wrapped router.
    """

    def __init__(
        self,
        *,
        inner: InnerRouter,
        store: OnboardingStore,
        settings: InstanceSettings,
        max_turns: int = MAX_TURNS,
    ) -> None:
        self.inner = inner
        self.store = store
        self.settings = settings
        self.max_turns = max_turns
        self.state = store.load()

    @property
    def active(self) -> bool:
        return not self.state.completed

    def open_conversation(
        self,
        *,
        cancel_event: Event | None = None,
    ) -> AgentResponse | None:
        """Produce the assistant's unprompted first message, or ``None``."""

        if (
            self.state.completed
            or not self.state.opening_authorized
            or self.state.opening_sent
        ):
            return None
        response = self.inner.route(
            opening_prompt(self.settings),
            cancel_event=cancel_event,
        )
        if not response.success:
            return response
        text, _ = strip_completion_marker(response.text)
        return replace(response, text=text)

    def mark_opening_sent(self) -> None:
        """Persist delivery only after Telegram accepted the opening message."""

        if self.state.completed or self.state.opening_sent:
            return
        self.state = replace(
            self.state,
            opening_sent=True,
            turns=self.state.turns + 1,
        )
        if self.state.turns >= self.max_turns:
            self.state = replace(self.state, completed=True)
        self.store.save(self.state)

    def route(
        self,
        message: str,
        *,
        cancel_event: Event | None = None,
    ) -> AgentResponse:
        if self.state.completed:
            return self.inner.route(message, cancel_event=cancel_event)
        if message.strip().startswith("/") and not is_skip_request(message):
            # Router commands such as /new or /status must keep working.
            return self.inner.route(message, cancel_event=cancel_event)
        if is_skip_request(message):
            self._finish()
            return self.inner.route(
                f"{onboarding_brief(self.settings)}\n\n"
                "---\n\n"
                "The owner just asked to stop the first conversation. Save "
                "anything you already learned, then send one short, warm "
                "message: you will pick the rest up naturally later, and they "
                "can tell you anything about themselves whenever they like. "
                "Do not ask another question and do not output the marker.",
                cancel_event=cancel_event,
            )

        response = self.inner.route(
            self._wrap(message),
            cancel_event=cancel_event,
        )
        return self._absorb(response, owner_initiated=True)

    def _wrap(self, message: str) -> str:
        remaining = max(self.max_turns - self.state.turns, 0)
        opening = ""
        if not self.state.opening_sent:
            opening = (
                "\n\nThe owner chose to start by writing before you sent an "
                "opening message. Introduce yourself briefly, react to what "
                "they wrote, and ask at most one natural first question.\n"
            )
        closing = ""
        if remaining <= 1:
            closing = (
                "\n\nThis first conversation has gone on long enough. Write "
                "down what you have, close it warmly as described above, and "
                "output the marker at the end of this message.\n"
            )
        elif remaining <= 3:
            closing = (
                "\n\nThe first conversation is nearly done. Cover at most one "
                "more thing, then close it as described above.\n"
            )
        return (
            f"{onboarding_brief(self.settings)}{opening}{closing}\n\n"
            "---\n\n"
            "The owner just wrote the message below. Reply to it as the next "
            "turn of that first conversation.\n\n"
            f"{message}"
        )

    def _absorb(
        self,
        response: AgentResponse,
        *,
        owner_initiated: bool = False,
    ) -> AgentResponse:
        if not response.success:
            return response
        text, finished = strip_completion_marker(response.text)
        self.state = replace(
            self.state,
            turns=self.state.turns + 1,
            opening_sent=self.state.opening_sent or owner_initiated,
        )
        if finished or self.state.turns >= self.max_turns:
            self.state = replace(self.state, completed=True)
        self.store.save(self.state)
        if text == response.text:
            return response
        return replace(response, text=text)

    def _finish(self) -> None:
        self.state = replace(
            self.state,
            completed=True,
            opening_sent=True,
        )
        self.store.save(self.state)
