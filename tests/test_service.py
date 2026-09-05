from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from mundsen_agent.paths import MundsenAgentPaths
from mundsen_agent.service import (
    ServiceManager,
    render_launch_agent,
    render_systemd_service,
)


class FakeServiceRunner:
    def __init__(self) -> None:
        self.active = False
        self.calls: list[list[str]] = []

    def __call__(
        self, command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        self.calls.append(command)
        if "bootstrap" in command or "start" in command or "enable" in command:
            self.active = True
        if "bootout" in command or "stop" in command:
            self.active = False
        if "kickstart" in command or "restart" in command:
            self.active = True
        if "print" in command or "is-active" in command:
            return subprocess.CompletedProcess(command, 0 if self.active else 1)
        return subprocess.CompletedProcess(command, 0)


class TestServiceManagement(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        self.paths = MundsenAgentPaths.from_root(root / "Mundsen Agent with spaces")
        self.paths.bin_dir.mkdir(parents=True)
        self.paths.instance_dir.mkdir(parents=True)
        (self.paths.bin_dir / "mundsen-agent").write_text("", encoding="utf-8")
        self.user_home = root / "user"
        self.runner = FakeServiceRunner()

    def test_launch_agent_installs_starts_and_removes(self) -> None:
        manager = ServiceManager(
            self.paths,
            platform="darwin",
            user_home=self.user_home,
            runner=self.runner,
        )

        status = manager.install()

        self.assertTrue(status.installed)
        self.assertTrue(status.active)
        payload = render_launch_agent(self.paths)
        self.assertIn(b"Mundsen Agent with spaces", payload)
        manager.uninstall_registration()
        self.assertFalse(manager.registration_file.exists())

    def test_systemd_unit_quotes_spaces_and_percent_characters(self) -> None:
        paths = MundsenAgentPaths.from_root(
            Path(self.tempdir.name) / "Mundsen Agent 100% local"
        )
        unit = render_systemd_service(paths)

        self.assertIn("Mundsen Agent 100%% local", unit)
        self.assertIn("Restart=on-failure", unit)

        manager = ServiceManager(
            paths,
            platform="linux",
            user_home=self.user_home,
            runner=self.runner,
        )
        status = manager.install()
        self.assertTrue(status.active)
        self.assertTrue(manager.registration_file.is_file())

    def test_registration_replaces_symlink_without_touching_target(self) -> None:
        manager = ServiceManager(
            self.paths,
            platform="darwin",
            user_home=self.user_home,
            runner=self.runner,
        )
        target = Path(self.tempdir.name) / "do-not-overwrite"
        target.write_text("safe", encoding="utf-8")
        manager.registration_file.parent.mkdir(parents=True)
        manager.registration_file.symlink_to(target)

        manager.install()

        self.assertEqual(target.read_text(encoding="utf-8"), "safe")
        self.assertFalse(manager.registration_file.is_symlink())


if __name__ == "__main__":
    unittest.main()
