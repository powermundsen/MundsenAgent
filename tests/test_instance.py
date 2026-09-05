from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mundsen_agent.instance import (
    InstanceConfigurationError,
    InstanceSettings,
    load_instance,
    parse_instance_text,
    render_instance,
)


class TestInstanceSettings(unittest.TestCase):
    def valid_text(self) -> str:
        return """
schema_version: 1
assistant_name: "Fjord"
language: nb
timezone: Europe/Oslo
tone: "naturlig og kort"
technical_level: generell
preferred_agent: auto
"""

    def test_valid_flat_yaml_round_trips(self) -> None:
        settings = parse_instance_text(self.valid_text())
        rendered = render_instance(settings)

        self.assertEqual(settings.assistant_name, "Fjord")
        self.assertEqual(parse_instance_text(rendered), settings)

    def test_unknown_duplicate_and_missing_keys_are_rejected(self) -> None:
        with self.assertRaises(InstanceConfigurationError):
            parse_instance_text(self.valid_text() + "private_name: Person\n")
        with self.assertRaises(InstanceConfigurationError):
            parse_instance_text(self.valid_text() + "language: en\n")
        with self.assertRaises(InstanceConfigurationError):
            parse_instance_text("schema_version: 1\n")

    def test_agent_schema_and_timezone_are_validated(self) -> None:
        with self.assertRaises(InstanceConfigurationError):
            parse_instance_text(
                self.valid_text().replace("preferred_agent: auto", "preferred_agent: api")
            )
        with self.assertRaises(InstanceConfigurationError):
            parse_instance_text(
                self.valid_text().replace("Europe/Oslo", "Private/Office")
            )

    def test_symlinked_instance_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            real = root / "real.yaml"
            real.write_text(self.valid_text(), encoding="utf-8")
            link = root / "instance.yaml"
            link.symlink_to(real)

            with self.assertRaises(InstanceConfigurationError):
                load_instance(link)

    def test_render_rejects_control_characters_on_parse(self) -> None:
        settings = InstanceSettings(
            schema_version=1,
            assistant_name="Bad\nName",
            language="nb",
            timezone="UTC",
            tone="natural",
            technical_level="general",
            preferred_agent="auto",
        )

        with self.assertRaises(InstanceConfigurationError):
            parse_instance_text(render_instance(settings))


if __name__ == "__main__":
    unittest.main()
