"""Install and control a per-user background service on macOS or Linux."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from mundsen_agent.paths import MundsenAgentPaths, ensure_private_directories

Run = Callable[..., subprocess.CompletedProcess[str]]
SERVICE_NAME = "ai.mundsenagent.bridge"


class ServiceError(RuntimeError):
    """Raised when the operating-system service manager rejects an action."""


@dataclass(frozen=True, slots=True)
class ServiceStatus:
    platform: str
    installed: bool
    active: bool


def _write_file(
    path: Path,
    data: bytes,
    *,
    mode: int = 0o600,
    private_parent: bool = True,
) -> None:
    if private_parent:
        ensure_private_directories((path.parent,))
    else:
        if path.parent.is_symlink() or (
            path.parent.exists() and not path.parent.is_dir()
        ):
            raise ServiceError("service registration directory is unsafe")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".service-", dir=path.parent)
    temp_path = Path(temporary)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        os.chmod(path, mode)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temp_path.unlink(missing_ok=True)


def _systemd_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{escaped}"'


def render_launch_agent(paths: MundsenAgentPaths) -> bytes:
    payload = {
        "Label": SERVICE_NAME,
        "ProgramArguments": [
            str(paths.bin_dir / "mundsen-agent"),
            "service-run",
        ],
        "WorkingDirectory": str(paths.instance_dir),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Background",
        "StandardOutPath": str(paths.logs_dir / "service.stdout.log"),
        "StandardErrorPath": str(paths.logs_dir / "service.stderr.log"),
        "EnvironmentVariables": {"MUNDSEN_HOME": str(paths.home)},
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)


def render_systemd_service(paths: MundsenAgentPaths) -> str:
    command = _systemd_quote(str(paths.bin_dir / "mundsen-agent"))
    working = _systemd_quote(str(paths.instance_dir))
    stdout = _systemd_quote(str(paths.logs_dir / "service.stdout.log"))
    stderr = _systemd_quote(str(paths.logs_dir / "service.stderr.log"))
    return "\n".join(
        (
            "[Unit]",
            "Description=Mundsen Agent Telegram bridge",
            "After=network-online.target",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"ExecStart={command} service-run",
            f"WorkingDirectory={working}",
            f"Environment={_systemd_quote(f'MUNDSEN_HOME={paths.home}')}",
            "Restart=on-failure",
            "RestartSec=5",
            f"StandardOutput=append:{stdout}",
            f"StandardError=append:{stderr}",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        )
    )


class ServiceManager:
    def __init__(
        self,
        paths: MundsenAgentPaths,
        *,
        platform: str | None = None,
        user_home: str | Path | None = None,
        runner: Run = subprocess.run,
    ) -> None:
        self.paths = paths
        self.platform = platform or sys.platform
        self.user_home = Path(user_home) if user_home is not None else Path.home()
        self.runner = runner

    @property
    def kind(self) -> str:
        if self.platform == "darwin":
            return "launchd"
        if self.platform.startswith("linux"):
            return "systemd"
        raise ServiceError("only macOS and Linux services are supported")

    @property
    def source_file(self) -> Path:
        suffix = "plist" if self.kind == "launchd" else "service"
        return self.paths.service_dir / f"{SERVICE_NAME}.{suffix}"

    @property
    def registration_file(self) -> Path:
        if self.kind == "launchd":
            return self.user_home / "Library" / "LaunchAgents" / f"{SERVICE_NAME}.plist"
        return self.user_home / ".config" / "systemd" / "user" / "mundsen-agent.service"

    def _run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return self.runner(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=30,
            check=False,
        )

    def install(self) -> ServiceStatus:
        ensure_private_directories((self.paths.service_dir, self.paths.logs_dir))
        if self.kind == "launchd":
            _write_file(self.source_file, render_launch_agent(self.paths))
        else:
            _write_file(
                self.source_file,
                render_systemd_service(self.paths).encode("utf-8"),
            )
        _write_file(
            self.registration_file,
            self.source_file.read_bytes(),
            private_parent=False,
        )
        if self.kind == "launchd":
            domain = f"gui/{os.getuid()}"
            self._run(["launchctl", "bootout", domain, str(self.registration_file)])
            result = self._run(
                ["launchctl", "bootstrap", domain, str(self.registration_file)]
            )
        else:
            reload_result = self._run(["systemctl", "--user", "daemon-reload"])
            if reload_result.returncode != 0:
                raise ServiceError("systemd user manager is not available")
            result = self._run(
                ["systemctl", "--user", "enable", "--now", "mundsen-agent.service"]
            )
        if result.returncode != 0:
            raise ServiceError(f"{self.kind} could not start Mundsen Agent")
        return self.status()

    def stop(self) -> None:
        if self.kind == "launchd":
            self._run(
                [
                    "launchctl",
                    "bootout",
                    f"gui/{os.getuid()}",
                    str(self.registration_file),
                ]
            )
        else:
            self._run(["systemctl", "--user", "stop", "mundsen-agent.service"])

    def start(self) -> ServiceStatus:
        if not self.registration_file.exists():
            raise ServiceError("the Mundsen Agent service is not installed")
        if self.kind == "launchd":
            result = self._run(
                [
                    "launchctl",
                    "bootstrap",
                    f"gui/{os.getuid()}",
                    str(self.registration_file),
                ]
            )
            if result.returncode != 0 and not self.status().active:
                raise ServiceError("launchd could not start Mundsen Agent")
        else:
            result = self._run(
                ["systemctl", "--user", "start", "mundsen-agent.service"]
            )
            if result.returncode != 0:
                raise ServiceError("systemd could not start Mundsen Agent")
        return self.status()

    def restart(self) -> ServiceStatus:
        if self.kind == "launchd":
            target = f"gui/{os.getuid()}/{SERVICE_NAME}"
            result = self._run(["launchctl", "kickstart", "-k", target])
            if result.returncode != 0:
                self.stop()
                return self.start()
        else:
            result = self._run(
                ["systemctl", "--user", "restart", "mundsen-agent.service"]
            )
            if result.returncode != 0:
                raise ServiceError("systemd could not restart Mundsen Agent")
        return self.status()

    def status(self) -> ServiceStatus:
        installed = self.registration_file.is_file()
        if not installed:
            return ServiceStatus(self.kind, False, False)
        if self.kind == "launchd":
            result = self._run(
                ["launchctl", "print", f"gui/{os.getuid()}/{SERVICE_NAME}"]
            )
        else:
            result = self._run(
                ["systemctl", "--user", "is-active", "--quiet", "mundsen-agent.service"]
            )
        return ServiceStatus(self.kind, True, result.returncode == 0)

    def uninstall_registration(self) -> None:
        self.stop()
        self.registration_file.unlink(missing_ok=True)
        if self.kind == "systemd":
            self._run(["systemctl", "--user", "disable", "mundsen-agent.service"])
            self._run(["systemctl", "--user", "daemon-reload"])
