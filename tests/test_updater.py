from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import urllib.request
from argparse import Namespace
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mundsen_agent import cli
from mundsen_agent.paths import MundsenAgentPaths
from mundsen_agent.updater import (
    GitHubRelease,
    UpdateError,
    download_release,
    fetch_latest_release,
    fetch_release_manifest,
    version_is_newer,
    UpdateNotificationEngine,
    _SafeRedirectHandler,
)


def metadata(*, repository: str = "example/mundsen-agent", version: str = "0.3.0") -> bytes:
    return json.dumps(
        {
            "tag_name": f"v{version}",
            "name": f"Mundsen Agent {version}",
            "body": "Changes",
            "html_url": f"https://github.com/{repository}/releases/tag/v{version}",
            "published_at": "2026-08-18T00:00:00Z",
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": "release-manifest.json",
                    "url": f"https://api.github.com/repos/{repository}/releases/assets/1",
                },
                {
                    "name": f"mundsen-agent-{version}.tar.gz",
                    "url": f"https://api.github.com/repos/{repository}/releases/assets/2",
                },
            ],
        }
    ).encode()


def manifest_payload(*, version: str = "0.3.0", archive: bytes = b"archive") -> bytes:
    return json.dumps(
        {
            "manifest_version": 1,
            "version": version,
            "minimum_instance_schema": 1,
            "maximum_instance_schema": 1,
            "files": [
                {
                    "path": f"mundsen-agent-{version}.tar.gz",
                    "sha256": hashlib.sha256(archive).hexdigest(),
                }
            ],
            "modules_changed": ["updater", "skills"],
            "personal_data_impact": "none",
            "migrations": [],
            "rollback_supported": True,
        }
    ).encode()


class TestUpdater(unittest.TestCase):
    def test_release_redirects_strip_credentials_and_reject_unknown_hosts(self) -> None:
        request = urllib.request.Request(
            "https://api.github.com/repos/example/mundsen-agent/releases/assets/1",
            headers={"Authorization": "Bearer synthetic"},
        )
        redirected = _SafeRedirectHandler().redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://release-assets.githubusercontent.com/example/archive",
        )
        self.assertIsNotNone(redirected)
        self.assertNotIn("Authorization", redirected.headers)
        with self.assertRaisesRegex(UpdateError, "untrusted"):
            _SafeRedirectHandler().redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "https://example.net/archive",
            )

    def test_weekly_update_notification_is_metadata_only_and_idempotent(self) -> None:
        release = fetch_latest_release(
            "example/mundsen-agent",
            fetch_json=lambda url, token: metadata(),
        )
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state" / "updates.json"
            engine = UpdateNotificationEngine(
                state_file=state,
                repository="example/mundsen-agent",
                current_version="0.2.0",
                now=lambda: datetime(2026, 8, 18, tzinfo=timezone.utc),
                fetch_release=lambda repository, token="": release,
                fetch_manifest=lambda release, token="": fetch_release_manifest(
                    release,
                    token=token,
                    downloader=lambda url, target, token, limit: target.write_bytes(
                        manifest_payload()
                    ),
                ),
            )

            pending = engine.pending()
            self.assertEqual([item.key for item in pending], ["update:0.3.0"])
            engine.mark_sent(pending[0].key)
            self.assertEqual(engine.pending(), [])

            six_days_later = UpdateNotificationEngine(
                state_file=state,
                repository="example/mundsen-agent",
                current_version="0.2.0",
                now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
                fetch_release=lambda repository, token="": release,
                fetch_manifest=lambda release, token="": self.fail(
                    "weekly check ran too early"
                ),
            )
            self.assertEqual(six_days_later.pending(), [])

            next_week = UpdateNotificationEngine(
                state_file=state,
                repository="example/mundsen-agent",
                current_version="0.2.0",
                now=lambda: datetime(2026, 8, 25, tzinfo=timezone.utc),
                fetch_release=lambda repository, token="": release,
                fetch_manifest=lambda release, token="": fetch_release_manifest(
                    release,
                    token=token,
                    downloader=lambda url, target, token, limit: target.write_bytes(
                        manifest_payload()
                    ),
                ),
            )
            self.assertEqual(next_week.pending(), [])

    def test_latest_release_requires_exact_assets_and_trusted_urls(self) -> None:
        release = fetch_latest_release(
            "example/mundsen-agent",
            fetch_json=lambda url, token: metadata(),
        )

        self.assertEqual(release.version, "0.3.0")
        self.assertEqual(release.archive_name, "mundsen-agent-0.3.0.tar.gz")

        unsafe = json.loads(metadata())
        unsafe["assets"][0]["url"] = "https://example.net/release-manifest.json"
        with self.assertRaisesRegex(UpdateError, "untrusted"):
            fetch_latest_release(
                "example/mundsen-agent",
                fetch_json=lambda url, token: json.dumps(unsafe).encode(),
            )

    def test_latest_release_rejects_missing_duplicate_and_prerelease_assets(self) -> None:
        missing = json.loads(metadata())
        missing["assets"] = missing["assets"][:1]
        with self.assertRaisesRegex(UpdateError, "missing"):
            fetch_latest_release(
                "example/mundsen-agent",
                fetch_json=lambda url, token: json.dumps(missing).encode(),
            )

        duplicate = json.loads(metadata())
        duplicate["assets"].append(duplicate["assets"][0])
        with self.assertRaisesRegex(UpdateError, "duplicate"):
            fetch_latest_release(
                "example/mundsen-agent",
                fetch_json=lambda url, token: json.dumps(duplicate).encode(),
            )

        prerelease = json.loads(metadata())
        prerelease["prerelease"] = True
        with self.assertRaisesRegex(UpdateError, "stable"):
            fetch_latest_release(
                "example/mundsen-agent",
                fetch_json=lambda url, token: json.dumps(prerelease).encode(),
            )

    def test_version_comparison_is_strict(self) -> None:
        self.assertTrue(version_is_newer("0.3.0", "0.2.9"))
        self.assertFalse(version_is_newer("0.3.0", "0.3.0"))
        self.assertFalse(version_is_newer("0.2.9", "0.3.0"))
        with self.assertRaises(UpdateError):
            version_is_newer("latest", "0.3.0")

    def test_download_validates_manifest_against_release(self) -> None:
        release = fetch_latest_release(
            "example/mundsen-agent",
            fetch_json=lambda url, token: metadata(),
        )
        archive = b"archive"
        def downloader(url: str, target: Path, token: str, limit: int) -> None:
            del token, limit
            payload = manifest_payload(archive=archive) if url.endswith("/1") else archive
            target.write_bytes(payload)

        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "downloads" / release.version
            path = download_release(release, destination, downloader=downloader)
            self.assertEqual(path, destination / "release-manifest.json")
            self.assertEqual((destination / release.archive_name).read_bytes(), archive)

            mismatched = replace(release, version="0.4.0")
            with self.assertRaisesRegex(UpdateError, "do not match"):
                download_release(mismatched, Path(temp) / "mismatch", downloader=downloader)

            def corrupt_downloader(
                url: str, target: Path, token: str, limit: int
            ) -> None:
                del token, limit
                payload = manifest_payload(archive=archive) if url.endswith("/1") else b"bad"
                target.write_bytes(payload)

            with self.assertRaisesRegex(UpdateError, "checksum"):
                download_release(
                    release,
                    Path(temp) / "corrupt",
                    downloader=corrupt_downloader,
                )

    @patch("mundsen_agent.cli.load_runtime_settings")
    @patch("mundsen_agent.cli.active_version", return_value="0.2.0")
    @patch("mundsen_agent.cli.fetch_latest_release")
    @patch("mundsen_agent.cli.fetch_release_manifest")
    def test_cli_check_does_not_download_or_install(
        self,
        fetch_manifest,
        fetch_latest,
        active,
        load_runtime,
    ) -> None:
        del active
        load_runtime.return_value = SimpleNamespace(get=lambda name: "")
        fetch_latest.return_value = fetch_latest_release(
            "example/mundsen-agent",
            fetch_json=lambda url, token: metadata(),
        )
        fetch_manifest.return_value = fetch_release_manifest(
            fetch_latest.return_value,
            downloader=lambda url, target, token, limit: target.write_bytes(
                manifest_payload()
            ),
        )
        with tempfile.TemporaryDirectory() as temp, patch(
            "mundsen_agent.cli.download_release"
        ) as download, patch("mundsen_agent.cli._cmd_upgrade") as upgrade:
            result = cli._cmd_update(
                MundsenAgentPaths.from_root(Path(temp) / "Mundsen Agent"),
                Namespace(
                    action="check",
                    repository="example/mundsen-agent",
                    json=False,
                    yes=False,
                ),
            )

        self.assertEqual(result, 0)
        download.assert_not_called()
        upgrade.assert_not_called()

    @patch("mundsen_agent.cli.load_runtime_settings")
    @patch("mundsen_agent.cli.active_version", return_value="0.2.0")
    @patch("mundsen_agent.cli.fetch_latest_release")
    @patch("mundsen_agent.cli.fetch_release_manifest")
    @patch("mundsen_agent.cli.download_release")
    @patch("mundsen_agent.cli._cmd_upgrade", return_value=0)
    def test_cli_install_downloads_then_uses_guarded_upgrade(
        self,
        upgrade,
        download,
        fetch_manifest,
        fetch_latest,
        active,
        load_runtime,
    ) -> None:
        del active
        load_runtime.return_value = SimpleNamespace(get=lambda name: "")
        fetch_latest.return_value = fetch_latest_release(
            "example/mundsen-agent",
            fetch_json=lambda url, token: metadata(),
        )
        fetch_manifest.return_value = fetch_release_manifest(
            fetch_latest.return_value,
            downloader=lambda url, target, token, limit: target.write_bytes(
                manifest_payload()
            ),
        )
        with tempfile.TemporaryDirectory() as temp:
            paths = MundsenAgentPaths.from_root(Path(temp) / "Mundsen Agent")
            manifest = paths.cache_dir / "updates" / "0.3.0" / "release-manifest.json"
            download.return_value = manifest
            result = cli._cmd_update(
                paths,
                Namespace(
                    action="install",
                    repository="example/mundsen-agent",
                    json=False,
                    yes=True,
                ),
            )

        self.assertEqual(result, 0)
        download.assert_called_once()
        self.assertEqual(upgrade.call_args.args[0], paths)
        self.assertEqual(upgrade.call_args.args[1].manifest, str(manifest))
        self.assertTrue(upgrade.call_args.args[1].yes)


if __name__ == "__main__":
    unittest.main()
