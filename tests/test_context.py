from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mundsen_agent.context import ContextError, build_instance_context
from mundsen_agent.paths import MundsenAgentPaths


class TestInstanceContext(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.paths = MundsenAgentPaths.from_root(Path(self.tempdir.name) / "Mundsen Agent")
        (self.paths.instance_dir / "memory").mkdir(parents=True)

    def test_loads_profile_threads_reminders_and_today(self) -> None:
        (self.paths.instance_dir / "MEMORY.md").write_text(
            "Long memory", encoding="utf-8"
        )
        (self.paths.instance_dir / "memory" / "user_profile.md").write_text(
            "Likes short replies", encoding="utf-8"
        )
        (self.paths.instance_dir / "memory" / "open-threads.md").write_text(
            "Follow up project", encoding="utf-8"
        )
        (self.paths.instance_dir / "reminders.md").write_text(
            "2026-07-30 | 2 | Pack bag", encoding="utf-8"
        )
        (self.paths.instance_dir / "memory" / "2026-07-28.md").write_text(
            "Worked on setup", encoding="utf-8"
        )
        for path in self.paths.instance_dir.rglob("*.md"):
            os.chmod(path, 0o600)

        context = build_instance_context(
            self.paths,
            timezone="Europe/Oslo",
            now=lambda: datetime(2026, 7, 28, 10, tzinfo=ZoneInfo("Europe/Oslo")),
        )

        self.assertIn("Likes short replies", context)
        self.assertIn("Follow up project", context)
        self.assertIn("Pack bag", context)
        self.assertIn("Worked on setup", context)
        self.assertIn("Local date: 2026-07-28", context)

    def test_symlinked_context_is_rejected(self) -> None:
        target = Path(self.tempdir.name) / "outside"
        target.write_text("outside", encoding="utf-8")
        link = self.paths.instance_dir / "MEMORY.md"
        link.symlink_to(target)

        with self.assertRaises(ContextError):
            build_instance_context(self.paths, timezone="UTC")

    def test_empty_instance_has_no_context(self) -> None:
        self.assertEqual(
            build_instance_context(self.paths, timezone="UTC"),
            "",
        )

    def test_world_readable_context_is_rejected(self) -> None:
        profile = self.paths.instance_dir / "memory" / "user_profile.md"
        profile.write_text("private", encoding="utf-8")
        os.chmod(profile, 0o644)

        with self.assertRaises(ContextError):
            build_instance_context(self.paths, timezone="UTC")


if __name__ == "__main__":
    unittest.main()
