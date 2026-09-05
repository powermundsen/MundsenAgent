"""Read private operational metadata and create sanitized support bundles."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import zipfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from mundsen_agent import __version__
from mundsen_agent.health import run_health_checks
from mundsen_agent.paths import MundsenAgentPaths

MAX_AUDIT_READ_BYTES = 2 * 1024 * 1024
MAX_AUDIT_EVENTS = 500
MAX_AUDIT_BACKUPS = 5


class DiagnosticsError(RuntimeError):
    """Raised when local diagnostic data or output paths are unsafe."""


def read_audit_events(
    path: str | Path,
    *,
    limit: int = 100,
    source: str = "",
    event: str = "",
) -> list[dict[str, object]]:
    target = Path(path)
    if limit < 1 or limit > MAX_AUDIT_EVENTS:
        raise DiagnosticsError(f"limit must be between 1 and {MAX_AUDIT_EVENTS}")
    if not target.is_absolute() or target.is_symlink():
        raise DiagnosticsError("audit log is unsafe")
    paths = [
        candidate
        for candidate in (
            *(
                target.with_name(f"{target.name}.{index}")
                for index in range(MAX_AUDIT_BACKUPS, 0, -1)
            ),
            target,
        )
        if candidate.exists() or candidate.is_symlink()
    ]
    if not paths:
        return []
    lines: list[str] = []
    for candidate in paths:
        lines.extend(_read_private_tail(candidate))
    selected: list[dict[str, object]] = []
    for line in reversed(lines):
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        if source and item.get("source") != source:
            continue
        if event and item.get("event") != event:
            continue
        selected.append(item)
        if len(selected) >= limit:
            break
    return list(reversed(selected))


def _read_private_tail(target: Path) -> list[str]:
    if (
        target.is_symlink()
        or not target.is_file()
        or stat.S_IMODE(target.stat().st_mode) != 0o600
    ):
        raise DiagnosticsError("audit log is unsafe")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(target, flags)
    try:
        size = os.fstat(descriptor).st_size
        os.lseek(descriptor, max(0, size - MAX_AUDIT_READ_BYTES), os.SEEK_SET)
        raw = os.read(descriptor, MAX_AUDIT_READ_BYTES)
    finally:
        os.close(descriptor)
    lines = raw.decode("utf-8", errors="ignore").splitlines()
    if size > MAX_AUDIT_READ_BYTES and lines:
        lines = lines[1:]
    return lines


def create_support_bundle(paths: MundsenAgentPaths, output: str | Path) -> Path:
    target = Path(output)
    if not target.is_absolute() or target.is_symlink() or target.exists():
        raise DiagnosticsError("support bundle path must be a new absolute path")
    if target.parent.is_symlink() or not target.parent.is_dir():
        raise DiagnosticsError("support bundle parent must be an existing directory")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mundsen_agent_version": __version__,
        "health": [
            asdict(check)
            for check in run_health_checks(paths, network=False, service=False)
        ],
        "audit_events": read_audit_events(paths.audit_log_file, limit=200),
        "privacy": "No configuration values, secrets, conversations, or health data included.",
    }
    descriptor, temporary = tempfile.mkstemp(prefix=".support-", dir=target.parent)
    temp_path = Path(temporary)
    os.close(descriptor)
    try:
        with zipfile.ZipFile(
            temp_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            archive.writestr(
                "diagnostics.json",
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
            )
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, target)
        os.chmod(target, 0o600)
    finally:
        temp_path.unlink(missing_ok=True)
    return target
