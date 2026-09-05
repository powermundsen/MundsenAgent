from __future__ import annotations

import os
import stat
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mundsen_agent.reminders import ReminderEngine, ReminderError, parse_reminders


class TestReminderEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        self.reminders = root / "instance" / "reminders.md"
        self.reminders.parent.mkdir()
        self.state = root / "state" / "reminders.json"
        self.now = datetime(2026, 7, 28, 9, tzinfo=ZoneInfo("Europe/Oslo"))

    def engine(self) -> ReminderEngine:
        return ReminderEngine(
            reminders_file=self.reminders,
            state_file=self.state,
            timezone="Europe/Oslo",
            language="nb",
            now=lambda: self.now,
        )

    def test_warning_and_due_delivery_are_each_idempotent(self) -> None:
        self.reminders.write_text(
            "2026-07-30 | 2 | Pakk kofferten\n",
            encoding="utf-8",
        )
        os.chmod(self.reminders, 0o600)
        engine = self.engine()

        warning = engine.pending()
        self.assertEqual(len(warning), 1)
        self.assertIn("om 2 dager", warning[0].text)
        engine.mark_sent(warning[0].key)
        self.assertEqual(engine.pending(), [])

        self.now = datetime(2026, 7, 30, 9, tzinfo=ZoneInfo("Europe/Oslo"))
        due = engine.pending()
        self.assertEqual(len(due), 1)
        self.assertIn("i dag", due[0].text)
        engine.mark_sent(due[0].key)
        self.assertEqual(engine.pending(), [])
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o600)

    def test_missed_due_date_is_reported_once_as_overdue(self) -> None:
        self.reminders.write_text(
            "2026-07-27 | 0 | Ring tannlegen\n",
            encoding="utf-8",
        )
        os.chmod(self.reminders, 0o600)
        pending = self.engine().pending()
        self.assertEqual(len(pending), 1)
        self.assertIn("forfalt", pending[0].text)

    def test_invalid_line_and_symlink_are_rejected(self) -> None:
        self.reminders.write_text("not a reminder\n", encoding="utf-8")
        os.chmod(self.reminders, 0o600)
        with self.assertRaises(ReminderError):
            parse_reminders(self.reminders)

        target = Path(self.tempdir.name) / "real.md"
        target.write_text("2026-07-30 | 0 | Test\n", encoding="utf-8")
        os.chmod(target, 0o600)
        self.reminders.unlink()
        self.reminders.symlink_to(target)
        with self.assertRaises(ReminderError):
            parse_reminders(self.reminders)

    def test_invalid_state_permissions_are_rejected(self) -> None:
        self.reminders.write_text("2026-07-30 | 0 | Test\n", encoding="utf-8")
        os.chmod(self.reminders, 0o600)
        self.state.parent.mkdir()
        self.state.write_text('{"sent":{}}\n', encoding="utf-8")
        os.chmod(self.state, 0o644)
        with self.assertRaises(ReminderError):
            self.engine().pending()

    def test_world_readable_reminders_are_rejected(self) -> None:
        self.reminders.write_text("2026-07-30 | 0 | Test\n", encoding="utf-8")
        os.chmod(self.reminders, 0o644)

        with self.assertRaises(ReminderError):
            self.engine().pending()


if __name__ == "__main__":
    unittest.main()
