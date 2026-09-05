"""Explicit, disabled-by-default extension points for local Mundsen Agent modules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol

from mundsen_agent.config import ConfigurationError, RuntimeSettings
from mundsen_agent.instance import InstanceSettings
from mundsen_agent.paths import MundsenAgentPaths

_MODULE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


@dataclass(frozen=True, slots=True)
class ModuleHealth:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class ModuleNotification:
    key: str
    text: str


class MundsenAgentModule(Protocol):
    name: str

    def context(self) -> str: ...

    def health(self) -> list[ModuleHealth]: ...

    def run_scheduled(self, now: datetime) -> None: ...

    def pending_notifications(self) -> list[ModuleNotification]: ...

    def mark_notification_sent(self, key: str) -> None: ...


ModuleFactory = Callable[
    [MundsenAgentPaths, RuntimeSettings, InstanceSettings],
    MundsenAgentModule,
]


class ModuleRegistry:
    """Registry boundary for disabled-by-default integration modules."""

    def __init__(self) -> None:
        self._factories: dict[str, ModuleFactory] = {}

    def register(self, name: str, factory: ModuleFactory) -> None:
        if not _MODULE_RE.fullmatch(name):
            raise ValueError("invalid module name")
        if name in self._factories:
            raise ValueError(f"duplicate module: {name}")
        self._factories[name] = factory

    def create(
        self,
        name: str,
        paths: MundsenAgentPaths,
        runtime: RuntimeSettings,
        instance: InstanceSettings,
    ) -> MundsenAgentModule:
        try:
            factory = self._factories[name]
        except KeyError:
            raise ConfigurationError(f"unknown Mundsen Agent module: {name}") from None
        return factory(paths, runtime, instance)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))


def _local_health_factory(
    paths: MundsenAgentPaths,
    runtime: RuntimeSettings,
    instance: InstanceSettings,
) -> MundsenAgentModule:
    del runtime
    from mundsen_agent.modules.local_health import LocalHealthModule

    return LocalHealthModule(paths, instance)


def builtin_module_registry() -> ModuleRegistry:
    registry = ModuleRegistry()
    registry.register("local-health", _local_health_factory)
    from mundsen_agent.modules.command_connector import connector_factory

    for name, setting in (
        ("calendar", "MUNDSEN_CALENDAR_COMMAND"),
        ("morning-brief", "MUNDSEN_MORNING_BRIEF_COMMAND"),
        ("observability", "MUNDSEN_OBSERVABILITY_COMMAND"),
        ("smart-home", "MUNDSEN_SMART_HOME_COMMAND"),
    ):
        registry.register(name, connector_factory(name, setting))
    return registry


def parse_enabled_modules(
    value: str,
    *,
    registry: ModuleRegistry | None = None,
) -> tuple[str, ...]:
    if not value.strip():
        return ()
    names = tuple(part.strip() for part in value.split(",") if part.strip())
    if any(not _MODULE_RE.fullmatch(name) for name in names):
        raise ConfigurationError("MUNDSEN_MODULES contains an invalid module name")
    if len(set(names)) != len(names):
        raise ConfigurationError("MUNDSEN_MODULES contains duplicates")
    catalog = registry or builtin_module_registry()
    unknown = sorted(set(names) - set(catalog.names))
    if unknown:
        raise ConfigurationError(f"unknown Mundsen Agent modules: {', '.join(unknown)}")
    return names


class ModuleManager:
    def __init__(
        self,
        paths: MundsenAgentPaths,
        runtime: RuntimeSettings,
        instance: InstanceSettings,
        *,
        now: Callable[[], datetime] | None = None,
        registry: ModuleRegistry | None = None,
    ) -> None:
        self.paths = paths
        self.runtime = runtime
        self.instance = instance
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.registry = registry or builtin_module_registry()
        self.enabled_names = parse_enabled_modules(
            runtime.get("MUNDSEN_MODULES"),
            registry=self.registry,
        )
        self.modules = [
            self.registry.create(name, paths, runtime, instance)
            for name in self.enabled_names
        ]

    def context(self) -> str:
        sections: list[str] = []
        for module in self.modules:
            text = module.context().strip()
            if text:
                sections.append(f"## Module: {module.name}\n{text}")
        return "\n\n".join(sections)[:32_000]

    def health(self) -> list[ModuleHealth]:
        checks: list[ModuleHealth] = []
        for module in self.modules:
            checks.extend(module.health())
        return checks

    def pending_notifications(self) -> list[ModuleNotification]:
        notifications: list[ModuleNotification] = []
        clock = self.now()
        if clock.tzinfo is None:
            clock = clock.replace(tzinfo=timezone.utc)
        for module in self.modules:
            module.run_scheduled(clock.astimezone(timezone.utc))
            notifications.extend(module.pending_notifications())
        return notifications

    def mark_notification_sent(self, key: str) -> None:
        prefix = key.split(":", 1)[0]
        for module in self.modules:
            if module.name == prefix:
                module.mark_notification_sent(key)
                return
        raise ValueError("notification does not belong to an enabled module")

    def require(self, name: str) -> MundsenAgentModule:
        for module in self.modules:
            if module.name == name:
                return module
        raise ConfigurationError(
            f"module {name} is disabled; add it to MUNDSEN_MODULES in runtime.env"
        )
