"""Local date-based reminders with private, idempotent delivery state."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from mundsen_agent.paths import ensure_private_directories

MAX_REMINDER_BYTES = 128 * 1024
MAX_REMINDERS = 512


class ReminderError(ValueError):
    """Raised when reminder input or state is unsafe."""


@dataclass(frozen=True, slots=True)
class Reminder:
    due: date
    warn_days: int
    text: str
    identifier: str


@dataclass(frozen=True, slots=True)
class ReminderNotification:
    key: str
    text: str


def parse_reminders(path: str | Path) -> list[Reminder]:
    target = Path(path)
    if target.is_symlink():
        raise ReminderError("reminders file must not be a symlink")
    if not target.exists():
        return []
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(target, flags)
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ReminderError("reminders path is not a regular file")
        if stat.S_IMODE(file_stat.st_mode) & 0o077:
            raise ReminderError("reminders file must be private")
        if file_stat.st_size > MAX_REMINDER_BYTES:
            raise ReminderError("reminders file is too large")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            text = handle.read(MAX_REMINDER_BYTES + 1)
        if len(text.encode("utf-8")) > MAX_REMINDER_BYTES:
            raise ReminderError("reminders file is too large")
        lines = text.splitlines()
    except UnicodeDecodeError as exc:
        raise ReminderError("reminders file is not valid UTF-8") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    reminders: list[Reminder] = []
    for line_number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|", 2)]
        if len(parts) != 3:
            raise ReminderError(
                f"reminders line {line_number} must use date | warn_days | text"
            )
        try:
            due = date.fromisoformat(parts[0])
            warn_days = int(parts[1])
        except ValueError as exc:
            raise ReminderError(
                f"reminders line {line_number} has an invalid date or warning"
            ) from exc
        text = parts[2]
        if warn_days < 0 or warn_days > 365 or not text or len(text) > 500:
            raise ReminderError(f"reminders line {line_number} is invalid")
        identifier = hashlib.sha256(
            f"{due.isoformat()}|{warn_days}|{text}".encode("utf-8")
        ).hexdigest()[:24]
        reminders.append(Reminder(due, warn_days, text, identifier))
        if len(reminders) > MAX_REMINDERS:
            raise ReminderError("too many reminders")
    return reminders


class ReminderEngine:
    def __init__(
        self,
        *,
        reminders_file: str | Path,
        state_file: str | Path,
        timezone: str,
        language: str = "nb",
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.reminders_file = Path(reminders_file)
        self.state_file = Path(state_file)
        self.timezone = ZoneInfo(timezone)
        self.language = language
        self.now = now or (lambda: datetime.now(self.timezone))

    def pending(self) -> list[ReminderNotification]:
        today = self.now().astimezone(self.timezone).date()
        sent = self._load_state()
        notifications: list[ReminderNotification] = []
        for reminder in parse_reminders(self.reminders_file):
            phase = self._phase(reminder, today, sent.get(reminder.identifier, []))
            if not phase:
                continue
            key = f"reminder:{reminder.identifier}:{phase}"
            notifications.append(
                ReminderNotification(
                    key=key,
                    text=self._render(reminder, today),
                )
            )
        return notifications

    def mark_sent(self, notification_key: str) -> None:
        parts = notification_key.split(":")
        if len(parts) != 3 or parts[0] != "reminder":
            raise ReminderError("invalid reminder notification key")
        identifier, phase = parts[1], parts[2]
        if (
            len(identifier) != 24
            or not all(character in "0123456789abcdef" for character in identifier)
            or phase not in {"warning", "due"}
        ):
            raise ReminderError("invalid reminder notification key")
        sent = self._load_state()
        phases = set(sent.get(identifier, []))
        phases.add(phase)
        sent[identifier] = sorted(phases)
        self._save_state(sent)

    def _phase(
        self,
        reminder: Reminder,
        today: date,
        sent_phases: list[str],
    ) -> str:
        sent = set(sent_phases)
        if today >= reminder.due and "due" not in sent:
            return "due"
        warning_date = reminder.due - timedelta(days=reminder.warn_days)
        if (
            reminder.warn_days > 0
            and warning_date <= today < reminder.due
            and "warning" not in sent
        ):
            return "warning"
        return ""

    def _render(self, reminder: Reminder, today: date) -> str:
        days = (reminder.due - today).days
        english = self.language.lower().startswith("en")
        if days > 0:
            suffix = (
                f"due in {days} day{'s' if days != 1 else ''}"
                if english
                else f"forfaller om {days} dag{'er' if days != 1 else ''}"
            )
        elif days == 0:
            suffix = "due today" if english else "forfaller i dag"
        else:
            overdue = abs(days)
            suffix = (
                f"overdue by {overdue} day{'s' if overdue != 1 else ''}"
                if english
                else f"forfalt for {overdue} dag{'er' if overdue != 1 else ''} siden"
            )
        prefix = "Reminder" if english else "Påminnelse"
        return f"{prefix}: {reminder.text} ({suffix})"

    def _load_state(self) -> dict[str, list[str]]:
        if not self.state_file.exists():
            return {}
        if not self.state_file.is_absolute() or self.state_file.is_symlink():
            raise ReminderError("reminder state path is unsafe")
        file_stat = self.state_file.stat()
        if (
            not stat.S_ISREG(file_stat.st_mode)
            or stat.S_IMODE(file_stat.st_mode) != 0o600
            or file_stat.st_size > MAX_REMINDER_BYTES
        ):
            raise ReminderError("reminder state is unsafe")
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReminderError("reminder state is invalid") from exc
        if not isinstance(data, dict) or not isinstance(data.get("sent", {}), dict):
            raise ReminderError("reminder state is invalid")
        result: dict[str, list[str]] = {}
        for key, value in data.get("sent", {}).items():
            if (
                not isinstance(key, str)
                or len(key) != 24
                or not isinstance(value, list)
                or any(item not in {"warning", "due"} for item in value)
            ):
                raise ReminderError("reminder state is invalid")
            result[key] = list(value)
        return result

    def _save_state(self, sent: dict[str, list[str]]) -> None:
        if not self.state_file.is_absolute() or self.state_file.is_symlink():
            raise ReminderError("reminder state path is unsafe")
        ensure_private_directories((self.state_file.parent,))
        descriptor, temporary = tempfile.mkstemp(
            prefix=".reminder-state-",
            dir=self.state_file.parent,
        )
        temp_path = Path(temporary)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                json.dump({"sent": sent}, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.state_file)
            os.chmod(self.state_file, 0o600)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temp_path.unlink(missing_ok=True)
