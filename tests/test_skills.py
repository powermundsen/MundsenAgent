from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from mundsen_agent.paths import MundsenAgentPaths, ensure_private_directories
from mundsen_agent.skills import SkillError, skill_discovery_is_current, sync_skill_discovery


class TestSkillDiscovery(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.paths = MundsenAgentPaths.from_root(Path(self.tempdir.name) / "Mundsen Agent")
        ensure_private_directories(
            (
                self.paths.home,
                self.paths.releases_dir,
                self.paths.provider_home,
                self.paths.instance_dir,
                self.paths.instance_dir / "skills",
            )
        )
        release = self.paths.releases_dir / "0.3.0"
        core = release / "src" / "mundsen_agent" / "bundled_skills" / "core-check"
        core.mkdir(parents=True)
        (core / "SKILL.md").write_text("---\nname: core-check\ndescription: test\n---\n")
        local = self.paths.instance_dir / "skills" / "local-check"
        local.mkdir()
        (local / "SKILL.md").write_text("---\nname: local-check\ndescription: test\n---\n")
        os.chmod(local / "SKILL.md", 0o600)
        self.paths.current_release.symlink_to(release)

    def test_shared_skills_are_linked_for_both_providers(self) -> None:
        result = sync_skill_discovery(self.paths)

        self.assertEqual(result.names, ("core-check", "local-check"))
        for root in (result.claude_directory, result.codex_directory):
            self.assertTrue((root / "core-check").is_symlink())
            self.assertTrue((root / "local-check").is_symlink())
        self.assertTrue(skill_discovery_is_current(self.paths))

    def test_sync_is_idempotent_and_removes_only_managed_links(self) -> None:
        result = sync_skill_discovery(self.paths)
        unrelated = result.claude_directory / "unrelated"
        unrelated.mkdir()
        (self.paths.instance_dir / "skills" / "local-check" / "SKILL.md").unlink()
        (self.paths.instance_dir / "skills" / "local-check").rmdir()

        sync_skill_discovery(self.paths)

        self.assertTrue(unrelated.is_dir())
        self.assertFalse((result.claude_directory / "local-check").exists())

    def test_collision_with_unmanaged_provider_skill_is_rejected(self) -> None:
        root = self.paths.provider_home / ".claude" / "skills" / "core-check"
        root.mkdir(parents=True)
        with self.assertRaisesRegex(SkillError, "occupied"):
            sync_skill_discovery(self.paths)

    def test_duplicate_core_and_private_skill_names_are_rejected(self) -> None:
        duplicate = self.paths.instance_dir / "skills" / "core-check"
        duplicate.mkdir()
        (duplicate / "SKILL.md").write_text("---\ndescription: duplicate\n---\n")
        with self.assertRaisesRegex(SkillError, "duplicate"):
            sync_skill_discovery(self.paths)


if __name__ == "__main__":
    unittest.main()
