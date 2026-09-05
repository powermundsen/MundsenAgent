"""Subscription-only authentication helpers for provider CLIs."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from mundsen_agent.paths import MundsenAgentPaths
from mundsen_agent.router.agents import provider_environment

Run = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class ProviderAuth:
    provider: str
    installed: bool
    authenticated: bool


_CLAUDE_SUBSCRIPTIONS = frozenset(
    {"pro", "max", "team", "business", "enterprise"}
)


def provider_executable(paths: MundsenAgentPaths, provider: str) -> Path:
    if provider == "claude":
        return paths.provider_home / ".local" / "bin" / "claude"
    if provider == "codex":
        return paths.provider_bin_dir / "codex"
    raise ValueError("provider must be claude or codex")


def _auth_command(paths: MundsenAgentPaths, provider: str, action: str) -> list[str]:
    executable = str(provider_executable(paths, provider))
    if provider == "claude":
        return [executable, "auth", "status" if action == "status" else "login"]
    return [executable, "login", "status"] if action == "status" else [
        executable,
        "login",
    ]


def authentication_status(
    paths: MundsenAgentPaths,
    provider: str,
    *,
    runner: Run = subprocess.run,
) -> ProviderAuth:
    executable = provider_executable(paths, provider)
    if not executable.is_file():
        return ProviderAuth(provider, False, False)
    try:
        result = runner(
            _auth_command(paths, provider, "status"),
            cwd=str(paths.instance_dir if paths.instance_dir.exists() else paths.home),
            env=provider_environment(paths),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ProviderAuth(provider, True, False)
    authenticated = (
        result.returncode == 0
        and _uses_subscription(provider, result.stdout or "")
    )
    return ProviderAuth(provider, True, authenticated)


def _uses_subscription(provider: str, stdout: str) -> bool:
    """Accept only monthly subscription authentication, never API billing."""

    if provider == "codex":
        return "chatgpt" in " ".join(stdout.lower().split())
    try:
        status = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(status, dict) or status.get("loggedIn") is not True:
        return False
    auth_method = str(status.get("authMethod", "")).strip().lower()
    subscription = str(status.get("subscriptionType", "")).strip().lower()
    return (
        auth_method == "claude.ai"
        and subscription in _CLAUDE_SUBSCRIPTIONS
    )


def interactive_login(
    paths: MundsenAgentPaths,
    provider: str,
    *,
    runner: Run = subprocess.run,
) -> bool:
    """Run the provider's browser login without accepting API credentials."""

    executable = provider_executable(paths, provider)
    if not executable.is_file():
        raise FileNotFoundError(f"{provider} is not installed")
    result = runner(
        _auth_command(paths, provider, "login"),
        cwd=str(paths.instance_dir),
        env=provider_environment(paths),
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    return authentication_status(paths, provider, runner=runner).authenticated
