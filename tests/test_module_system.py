from __future__ import annotations

import tempfile
import unittest
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from mundsen_agent.config import ConfigurationError, RuntimeSettings
from mundsen_agent.instance import InstanceSettings
from mundsen_agent.module_system import ModuleManager, ModuleRegistry
from mundsen_agent.paths import MundsenAgentPaths


@dataclass
class ExampleModule:
    name: str = "example"

    def context(self) -> str:
        return "example context"

    def health(self) -> list[object]:
        return []

    def run_scheduled(self, now: datetime) -> None:
        del now

    def pending_notifications(self) -> list[object]:
        return []

    def mark_notification_sent(self, key: str) -> None:
        del key


class TestModuleRegistry(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.paths = MundsenAgentPaths.from_root(Path(self.tempdir.name) / "Mundsen Agent")
        self.instance = InstanceSettings(
            1, "Fjord", "nb", "Europe/Oslo", "natural", "general", "auto"
        )

    def test_custom_module_is_one_registry_entry(self) -> None:
        registry = ModuleRegistry()
        registry.register(
            "example",
            lambda paths, runtime, instance: ExampleModule(),
        )
        manager = ModuleManager(
            self.paths,
            RuntimeSettings({"MUNDSEN_MODULES": "example"}),
            self.instance,
            registry=registry,
        )

        self.assertEqual(manager.enabled_names, ("example",))
        self.assertIn("example context", manager.context())

    def test_unknown_module_is_rejected(self) -> None:
        with self.assertRaises(ConfigurationError):
            ModuleManager(
                self.paths,
                RuntimeSettings({"MUNDSEN_MODULES": "unknown"}),
                self.instance,
            )

    def test_command_connector_is_opt_in_and_does_not_inherit_secrets(self) -> None:
        command = Path(self.tempdir.name) / "calendar-connector"
        command.write_text(
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "  context) printf 'calendar:%s' \"${TELEGRAM_BOT_TOKEN-unset}\" ;;\n"
            "  health) exit 0 ;;\n"
            "  notifications) printf '[{\"key\":\"calendar:event-1\",\"text\":\"Soon\"}]' ;;\n"
            "  ack) exit 0 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        os.chmod(command, 0o700)
        runtime = RuntimeSettings(
            {
                "MUNDSEN_MODULES": "calendar",
                "MUNDSEN_CALENDAR_COMMAND": str(command),
                "MUNDSEN_CALENDAR_NOTIFICATIONS": "1",
            }
        )
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "must-not-leak"}):
            manager = ModuleManager(self.paths, runtime, self.instance)
            self.assertIn("calendar:unset", manager.context())
            notifications = manager.pending_notifications()

        self.assertEqual(notifications[0].key, "calendar:event-1")
        manager.mark_notification_sent("calendar:event-1")


if __name__ == "__main__":
    unittest.main()
