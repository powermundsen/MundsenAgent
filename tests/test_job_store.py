from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from mundsen_agent.bridge.job_store import (
    JobStoreError,
    MessageJob,
    PersistentJobStore,
)
from mundsen_agent.router.models import AgentResponse


class TestPersistentJobStore(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.directory = Path(self.tempdir.name) / "queue"
        self.store = PersistentJobStore(self.directory)

    def test_inbox_response_progress_and_delete_roundtrip(self) -> None:
        job = MessageJob(update_id=42, chat_id=1001, text="Hei")
        self.assertTrue(self.store.put_if_absent(job))
        self.assertFalse(self.store.put_if_absent(job))

        saved = self.store.save_response(
            42,
            AgentResponse("Svar", "claude", True),
        )
        self.assertTrue(saved.response_ready)
        self.store.advance_chunk(42, 1)
        self.store.advance_artifact(42, 1)
        loaded = self.store.get(42)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.next_chunk, 1)
        self.assertEqual(loaded.next_artifact, 1)
        path = self.directory / "00000000000000000042.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

        self.store.delete(42)
        self.assertIsNone(self.store.get(42))

    def test_deferred_jobs_are_loaded_only_when_due(self) -> None:
        self.store.put_if_absent(MessageJob(42, 1001, "Hei"))
        deferred = self.store.defer(42, now=100)
        self.assertGreater(deferred.retry_at, 100)
        self.assertEqual(self.store.load_all(now=101), [])
        self.assertEqual(self.store.load_all(now=1000)[0].update_id, 42)

    def test_symlink_and_wide_permissions_are_rejected(self) -> None:
        self.directory.mkdir()
        target = Path(self.tempdir.name) / "target.json"
        target.write_text(
            '{"update_id":42,"chat_id":1001,"text":"x"}',
            encoding="utf-8",
        )
        os.chmod(target, 0o600)
        link = self.directory / "00000000000000000042.json"
        link.symlink_to(target)
        with self.assertRaises(JobStoreError):
            self.store.get(42)

        link.unlink()
        self.store.put_if_absent(MessageJob(42, 1001, "Hei"))
        os.chmod(link, 0o644)
        with self.assertRaises(JobStoreError):
            self.store.get(42)

    def test_clear_all_removes_due_and_deferred_jobs(self) -> None:
        self.store.put_if_absent(MessageJob(42, 1001, "Hei"))
        self.store.put_if_absent(MessageJob(43, 1001, "Senere"))
        self.store.defer(43, now=10_000)

        self.assertEqual(self.store.clear_all(), 2)
        self.assertEqual(self.store.load_all(now=100_000), [])

    def test_non_finite_retry_time_is_rejected(self) -> None:
        with self.assertRaises(JobStoreError):
            self.store.put_if_absent(
                MessageJob(42, 1001, "Hei", retry_at=float("nan"))
            )


if __name__ == "__main__":
    unittest.main()
