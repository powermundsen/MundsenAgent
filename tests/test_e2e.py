from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest
from pathlib import Path


class TestFreshInstallation(unittest.TestCase):
    def test_bundled_install_routes_one_message_with_subscription_agents(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            dist = work / "dist"
            build = subprocess.run(
                [
                    "bash",
                    str(repo / "installer" / "build-release.sh"),
                    "0.3.0",
                    str(dist),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            archive = dist / "mundsen-agent-0.3.0.tar.gz"
            manifest = json.loads(
                (dist / "release-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["version"], "0.3.0")
            self.assertEqual(
                manifest["files"][0]["sha256"],
                hashlib.sha256(archive.read_bytes()).hexdigest(),
            )
            installer = dist / "MundsenAgent-0.3.0-installer.sh"
            expected_installer_hash = (
                (dist / "MundsenAgent-0.3.0-installer.sh.sha256")
                .read_text(encoding="utf-8")
                .split()[0]
            )
            self.assertEqual(
                expected_installer_hash,
                hashlib.sha256(installer.read_bytes()).hexdigest(),
            )
            checksums = {
                name: digest
                for digest, name in (
                    line.split(maxsplit=1)
                    for line in (dist / "SHA256SUMS")
                    .read_text(encoding="utf-8")
                    .splitlines()
                )
            }
            self.assertEqual(checksums[archive.name], hashlib.sha256(archive.read_bytes()).hexdigest())
            self.assertEqual(checksums[installer.name], hashlib.sha256(installer.read_bytes()).hexdigest())
            with tarfile.open(archive, "r:gz") as bundle:
                names = bundle.getnames()
                self.assertIn("mundsen-agent-0.3.0/LICENSE", names)
            self.assertFalse(
                any(
                    "__pycache__" in name
                    or name.endswith((".pyc", ".pyo"))
                    or ".egg-info/" in name
                    for name in names
                )
            )

            root = work / "Fresh Assistant"
            environment = dict(os.environ)
            environment.pop("TELEGRAM_BOT_TOKEN", None)
            environment.pop("TELEGRAM_CHAT_ID", None)
            environment.update(
                {
                    "MUNDSEN_SKIP_PROVIDER_INSTALL": "1",
                    "MUNDSEN_TEST_PYTHON": sys.executable,
                }
            )
            install = subprocess.run(
                [
                    "bash",
                    str(dist / "MundsenAgent-0.3.0-installer.sh"),
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
            self.assertEqual(install.returncode, 0, install.stderr)

            self._write_fake_providers(root)
            installed_python = textwrap.dedent(
                """
                from mundsen_agent.app import build_bridge
                from mundsen_agent.instance import InstanceSettings
                from mundsen_agent.paths import MundsenAgentPaths
                from mundsen_agent.setup import configure_telegram, create_instance

                class LocalTelegram:
                    def __init__(self):
                        self.messages = []

                    def send_typing(self, chat_id):
                        pass

                    def send_message(self, chat_id, html_text):
                        self.messages.append((chat_id, html_text))
                        return len(self.messages)

                paths = MundsenAgentPaths.from_root(__import__("os").environ["MUNDSEN_HOME"])
                create_instance(
                    paths,
                    InstanceSettings(
                        schema_version=1,
                        assistant_name="Local",
                        language="en",
                        timezone="UTC",
                        tone="natural",
                        technical_level="general",
                        preferred_agent="auto",
                    ),
                )
                configure_telegram(
                    paths,
                    token="123456789:abcdefghijklmnopqrstuvwxyzABCDE",
                    chat_id=1001,
                )
                bridge = build_bridge(paths)
                client = LocalTelegram()
                bridge.client = client
                bridge.process_update(
                    {"message": {"chat": {"id": 1001}, "text": "hello"}}
                )
                if not bridge.process_one_job():
                    raise SystemExit("message was not processed")
                print(client.messages[-1][1])
                """
            )
            run_environment = dict(environment)
            run_environment.update(
                {
                    "MUNDSEN_HOME": str(root),
                    "PYTHONPATH": str(root / "current" / "src"),
                    "PYTHONDONTWRITEBYTECODE": "1",
                }
            )
            routed = subprocess.run(
                [str(root / "tools" / "bin" / "python3"), "-c", installed_python],
                env=run_environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(routed.returncode, 0, routed.stderr)
            self.assertIn("<b>E2E OK</b>", routed.stdout)

            health = subprocess.run(
                [str(root / "bin" / "mundsen-agent"), "health", "--no-service"],
                env=run_environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(health.returncode, 0, health.stderr + health.stdout)
            self.assertNotIn("FAIL", health.stdout)
            self.assertEqual(
                stat.S_IMODE((root / "config" / "secrets.env").stat().st_mode),
                0o600,
            )

    def _write_fake_providers(self, root: Path) -> None:
        claude = root / "providers" / "home" / ".local" / "bin" / "claude"
        codex = root / "providers" / "bin" / "codex"
        claude.parent.mkdir(parents=True, exist_ok=True)
        codex.parent.mkdir(parents=True, exist_ok=True)
        claude.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json
                import sys

                if sys.argv[1:] == ["auth", "status"]:
                    print(json.dumps({
                        "loggedIn": True,
                        "authMethod": "claude.ai",
                        "subscriptionType": "pro",
                    }))
                    raise SystemExit(0)
                sys.stdin.read()
                print(json.dumps({
                    "type": "system",
                    "subtype": "init",
                    "session_id": "e2e-session",
                }))
                print(json.dumps({
                    "type": "result",
                    "session_id": "e2e-session",
                    "result": "**E2E OK**",
                    "usage": {"input_tokens": 2, "output_tokens": 2},
                }))
                """
            ),
            encoding="utf-8",
        )
        codex.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json
                import sys

                if sys.argv[1:] == ["login", "status"]:
                    print("Logged in using ChatGPT")
                    raise SystemExit(0)
                sys.stdin.read()
                print(json.dumps({
                    "type": "thread.started",
                    "thread_id": "e2e-thread",
                }))
                print(json.dumps({
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "**E2E OK**"},
                }))
                print(json.dumps({
                    "type": "turn.completed",
                    "usage": {"input_tokens": 2, "output_tokens": 2},
                }))
                """
            ),
            encoding="utf-8",
        )
        claude.chmod(0o700)
        codex.chmod(0o700)


if __name__ == "__main__":
    unittest.main()
