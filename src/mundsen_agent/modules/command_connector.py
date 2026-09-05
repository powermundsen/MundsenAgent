"""Read-only local connector contract for optional integration modules."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable

from mundsen_agent.config import ConfigurationError, RuntimeSettings
from mundsen_agent.instance import InstanceSettings
from mundsen_agent.module_system import MundsenAgentModule, ModuleHealth, ModuleNotification
from mundsen_agent.paths import MundsenAgentPaths

MAX_CONNECTOR_OUTPUT_BYTES = 64 * 1024
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,159}$")


class LocalCommandConnector:
    """Invoke one explicit local executable without a shell or secret env."""

    def __init__(
        self,
        *,
        name: str,
        command: str,
        notifications_enabled: bool = False,
        timeout_seconds: int = 30,
    ) -> None:
        target = Path(command)
        if (
            not target.is_absolute()
            or target.is_symlink()
            or not target.is_file()
            or not os.access(target, os.X_OK)
        ):
            raise ConfigurationError(
                f"module {name} requires an absolute executable connector command"
            )
        self.name = name
        self.command = str(target)
        self.notifications_enabled = notifications_enabled
        self.timeout_seconds = timeout_seconds
        self._notifications: list[ModuleNotification] = []
        self._last_ok = True
        self._last_detail = "ready"

    def context(self) -> str:
        result = self._run("context")
        if result is None:
            return ""
        try:
            return result.decode("utf-8").strip()
        except UnicodeDecodeError:
            self._last_ok = False
            self._last_detail = "connector returned invalid context data"
            return ""

    def health(self) -> list[ModuleHealth]:
        result = self._run("health")
        ok = result is not None
        detail = "connector ready" if ok else self._last_detail
        return [ModuleHealth(self.name, ok, detail)]

    def run_scheduled(self, now: datetime) -> None:
        self._notifications = []
        if not self.notifications_enabled:
            return
        result = self._run("notifications", now.isoformat())
        if result is None:
            return
        try:
            payload = json.loads(result.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._last_ok = False
            self._last_detail = "connector returned invalid notification data"
            return
        if not isinstance(payload, list) or len(payload) > 20:
            self._last_ok = False
            self._last_detail = "connector returned invalid notification data"
            return
        notifications: list[ModuleNotification] = []
        for item in payload:
            if not isinstance(item, dict):
                notifications = []
                self._last_ok = False
                self._last_detail = "connector returned invalid notification data"
                break
            key = str(item.get("key") or "")
            text = str(item.get("text") or "")
            if (
                not key.startswith(f"{self.name}:")
                or not _KEY_RE.fullmatch(key)
                or not text.strip()
                or len(text) > 4000
            ):
                notifications = []
                self._last_ok = False
                self._last_detail = "connector returned invalid notification data"
                break
            notifications.append(ModuleNotification(key, text))
        self._notifications = notifications

    def pending_notifications(self) -> list[ModuleNotification]:
        return list(self._notifications)

    def mark_notification_sent(self, key: str) -> None:
        if not self.notifications_enabled or not key.startswith(f"{self.name}:"):
            raise ValueError("invalid connector notification key")
        if self._run("ack", key) is None:
            raise ValueError("connector could not acknowledge notification")

    def _run(self, *arguments: str) -> bytes | None:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key in {"HOME", "LANG", "LC_ALL", "PATH", "TMPDIR"}
        }
        try:
            result = subprocess.run(
                [self.command, *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=self.timeout_seconds,
                check=False,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired):
            self._last_ok = False
            self._last_detail = "connector command failed"
            return None
        if result.returncode != 0 or len(result.stdout) > MAX_CONNECTOR_OUTPUT_BYTES:
            self._last_ok = False
            self._last_detail = "connector command failed"
            return None
        self._last_ok = True
        self._last_detail = "ready"
        return result.stdout


def connector_factory(
    name: str,
    command_setting: str,
) -> Callable[[MundsenAgentPaths, RuntimeSettings, InstanceSettings], MundsenAgentModule]:
    prefix = command_setting.removesuffix("_COMMAND")

    def factory(
        paths: MundsenAgentPaths,
        runtime: RuntimeSettings,
        instance: InstanceSettings,
    ) -> MundsenAgentModule:
        del paths, instance
        command = runtime.get(command_setting)
        if not command:
            raise ConfigurationError(
                f"module {name} requires setting {command_setting}"
            )
        notifications_setting = f"{prefix}_NOTIFICATIONS"
        notifications_value = runtime.get(notifications_setting, "0")
        if notifications_value not in {"0", "1"}:
            raise ConfigurationError(
                f"{notifications_setting} must be 0 or 1"
            )
        return LocalCommandConnector(
            name=name,
            command=command,
            notifications_enabled=notifications_value == "1",
        )

    return factory
