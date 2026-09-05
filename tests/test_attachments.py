from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from mundsen_agent.bridge.attachments import AttachmentError, AttachmentProcessor
from mundsen_agent.paths import MundsenAgentPaths


class FakeFileClient:
    def get_file(self, file_id: str) -> tuple[str, int]:
        self.file_id = file_id
        return "voice/file.ogg", 5

    def download_file(self, file_path: str, *, maximum_bytes: int) -> bytes:
        self.file_path = file_path
        self.maximum_bytes = maximum_bytes
        return b"audio"


class InvalidFileClient(FakeFileClient):
    def get_file(self, file_id: str) -> tuple[str, int]:
        del file_id
        raise ValueError("raw validation detail")


class TestAttachmentProcessor(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.paths = MundsenAgentPaths.from_root(Path(self.tempdir.name) / "Mundsen Agent")
        self.client = FakeFileClient()

    def test_document_is_private_and_referenced_without_original_name(self) -> None:
        processor = AttachmentProcessor(
            client=self.client,  # type: ignore[arg-type]
            paths=self.paths,
        )
        content = processor.process(
            42,
            {
                "caption": "Please inspect",
                "document": {
                    "file_id": "example_file_id_123",
                    "file_name": "private report.txt",
                },
            },
        )

        path = self.paths.attachments_dir / "job-42" / "input.txt"
        self.assertTrue(path.is_file())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertIn(str(path), content.text)
        self.assertNotIn("private report", content.text)
        processor.cleanup_job(42)
        self.assertFalse(path.parent.exists())

    def test_voice_transcription_uses_local_executable(self) -> None:
        command = Path(self.tempdir.name) / "transcribe"
        command.write_text("#!/bin/sh\nprintf 'local words'\n", encoding="utf-8")
        os.chmod(command, 0o700)
        processor = AttachmentProcessor(
            client=self.client,  # type: ignore[arg-type]
            paths=self.paths,
            transcribe_command=str(command),
        )

        content = processor.process(
            43,
            {"voice": {"file_id": "example_file_id_456"}},
        )

        self.assertIn("local words", content.text)
        self.assertNotIn("path:", content.text)

    def test_invalid_telegram_metadata_is_sanitized(self) -> None:
        processor = AttachmentProcessor(
            client=InvalidFileClient(),  # type: ignore[arg-type]
            paths=self.paths,
        )

        with self.assertRaises(AttachmentError) as raised:
            processor.process(
                44,
                {"document": {"file_id": "not-valid"}},
            )

        self.assertEqual(raised.exception.category, "invalid_file_metadata")


if __name__ == "__main__":
    unittest.main()
