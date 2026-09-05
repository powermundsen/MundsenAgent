from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from mundsen_agent.instance import InstanceSettings, load_instance
from mundsen_agent.paths import MundsenAgentPaths
from mundsen_agent.setup import (
    configure_telegram,
    create_instance,
    detect_private_chat_id,
)


class FakeTelegramClient:
    def __init__(self, batches: list[list[dict[str, object]]]) -> None:
        self.batches = batches

    def get_updates(
        self,
        *,
        offset: int,
        long_poll_seconds: int,
    ) -> list[dict[str, object]]:
        return self.batches.pop(0)


class TestSetup(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.paths = MundsenAgentPaths.from_root(Path(self.tempdir.name) / "Mundsen Agent")
        self.settings = InstanceSettings(
            schema_version=1,
            assistant_name="Fjord",
            language="nb",
            timezone="Europe/Oslo",
            tone="natural",
            technical_level="general",
            preferred_agent="auto",
        )

    def test_create_instance_is_private_and_idempotent(self) -> None:
        created = create_instance(self.paths, self.settings)
        memory = self.paths.instance_dir / "MEMORY.md"
        memory.write_text("# My memory\n", encoding="utf-8")
        created_again = create_instance(self.paths, self.settings)

        self.assertGreaterEqual(len(created), 8)
        self.assertEqual(created_again, [])
        self.assertEqual(memory.read_text(encoding="utf-8"), "# My memory\n")
        self.assertEqual(load_instance(self.paths.instance_dir / "instance.yaml"), self.settings)
        self.assertTrue((self.paths.instance_dir / "AUTONOMY.md").is_file())
        runtime = self.paths.runtime_config_file.read_text(encoding="utf-8")
        self.assertIn("MUNDSEN_FEATURES=", runtime)
        self.assertEqual(
            stat.S_IMODE((self.paths.instance_dir / "AGENTS.md").stat().st_mode),
            0o600,
        )
        for instruction_file in ("AGENTS.md", "CLAUDE.md"):
            instructions = (
                self.paths.instance_dir / instruction_file
            ).read_text(encoding="utf-8")
            self.assertIn(
                "Never install, update, or remove Claude Code",
                instructions,
            )
            self.assertIn("clear owner reply", instructions)
            self.assertIn(
                "no third-party messaging, purchase, or account-changing tools",
                instructions,
            )

    def test_configure_telegram_writes_private_secret_once(self) -> None:
        written = configure_telegram(
            self.paths,
            token="123456789:abcdefghijklmnopqrstuvwxyzABCDE",
            chat_id=1001,
        )
        written_again = configure_telegram(
            self.paths,
            token="999999999:abcdefghijklmnopqrstuvwxyzABCDE",
            chat_id=2002,
        )

        self.assertTrue(written)
        self.assertFalse(written_again)
        self.assertIn("1001", self.paths.secrets_file.read_text())
        self.assertNotIn("2002", self.paths.secrets_file.read_text())
        self.assertEqual(stat.S_IMODE(self.paths.secrets_file.stat().st_mode), 0o600)

    def test_detects_one_private_chat_after_prompt(self) -> None:
        client = FakeTelegramClient(
            [
                [],
                [
                    {
                        "update_id": 5,
                        "message": {
                            "chat": {"id": 1001, "type": "private"},
                            "text": "/start pair1234",
                        },
                    }
                ],
            ]
        )
        prompts: list[str] = []

        chat_id = detect_private_chat_id(
            client,  # type: ignore[arg-type]
            pairing_code="pair1234",
            wait_for_user=lambda prompt: prompts.append(prompt) or "",
        )

        self.assertEqual(chat_id, 1001)
        self.assertEqual(len(prompts), 1)
        self.assertIn("/start pair1234", prompts[0])

    def test_multiple_private_chats_are_rejected(self) -> None:
        client = FakeTelegramClient(
            [
                [
                    {
                        "update_id": 1,
                        "message": {"chat": {"id": 1001, "type": "private"}},
                    },
                    {
                        "update_id": 2,
                        "message": {"chat": {"id": 1002, "type": "private"}},
                    },
                ]
            ]
        )

        with self.assertRaises(ValueError):
            detect_private_chat_id(client)  # type: ignore[arg-type]

    def test_pairing_code_ignores_unrelated_private_chats(self) -> None:
        client = FakeTelegramClient(
            [
                [
                    {
                        "update_id": 1,
                        "message": {
                            "chat": {"id": 9999, "type": "private"},
                            "text": "/start wrong",
                        },
                    },
                    {
                        "update_id": 2,
                        "message": {
                            "chat": {"id": 1001, "type": "private"},
                            "text": "/start correct",
                        },
                    },
                ]
            ]
        )

        self.assertEqual(
            detect_private_chat_id(
                client,  # type: ignore[arg-type]
                pairing_code="correct",
            ),
            1001,
        )

    def test_invalid_interactive_settings_are_not_written(self) -> None:
        invalid = InstanceSettings(
            schema_version=1,
            assistant_name="bad\nname",
            language="nb",
            timezone="Europe/Oslo",
            tone="natural",
            technical_level="general",
            preferred_agent="auto",
        )

        with self.assertRaises(ValueError):
            create_instance(self.paths, invalid)
        self.assertFalse((self.paths.instance_dir / "instance.yaml").exists())


if __name__ == "__main__":
    unittest.main()
