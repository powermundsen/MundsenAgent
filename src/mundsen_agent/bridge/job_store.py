"""Private durable inbox and outbox for Telegram message jobs."""

from __future__ import annotations

import json
import math
import os
import stat
import tempfile
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from mundsen_agent.paths import ensure_private_directories
from mundsen_agent.router.models import AgentResponse

MAX_JOB_BYTES = 256 * 1024
MAX_MESSAGE_CHARS = 64 * 1024
MAX_RESPONSE_CHARS = 128 * 1024


class JobStoreError(ValueError):
    """Raised when a persisted Telegram job is unsafe or invalid."""


@dataclass(frozen=True, slots=True)
class MessageJob:
    update_id: int
    chat_id: int
    text: str
    response_text: str = ""
    response_agent: str = ""
    response_success: bool = False
    response_error_category: str = ""
    next_chunk: int = 0
    next_artifact: int = 0
    attempts: int = 0
    retry_at: float = 0.0

    @property
    def response_ready(self) -> bool:
        return bool(self.response_text)


class PersistentJobStore:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def load_all(self, *, now: float | None = None) -> list[MessageJob]:
        if not self.directory.exists():
            return []
        if not self.directory.is_absolute() or self.directory.is_symlink():
            raise JobStoreError("Telegram job directory is unsafe")
        current = time.time() if now is None else now
        jobs: list[MessageJob] = []
        for path in sorted(self.directory.glob("*.json")):
            job = self._load_path(path)
            if job.retry_at <= current:
                jobs.append(job)
        return jobs

    def list_all(self) -> list[MessageJob]:
        """Load all persisted jobs, including deferred retries."""

        if not self.directory.exists():
            return []
        if not self.directory.is_absolute() or self.directory.is_symlink():
            raise JobStoreError("Telegram job directory is unsafe")
        return [self._load_path(path) for path in sorted(self.directory.glob("*.json"))]

    def get(self, update_id: int) -> MessageJob | None:
        path = self._path(update_id)
        return self._load_path(path) if path.exists() or path.is_symlink() else None

    def put_if_absent(self, job: MessageJob) -> bool:
        self._validate(job)
        path = self._path(job.update_id)
        if path.exists() or path.is_symlink():
            self._load_path(path)
            return False
        self._write(job)
        return True

    def save_response(self, update_id: int, response: AgentResponse) -> MessageJob:
        current = self._required(update_id)
        text = response.text.strip()
        if not text:
            raise JobStoreError("cannot persist an empty Telegram response")
        updated = replace(
            current,
            response_text=text,
            response_agent=response.agent,
            response_success=response.success,
            response_error_category=response.error_category,
            next_chunk=0,
            next_artifact=0,
            attempts=0,
            retry_at=0.0,
        )
        self._write(updated)
        return updated

    def advance_chunk(self, update_id: int, next_chunk: int) -> MessageJob:
        current = self._required(update_id)
        updated = replace(current, next_chunk=next_chunk)
        self._write(updated)
        return updated

    def advance_artifact(self, update_id: int, next_artifact: int) -> MessageJob:
        current = self._required(update_id)
        updated = replace(current, next_artifact=next_artifact)
        self._write(updated)
        return updated

    def defer(
        self,
        update_id: int,
        *,
        now: float | None = None,
        maximum_seconds: int = 300,
    ) -> MessageJob:
        current = self._required(update_id)
        attempts = min(current.attempts + 1, 20)
        delay = min(2 ** min(attempts, 8), maximum_seconds)
        updated = replace(
            current,
            attempts=attempts,
            retry_at=(time.time() if now is None else now) + delay,
        )
        self._write(updated)
        return updated

    def delete(self, update_id: int) -> None:
        path = self._path(update_id)
        if path.is_symlink():
            raise JobStoreError("Telegram job file is unsafe")
        path.unlink(missing_ok=True)

    def clear_all(self) -> int:
        """Delete every persisted inbox/outbox job and return the count."""

        if not self.directory.exists():
            return 0
        if not self.directory.is_absolute() or self.directory.is_symlink():
            raise JobStoreError("Telegram job directory is unsafe")
        deleted = 0
        for path in sorted(self.directory.iterdir()):
            if path.name.startswith(".telegram-job-"):
                if path.is_symlink() or not path.is_file():
                    raise JobStoreError("Telegram job file is unsafe")
                path.unlink()
                continue
            if path.suffix != ".json":
                continue
            self._load_path(path)
            path.unlink()
            deleted += 1
        return deleted

    def _required(self, update_id: int) -> MessageJob:
        job = self.get(update_id)
        if job is None:
            raise JobStoreError("Telegram job does not exist")
        return job

    def _path(self, update_id: int) -> Path:
        if isinstance(update_id, bool) or update_id < 0 or update_id > 10**20:
            raise JobStoreError("invalid Telegram update identifier")
        return self.directory / f"{update_id:020d}.json"

    def _load_path(self, path: Path) -> MessageJob:
        if path.is_symlink():
            raise JobStoreError("Telegram job file is unsafe")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            file_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(file_stat.st_mode)
                or stat.S_IMODE(file_stat.st_mode) != 0o600
                or file_stat.st_size > MAX_JOB_BYTES
            ):
                raise JobStoreError("Telegram job file is unsafe")
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                descriptor = -1
                data = json.load(handle)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JobStoreError("Telegram job file is invalid") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if not isinstance(data, dict):
            raise JobStoreError("Telegram job file is invalid")
        try:
            job = MessageJob(
                update_id=data["update_id"],
                chat_id=data["chat_id"],
                text=data["text"],
                response_text=data.get("response_text", ""),
                response_agent=data.get("response_agent", ""),
                response_success=data.get("response_success", False),
                response_error_category=data.get("response_error_category", ""),
                next_chunk=data.get("next_chunk", 0),
                next_artifact=data.get("next_artifact", 0),
                attempts=data.get("attempts", 0),
                retry_at=data.get("retry_at", 0.0),
            )
        except TypeError as exc:
            raise JobStoreError("Telegram job file is invalid") from exc
        self._validate(job)
        if path != self._path(job.update_id):
            raise JobStoreError("Telegram job filename does not match its identifier")
        return job

    def _validate(self, job: MessageJob) -> None:
        if (
            isinstance(job.update_id, bool)
            or not isinstance(job.update_id, int)
            or job.update_id < 0
            or job.update_id > 10**20
            or isinstance(job.chat_id, bool)
            or not isinstance(job.chat_id, int)
            or job.chat_id == 0
            or not isinstance(job.text, str)
            or not job.text.strip()
            or len(job.text) > MAX_MESSAGE_CHARS
            or not isinstance(job.response_text, str)
            or len(job.response_text) > MAX_RESPONSE_CHARS
            or not isinstance(job.response_agent, str)
            or len(job.response_agent) > 32
            or not isinstance(job.response_success, bool)
            or not isinstance(job.response_error_category, str)
            or len(job.response_error_category) > 80
            or isinstance(job.next_chunk, bool)
            or not isinstance(job.next_chunk, int)
            or job.next_chunk < 0
            or isinstance(job.next_artifact, bool)
            or not isinstance(job.next_artifact, int)
            or job.next_artifact < 0
            or isinstance(job.attempts, bool)
            or not isinstance(job.attempts, int)
            or job.attempts < 0
            or job.attempts > 20
            or isinstance(job.retry_at, bool)
            or not isinstance(job.retry_at, (int, float))
            or job.retry_at < 0
            or not math.isfinite(job.retry_at)
        ):
            raise JobStoreError("Telegram job is invalid")

    def _write(self, job: MessageJob) -> None:
        self._validate(job)
        if not self.directory.is_absolute() or self.directory.is_symlink():
            raise JobStoreError("Telegram job directory is unsafe")
        ensure_private_directories((self.directory,))
        path = self._path(job.update_id)
        if path.is_symlink():
            raise JobStoreError("Telegram job file is unsafe")
        descriptor, temporary = tempfile.mkstemp(
            prefix=".telegram-job-",
            dir=self.directory,
        )
        temp_path = Path(temporary)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                json.dump(
                    asdict(job),
                    handle,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            if temp_path.stat().st_size > MAX_JOB_BYTES:
                raise JobStoreError("Telegram job file is too large")
            os.replace(temp_path, path)
            os.chmod(path, 0o600)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temp_path.unlink(missing_ok=True)
