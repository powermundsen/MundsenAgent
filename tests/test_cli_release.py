from __future__ import annotations

import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

from mundsen_agent import cli
from mundsen_agent.paths import MundsenAgentPaths
from mundsen_agent.release import ReleaseError


class TestReleaseCommands(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.paths = MundsenAgentPaths.from_root(
            Path(self.tempdir.name) / "Mundsen Agent"
        )
        self.manifest = SimpleNamespace(
            version="0.3.0",
            modules_changed=("telegram", "router"),
            personal_data_impact="none",
            migrations=(),
        )

    @patch("mundsen_agent.cli.create_upgrade_backup")
    @patch("mundsen_agent.cli.protected_snapshot", side_effect=[{"a": "1"}, {"a": "1"}])
    @patch("mundsen_agent.cli.install_release", return_value="0.3.0")
    @patch("mundsen_agent.cli._require_healthy")
    @patch("mundsen_agent.cli._service_is_installed", return_value=False)
    @patch("mundsen_agent.cli.active_version", return_value="0.2.0")
    @patch("mundsen_agent.cli.load_manifest")
    def test_upgrade_checks_health_before_and_after_activation(
        self,
        load_manifest,
        active_version,
        service_is_installed,
        require_healthy,
        install_release,
        protected_snapshot,
        create_upgrade_backup,
    ) -> None:
        del active_version, service_is_installed, protected_snapshot
        load_manifest.return_value = self.manifest

        result = cli._cmd_upgrade(
            self.paths,
            Namespace(manifest="/tmp/release-manifest.json", yes=True),
        )

        self.assertEqual(result, 0)
        self.assertEqual(
            require_healthy.call_args_list,
            [call(self.paths, service=False), call(self.paths, service=False)],
        )
        install_release.assert_called_once_with(
            self.paths,
            "/tmp/release-manifest.json",
        )
        create_upgrade_backup.assert_called_once()

    @patch("mundsen_agent.cli.create_upgrade_backup")
    @patch("mundsen_agent.cli.protected_snapshot", return_value={"a": "1"})
    @patch("mundsen_agent.cli.install_release", return_value="0.3.0")
    @patch("mundsen_agent.cli.activate_release")
    @patch(
        "mundsen_agent.cli._require_healthy",
        side_effect=[None, ReleaseError("unhealthy"), None],
    )
    @patch("mundsen_agent.cli._service_is_installed", return_value=False)
    @patch("mundsen_agent.cli.active_version", return_value="0.2.0")
    @patch("mundsen_agent.cli.load_manifest")
    def test_failed_upgrade_restores_verified_previous_release(
        self,
        load_manifest,
        active_version,
        service_is_installed,
        require_healthy,
        activate_release,
        install_release,
        protected_snapshot,
        create_upgrade_backup,
    ) -> None:
        del (
            active_version,
            service_is_installed,
            install_release,
            protected_snapshot,
            create_upgrade_backup,
        )
        load_manifest.return_value = self.manifest

        with self.assertRaisesRegex(ReleaseError, "previous release was restored"):
            cli._cmd_upgrade(
                self.paths,
                Namespace(manifest="/tmp/release-manifest.json", yes=True),
            )

        activate_release.assert_called_once_with(self.paths, "0.2.0")
        self.assertEqual(require_healthy.call_count, 3)

    @patch("mundsen_agent.cli.create_upgrade_backup")
    @patch("mundsen_agent.cli.protected_snapshot", side_effect=[{"a": "1"}, {"a": "1"}])
    @patch("mundsen_agent.cli.rollback_release", return_value="0.1.0")
    @patch("mundsen_agent.cli._require_healthy")
    @patch("mundsen_agent.cli._service_is_installed", return_value=False)
    @patch("mundsen_agent.cli.active_version", return_value="0.2.0")
    def test_rollback_checks_health_before_and_after_activation(
        self,
        active_version,
        service_is_installed,
        require_healthy,
        rollback_release,
        protected_snapshot,
        create_upgrade_backup,
    ) -> None:
        del (
            active_version,
            service_is_installed,
            protected_snapshot,
            create_upgrade_backup,
        )

        result = cli._cmd_rollback(
            self.paths,
            Namespace(version="0.1.0", yes=True),
        )

        self.assertEqual(result, 0)
        rollback_release.assert_called_once_with(self.paths, "0.1.0")
        self.assertEqual(
            require_healthy.call_args_list,
            [call(self.paths, service=False), call(self.paths, service=False)],
        )


if __name__ == "__main__":
    unittest.main()
