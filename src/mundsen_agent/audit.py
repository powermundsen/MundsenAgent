"""Private-by-default local JSONL audit logging."""

from __future__ import annotations

import json
import math
import os
import re
import stat
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mundsen_agent.paths import PathConfigurationError, ensure_private_directories

ALLOWED_FIELDS = frozenset(
    {
        "agent",
        "attachment_count",
        "attempt",
        "cached_input_tokens",
        "command_name",
        "duration_ms",
        "error_category",
        "exit_code",
        "http_status",
        "input_tokens",
        "mode",
        "model",
        "modules_changed",
        "output_tokens",
        "queue_depth",
        "queue_wait_ms",
        "reason_code",
        "release",
        "rollback",
        "signal",
        "success",
        "target",
        "version",
    }
)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,159}$")
_EVENT_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_SECRET_VALUE_RE = re.compile(
    r"(?:"
    r"github_pat_[A-Za-z0-9_]+|"
    r"ghp_[A-Za-z0-9]+|"
    r"sk-[A-Za-z0-9_-]{12,}|"
    r"Bearer\s+[A-Za-z0-9._-]{12,}|"
    r"bot\d{8,}:[A-Za-z0-9_-]+|"
    r"https://[^/\s:@]+:[^@\s/]+@"
    r")",
    re.IGNORECASE,
)
_IDENTIFIER_FIELDS = frozenset(
    {
        "agent",
        "command_name",
        "error_category",
        "mode",
        "model",
        "reason_code",
        "release",
        "target",
        "version",
    }
)
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5


def _safe_identifier(value: str) -> str:
    normalized = value.strip()
    if _SECRET_VALUE_RE.search(normalized):
        return "[redacted]"
    if not _IDENTIFIER_RE.fullmatch(normalized):
        return "[invalid]"
    return normalized


def _coerce_field(name: str, value: Any) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if name == "modules_changed" and isinstance(value, (list, tuple, set)):
        return [
            _safe_identifier(str(item))
            for item in list(value)[:20]
        ]
    if isinstance(value, str):
        if name in _IDENTIFIER_FIELDS:
            return _safe_identifier(value)
        normalized = " ".join(value.split())[:160]
        return "[redacted]" if _SECRET_VALUE_RE.search(normalized) else normalized
    return "[unsupported]"


class AuditLogger:
    """Append allowlisted operational events to one private local file."""

    def __init__(
        self,
        path: str | Path,
        *,
        now: Callable[[], datetime] | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        backup_count: int = DEFAULT_BACKUP_COUNT,
    ) -> None:
        if max_bytes < 1024 or backup_count < 1 or backup_count > 20:
            raise ValueError("invalid audit rotation settings")
        self.path = Path(path)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self._lock = threading.Lock()

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        if not self.path.exists():
            return
        file_stat = self.path.lstat()
        if (
            not stat.S_ISREG(file_stat.st_mode)
            or stat.S_IMODE(file_stat.st_mode) != 0o600
        ):
            raise OSError("audit log is unsafe")
        if file_stat.st_size + incoming_bytes <= self.max_bytes:
            return
        oldest = self.path.with_name(f"{self.path.name}.{self.backup_count}")
        if oldest.exists() or oldest.is_symlink():
            if oldest.is_symlink() or not oldest.is_file():
                raise OSError("audit rotation target is unsafe")
            oldest.unlink()
        for index in range(self.backup_count - 1, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{index}")
            if not source.exists() and not source.is_symlink():
                continue
            if source.is_symlink() or not source.is_file():
                raise OSError("audit rotation source is unsafe")
            os.replace(
                source,
                self.path.with_name(f"{self.path.name}.{index + 1}"),
            )
        os.replace(self.path, self.path.with_name(f"{self.path.name}.1"))

    def emit(self, source: str, event: str, **fields: Any) -> bool:
        if not _EVENT_RE.fullmatch(source):
            raise ValueError("audit source must be a lowercase identifier")
        if not _EVENT_RE.fullmatch(event):
            raise ValueError("audit event must be a lowercase identifier")

        timestamp = self._now()
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        payload: dict[str, Any] = {
            "ts": timestamp.astimezone(timezone.utc).isoformat(),
            "source": source,
            "event": event,
        }
        for name, value in fields.items():
            if name in ALLOWED_FIELDS:
                payload[name] = _coerce_field(name, value)

        encoded = (
            json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        try:
            if not self.path.is_absolute() or self.path.is_symlink():
                return False
            ensure_private_directories((self.path.parent,))
            with self._lock:
                self._rotate_if_needed(len(encoded))
                flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                descriptor = os.open(self.path, flags, 0o600)
                try:
                    file_stat = os.fstat(descriptor)
                    if not stat.S_ISREG(file_stat.st_mode):
                        return False
                    os.fchmod(descriptor, 0o600)
                    written = os.write(descriptor, encoded)
                finally:
                    os.close(descriptor)
            return written == len(encoded)
        except (OSError, PathConfigurationError):
            return False


def emit_audit_event(
    path: str | Path,
    source: str,
    event: str,
    **fields: Any,
) -> bool:
    """Convenience wrapper for best-effort one-shot audit events."""

    return AuditLogger(path).emit(source, event, **fields)
