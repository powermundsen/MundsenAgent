"""Strict loading of Mundsen Agent runtime and secret settings."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from mundsen_agent.paths import MundsenAgentPaths

MAX_CONFIG_BYTES = 64 * 1024
_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SECRET_NAME_RE = re.compile(
    r"(?:^|_)(?:TOKEN|SECRET|PASSWORD|PASSWD|PRIVATE_KEY|API_KEY|CHAT_ID|SESSION_ID)(?:_|$)",
    re.IGNORECASE,
)


class ConfigurationError(ValueError):
    """Raised for invalid or unsafe runtime configuration."""


def _is_secret_name(name: str) -> bool:
    return bool(_SECRET_NAME_RE.search(name))


def _unquote_value(raw: str, *, path: Path, line_number: int) -> str:
    value = raw.strip()
    if not value:
        return ""
    if "\x00" in value:
        raise ConfigurationError(
            f"{path}: line {line_number} contains a null byte"
        )
    if value[0] in {"'", '"'}:
        if len(value) < 2 or value[-1] != value[0]:
            raise ConfigurationError(
                f"{path}: line {line_number} has an unmatched quote"
            )
        return value[1:-1]
    if value[-1:] in {"'", '"'}:
        raise ConfigurationError(
            f"{path}: line {line_number} has an unmatched quote"
        )
    return value


def parse_env_file(
    path: str | Path,
    *,
    require_private: bool = False,
) -> dict[str, str]:
    """Parse a small dotenv subset without shell evaluation or interpolation."""

    target = Path(path)
    if target.is_symlink():
        raise ConfigurationError(f"refusing symlink configuration: {target}")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(target, flags)
    except FileNotFoundError:
        raise
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ConfigurationError(
                f"configuration is not a regular file: {target}"
            )
        if file_stat.st_size > MAX_CONFIG_BYTES:
            raise ConfigurationError(
                f"configuration exceeds {MAX_CONFIG_BYTES} bytes"
            )
        if require_private and stat.S_IMODE(file_stat.st_mode) != 0o600:
            raise ConfigurationError(
                f"secret configuration must use mode 0600: {target}"
            )
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            text = handle.read(MAX_CONFIG_BYTES + 1)
        if len(text.encode("utf-8")) > MAX_CONFIG_BYTES:
            raise ConfigurationError(
                f"configuration exceeds {MAX_CONFIG_BYTES} bytes"
            )
    except UnicodeDecodeError as exc:
        raise ConfigurationError(f"configuration is not valid UTF-8: {target}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigurationError(
                f"{target}: line {line_number} must use KEY=VALUE"
            )
        raw_key, raw_value = line.split("=", 1)
        key = raw_key.strip()
        if not _KEY_RE.fullmatch(key):
            raise ConfigurationError(
                f"{target}: line {line_number} has an invalid key"
            )
        if key in values:
            raise ConfigurationError(f"{target}: duplicate key {key}")
        values[key] = _unquote_value(
            raw_value, path=target, line_number=line_number
        )
    return values


class RuntimeSettings:
    """Immutable settings with secret-safe representation."""

    __slots__ = ("_secret_names", "_values")

    def __init__(
        self,
        values: Mapping[str, str],
        *,
        secret_names: set[str] | frozenset[str] = frozenset(),
    ) -> None:
        copied = {str(key): str(value) for key, value in values.items()}
        self._values = MappingProxyType(copied)
        self._secret_names = frozenset(secret_names)

    def __repr__(self) -> str:
        keys = ", ".join(sorted(self._values))
        return f"RuntimeSettings(keys=[{keys}])"

    def get(self, name: str, default: str = "") -> str:
        return self._values.get(name, default)

    def require(self, name: str) -> str:
        value = self.get(name)
        if not value:
            raise ConfigurationError(f"missing required setting: {name}")
        return value

    @property
    def secret_names(self) -> frozenset[str]:
        return self._secret_names

    def redacted(self) -> dict[str, str]:
        return {
            key: "[redacted]" if key in self._secret_names else value
            for key, value in self._values.items()
        }

    def as_environment(
        self,
        base: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        """Return a new subprocess environment without mutating ``os.environ``."""

        result = dict(os.environ if base is None else base)
        result.update(self._values)
        return result


def load_runtime_settings(
    paths: MundsenAgentPaths,
    *,
    environ: Mapping[str, str] | None = None,
    required_names: tuple[str, ...] = (),
) -> RuntimeSettings:
    """Load runtime config, private secrets and explicit environment overrides.

    Precedence is process environment, secrets file, then runtime file.
    Environment keys are imported only when they already occur in a file, are
    explicitly required, or use the ``MUNDSEN_`` namespace.
    """

    env = os.environ if environ is None else environ
    runtime_values = (
        parse_env_file(paths.runtime_config_file)
        if (
            paths.runtime_config_file.exists()
            or paths.runtime_config_file.is_symlink()
        )
        else {}
    )
    misplaced_secrets = sorted(
        name
        for name, value in runtime_values.items()
        if value and _is_secret_name(name)
    )
    if misplaced_secrets:
        joined = ", ".join(misplaced_secrets)
        raise ConfigurationError(
            f"secret-like settings must be in secrets.env or the environment: {joined}"
        )

    secret_values = (
        parse_env_file(paths.secrets_file, require_private=True)
        if paths.secrets_file.exists() or paths.secrets_file.is_symlink()
        else {}
    )
    merged = dict(runtime_values)
    merged.update(secret_values)

    imported_names = set(merged)
    imported_names.update(required_names)
    imported_names.update(name for name in env if name.startswith("MUNDSEN_"))
    for name in imported_names:
        if name in env:
            merged[name] = str(env[name])

    missing = sorted(name for name in required_names if not merged.get(name))
    if missing:
        raise ConfigurationError(
            f"missing required settings: {', '.join(missing)}"
        )

    secret_names = {
        name for name in merged if name in secret_values or _is_secret_name(name)
    }
    return RuntimeSettings(merged, secret_names=secret_names)
