from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mundsen_agent.paths import MundsenAgentPaths
from mundsen_agent.release import (
    ReleaseError,
    active_version,
    install_release,
    rollback_release,
    verify_installed_release,
)


class TestReleaseManagement(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        self.paths = MundsenAgentPaths.from_root(root / "Mundsen Agent")
        self.artifacts = root / "artifacts"
        self.artifacts.mkdir()

    def make_release(self, version: str) -> Path:
        archive_name = f"mundsen-agent-{version}.tar.gz"
        archive = self.artifacts / archive_name
        content = f'__version__ = "{version}"\n'.encode()
        with tarfile.open(archive, "w:gz") as bundle:
            member = tarfile.TarInfo(
                f"mundsen-agent-{version}/src/mundsen_agent/__init__.py"
            )
            member.size = len(content)
            member.mode = 0o644
            bundle.addfile(member, io.BytesIO(content))
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        manifest = self.artifacts / f"manifest-{version}.json"
        manifest.write_text(
            json.dumps(
                {
                    "manifest_version": 1,
                    "version": version,
                    "minimum_instance_schema": 1,
                    "maximum_instance_schema": 1,
                    "files": [{"path": archive_name, "sha256": digest}],
                    "rollback_supported": True,
                }
            ),
            encoding="utf-8",
        )
        return manifest

    def test_verified_install_and_rollback_are_atomic(self) -> None:
        install_release(self.paths, self.make_release("0.1.0"))
        install_release(self.paths, self.make_release("0.1.1"))

        self.assertEqual(active_version(self.paths), "0.1.1")
        self.assertEqual(rollback_release(self.paths), "0.1.0")
        self.assertEqual(active_version(self.paths), "0.1.0")

    def test_checksum_mismatch_is_rejected(self) -> None:
        manifest = self.make_release("0.1.0")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["files"][0]["sha256"] = "0" * 64
        manifest.write_text(json.dumps(data), encoding="utf-8")

        with self.assertRaises(ReleaseError):
            install_release(self.paths, manifest)
        self.assertEqual(active_version(self.paths), "")

    def test_symlinked_manifest_and_archive_are_rejected(self) -> None:
        manifest = self.make_release("0.1.0")
        manifest_link = self.artifacts / "manifest-link.json"
        manifest_link.symlink_to(manifest)
        with self.assertRaises(ReleaseError):
            install_release(self.paths, manifest_link)

        archive = self.artifacts / "mundsen-agent-0.1.0.tar.gz"
        archive_target = self.artifacts / "archive-target.tar.gz"
        archive.rename(archive_target)
        archive.symlink_to(archive_target)
        with self.assertRaises(ReleaseError):
            install_release(self.paths, manifest)

    def test_archive_traversal_is_rejected(self) -> None:
        archive = self.artifacts / "mundsen-agent-0.1.0.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            member = tarfile.TarInfo("../outside")
            member.size = 1
            bundle.addfile(member, io.BytesIO(b"x"))
        manifest = self.artifacts / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "manifest_version": 1,
                    "version": "0.1.0",
                    "minimum_instance_schema": 1,
                    "maximum_instance_schema": 1,
                    "files": [
                        {
                            "path": archive.name,
                            "sha256": hashlib.sha256(
                                archive.read_bytes()
                            ).hexdigest(),
                        }
                    ],
                    "rollback_supported": True,
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaises(ReleaseError):
            install_release(self.paths, manifest)

    def test_oversized_archive_member_is_rejected(self) -> None:
        archive = self.artifacts / "mundsen-agent-0.1.0.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            member = tarfile.TarInfo(
                "mundsen-agent-0.1.0/src/mundsen_agent/__init__.py"
            )
            member.size = 2
            bundle.addfile(member, io.BytesIO(b"xx"))
        manifest = self.artifacts / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "manifest_version": 1,
                    "version": "0.1.0",
                    "minimum_instance_schema": 1,
                    "maximum_instance_schema": 1,
                    "files": [
                        {
                            "path": archive.name,
                            "sha256": hashlib.sha256(
                                archive.read_bytes()
                            ).hexdigest(),
                        }
                    ],
                    "rollback_supported": True,
                }
            ),
            encoding="utf-8",
        )

        with patch("mundsen_agent.release.MAX_ARCHIVE_FILE_BYTES", 1):
            with self.assertRaises(ReleaseError):
                install_release(self.paths, manifest)

    def test_invalid_manifest_types_are_rejected(self) -> None:
        manifest = self.make_release("0.1.0")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["minimum_instance_schema"] = "1"
        manifest.write_text(json.dumps(data), encoding="utf-8")

        with self.assertRaises(ReleaseError):
            install_release(self.paths, manifest)

    def test_installed_release_integrity_detects_file_changes(self) -> None:
        install_release(self.paths, self.make_release("0.1.0"))
        release = self.paths.releases_dir / "0.1.0"
        source = release / "src" / "mundsen_agent" / "__init__.py"
        source.write_text('__version__ = "tampered"\n', encoding="utf-8")

        with self.assertRaisesRegex(ReleaseError, "integrity"):
            verify_installed_release(release)

    def test_manifest_with_migrations_is_rejected_before_activation(self) -> None:
        manifest = self.make_release("0.1.0")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["migrations"] = [{"name": "rewrite-profile"}]
        manifest.write_text(json.dumps(data), encoding="utf-8")

        with self.assertRaisesRegex(ReleaseError, "does not support"):
            install_release(self.paths, manifest)
        self.assertEqual(active_version(self.paths), "")

    def test_incompatible_rollback_target_is_not_activated(self) -> None:
        install_release(self.paths, self.make_release("0.1.0"))
        install_release(self.paths, self.make_release("0.2.0"))
        metadata = (
            self.paths.releases_dir / "0.1.0" / ".mundsen-agent-release.json"
        )
        data = json.loads(metadata.read_text(encoding="utf-8"))
        data["minimum_instance_schema"] = 2
        data["maximum_instance_schema"] = 2
        metadata.write_text(json.dumps(data), encoding="utf-8")

        with self.assertRaisesRegex(ReleaseError, "incompatible"):
            rollback_release(self.paths, "0.1.0")
        self.assertEqual(active_version(self.paths), "0.2.0")


if __name__ == "__main__":
    unittest.main()
