"""Local health checks with secret-safe results."""

from __future__ import annotations

import stat
from dataclasses import asdict, dataclass
from pathlib import Path

from mundsen_agent.auth import authentication_status
from mundsen_agent.bridge.telegram_client import TelegramClient, TelegramError
from mundsen_agent.config import ConfigurationError, load_runtime_settings
from mundsen_agent.features import FeatureSet
from mundsen_agent.instance import InstanceConfigurationError, load_instance
from mundsen_agent.module_system import ModuleManager
from mundsen_agent.paths import MundsenAgentPaths
from mundsen_agent.release import ReleaseError, verify_installed_release
from mundsen_agent.service import ServiceError, ServiceManager
from mundsen_agent.skills import skill_discovery_is_current


@dataclass(frozen=True, slots=True)
class HealthCheck:
    name: str
    ok: bool
    detail: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _private_mode(path: Path, expected: int) -> bool:
    return path.exists() and stat.S_IMODE(path.stat().st_mode) == expected


def run_health_checks(
    paths: MundsenAgentPaths,
    *,
    network: bool = False,
    service: bool = True,
    platform: str | None = None,
    user_home: str | Path | None = None,
) -> list[HealthCheck]:
    checks: list[HealthCheck] = []
    checks.append(
        HealthCheck(
            "root",
            paths.home.is_dir() and _private_mode(paths.home, 0o700),
            "private directory" if paths.home.is_dir() else "missing",
        )
    )
    current_ok = paths.current_release.is_symlink() and paths.current_release.exists()
    if current_ok:
        try:
            verify_installed_release(paths.current_release.resolve())
            release_detail = "active and verified"
        except ReleaseError:
            current_ok = False
            release_detail = "active release failed integrity or compatibility"
    else:
        release_detail = "missing active release"
    checks.append(
        HealthCheck(
            "release",
            current_ok,
            release_detail,
        )
    )
    try:
        instance = load_instance(paths.instance_dir / "instance.yaml")
        instance_ok = True
        instance_detail = "valid schema"
    except (FileNotFoundError, InstanceConfigurationError, OSError):
        instance_ok = False
        instance_detail = "missing or invalid"
    checks.append(HealthCheck("instance", instance_ok, instance_detail))
    skills_ok = skill_discovery_is_current(paths)
    checks.append(
        HealthCheck(
            "skills",
            skills_ok,
            "shared discovery is current" if skills_ok else "discovery is missing or stale",
        )
    )

    try:
        runtime = load_runtime_settings(
            paths,
            required_names=("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
        )
        config_ok = _private_mode(paths.secrets_file, 0o600)
        config_detail = "valid and private" if config_ok else "unsafe permissions"
    except (ConfigurationError, FileNotFoundError, OSError):
        runtime = None
        config_ok = False
        config_detail = "missing or invalid"
    checks.append(HealthCheck("configuration", config_ok, config_detail))
    if runtime is not None and instance_ok:
        try:
            features = FeatureSet(paths, runtime)
            checks.append(
                HealthCheck(
                    "features",
                    True,
                    ", ".join(features.enabled_names) or "none enabled",
                )
            )
            manager = ModuleManager(paths, runtime, instance)
            checks.extend(
                HealthCheck(check.name, check.ok, check.detail)
                for check in manager.health()
            )
        except (ConfigurationError, OSError, ValueError):
            checks.append(
                HealthCheck("features_or_modules", False, "invalid or unsafe")
            )

    provider_states = [
        authentication_status(paths, "claude"),
        authentication_status(paths, "codex"),
    ]
    installed = [state.provider for state in provider_states if state.installed]
    authenticated = [
        state.provider for state in provider_states if state.authenticated
    ]
    checks.append(
        HealthCheck(
            "providers_installed",
            len(installed) == 2,
            ", ".join(installed) if installed else "none",
        )
    )
    checks.append(
        HealthCheck(
            "subscription_auth",
            bool(authenticated),
            ", ".join(authenticated) if authenticated else "none",
        )
    )

    if network:
        telegram_ok = False
        detail = "configuration unavailable"
        if runtime is not None:
            try:
                TelegramClient(runtime.require("TELEGRAM_BOT_TOKEN")).get_me()
                telegram_ok = True
                detail = "reachable"
            except (TelegramError, ValueError):
                detail = "unreachable or rejected"
        checks.append(HealthCheck("telegram", telegram_ok, detail))

    if service:
        try:
            status = ServiceManager(
                paths,
                platform=platform,
                user_home=user_home,
            ).status()
            checks.append(
                HealthCheck(
                    "service",
                    status.installed and status.active,
                    (
                        f"{status.platform}: active"
                        if status.active
                        else f"{status.platform}: inactive or missing"
                    ),
                )
            )
        except ServiceError:
            checks.append(HealthCheck("service", False, "unsupported"))
    return checks
