from __future__ import annotations

import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from mundsen_agent.config import ConfigurationError, RuntimeSettings
from mundsen_agent.cli import build_parser
from mundsen_agent.instance import InstanceSettings
from mundsen_agent.module_system import ModuleManager, parse_enabled_modules
from mundsen_agent.modules.local_health import LocalHealthError, LocalHealthModule
from mundsen_agent.paths import MundsenAgentPaths


# The fixture data must stay inside the "last 14 days" window relative to
# whenever the test suite actually runs, so it is anchored to today instead
# of a hardcoded calendar date that eventually rots.
_RECENT_DAY = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")

APPLE_HEALTH = f"""<?xml version="1.0" encoding="UTF-8"?>
<HealthData>
 <Record type="HKQuantityTypeIdentifierHeartRateVariabilitySDNN" value="48.5" unit="ms" startDate="{_RECENT_DAY} 07:00:00 +0000" endDate="{_RECENT_DAY} 07:00:00 +0000"/>
 <Record type="HKQuantityTypeIdentifierStepCount" value="8342" unit="count" startDate="{_RECENT_DAY} 00:00:00 +0000" endDate="{_RECENT_DAY} 23:59:59 +0000"/>
 <Record type="IgnoredType" value="123" unit="count" startDate="{_RECENT_DAY} 00:00:00 +0000" endDate="{_RECENT_DAY} 01:00:00 +0000"/>
 <Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="42" durationUnit="min" totalDistance="8.1" totalDistanceUnit="km" totalEnergyBurned="510" totalEnergyBurnedUnit="kcal" startDate="{_RECENT_DAY} 18:00:00 +0000" endDate="{_RECENT_DAY} 18:42:00 +0000"/>
</HealthData>
"""


def settings() -> InstanceSettings:
    return InstanceSettings(1, "Helper", "en", "Europe/Oslo", "concise", "high", "auto")


class TestLocalHealth(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.paths = MundsenAgentPaths.from_root(self.root / "Mundsen Agent")
        self.module = LocalHealthModule(self.paths, settings())
        self.export = self.root / "export.xml"
        self.export.write_text(APPLE_HEALTH, encoding="utf-8")

    def test_import_is_private_deduplicated_and_summarized(self) -> None:
        result = self.module.import_apple_health(self.export.resolve())
        duplicate = self.module.import_apple_health(self.export.resolve())

        self.assertEqual((result.workouts_added, result.samples_added), (1, 2))
        self.assertTrue(duplicate.already_imported)
        self.assertEqual(stat.S_IMODE(self.module.database.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.module.summary.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.module.summary_json.stat().st_mode), 0o600)
        structured = json.loads(self.module.summary_json.read_text(encoding="utf-8"))
        self.assertEqual(structured["schema_version"], 1)
        self.assertEqual(structured["last_14_days"]["workouts"], 1)
        summary = self.module.context()
        self.assertIn("Running: 1 workouts", summary)
        self.assertIn("HeartRateVariabilitySDNN: 48.50 ms", summary)
        self.assertNotIn(str(self.export), summary)

    def test_relative_symlink_and_invalid_xml_are_rejected(self) -> None:
        with self.assertRaisesRegex(LocalHealthError, "unsafe"):
            self.module.import_apple_health("export.xml")
        link = self.root / "link.xml"
        link.symlink_to(self.export)
        with self.assertRaisesRegex(LocalHealthError, "unsafe"):
            self.module.import_apple_health(link.resolve().parent / "link.xml")
        broken = self.root / "broken.xml"
        broken.write_text("<HealthData>", encoding="utf-8")
        with self.assertRaisesRegex(LocalHealthError, "valid XML"):
            self.module.import_apple_health(broken.resolve())

    def test_summary_uses_requested_clock(self) -> None:
        self.module.import_apple_health(self.export.resolve())
        self.module.write_summary(now=datetime(2026, 8, 17, tzinfo=timezone.utc))
        self.assertIn("Workouts: 1", self.module.context())

    def test_invalid_root_and_reversed_dates_are_rejected(self) -> None:
        invalid_root = self.root / "invalid-root.xml"
        invalid_root.write_text("<NotHealthData />", encoding="utf-8")
        with self.assertRaisesRegex(LocalHealthError, "invalid root"):
            self.module.import_apple_health(invalid_root.resolve())

        reversed_dates = self.root / "reversed.xml"
        reversed_dates.write_text(
            """<HealthData><Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="1" durationUnit="min" startDate="2026-08-17 10:01:00 +0200" endDate="2026-08-17 10:00:00 +0200" /></HealthData>""",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(LocalHealthError, "invalid workout"):
            self.module.import_apple_health(reversed_dates.resolve())


class TestModuleManager(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.paths = MundsenAgentPaths.from_root(Path(self.tempdir.name) / "Mundsen Agent")

    def test_disabled_by_default_and_explicit_enable(self) -> None:
        disabled = ModuleManager(self.paths, RuntimeSettings({}), settings())
        enabled = ModuleManager(
            self.paths,
            RuntimeSettings({"MUNDSEN_MODULES": "local-health"}),
            settings(),
        )
        self.assertEqual(disabled.modules, [])
        self.assertEqual(enabled.enabled_names, ("local-health",))
        self.assertIn("no training summary", enabled.context())

    def test_invalid_module_configuration_is_rejected(self) -> None:
        with self.assertRaises(ConfigurationError):
            parse_enabled_modules("local-health,local-health")
        with self.assertRaises(ConfigurationError):
            parse_enabled_modules("remote-health")

    def test_scheduler_repairs_missing_summary(self) -> None:
        module = LocalHealthModule(self.paths, settings())
        export = Path(self.tempdir.name) / "export.xml"
        export.write_text(APPLE_HEALTH, encoding="utf-8")
        module.import_apple_health(export.resolve())
        module.summary.unlink()
        manager = ModuleManager(
            self.paths,
            RuntimeSettings({"MUNDSEN_MODULES": "local-health"}),
            settings(),
            now=lambda: datetime(2026, 8, 17, tzinfo=timezone.utc),
        )

        self.assertEqual(manager.pending_notifications(), [])
        self.assertTrue(module.summary.exists())

    def test_training_command_defaults_to_status(self) -> None:
        args = build_parser().parse_args(["training"])
        self.assertEqual(args.action, "status")


if __name__ == "__main__":
    unittest.main()
