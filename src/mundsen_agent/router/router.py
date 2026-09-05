"""Minimal dual-agent orchestration with explicit modes and safe fallback."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event, RLock
import re
import time
from typing import Protocol

from mundsen_agent.audit import AuditLogger
from mundsen_agent.router.models import AgentResponse, RouterMode, RouterState
from mundsen_agent.router.state import RouterStateStore


class AgentAdapter(Protocol):
    name: str

    def call(
        self,
        message: str,
        *,
        session_id: str = "",
        cancel_event: Event | None = None,
    ) -> AgentResponse: ...


def _user_error(category: str, language: str) -> str:
    norwegian = not language.lower().startswith("en")
    messages = {
        "cancelled": (
            "Jobben ble stoppet."
            if norwegian
            else "The active job was stopped."
        ),
        "timeout": (
            "Agenten brukte for lang tid. Prøv igjen."
            if norwegian
            else "The agent timed out. Please try again."
        ),
        "auth": (
            "Agenten må logges inn på nytt. Kjør `mundsen auth` lokalt."
            if norwegian
            else "The agent must sign in again. Run `mundsen auth` locally."
        ),
        "binary_missing": (
            "En agent mangler. Kjør Mundsen Agent-installeren på nytt lokalt."
            if norwegian
            else "An agent is missing. Run the Mundsen Agent installer again locally."
        ),
    }
    return messages.get(
        category,
        (
            "Det oppsto en teknisk agentfeil. Detaljene er logget lokalt."
            if norwegian
            else "A technical agent error occurred. Details were logged locally."
        ),
    )


class AgentRouter:
    def __init__(
        self,
        *,
        claude: AgentAdapter,
        codex: AgentAdapter,
        state_store: RouterStateStore,
        audit: AuditLogger,
        preferred_agent: str = "auto",
        language: str = "nb",
        context_provider: Callable[[], str] | None = None,
        extended_commands: bool = False,
        version_text: str = "",
        claude_model_aliases: dict[str, str] | None = None,
        codex_model_aliases: dict[str, str] | None = None,
        circuit_breaker_threshold: int = 0,
        circuit_breaker_cooldown_seconds: int = 300,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.adapters = {"claude": claude, "codex": codex}
        self.state_store = state_store
        self.audit = audit
        self.language = language
        self.context_provider = context_provider
        self.extended_commands = extended_commands
        self.version_text = version_text.strip()
        self.claude_model_aliases = dict(claude_model_aliases or {})
        self.codex_model_aliases = dict(codex_model_aliases or {})
        self.circuit_breaker_threshold = max(circuit_breaker_threshold, 0)
        self.circuit_breaker_cooldown_seconds = max(
            circuit_breaker_cooldown_seconds,
            1,
        )
        self.monotonic = monotonic
        self._failure_counts = {"claude": 0, "codex": 0}
        self._open_until = {"claude": 0.0, "codex": 0.0}
        self._lock = RLock()
        self.state = state_store.load()
        if self.state.claude_model:
            self._configure_adapter("claude", model=self.state.claude_model)
        if self.state.codex_model:
            self._configure_adapter("codex", model=self.state.codex_model)
        if self.state.codex_reasoning_effort:
            self._configure_adapter(
                "codex",
                reasoning=self.state.codex_reasoning_effort,
            )
        if preferred_agent in {"claude", "codex"}:
            self.preferred_agent = preferred_agent
        else:
            self.preferred_agent = "claude"

    def handle_command(self, message: str) -> str | None:
        parts = message.strip().split(maxsplit=1)
        if not parts:
            return None
        command = parts[0].lower()
        with self._lock:
            if command in {"/new", "/stop"}:
                self.state.claude_session_id = ""
                self.state.codex_session_id = ""
                self.state.last_error_category = ""
                self.state_store.save(self.state)
                self.audit.emit(
                    "router",
                    "sessions_closed" if command == "/stop" else "session_reset",
                    success=True,
                )
                if command == "/stop":
                    return self._text(
                        "Alle aktive Claude- og Codex-sesjoner er lukket.",
                        "All active Claude and Codex sessions are closed.",
                    )
                return self._text("Ny samtale startet.", "New conversation started.")
            if command in {"/auto", "/claude", "/codex"} or (
                self.extended_commands and command == "/gpt"
            ):
                mode_name = "codex" if command == "/gpt" else command[1:]
                self.state.mode = RouterMode(mode_name)
                self.state_store.save(self.state)
                self.audit.emit(
                    "router",
                    "mode_changed",
                    mode=self.state.mode.value,
                    success=True,
                )
                return self._text(
                    f"Agentmodus: {self.state.mode.value}.",
                    f"Agent mode: {self.state.mode.value}.",
                )
            if command == "/status":
                extra = ""
                if self.extended_commands:
                    selected = []
                    if self.state.claude_model:
                        selected.append(f"Claude: {self.state.claude_model}")
                    if self.state.codex_model:
                        selected.append(f"Codex: {self.state.codex_model}")
                    if self.state.codex_reasoning_effort:
                        selected.append(
                            f"reasoning: {self.state.codex_reasoning_effort}"
                        )
                    open_agents = self._open_agents()
                    if open_agents:
                        selected.append(f"circuit open: {', '.join(open_agents)}")
                    if selected:
                        extra = " " + "; ".join(selected) + "."
                return self._text(
                    (
                        f"Modus: {self.state.mode.value}. "
                        f"Siste agent: {self.state.last_agent or 'ingen'}."
                        f"{extra}"
                    ),
                    (
                        f"Mode: {self.state.mode.value}. "
                        f"Last agent: {self.state.last_agent or 'none'}."
                        f"{extra}"
                    ),
                )
            if not self.extended_commands:
                return None
            if command == "/version":
                return self.version_text or self._text(
                    "Versjonsinformasjon er ikke tilgjengelig.",
                    "Version information is unavailable.",
                )
            if command == "/models":
                aliases = [
                    *(f"/{name} (Claude)" for name in sorted(self.claude_model_aliases)),
                    *(f"/{name} (Codex)" for name in sorted(self.codex_model_aliases)),
                ]
                reasoning = "/think minimal|low|medium|high|xhigh"
                return self._text("Tilgjengelig: ", "Available: ") + ", ".join(
                    [*aliases, reasoning]
                )
            if command == "/rollback":
                reference = parts[1].strip() if len(parts) == 2 else ""
                if reference and not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", reference):
                    return self._text(
                        "Ugyldig versjonsreferanse.",
                        "Invalid version reference.",
                    )
                suffix = f" {reference}" if reference else ""
                return self._text(
                    (
                        "Rollback kjøres ikke fra Telegram. Kontroller health og kjør "
                        f"`mundsen rollback{suffix}` lokalt etter godkjenning."
                    ),
                    (
                        "Rollback is not run from Telegram. Check health and run "
                        f"`mundsen rollback{suffix}` locally after approval."
                    ),
                )
            alias = command[1:]
            if alias in self.claude_model_aliases:
                model = self.claude_model_aliases[alias]
                self._configure_adapter("claude", model=model)
                self.state.mode = RouterMode.CLAUDE
                self.state.claude_model = model
                self.state.claude_session_id = ""
                self.state_store.save(self.state)
                return self._text(
                    f"Claude-modell: {alias}.",
                    f"Claude model: {alias}.",
                )
            if alias in self.codex_model_aliases:
                model = self.codex_model_aliases[alias]
                self._configure_adapter("codex", model=model)
                self.state.mode = RouterMode.CODEX
                self.state.codex_model = model
                self.state.codex_session_id = ""
                self.state_store.save(self.state)
                return self._text(
                    f"Codex-modell: {alias}.",
                    f"Codex model: {alias}.",
                )
            reasoning = self._reasoning_command(parts)
            if reasoning:
                self._configure_adapter("codex", reasoning=reasoning)
                self.state.mode = RouterMode.CODEX
                self.state.codex_reasoning_effort = reasoning
                self.state.codex_session_id = ""
                self.state_store.save(self.state)
                return self._text(
                    f"Codex reasoning: {reasoning}.",
                    f"Codex reasoning: {reasoning}.",
                )
        return None

    def route(
        self,
        message: str,
        *,
        cancel_event: Event | None = None,
    ) -> AgentResponse:
        command_response = self.handle_command(message)
        if command_response is not None:
            return AgentResponse(
                text=command_response,
                agent="router",
                success=True,
            )
        with self._lock:
            routed_message = message
            if self.context_provider is not None:
                context = self.context_provider().strip()
                if context:
                    routed_message = f"{context}\n\n<owner_message>\n{message}\n</owner_message>"
            mode = self.state.mode
            one_shot_agent = (
                "claude"
                if self.extended_commands
                and message.strip().split(maxsplit=1)[0].lower() == "/powerup"
                else ""
            )
            if one_shot_agent:
                order = [one_shot_agent]
            elif mode == RouterMode.AUTO:
                order = [self.preferred_agent]
                order.append("codex" if order[0] == "claude" else "claude")
                available = [name for name in order if not self._circuit_open(name)]
                if available:
                    order = available
            else:
                order = [mode.value]

            last_response: AgentResponse | None = None
            for attempt, name in enumerate(order, start=1):
                session_id = (
                    self.state.claude_session_id
                    if name == "claude"
                    else self.state.codex_session_id
                )
                response = self.adapters[name].call(
                    routed_message,
                    session_id=session_id,
                    cancel_event=cancel_event,
                )
                last_response = response
                self.audit.emit(
                    "router",
                    "agent_attempt",
                    agent=name,
                    attempt=attempt,
                    success=response.success,
                    error_category=response.error_category or None,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    cached_input_tokens=response.cached_input_tokens,
                )
                if response.session_id:
                    if name == "claude":
                        self.state.claude_session_id = response.session_id
                    else:
                        self.state.codex_session_id = response.session_id
                if response.success:
                    self._record_success(name)
                    self.state.last_agent = name
                    self.state.last_error_category = ""
                    self.state_store.save(self.state)
                    return response
                self.state.last_error_category = response.error_category
                self._record_failure(name, response.error_category)
                if response.error_category == "cancelled":
                    break

            self.state_store.save(self.state)
            category = (
                last_response.error_category
                if last_response is not None
                else "provider_error"
            )
            return AgentResponse(
                text=_user_error(category, self.language),
                agent=(last_response.agent if last_response else "router"),
                success=False,
                error_category=category,
            )

    def _configure_adapter(
        self,
        name: str,
        *,
        model: str = "",
        reasoning: str = "",
    ) -> None:
        adapter = self.adapters[name]
        if model:
            setter = getattr(adapter, "set_model", None)
            if not callable(setter):
                raise ValueError(f"{name} adapter does not support model selection")
            setter(model)
        if reasoning:
            setter = getattr(adapter, "set_reasoning_effort", None)
            if not callable(setter):
                raise ValueError(f"{name} adapter does not support reasoning selection")
            setter(reasoning)

    @staticmethod
    def _reasoning_command(parts: list[str]) -> str:
        command = parts[0].lower()
        levels = {"minimal", "low", "medium", "high", "xhigh"}
        candidate = ""
        if command == "/think" and len(parts) == 2:
            candidate = parts[1].strip().lower()
        else:
            for prefix in ("/think-", "/gpt-", "/codex-"):
                if command.startswith(prefix):
                    candidate = command[len(prefix):]
                    break
            if command[1:] in levels:
                candidate = command[1:]
        return candidate if candidate in levels else ""

    def _circuit_open(self, name: str) -> bool:
        return (
            self.circuit_breaker_threshold > 0
            and self._open_until[name] > self.monotonic()
        )

    def _open_agents(self) -> list[str]:
        return [name for name in ("claude", "codex") if self._circuit_open(name)]

    def _record_success(self, name: str) -> None:
        self._failure_counts[name] = 0
        self._open_until[name] = 0.0

    def _record_failure(self, name: str, category: str) -> None:
        if self.circuit_breaker_threshold <= 0 or category == "cancelled":
            return
        self._failure_counts[name] += 1
        if self._failure_counts[name] >= self.circuit_breaker_threshold:
            self._open_until[name] = (
                self.monotonic() + self.circuit_breaker_cooldown_seconds
            )
            self._failure_counts[name] = 0

    def _text(self, norwegian: str, english: str) -> str:
        return english if self.language.lower().startswith("en") else norwegian
