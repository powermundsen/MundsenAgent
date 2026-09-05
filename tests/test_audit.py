from __future__ import annotations

import json
import math
import os
import stat
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from mundsen_agent.audit import AuditLogger


class TestAuditLogger(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.path = self.root / "state" / "logs" / "audit.jsonl"
        self.now = lambda: datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    def read_events(self) -> list[dict]:
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
        ]

    def test_writes_private_allowlisted_event(self) -> None:
        logger = AuditLogger(self.path, now=self.now)

        result = logger.emit(
            "router",
            "route_success",
            agent="claude",
            model="example-model",
            success=True,
            duration_ms=125,
            message_text="private conversation",
            chat_id=100200300,
            raw_error="private failure detail",
        )

        self.assertTrue(result)
        event = self.read_events()[0]
        self.assertEqual(event["ts"], "2026-01-02T03:04:05+00:00")
        self.assertEqual(event["source"], "router")
        self.assertEqual(event["event"], "route_success")
        self.assertEqual(event["model"], "example-model")
        self.assertNotIn("message_text", event)
        self.assertNotIn("chat_id", event)
        self.assertNotIn("raw_error", event)
        self.assertNotIn("host", event)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)

    def test_repeated_events_are_valid_json_lines(self) -> None:
        logger = AuditLogger(self.path, now=self.now)

        self.assertTrue(logger.emit("bridge", "start", version="0.0.0"))
        self.assertTrue(
            logger.emit("bridge", "stop", exit_code=0, duration_ms=math.nan)
        )

        events = self.read_events()
        self.assertEqual(len(events), 2)
        self.assertIsNone(events[1]["duration_ms"])

    def test_concurrent_events_remain_separate_json_lines(self) -> None:
        logger = AuditLogger(self.path, now=self.now)
        threads = [
            threading.Thread(
                target=lambda worker=index: [
                    logger.emit(
                        "router",
                        "attempt",
                        attempt=worker * 20 + item,
                    )
                    for item in range(20)
                ]
            )
            for index in range(5)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        events = self.read_events()
        self.assertEqual(len(events), 100)
        self.assertEqual(
            {event["attempt"] for event in events},
            set(range(100)),
        )

    def test_secret_shaped_allowlisted_value_is_redacted(self) -> None:
        logger = AuditLogger(self.path, now=self.now)
        fake_secret = "Bearer " + ("A" * 20)

        self.assertTrue(
            logger.emit("router", "failure", model=fake_secret)
        )

        self.assertEqual(self.read_events()[0]["model"], "[redacted]")

    def test_invalid_event_identifiers_are_rejected(self) -> None:
        logger = AuditLogger(self.path, now=self.now)
        with self.assertRaises(ValueError):
            logger.emit("Router With Spaces", "start")
        with self.assertRaises(ValueError):
            logger.emit("router", "../escape")

    def test_rotation_is_bounded_and_private(self) -> None:
        logger = AuditLogger(self.path, now=self.now, max_bytes=1024, backup_count=2)
        for _ in range(80):
            self.assertTrue(
                logger.emit("router", "completed", model="synthetic-model")
            )

        self.assertTrue(self.path.exists())
        self.assertTrue(self.path.with_name(f"{self.path.name}.1").exists())
        self.assertFalse(self.path.with_name(f"{self.path.name}.3").exists())
        for path in self.path.parent.glob("audit.jsonl*"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_symlink_log_is_rejected_without_fallback(self) -> None:
        self.path.parent.mkdir(parents=True)
        target = self.root / "outside.jsonl"
        target.write_text("", encoding="utf-8")
        self.path.symlink_to(target)
        logger = AuditLogger(self.path, now=self.now)

        self.assertFalse(logger.emit("router", "start", success=True))
        self.assertEqual(target.read_text(encoding="utf-8"), "")

    def test_relative_log_path_is_rejected(self) -> None:
        logger = AuditLogger(Path("relative-audit.jsonl"), now=self.now)
        self.assertFalse(logger.emit("router", "start"))
        self.assertFalse(Path("relative-audit.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
