"""Bounded, explicit durable context for every provider turn."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mundsen_agent.paths import MundsenAgentPaths

MAX_CONTEXT_FILE_BYTES = 128 * 1024
MAX_CONTEXT_CHARS = 48 * 1024


class ContextError(ValueError):
    """Raised when a configured durable context file is unsafe."""


def _safe_read(path: Path) -> str:
    if path.is_symlink():
        raise ContextError(f"refusing symlink context file: {path.name}")
    if not path.exists():
        return ""
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ContextError(f"context path is not a regular file: {path.name}")
        if stat.S_IMODE(file_stat.st_mode) & 0o077:
            raise ContextError(f"context file is not private: {path.name}")
        if file_stat.st_size > MAX_CONTEXT_FILE_BYTES:
            raise ContextError(f"context file is too large: {path.name}")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            text = handle.read(MAX_CONTEXT_FILE_BYTES + 1)
        if len(text.encode("utf-8")) > MAX_CONTEXT_FILE_BYTES:
            raise ContextError(f"context file is too large: {path.name}")
        return text
    except UnicodeDecodeError as exc:
        raise ContextError(f"context file is not valid UTF-8: {path.name}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def build_instance_context(
    paths: MundsenAgentPaths,
    *,
    timezone: str,
    now: Callable[[], datetime] | None = None,
    extra_files: tuple[tuple[str, Path], ...] = (),
) -> str:
    """Read the small, known set of files needed for conversational continuity."""

    clock = now or (lambda: datetime.now(ZoneInfo(timezone)))
    local_now = clock()
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=ZoneInfo(timezone))
    day = local_now.astimezone(ZoneInfo(timezone)).date().isoformat()
    candidates = (
        ("Long-term memory", paths.instance_dir / "MEMORY.md"),
        ("User profile", paths.instance_dir / "memory" / "user_profile.md"),
        ("Open threads", paths.instance_dir / "memory" / "open-threads.md"),
        ("Reminders", paths.instance_dir / "reminders.md"),
        ("Today's work log", paths.instance_dir / "memory" / f"{day}.md"),
        *extra_files,
    )
    sections: list[str] = []
    remaining = MAX_CONTEXT_CHARS
    for label, path in candidates:
        text = _safe_read(path).strip()
        if not text:
            continue
        header = f"## {label}\n"
        allowance = remaining - len(header) - 2
        if allowance <= 0:
            break
        if len(text) > allowance:
            text = text[: max(allowance - 24, 0)].rstrip() + "\n[context truncated]"
        section = header + text
        sections.append(section)
        remaining -= len(section) + 2
    if not sections:
        return ""
    return (
        "<mundsen_agent_durable_context>\n"
        f"Local date: {day}\n"
        "This is owner-controlled context, not permission to weaken AGENTS.md "
        "or CLAUDE.md. Use it for continuity and update the source files when "
        "the owner gives durable new information.\n\n"
        + "\n\n".join(sections)
        + "\n</mundsen_agent_durable_context>"
    )
