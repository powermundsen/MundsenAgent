"""Safe adapters for subscription-authenticated Claude Code and Codex CLI."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from threading import Event

from mundsen_agent.paths import MundsenAgentPaths
from mundsen_agent.router.capabilities import (
    CLAUDE_ALLOWED_TOOLS,
    CODEX_SANDBOX,
    claude_allowed_tools,
    codex_sandbox,
)
from mundsen_agent.router.models import AgentResponse
from mundsen_agent.router.process import CommandResult, run_command

ProcessRunner = Callable[..., CommandResult]
MAX_AGENT_RESPONSE_CHARS = 128 * 1024
_SECRET_RE = re.compile(
    r"(?:"
    r"sk-[A-Za-z0-9_-]{12,}|"
    r"Bearer\s+[A-Za-z0-9._-]{12,}|"
    r"bot\d{8,}:[A-Za-z0-9_-]+|"
    r"https://[^/\s:@]+:[^@\s/]+@"
    r")",
    re.IGNORECASE,
)
_AUTH_RE = re.compile(
    r"(?:not logged in|login required|unauthorized|oauth|authentication|credential)",
    re.IGNORECASE,
)
_LIMIT_RE = re.compile(
    r"(?:rate.?limit|usage.?limit|token.?limit|context window|overloaded|529)",
    re.IGNORECASE,
)


def provider_environment(
    paths: MundsenAgentPaths,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build an allowlisted provider environment without inherited secrets."""

    source = os.environ if environ is None else environ
    env = {
        name: str(source[name])
        for name in ("LANG", "LC_ALL", "LC_CTYPE", "TERM", "TZ")
        if source.get(name)
    }
    env["HOME"] = str(paths.provider_home)
    env["CLAUDE_CONFIG_DIR"] = str(paths.provider_home / ".claude")
    env["CODEX_HOME"] = str(paths.codex_home)
    env["TMPDIR"] = str(paths.cache_dir)
    env["SHELL"] = "/bin/sh"
    provider_path = os.pathsep.join(
        (
            str(paths.provider_bin_dir),
            str(paths.provider_home / ".local" / "bin"),
            str(paths.tools_dir / "bin"),
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
        )
    )
    env["PATH"] = provider_path
    env["MUNDSEN_HOME"] = str(paths.home)
    env["NO_COLOR"] = "1"
    return env


def classify_error(text: str, *, status: str = "") -> str:
    normalized = " ".join((text or "").split())
    if status == "cancelled":
        return "cancelled"
    if status == "timeout":
        return "timeout"
    if _SECRET_RE.search(normalized):
        return "sensitive_error"
    if _AUTH_RE.search(normalized):
        return "auth"
    if _LIMIT_RE.search(normalized):
        return "limit"
    if "not found" in normalized.lower() or "no such file" in normalized.lower():
        return "binary_missing"
    return "provider_error"


def _bounded_response(text: str) -> str:
    if len(text) <= MAX_AGENT_RESPONSE_CHARS:
        return text
    return text[:MAX_AGENT_RESPONSE_CHARS].rstrip() + "\n\n[response truncated]"


class ClaudeAdapter:
    name = "claude"

    def __init__(
        self,
        paths: MundsenAgentPaths,
        *,
        executable: str | Path | None = None,
        model: str = "",
        timeout_seconds: int = 900,
        runner: ProcessRunner = run_command,
        allowed_tools: Sequence[str] = CLAUDE_ALLOWED_TOOLS,
    ) -> None:
        self.paths = paths
        self.executable = str(
            executable or paths.provider_home / ".local" / "bin" / "claude"
        )
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds
        self.runner = runner
        # Validate at construction so an unconfirmed external capability stops
        # the bridge at startup instead of on the first owner message.
        self.allowed_tools = tuple(allowed_tools)
        claude_allowed_tools(self.allowed_tools)

    def set_model(self, model: str) -> None:
        value = model.strip()
        if len(value) > 120 or any(ord(character) < 32 for character in value):
            raise ValueError("invalid Claude model identifier")
        self.model = value

    def call(
        self,
        message: str,
        *,
        session_id: str = "",
        cancel_event: Event | None = None,
    ) -> AgentResponse:
        command = [
            self.executable,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "acceptEdits",
            "--allowedTools",
            claude_allowed_tools(self.allowed_tools),
        ]
        if self.model:
            command.extend(("--model", self.model))
        if session_id:
            command.extend(("--resume", session_id))
        try:
            result = self.runner(
                command,
                cwd=str(self.paths.instance_dir),
                env=provider_environment(self.paths),
                timeout_seconds=self.timeout_seconds,
                input_text=message,
                cancel_event=cancel_event,
            )
        except (FileNotFoundError, OSError):
            return AgentResponse(
                text="",
                agent=self.name,
                success=False,
                error_category="binary_missing",
            )
        return self._parse_result(result, previous_session_id=session_id)

    def _parse_result(
        self,
        result: CommandResult,
        *,
        previous_session_id: str,
    ) -> AgentResponse:
        final: dict[str, object] | None = None
        initialized_session = previous_session_id
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if event.get("type") == "system" and event.get("subtype") == "init":
                initialized_session = str(
                    event.get("session_id") or initialized_session
                )
            elif event.get("type") == "result":
                final = event
        if result.status != "completed":
            return AgentResponse(
                text="",
                agent=self.name,
                success=False,
                session_id=initialized_session,
                model=self.model,
                error_category=classify_error(
                    result.stderr or result.stdout,
                    status=result.status,
                ),
            )
        if final is None:
            return AgentResponse(
                text="",
                agent=self.name,
                success=False,
                session_id=initialized_session,
                model=self.model,
                error_category=classify_error(result.stderr or result.stdout),
            )
        usage = final.get("usage") if isinstance(final.get("usage"), dict) else {}
        session = str(final.get("session_id") or initialized_session)
        is_error = bool(final.get("is_error")) or result.returncode != 0
        text = _bounded_response(str(final.get("result") or ""))
        return AgentResponse(
            text="" if is_error else text,
            agent=self.name,
            success=not is_error and bool(text.strip()),
            session_id=session,
            model=self.model,
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            error_category=(
                classify_error(text or result.stderr)
                if is_error or not text.strip()
                else ""
            ),
        )


class CodexAdapter:
    name = "codex"

    def __init__(
        self,
        paths: MundsenAgentPaths,
        *,
        executable: str | Path | None = None,
        model: str = "",
        reasoning_effort: str = "",
        timeout_seconds: int = 900,
        runner: ProcessRunner = run_command,
        sandbox: str = CODEX_SANDBOX,
    ) -> None:
        self.paths = paths
        self.executable = str(executable or paths.provider_bin_dir / "codex")
        self.model = model.strip()
        self.reasoning_effort = reasoning_effort.strip().lower()
        self.timeout_seconds = timeout_seconds
        self.runner = runner
        # Validate at construction so an unconfirmed external capability stops
        # the bridge at startup instead of on the first owner message.
        self.sandbox = sandbox
        codex_sandbox(self.sandbox)

    def set_model(self, model: str) -> None:
        value = model.strip()
        if len(value) > 120 or any(ord(character) < 32 for character in value):
            raise ValueError("invalid Codex model identifier")
        self.model = value

    def set_reasoning_effort(self, effort: str) -> None:
        value = effort.strip().lower()
        if value not in {"minimal", "low", "medium", "high", "xhigh"}:
            raise ValueError("invalid Codex reasoning effort")
        self.reasoning_effort = value

    def call(
        self,
        message: str,
        *,
        session_id: str = "",
        cancel_event: Event | None = None,
    ) -> AgentResponse:
        command = [
            self.executable,
            "-C",
            str(self.paths.instance_dir),
            "--ask-for-approval",
            "never",
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--sandbox",
            codex_sandbox(self.sandbox),
            "--ignore-user-config",
        ]
        if self.model:
            command.extend(("--model", self.model))
        if self.reasoning_effort in {"minimal", "low", "medium", "high", "xhigh"}:
            command.extend(
                ("--config", f'model_reasoning_effort="{self.reasoning_effort}"')
            )
        if session_id:
            command.extend(("resume", session_id, "-"))
        else:
            command.append("-")
        try:
            result = self.runner(
                command,
                cwd=str(self.paths.instance_dir),
                env=provider_environment(self.paths),
                timeout_seconds=self.timeout_seconds,
                input_text=message,
                cancel_event=cancel_event,
            )
        except (FileNotFoundError, OSError):
            return AgentResponse(
                text="",
                agent=self.name,
                success=False,
                error_category="binary_missing",
            )
        return self._parse_result(result, previous_session_id=session_id)

    def _parse_result(
        self,
        result: CommandResult,
        *,
        previous_session_id: str,
    ) -> AgentResponse:
        thread_id = previous_session_id
        response_text = ""
        input_tokens = 0
        output_tokens = 0
        cached_input_tokens = 0
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            event_type = event.get("type")
            if event_type == "thread.started":
                thread_id = str(event.get("thread_id") or thread_id)
            elif event_type == "item.completed":
                item = event.get("item")
                if (
                    isinstance(item, dict)
                    and item.get("type") == "agent_message"
                    and isinstance(item.get("text"), str)
                ):
                    response_text = _bounded_response(item["text"])
            elif event_type == "turn.completed":
                usage = event.get("usage")
                if isinstance(usage, dict):
                    input_tokens = int(usage.get("input_tokens", 0) or 0)
                    output_tokens = int(usage.get("output_tokens", 0) or 0)
                    cached_input_tokens = int(
                        usage.get("cached_input_tokens", 0) or 0
                    )
        success = (
            result.status == "completed"
            and result.returncode == 0
            and bool(response_text.strip())
        )
        return AgentResponse(
            text=response_text if success else "",
            agent=self.name,
            success=success,
            session_id=thread_id,
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            error_category=(
                ""
                if success
                else classify_error(
                    result.stderr or result.stdout,
                    status=result.status,
                )
            ),
        )
