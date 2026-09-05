"""Central, injectable filesystem paths for Mundsen Agent."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path


class PathConfigurationError(ValueError):
    """Raised when a configured Mundsen Agent path is unsafe or ambiguous."""


def _absolute_path(value: str | Path, setting: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise PathConfigurationError(f"{setting} must be an absolute path")
    return path


def _configured_path(
    environ: Mapping[str, str],
    setting: str,
    default: Path,
) -> Path:
    raw = environ.get(setting)
    return _absolute_path(raw, setting) if raw else default


@dataclass(frozen=True, slots=True)
class MundsenAgentPaths:
    """All writable Mundsen Agent roots.

    Constructing this object performs no filesystem writes. Tests can inject a
    complete environment and home directory without touching the real user.
    """

    home: Path
    config_dir: Path
    data_dir: Path
    state_dir: Path
    cache_dir: Path
    instance_dir: Path

    @classmethod
    def from_root(cls, root: str | Path) -> "MundsenAgentPaths":
        """Build the portable single-directory installer layout."""

        root_dir = _absolute_path(root, "MUNDSEN_HOME")
        return cls(
            home=root_dir,
            config_dir=root_dir / "config",
            data_dir=root_dir,
            state_dir=root_dir / "state",
            cache_dir=root_dir / "cache",
            instance_dir=root_dir / "instance",
        )

    @classmethod
    def from_environ(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        home: str | Path | None = None,
    ) -> "MundsenAgentPaths":
        env = os.environ if environ is None else environ
        if home is not None:
            home_dir = _absolute_path(home, "home")
        elif env.get("HOME"):
            home_dir = _absolute_path(env["HOME"], "HOME")
        else:
            home_dir = _absolute_path(Path.home(), "home")

        xdg_config = _configured_path(
            env, "XDG_CONFIG_HOME", home_dir / ".config"
        )
        xdg_data = _configured_path(
            env, "XDG_DATA_HOME", home_dir / ".local" / "share"
        )
        xdg_state = _configured_path(
            env, "XDG_STATE_HOME", home_dir / ".local" / "state"
        )
        xdg_cache = _configured_path(
            env, "XDG_CACHE_HOME", home_dir / ".cache"
        )

        return cls(
            home=home_dir,
            config_dir=_configured_path(
                env, "MUNDSEN_CONFIG_DIR", xdg_config / "mundsen-agent"
            ),
            data_dir=_configured_path(
                env, "MUNDSEN_DATA_DIR", xdg_data / "mundsen-agent"
            ),
            state_dir=_configured_path(
                env, "MUNDSEN_STATE_DIR", xdg_state / "mundsen-agent"
            ),
            cache_dir=_configured_path(
                env, "MUNDSEN_CACHE_DIR", xdg_cache / "mundsen-agent"
            ),
            instance_dir=_configured_path(
                env, "MUNDSEN_INSTANCE_DIR", home_dir / "MundsenAgentInstance"
            ),
        )

    @property
    def releases_dir(self) -> Path:
        return self.data_dir / "releases"

    @property
    def current_release(self) -> Path:
        return self.data_dir / "current"

    @property
    def runtime_config_file(self) -> Path:
        return self.config_dir / "runtime.env"

    @property
    def secrets_file(self) -> Path:
        return self.config_dir / "secrets.env"

    @property
    def sessions_dir(self) -> Path:
        return self.state_dir / "sessions"

    @property
    def queue_dir(self) -> Path:
        return self.state_dir / "queue"

    @property
    def attachments_dir(self) -> Path:
        return self.state_dir / "attachments"

    @property
    def logs_dir(self) -> Path:
        return self.state_dir / "logs"

    @property
    def backups_dir(self) -> Path:
        return self.state_dir / "backups"

    @property
    def audit_log_file(self) -> Path:
        return self.logs_dir / "audit.jsonl"

    @property
    def router_state_file(self) -> Path:
        return self.sessions_dir / "router.json"

    @property
    def telegram_offset_file(self) -> Path:
        return self.sessions_dir / "telegram-offset.json"

    @property
    def onboarding_state_file(self) -> Path:
        return self.state_dir / "onboarding.json"

    @property
    def reminder_state_file(self) -> Path:
        return self.state_dir / "reminders.json"

    @property
    def update_state_file(self) -> Path:
        return self.state_dir / "update-check.json"

    @property
    def install_metadata_file(self) -> Path:
        return self.state_dir / "installation.json"

    @property
    def provider_home(self) -> Path:
        return self.home / "providers" / "home"

    @property
    def provider_bin_dir(self) -> Path:
        return self.home / "providers" / "bin"

    @property
    def codex_home(self) -> Path:
        return self.home / "providers" / "codex"

    @property
    def tools_dir(self) -> Path:
        return self.home / "tools"

    @property
    def tools_bin_dir(self) -> Path:
        return self.tools_dir / "bin"

    @property
    def python_dir(self) -> Path:
        return self.tools_dir / "python"

    @property
    def bin_dir(self) -> Path:
        return self.home / "bin"

    @property
    def service_dir(self) -> Path:
        return self.home / "service"

    def private_runtime_directories(self) -> tuple[Path, ...]:
        """Return directories created by the runtime bootstrap."""

        return (
            self.config_dir,
            self.data_dir,
            self.state_dir,
            self.cache_dir,
            self.sessions_dir,
            self.queue_dir,
            self.attachments_dir,
            self.logs_dir,
            self.backups_dir,
            self.provider_home,
            self.provider_bin_dir,
            self.codex_home,
            self.tools_dir,
            self.tools_bin_dir,
            self.python_dir,
            self.bin_dir,
            self.service_dir,
        )


def ensure_private_directories(paths: Iterable[Path]) -> None:
    """Create explicit runtime directories with mode 0700.

    Existing symlinks and non-directories are rejected. Callers choose the
    exact directory list, so constructing :class:`MundsenAgentPaths` stays
    side-effect free.
    """

    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_absolute():
            raise PathConfigurationError("runtime directories must be absolute")
        if path.is_symlink():
            raise PathConfigurationError(f"refusing symlink directory: {path}")
        if path.exists() and not path.is_dir():
            raise PathConfigurationError(f"runtime path is not a directory: {path}")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise PathConfigurationError(
                f"unable to open runtime directory safely: {path}"
            ) from exc
        try:
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise PathConfigurationError(
                    f"runtime path is not a directory: {path}"
                )
            os.fchmod(descriptor, 0o700)
        finally:
            os.close(descriptor)
