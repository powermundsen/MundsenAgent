"""Enforced capability boundary for provider agent processes.

Mundsen Agent promises that an action reaching outside the machine needs a separate,
concrete owner confirmation. The base runtime keeps that promise by never
handing an agent a tool that can send messages, spend money, change accounts,
or reach the network on its own. Prompt text alone cannot enforce that, so the
boundary lives here: one place that defines which capabilities exist, and
validation that fails loudly if an adapter is ever configured beyond them.

Widening the boundary is therefore a deliberate change to this module with a
failing test, not an edit hidden inside one adapter's argument list.
"""

from __future__ import annotations

from collections.abc import Iterable

# Tools that can only read or write inside the instance directory the agent
# already runs in. None of them can reach another person, service, or account.
LOCAL_ONLY_CLAUDE_TOOLS = frozenset(
    {
        "Edit",
        "Glob",
        "Grep",
        "Read",
        "Write",
    }
)

# The exact set the runtime grants today, in the order passed to the CLI.
CLAUDE_ALLOWED_TOOLS: tuple[str, ...] = ("Read", "Edit", "Write", "Glob", "Grep")

# Codex sandboxes that keep the agent inside its own workspace without
# network access. "danger-full-access" is deliberately absent.
LOCAL_ONLY_CODEX_SANDBOXES = frozenset({"read-only", "workspace-write"})
CODEX_SANDBOX = "workspace-write"


class CapabilityError(RuntimeError):
    """Raised when an agent would be granted an unconfirmed external capability."""


def claude_allowed_tools(tools: Iterable[str] = CLAUDE_ALLOWED_TOOLS) -> str:
    """Return the validated --allowedTools value for the Claude adapter."""

    selected = tuple(tools)
    if not selected:
        raise CapabilityError("at least one tool must be granted")
    for tool in selected:
        if not isinstance(tool, str) or not tool.strip():
            raise CapabilityError("tool names must be non-empty strings")
        if tool not in LOCAL_ONLY_CLAUDE_TOOLS:
            raise CapabilityError(
                f"tool {tool!r} can act outside the instance and needs a"
                " separate owner confirmation flow before it may be granted"
            )
    if len(set(selected)) != len(selected):
        raise CapabilityError("tool names must be unique")
    return ",".join(selected)


def codex_sandbox(sandbox: str = CODEX_SANDBOX) -> str:
    """Return the validated --sandbox value for the Codex adapter."""

    if sandbox not in LOCAL_ONLY_CODEX_SANDBOXES:
        raise CapabilityError(
            f"sandbox {sandbox!r} can act outside the instance and needs a"
            " separate owner confirmation flow before it may be used"
        )
    return sandbox
