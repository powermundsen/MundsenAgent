from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from mundsen_agent.audit import AuditLogger
from mundsen_agent.diagnostics import (
    DiagnosticsError,
    create_support_bundle,
    read_audit_events,
)
from mundsen_agent.paths import MundsenAgentPaths


class TestDiagnostics(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.paths = MundsenAgentPaths.from_root(Path(self.tempdir.name) / "Mundsen Agent")

    def test_log_reader_filters_safe_metadata(self) -> None:
        logger = AuditLogger(self.paths.audit_log_file)
        logger.emit("router", "completed", agent="codex", success=True)
        logger.emit("telegram", "sent", success=True)

        events = read_audit_events(
            self.paths.audit_log_file, source="router", limit=10
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "completed")
        self.assertNotIn("message", events[0])

    def test_unsafe_log_is_rejected(self) -> None:
        self.paths.logs_dir.mkdir(parents=True)
        self.paths.audit_log_file.write_text("{}\n", encoding="utf-8")
        os.chmod(self.paths.audit_log_file, 0o644)
        with self.assertRaisesRegex(DiagnosticsError, "unsafe"):
            read_audit_events(self.paths.audit_log_file)

    def test_log_reader_searches_rotated_files(self) -> None:
        logger = AuditLogger(
            self.paths.audit_log_file,
            max_bytes=1024,
            backup_count=2,
        )
        logger.emit("router", "first", success=True)
        for _ in range(12):
            logger.emit("telegram", "sent", model="synthetic-model", success=True)

        events = read_audit_events(
            self.paths.audit_log_file,
            limit=500,
            source="router",
        )

        self.assertEqual([item["event"] for item in events], ["first"])

    def test_support_bundle_contains_no_configuration_or_health_data(self) -> None:
        logger = AuditLogger(self.paths.audit_log_file)
        logger.emit("router", "completed", success=True)
        output = Path(self.tempdir.name) / "support.zip"
        parent_mode = stat.S_IMODE(output.parent.stat().st_mode)

        result = create_support_bundle(self.paths, output.resolve())

        self.assertEqual(stat.S_IMODE(result.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(output.parent.stat().st_mode), parent_mode)
        with zipfile.ZipFile(result) as archive:
            self.assertEqual(archive.namelist(), ["diagnostics.json"])
            payload = json.loads(archive.read("diagnostics.json"))
        self.assertIn("health", payload)
        self.assertIn("audit_events", payload)
        self.assertNotIn("configuration", payload)
        self.assertNotIn("training", payload)


if __name__ == "__main__":
    unittest.main()
