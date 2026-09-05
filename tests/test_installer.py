from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TestInstaller(unittest.TestCase):
    def test_source_installer_creates_single_directory_runtime(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Assistant Root"
            environment = dict(os.environ)
            environment.update(
                {
                    "MUNDSEN_SKIP_PROVIDER_INSTALL": "1",
                    "MUNDSEN_TEST_PYTHON": sys.executable,
                }
            )
            result = subprocess.run(
                [
                    "bash",
                    str(repo / "installer" / "install.sh"),
                    str(root),
                    "--no-setup",
                ],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / "current").resolve().name, "0.3.0")
            self.assertEqual(
                (root / ".mundsen-agent-root").read_text(encoding="utf-8"),
                "mundsen-agent-runtime-root\n",
            )
            self.assertEqual((root / ".mundsen-agent-root").stat().st_mode & 0o777, 0o600)
            version = subprocess.run(
                [str(root / "bin" / "mundsen-agent"), "version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(version.returncode, 0, version.stderr)
            self.assertIn("active=0.3.0", version.stdout)
            for child in root.iterdir():
                self.assertTrue(child == root / "current" or child.is_relative_to(root))

            repeated = subprocess.run(
                [
                    "bash",
                    str(repo / "installer" / "install.sh"),
                    str(root),
                    "--no-setup",
                ],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(repeated.returncode, 0, repeated.stderr)

            protected_release = root / "releases" / "0.3.0" / "README.md"
            protected_release.write_text("tampered\n", encoding="utf-8")
            tampered = subprocess.run(
                [
                    "bash",
                    str(repo / "installer" / "install.sh"),
                    str(root),
                    "--no-setup",
                ],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertNotEqual(tampered.returncode, 0)

    def test_installer_rejects_rebuilt_payload_for_installed_version(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        payload_members = (
            "src",
            "pyproject.toml",
            "README.md",
            "CHANGELOG.md",
            "CONTRIBUTING.md",
            "LICENSE",
            "PRIVACY.md",
            "SECURITY.md",
            "docs",
        )
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            source.mkdir()
            for member in payload_members:
                origin = repo / member
                target = source / member
                if origin.is_dir():
                    shutil.copytree(
                        origin,
                        target,
                        ignore=shutil.ignore_patterns(
                            "__pycache__",
                            "*.pyc",
                            "*.egg-info",
                        ),
                    )
                else:
                    shutil.copy2(origin, target)

            root = Path(temp) / "runtime"
            environment = dict(os.environ)
            environment.update(
                {
                    "MUNDSEN_SKIP_PROVIDER_INSTALL": "1",
                    "MUNDSEN_TEST_PYTHON": sys.executable,
                    "MUNDSEN_SOURCE_DIR": str(source),
                }
            )
            command = [
                "bash",
                str(repo / "installer" / "install.sh"),
                str(root),
                "--no-setup",
            ]
            first = subprocess.run(
                command,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            installed = root / "releases" / "0.3.0" / "README.md"
            original = installed.read_text(encoding="utf-8")

            # Same version number, different content: the old build must not
            # be left silently in place.
            (source / "README.md").write_text(
                original + "\nrebuilt without a version bump\n",
                encoding="utf-8",
            )
            rebuilt = subprocess.run(
                command,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertNotEqual(rebuilt.returncode, 0)
            self.assertIn("does not match this payload", rebuilt.stderr)
            self.assertEqual(installed.read_text(encoding="utf-8"), original)

            # An unchanged payload still reinstalls cleanly.
            (source / "README.md").write_text(original, encoding="utf-8")
            unchanged = subprocess.run(
                command,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(unchanged.returncode, 0, unchanged.stderr)

    def test_installer_rejects_git_worktree_as_runtime_root(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        environment = dict(os.environ)
        environment.update(
            {
                "MUNDSEN_SKIP_PROVIDER_INSTALL": "1",
                "MUNDSEN_TEST_PYTHON": sys.executable,
            }
        )

        result = subprocess.run(
            [
                "bash",
                str(repo / "installer" / "install.sh"),
                str(repo),
                "--no-setup",
            ],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Git worktree", result.stderr)
        self.assertFalse((repo / ".mundsen-agent-root").exists())

    def test_installer_rejects_unmarked_nonempty_directory(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Documents"
            root.mkdir()
            original = root / "keep.txt"
            original.write_text("keep\n", encoding="utf-8")
            environment = dict(os.environ)
            environment.update(
                {
                    "MUNDSEN_SKIP_PROVIDER_INSTALL": "1",
                    "MUNDSEN_TEST_PYTHON": sys.executable,
                }
            )

            result = subprocess.run(
                [
                    "bash",
                    str(repo / "installer" / "install.sh"),
                    str(root),
                    "--no-setup",
                ],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must be empty", result.stderr)
            self.assertEqual(original.read_text(encoding="utf-8"), "keep\n")
            self.assertFalse((root / ".mundsen-agent-root").exists())

    def test_installer_rejects_forged_root_marker(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Mundsen Agent"
            root.mkdir()
            (root / ".mundsen-agent-root").write_text(
                "not-mundsen\n",
                encoding="utf-8",
            )
            (root / "keep.txt").write_text("keep\n", encoding="utf-8")
            environment = dict(os.environ)
            environment.update(
                {
                    "MUNDSEN_SKIP_PROVIDER_INSTALL": "1",
                    "MUNDSEN_TEST_PYTHON": sys.executable,
                }
            )

            result = subprocess.run(
                [
                    "bash",
                    str(repo / "installer" / "install.sh"),
                    str(root),
                    "--no-setup",
                ],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("marker is invalid", result.stderr)

    def test_runtime_and_personal_paths_are_gitignored(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        for relative in (
            "instance/MEMORY.md",
            "instance/memory/user_profile.md",
            "config/runtime.env",
            "config/secrets.env",
            "providers/home/.claude/.credentials.json",
            "providers/codex/auth.json",
            "current",
            "releases/0.3.0/src/mundsen_agent/app.py",
        ):
            result = subprocess.run(
                ["git", "check-ignore", "--no-index", relative],
                cwd=repo,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(result.returncode, 0, relative)

    def test_installer_rejects_unsafe_current_path(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Mundsen Agent"
            root.mkdir()
            (root / ".mundsen-agent-root").write_text(
                "mundsen-agent-runtime-root\n", encoding="utf-8"
            )
            (root / "current").mkdir()
            environment = dict(os.environ)
            environment.update(
                {
                    "MUNDSEN_SOURCE_DIR": str(repo),
                    "MUNDSEN_SKIP_PROVIDER_INSTALL": "1",
                    "MUNDSEN_TEST_PYTHON": sys.executable,
                }
            )

            result = subprocess.run(
                [
                    "bash",
                    str(repo / "installer" / "install.sh"),
                    str(root),
                    "--no-setup",
                ],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must be a symlink", result.stderr)

    def test_provider_installers_receive_an_empty_allowlisted_environment(
        self,
    ) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            root = work / "Mundsen Agent"
            fake_bin = work / "fake-bin"
            fake_bin.mkdir()
            live_claude = fake_bin / "claude"
            live_codex = fake_bin / "codex"
            live_claude.write_text("live claude must survive\n", encoding="utf-8")
            live_codex.write_text("live codex must survive\n", encoding="utf-8")
            live_claude.chmod(0o700)
            live_codex.chmod(0o700)
            claude_report = work / "claude.env"
            codex_report = work / "codex.env"
            fake_curl = fake_bin / "curl"
            fake_curl.write_text(
                "\n".join(
                    (
                        f"#!{sys.executable}",
                        "import shlex",
                        "import sys",
                        f"claude_report = {str(claude_report)!r}",
                        f"codex_report = {str(codex_report)!r}",
                        "url = sys.argv[-1]",
                        "if 'claude.ai' in url:",
                        "    print('/usr/bin/env > ' + shlex.quote(claude_report))",
                        "    print('/bin/mkdir -p \"$HOME/.local/bin\"')",
                        "    print('/usr/bin/touch \"$HOME/.local/bin/claude\"')",
                        "    print('/bin/chmod 700 \"$HOME/.local/bin/claude\"')",
                        "elif 'chatgpt.com' in url:",
                        "    print('/usr/bin/env > ' + shlex.quote(codex_report))",
                        "    print('/bin/mkdir -p \"$CODEX_INSTALL_DIR\"')",
                        "    print('/usr/bin/touch \"$CODEX_INSTALL_DIR/codex\"')",
                        "    print('/bin/chmod 700 \"$CODEX_INSTALL_DIR/codex\"')",
                        "else:",
                        "    raise SystemExit('unexpected URL')",
                        "",
                    )
                ),
                encoding="utf-8",
            )
            fake_curl.chmod(0o700)
            environment = dict(os.environ)
            environment.update(
                {
                    "PATH": f"{fake_bin}{os.pathsep}{environment['PATH']}",
                    "MUNDSEN_TEST_PYTHON": sys.executable,
                    "NVM_DIR": "/live/npm",
                    "ANTHROPIC_API_KEY": "must-not-leak",
                    "OPENAI_API_KEY": "must-not-leak",
                }
            )

            result = subprocess.run(
                [
                    "bash",
                    str(repo / "installer" / "install.sh"),
                    str(root),
                    "--no-setup",
                ],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(
                (
                    root
                    / "providers"
                    / "home"
                    / ".local"
                    / "bin"
                    / "claude"
                ).is_file()
            )
            self.assertTrue((root / "providers" / "bin" / "codex").is_file())
            self.assertEqual(
                live_claude.read_text(encoding="utf-8"),
                "live claude must survive\n",
            )
            self.assertEqual(
                live_codex.read_text(encoding="utf-8"),
                "live codex must survive\n",
            )
            expected_home = str(root.resolve() / "providers" / "home")
            reports = {}
            for name, report in (
                ("claude", claude_report),
                ("codex", codex_report),
            ):
                values = dict(
                    line.split("=", 1)
                    for line in report.read_text(encoding="utf-8").splitlines()
                    if "=" in line
                )
                reports[name] = values
                self.assertEqual(values["HOME"], expected_home)
                self.assertEqual(
                    values["PATH"],
                    "/usr/bin:/bin:/usr/sbin:/sbin",
                )
                self.assertNotIn("NVM_DIR", values)
                self.assertNotIn("ANTHROPIC_API_KEY", values)
                self.assertNotIn("OPENAI_API_KEY", values)
            self.assertEqual(
                reports["codex"]["CODEX_HOME"],
                str(root.resolve() / "providers" / "codex"),
            )
            self.assertEqual(
                reports["codex"]["CODEX_INSTALL_DIR"],
                str(root.resolve() / "providers" / "bin"),
            )


if __name__ == "__main__":
    unittest.main()
