"""Small, dependency-free schema for one personal Mundsen Agent instance."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SCHEMA_VERSION = 1
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SAFE_TEXT_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,80}$")
_SUPPORTED_KEYS = frozenset(
    {
        "schema_version",
        "assistant_name",
        "language",
        "timezone",
        "tone",
        "technical_level",
        "preferred_agent",
    }
)
_AGENTS = frozenset({"auto", "claude", "codex"})


class InstanceConfigurationError(ValueError):
    """Raised when ``instance.yaml`` does not match the supported schema."""


@dataclass(frozen=True, slots=True)
class InstanceSettings:
    schema_version: int
    assistant_name: str
    language: str
    timezone: str
    tone: str
    technical_level: str
    preferred_agent: str


def _decode_scalar(raw: str, *, line_number: int) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value[0] in {"'", '"'}:
        if len(value) < 2 or value[-1] != value[0]:
            raise InstanceConfigurationError(
                f"line {line_number} has an unmatched quote"
            )
        if value[0] == '"':
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError as exc:
                raise InstanceConfigurationError(
                    f"line {line_number} has an invalid quoted value"
                ) from exc
            if not isinstance(decoded, str):
                raise InstanceConfigurationError(
                    f"line {line_number} must contain a string"
                )
            return decoded
        return value[1:-1].replace("''", "'")
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return value


def parse_instance_text(text: str) -> InstanceSettings:
    """Parse the intentionally flat subset used by ``instance.yaml``."""

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise InstanceConfigurationError(
                f"line {line_number} must use key: value"
            )
        raw_key, raw_value = line.split(":", 1)
        key = raw_key.strip()
        if not _KEY_RE.fullmatch(key) or key not in _SUPPORTED_KEYS:
            raise InstanceConfigurationError(
                f"line {line_number} has an unsupported key"
            )
        if key in values:
            raise InstanceConfigurationError(f"duplicate key: {key}")
        values[key] = _decode_scalar(raw_value, line_number=line_number)

    missing = sorted(_SUPPORTED_KEYS - set(values))
    if missing:
        raise InstanceConfigurationError(
            f"missing instance settings: {', '.join(missing)}"
        )
    try:
        schema_version = int(values["schema_version"])
    except ValueError as exc:
        raise InstanceConfigurationError("schema_version must be an integer") from exc
    if schema_version != SCHEMA_VERSION:
        raise InstanceConfigurationError(
            f"unsupported schema_version: {schema_version}"
        )

    for name in (
        "assistant_name",
        "language",
        "timezone",
        "tone",
        "technical_level",
    ):
        if not _SAFE_TEXT_RE.fullmatch(values[name]):
            raise InstanceConfigurationError(f"invalid value for {name}")
    if not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{2,8})?", values["language"]):
        raise InstanceConfigurationError("language must be a short language tag")
    try:
        ZoneInfo(values["timezone"])
    except ZoneInfoNotFoundError as exc:
        raise InstanceConfigurationError("timezone is not available") from exc
    if values["preferred_agent"] not in _AGENTS:
        raise InstanceConfigurationError(
            "preferred_agent must be auto, claude, or codex"
        )

    return InstanceSettings(
        schema_version=schema_version,
        assistant_name=values["assistant_name"],
        language=values["language"],
        timezone=values["timezone"],
        tone=values["tone"],
        technical_level=values["technical_level"],
        preferred_agent=values["preferred_agent"],
    )


def load_instance(path: str | Path) -> InstanceSettings:
    target = Path(path)
    if target.is_symlink():
        raise InstanceConfigurationError("refusing symlink instance configuration")
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise InstanceConfigurationError(
            "instance configuration is not valid UTF-8"
        ) from exc
    return parse_instance_text(text)


def render_instance(settings: InstanceSettings) -> str:
    """Render deterministic, human-readable YAML without a YAML dependency."""

    return "\n".join(
        (
            "# Mundsen Agent personal instance",
            f"schema_version: {settings.schema_version}",
            f"assistant_name: {json.dumps(settings.assistant_name, ensure_ascii=False)}",
            f"language: {json.dumps(settings.language)}",
            f"timezone: {json.dumps(settings.timezone)}",
            f"tone: {json.dumps(settings.tone, ensure_ascii=False)}",
            f"technical_level: {json.dumps(settings.technical_level, ensure_ascii=False)}",
            f"preferred_agent: {settings.preferred_agent}",
            "",
        )
    )
