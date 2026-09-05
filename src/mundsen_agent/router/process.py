"""Controlled subprocess execution without invoking a shell."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from threading import Event
from typing import Mapping, Sequence

MAX_CAPTURE_CHARS = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class CommandResult:
    status: str
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    except OSError:
        process.terminate()
    try:
        process.wait(timeout=3)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return
    except OSError:
        process.kill()


def run_command(
    command: Sequence[str],
    *,
    cwd: str,
    env: Mapping[str, str],
    timeout_seconds: int,
    input_text: str = "",
    cancel_event: Event | None = None,
) -> CommandResult:
    """Run one provider CLI with timeout and cancellation.

    ``command`` is always passed directly to ``Popen``. No shell interpolation
    is used, and captured provider output is bounded before returning.
    """

    if not command or timeout_seconds < 1:
        raise ValueError("command and a positive timeout are required")
    started = time.monotonic()
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        env=dict(env),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    sent_input = False
    while True:
        if cancel_event is not None and cancel_event.is_set():
            _terminate_process_group(process)
            stdout, stderr = process.communicate()
            status = "cancelled"
            break
        elapsed = time.monotonic() - started
        if elapsed >= timeout_seconds:
            _terminate_process_group(process)
            stdout, stderr = process.communicate()
            status = "timeout"
            break
        try:
            stdout, stderr = process.communicate(
                input=input_text if not sent_input else None,
                timeout=min(0.2, timeout_seconds - elapsed),
            )
            status = "completed"
            break
        except subprocess.TimeoutExpired:
            sent_input = True
            continue

    duration_ms = int((time.monotonic() - started) * 1000)
    return CommandResult(
        status=status,
        returncode=process.returncode if process.returncode is not None else 1,
        stdout=(stdout or "")[-MAX_CAPTURE_CHARS:],
        stderr=(stderr or "")[-MAX_CAPTURE_CHARS:],
        duration_ms=duration_ms,
    )
