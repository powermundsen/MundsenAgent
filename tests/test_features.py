from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from mundsen_agent.config import ConfigurationError, RuntimeSettings
from mundsen_agent.features import FeatureSet, parse_model_aliases
from mundsen_agent.paths import MundsenAgentPaths


class TestFeatures(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.paths = MundsenAgentPaths.from_root(Path(self.tempdir.name) / "Mundsen Agent")

    def test_features_are_disabled_by_default(self) -> None:
        features = FeatureSet(self.paths, RuntimeSettings({}))

        self.assertEqual(features.enabled_names, ())
        self.assertFalse(features.enabled("attachments"))

    def test_dependencies_and_required_settings_are_explicit(self) -> None:
        with self.assertRaises(ConfigurationError):
            FeatureSet(
                self.paths,
                RuntimeSettings({"MUNDSEN_FEATURES": "local-transcription"}),
            )

        command = Path(self.tempdir.name) / "transcribe"
        command.write_text("#!/bin/sh\nprintf transcript\n", encoding="utf-8")
        os.chmod(command, 0o700)
        features = FeatureSet(
            self.paths,
            RuntimeSettings(
                {
                    "MUNDSEN_FEATURES": "attachments,local-transcription",
                    "MUNDSEN_TRANSCRIBE_COMMAND": str(command),
                }
            ),
        )
        self.assertTrue(features.enabled("local-transcription"))

    def test_autonomy_context_is_opt_in(self) -> None:
        disabled = FeatureSet(self.paths, RuntimeSettings({}))
        self.paths.instance_dir.mkdir(parents=True)
        (self.paths.instance_dir / "AUTONOMY.md").write_text(
            "# Local policy\n",
            encoding="utf-8",
        )
        enabled = FeatureSet(
            self.paths,
            RuntimeSettings({"MUNDSEN_FEATURES": "autonomy-context"}),
        )

        self.assertEqual(disabled.context_files(), ())
        self.assertEqual(enabled.context_files()[0][1].name, "AUTONOMY.md")

    def test_autonomy_context_requires_the_owner_policy_file(self) -> None:
        with self.assertRaises(ConfigurationError):
            FeatureSet(
                self.paths,
                RuntimeSettings({"MUNDSEN_FEATURES": "autonomy-context"}),
            )

    def test_model_aliases_are_bounded_and_reserve_commands(self) -> None:
        self.assertEqual(
            parse_model_aliases("opus=provider-model", setting="MODELS"),
            {"opus": "provider-model"},
        )
        with self.assertRaises(ConfigurationError):
            parse_model_aliases("status=provider-model", setting="MODELS")

    def test_configured_mermaid_renderer_must_be_executable(self) -> None:
        with self.assertRaises(ConfigurationError):
            FeatureSet(
                self.paths,
                RuntimeSettings(
                    {
                        "MUNDSEN_FEATURES": "inline-visualizations",
                        "MUNDSEN_MERMAID_RENDER_COMMAND": str(
                            Path(self.tempdir.name) / "missing"
                        ),
                    }
                ),
            )


if __name__ == "__main__":
    unittest.main()
