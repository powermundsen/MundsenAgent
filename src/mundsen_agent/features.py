"""Explicit registry for optional runtime features.

Features change how the local runtime behaves. They are separate from modules,
which connect optional data sources or scheduled integrations. Nothing in this
registry is enabled unless its name occurs in ``MUNDSEN_FEATURES``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from mundsen_agent.config import ConfigurationError, RuntimeSettings
from mundsen_agent.paths import MundsenAgentPaths

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    name: str
    description: str
    dependencies: tuple[str, ...] = ()
    required_settings: tuple[str, ...] = ()


class FeatureRegistry:
    """Small deterministic registry used by setup, health and the runtime."""

    def __init__(self) -> None:
        self._specs: dict[str, FeatureSpec] = {}

    def register(self, spec: FeatureSpec) -> None:
        if not _NAME_RE.fullmatch(spec.name):
            raise ValueError("invalid feature name")
        if spec.name in self._specs:
            raise ValueError(f"duplicate feature: {spec.name}")
        self._specs[spec.name] = spec

    def require(self, name: str) -> FeatureSpec:
        try:
            return self._specs[name]
        except KeyError:
            raise ConfigurationError(f"unknown Mundsen Agent feature: {name}") from None

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs))


def builtin_feature_registry() -> FeatureRegistry:
    registry = FeatureRegistry()
    for spec in (
        FeatureSpec(
            "attachments",
            "Private, temporary Telegram photo and document downloads.",
        ),
        FeatureSpec(
            "local-transcription",
            "Local transcription of Telegram voice and audio attachments.",
            dependencies=("attachments",),
            required_settings=("MUNDSEN_TRANSCRIBE_COMMAND",),
        ),
        FeatureSpec(
            "inline-visualizations",
            "Extract SVG and Mermaid fences into private Telegram documents.",
        ),
        FeatureSpec(
            "live-progress",
            "Edit one temporary Telegram progress message during long jobs.",
        ),
        FeatureSpec(
            "extended-commands",
            "Version, model, reasoning and maintenance-plan chat commands.",
        ),
        FeatureSpec(
            "autonomy-context",
            "Inject the owner-editable AUTONOMY.md policy on each provider turn.",
        ),
        FeatureSpec(
            "circuit-breaker",
            "Temporarily skip a repeatedly failing provider in auto mode.",
        ),
    ):
        registry.register(spec)
    return registry


def parse_enabled_features(
    value: str,
    *,
    registry: FeatureRegistry | None = None,
) -> tuple[str, ...]:
    selected = tuple(part.strip() for part in value.split(",") if part.strip())
    if any(not _NAME_RE.fullmatch(name) for name in selected):
        raise ConfigurationError("MUNDSEN_FEATURES contains an invalid feature name")
    if len(set(selected)) != len(selected):
        raise ConfigurationError("MUNDSEN_FEATURES contains duplicates")
    catalog = registry or builtin_feature_registry()
    for name in selected:
        catalog.require(name)
    return selected


def parse_model_aliases(value: str, *, setting: str) -> dict[str, str]:
    """Parse ``alias=model`` pairs without shell syntax or interpolation."""

    aliases: dict[str, str] = {}
    if not value.strip():
        return aliases
    reserved = {
        "auto", "claude", "codex", "gpt", "new", "kill", "stop", "status",
        "version", "versions", "rollback", "models", "powerup", "think",
        "minimal", "low", "medium", "high", "xhigh",
    }
    for raw_pair in value.split(","):
        if "=" not in raw_pair:
            raise ConfigurationError(f"{setting} must use alias=model pairs")
        raw_alias, raw_model = raw_pair.split("=", 1)
        alias = raw_alias.strip().lower()
        model = raw_model.strip()
        if (
            not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", alias)
            or alias in reserved
            or not model
            or len(model) > 120
            or any(ord(character) < 32 for character in model)
        ):
            raise ConfigurationError(f"{setting} contains an invalid alias")
        if alias in aliases:
            raise ConfigurationError(f"{setting} contains duplicate aliases")
        aliases[alias] = model
        if len(aliases) > 20:
            raise ConfigurationError(f"{setting} contains too many aliases")
    return aliases


class FeatureSet:
    """Validated enabled features and their bounded configuration."""

    def __init__(
        self,
        paths: MundsenAgentPaths,
        runtime: RuntimeSettings,
        *,
        registry: FeatureRegistry | None = None,
    ) -> None:
        self.paths = paths
        self.runtime = runtime
        self.registry = registry or builtin_feature_registry()
        self.enabled_names = parse_enabled_features(
            runtime.get("MUNDSEN_FEATURES"),
            registry=self.registry,
        )
        enabled = set(self.enabled_names)
        for name in self.enabled_names:
            spec = self.registry.require(name)
            missing_dependencies = sorted(set(spec.dependencies) - enabled)
            if missing_dependencies:
                raise ConfigurationError(
                    f"feature {name} requires: {', '.join(missing_dependencies)}"
                )
            missing_settings = [
                setting for setting in spec.required_settings if not runtime.get(setting)
            ]
            if missing_settings:
                raise ConfigurationError(
                    f"feature {name} requires settings: {', '.join(missing_settings)}"
                )
        if self.enabled("local-transcription"):
            self._validate_executable(
                runtime.require("MUNDSEN_TRANSCRIBE_COMMAND"),
                setting="MUNDSEN_TRANSCRIBE_COMMAND",
            )
        mermaid_command = runtime.get("MUNDSEN_MERMAID_RENDER_COMMAND")
        if self.enabled("inline-visualizations") and mermaid_command:
            self._validate_executable(
                mermaid_command,
                setting="MUNDSEN_MERMAID_RENDER_COMMAND",
            )
        if self.enabled("autonomy-context"):
            autonomy_file = paths.instance_dir / "AUTONOMY.md"
            if (
                not autonomy_file.is_absolute()
                or autonomy_file.is_symlink()
                or not autonomy_file.is_file()
            ):
                raise ConfigurationError(
                    "autonomy-context requires instance/AUTONOMY.md"
                )

    def enabled(self, name: str) -> bool:
        self.registry.require(name)
        return name in self.enabled_names

    def context_files(self) -> tuple[tuple[str, Path], ...]:
        if not self.enabled("autonomy-context"):
            return ()
        return (("Owner autonomy policy", self.paths.instance_dir / "AUTONOMY.md"),)

    @staticmethod
    def _validate_executable(value: str, *, setting: str) -> None:
        path = Path(value)
        if (
            not path.is_absolute()
            or path.is_symlink()
            or not path.is_file()
            or not os.access(path, os.X_OK)
        ):
            raise ConfigurationError(
                f"{setting} must be an absolute executable file"
            )
