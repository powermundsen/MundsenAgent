"""Text-only Telegram runtime with allowlisting, queueing, and cancellation."""

from __future__ import annotations

import json
import os
import queue
import stat
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from mundsen_agent.audit import AuditLogger
from mundsen_agent.bridge.attachments import AttachmentError, AttachmentProcessor
from mundsen_agent.bridge.job_store import (
    JobStoreError,
    MessageJob,
    PersistentJobStore,
)
from mundsen_agent.bridge.telegram_client import TelegramClient, TelegramError
from mundsen_agent.bridge.telegram_format import (
    chunk_markdown,
    markdown_to_telegram_html,
)
from mundsen_agent.bridge.visualizations import VisualizationRenderer
from mundsen_agent.paths import ensure_private_directories
from mundsen_agent.router.models import AgentResponse


class MessageRouter(Protocol):
    def route(
        self,
        message: str,
        *,
        cancel_event: threading.Event | None = None,
    ) -> AgentResponse: ...


class ScheduledNotification(Protocol):
    key: str
    text: str


class OffsetStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> int:
        if not self.path.exists():
            return 0
        if not self.path.is_absolute() or self.path.is_symlink():
            raise ValueError("unsafe Telegram offset path")
        if stat.S_IMODE(self.path.stat().st_mode) != 0o600:
            raise ValueError("Telegram offset must use mode 0600")
        try:
            value = int(json.loads(self.path.read_text(encoding="utf-8"))["offset"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("invalid Telegram offset") from exc
        if value < 0:
            raise ValueError("invalid Telegram offset")
        return value

    def save(self, offset: int) -> None:
        if offset < 0 or not self.path.is_absolute() or self.path.is_symlink():
            raise ValueError("unsafe Telegram offset")
        ensure_private_directories((self.path.parent,))
        descriptor, temporary = tempfile.mkstemp(
            prefix=".telegram-offset-",
            dir=self.path.parent,
        )
        temp_path = Path(temporary)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                json.dump({"offset": offset}, handle, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


class TelegramBridge:
    def __init__(
        self,
        *,
        client: TelegramClient,
        router: MessageRouter,
        allowed_chat_id: int,
        offset_store: OffsetStore,
        audit: AuditLogger,
        queue_size: int = 20,
        language: str = "nb",
        opening_message: Callable[[], AgentResponse | None] | None = None,
        opening_delivered: Callable[[], None] | None = None,
        job_store: PersistentJobStore | None = None,
        typing_interval_seconds: float = 4.0,
        scheduled_notifications: Callable[
            [], list[ScheduledNotification]
        ] | None = None,
        scheduled_delivered: Callable[[str], None] | None = None,
        scheduler_interval_seconds: float = 300.0,
        attachment_processor: AttachmentProcessor | None = None,
        visualization_renderer: VisualizationRenderer | None = None,
        live_progress: bool = False,
        progress_interval_seconds: float = 45.0,
    ) -> None:
        if allowed_chat_id == 0:
            raise ValueError("allowed_chat_id is required")
        self.client = client
        self.router = router
        self.allowed_chat_id = allowed_chat_id
        self.offset_store = offset_store
        self.audit = audit
        self.language = language
        self.opening_message = opening_message
        self.opening_delivered = opening_delivered
        self.job_store = job_store
        if typing_interval_seconds <= 0 or scheduler_interval_seconds <= 0:
            raise ValueError("runtime intervals must be positive")
        self.typing_interval_seconds = typing_interval_seconds
        self.scheduled_notifications = scheduled_notifications
        self.scheduled_delivered = scheduled_delivered
        self.scheduler_interval_seconds = scheduler_interval_seconds
        self.attachment_processor = attachment_processor
        self.visualization_renderer = visualization_renderer
        self.live_progress = live_progress
        if progress_interval_seconds < 10 or progress_interval_seconds > 300:
            raise ValueError("progress interval must be between 10 and 300 seconds")
        self.progress_interval_seconds = progress_interval_seconds
        self.jobs: queue.Queue[MessageJob] = queue.Queue(maxsize=queue_size)
        self._queued_ids: set[int] = set()
        self._queue_lock = threading.Lock()
        self._local_update_id = int(time.time_ns())
        self.stop_event = threading.Event()
        self.current_cancel: threading.Event | None = None
        self._current_lock = threading.Lock()
        self.worker = threading.Thread(
            target=self._worker_loop,
            name="mundsen-agent-worker",
            daemon=True,
        )
        self._enqueue_persisted_jobs()

    def run_forever(self) -> None:
        self.worker.start()
        offset = self.offset_store.load()
        delay = 1.0
        while not self.stop_event.is_set():
            try:
                updates = self.client.get_updates(offset=offset)
                delay = 1.0
            except TelegramError as exc:
                self.audit.emit(
                    "telegram",
                    "poll_failed",
                    error_category=exc.category,
                    http_status=exc.http_status or None,
                    success=False,
                )
                self.stop_event.wait(delay)
                delay = min(delay * 2, 30)
                continue
            for update in updates:
                update_id = int(update.get("update_id", -1) or -1)
                if update_id < 0:
                    continue
                try:
                    accepted = self.process_update(update)
                except (JobStoreError, OSError, ValueError):
                    self.audit.emit(
                        "telegram",
                        "inbox_persist_failed",
                        error_category="local_state_error",
                        success=False,
                    )
                    accepted = False
                if not accepted:
                    break
                offset = max(offset, update_id + 1)
                self.offset_store.save(offset)

    def stop(self) -> None:
        self.stop_event.set()
        with self._current_lock:
            if self.current_cancel is not None:
                self.current_cancel.set()

    def process_update(self, update: dict[str, object]) -> bool:
        message = update.get("message")
        if not isinstance(message, dict):
            return True
        chat = message.get("chat")
        chat_id = int(chat.get("id", 0) or 0) if isinstance(chat, dict) else 0
        if chat_id != self.allowed_chat_id:
            self.audit.emit(
                "telegram",
                "chat_rejected",
                success=False,
                reason_code="chat_not_allowlisted",
            )
            return True
        raw_update_id = update.get("update_id")
        if isinstance(raw_update_id, bool) or not isinstance(raw_update_id, int):
            self._local_update_id += 1
            update_id = self._local_update_id
        else:
            update_id = raw_update_id
        attachment_count = 0
        if self.attachment_processor is not None:
            try:
                inbound = self.attachment_processor.process(update_id, message)
                text = inbound.text
                attachment_count = inbound.attachment_count
            except AttachmentError as exc:
                self.attachment_processor.cleanup_job(update_id)
                self.audit.emit(
                    "telegram",
                    "attachment_failed",
                    error_category=exc.category,
                    success=False,
                )
                self._send_plain(
                    chat_id,
                    self._text(
                        "Vedlegget kunne ikke behandles lokalt.",
                        "The attachment could not be processed locally.",
                    ),
                )
                return True
        else:
            raw_text = message.get("text")
            text = raw_text if isinstance(raw_text, str) else ""
        if not text.strip():
            self._send_plain(
                chat_id,
                self._text(
                    "Foreløpig støttes bare tekstmeldinger.",
                    "Only text messages are supported for now.",
                ),
            )
            return True
        command = text.strip().split(maxsplit=1)[0].lower()
        if command in {"/kill", "/stop"}:
            self._cancel_current_and_pending()
            if command == "/stop":
                # AgentRouter serialiserer state med samme lås som en aktiv
                # provider-route. Dermed skjer sesjonsresetten etter at den
                # avbrutte prosessen har returnert og kan ikke skrives tilbake.
                self.router.route("/stop")
            self._send_plain(
                chat_id,
                self._text(
                    (
                        "Aktiv jobb stoppes, køen tømmes og begge "
                        "agent-sesjonene lukkes."
                        if command == "/stop"
                        else "Aktiv jobb stoppes."
                    ),
                    (
                        "Stopping the active job, clearing the queue, and "
                        "closing both agent sessions."
                        if command == "/stop"
                        else "Stopping the active job."
                    ),
                ),
            )
            return True
        job = MessageJob(
            update_id=update_id,
            chat_id=chat_id,
            text=text,
        )
        if self.job_store is not None:
            self.job_store.put_if_absent(job)
            job = self.job_store.get(update_id) or job
        queued = self._enqueue(job)
        if not queued and self.job_store is None:
            self._cleanup_job(update_id)
            self._send_plain(
                chat_id,
                self._text(
                    "Køen er full. Prøv igjen om litt.",
                    "The queue is full. Please try again shortly.",
                ),
            )
            return True
        self.audit.emit(
            "telegram",
            "message_queued",
            queue_depth=self.jobs.qsize(),
            attachment_count=attachment_count,
            success=True,
        )
        return True

    def process_one_job(self, *, block: bool = False) -> bool:
        try:
            job = self.jobs.get(block=block, timeout=0.2 if block else None)
        except queue.Empty:
            return False
        cancel_event = threading.Event()
        with self._current_lock:
            self.current_cancel = cancel_event
        started = time.monotonic()
        delivered = False
        response: AgentResponse | None = None
        try:
            if job.response_ready:
                response = AgentResponse(
                    text=job.response_text,
                    agent=job.response_agent,
                    success=job.response_success,
                    error_category=job.response_error_category,
                )
            else:
                response = self._route_with_typing(job, cancel_event)
                if self.job_store is not None:
                    job = self.job_store.save_response(job.update_id, response)
            self._deliver_job(job, response)
            delivered = True
            if self.job_store is not None:
                self.job_store.delete(job.update_id)
            self._cleanup_job(job.update_id)
            self.audit.emit(
                "telegram",
                "response_sent",
                agent=response.agent,
                duration_ms=int((time.monotonic() - started) * 1000),
                success=response.success,
                error_category=response.error_category or None,
            )
        except TelegramError as exc:
            if self.job_store is not None:
                self.job_store.defer(job.update_id)
            self.audit.emit(
                "telegram",
                "send_failed",
                error_category=exc.category,
                http_status=exc.http_status or None,
                success=False,
            )
        except (JobStoreError, OSError, ValueError):
            if self.job_store is not None:
                try:
                    self.job_store.defer(job.update_id)
                except JobStoreError:
                    pass
            self.audit.emit(
                "telegram",
                "worker_failed",
                error_category="local_state_error",
                success=False,
            )
        finally:
            with self._current_lock:
                self.current_cancel = None
            with self._queue_lock:
                self._queued_ids.discard(job.update_id)
            self.jobs.task_done()
            if self.job_store is not None:
                self._enqueue_persisted_jobs()
        return True

    def _route_with_typing(
        self,
        job: MessageJob,
        cancel_event: threading.Event,
    ) -> AgentResponse:
        pulse_stop = threading.Event()
        pulse = threading.Thread(
            target=self._typing_pulse,
            args=(job.chat_id, pulse_stop),
            name="mundsen-typing-pulse",
            daemon=True,
        )
        pulse.start()
        progress_stop = threading.Event()
        progress_id = 0
        progress: threading.Thread | None = None
        if self.live_progress:
            try:
                progress_id = self.client.send_message(
                    job.chat_id,
                    self._text("Tenker…", "Thinking…"),
                )
            except TelegramError:
                progress_id = 0
            if progress_id:
                progress = threading.Thread(
                    target=self._progress_pulse,
                    args=(job.chat_id, progress_id, progress_stop),
                    name="mundsen-progress-pulse",
                    daemon=True,
                )
                progress.start()
        try:
            try:
                return self.router.route(
                    job.text,
                    cancel_event=cancel_event,
                )
            except Exception:
                self.audit.emit(
                    "telegram",
                    "worker_failed",
                    error_category="internal_error",
                    success=False,
                )
                return AgentResponse(
                    text=self._text(
                        "Det oppsto en lokal teknisk feil. Detaljene er logget.",
                        "A local technical error occurred. Details were logged.",
                    ),
                    agent="router",
                    success=False,
                    error_category="internal_error",
                )
        finally:
            pulse_stop.set()
            pulse.join(timeout=1)
            progress_stop.set()
            if progress is not None:
                progress.join(timeout=1)
            if progress_id:
                try:
                    self.client.delete_message(job.chat_id, progress_id)
                except TelegramError:
                    pass

    def _typing_pulse(
        self,
        chat_id: int,
        pulse_stop: threading.Event,
    ) -> None:
        while not pulse_stop.is_set():
            try:
                self.client.send_typing(chat_id)
            except TelegramError:
                pass
            if pulse_stop.wait(self.typing_interval_seconds):
                break

    def _progress_pulse(
        self,
        chat_id: int,
        message_id: int,
        progress_stop: threading.Event,
    ) -> None:
        started = time.monotonic()
        while not progress_stop.wait(self.progress_interval_seconds):
            elapsed = max(int(time.monotonic() - started), 1)
            text = self._text(
                f"Jobber fortsatt, {elapsed} sekunder…",
                f"Still working, {elapsed} seconds…",
            )
            try:
                self.client.edit_message(chat_id, message_id, text)
            except TelegramError:
                return

    def _deliver_job(
        self,
        job: MessageJob,
        response: AgentResponse,
    ) -> None:
        safe_text = response.text.strip() or self._text(
            "Agenten returnerte ikke noe svar.",
            "The agent returned no response.",
        )
        artifacts = ()
        if self.visualization_renderer is not None:
            rendered = self.visualization_renderer.render(job.update_id, safe_text)
            safe_text = rendered.text
            artifacts = rendered.artifacts
        if job.next_artifact > len(artifacts):
            raise JobStoreError("Telegram artifact position is invalid")
        for index in range(job.next_artifact, len(artifacts)):
            self.client.send_document(job.chat_id, artifacts[index])
            if self.job_store is not None:
                self.job_store.advance_artifact(job.update_id, index + 1)
        chunks = chunk_markdown(safe_text)
        if job.next_chunk > len(chunks):
            raise JobStoreError("Telegram outbox position is invalid")
        for index in range(job.next_chunk, len(chunks)):
            self.client.send_message(
                job.chat_id,
                markdown_to_telegram_html(chunks[index]),
            )
            if self.job_store is not None:
                self.job_store.advance_chunk(job.update_id, index + 1)

    def send_opening_message(self) -> bool:
        """Let the assistant speak first, once, before any owner message."""

        if self.opening_message is None:
            return False
        try:
            response = self.opening_message()
        except Exception:
            self.audit.emit(
                "telegram",
                "opening_failed",
                error_category="internal_error",
                success=False,
            )
            return False
        if response is None or not response.success or not response.text.strip():
            return False
        try:
            self._send_markdown(self.allowed_chat_id, response.text)
        except TelegramError as exc:
            self.audit.emit(
                "telegram",
                "send_failed",
                error_category=exc.category,
                http_status=exc.http_status or None,
                success=False,
            )
            return False
        if self.opening_delivered is not None:
            try:
                self.opening_delivered()
            except Exception:
                self.audit.emit(
                    "telegram",
                    "opening_state_failed",
                    error_category="local_state_error",
                    success=False,
                )
        self.audit.emit(
            "telegram",
            "opening_sent",
            agent=response.agent,
            success=True,
        )
        return True

    def _worker_loop(self) -> None:
        if self.jobs.empty():
            self.send_opening_message()
        self._send_scheduled_notifications()
        next_schedule = time.monotonic() + self.scheduler_interval_seconds
        while not self.stop_event.is_set():
            try:
                self.process_one_job(block=True)
                self._enqueue_persisted_jobs()
                if time.monotonic() >= next_schedule:
                    self._send_scheduled_notifications()
                    next_schedule = (
                        time.monotonic() + self.scheduler_interval_seconds
                    )
            except Exception:
                self.audit.emit(
                    "telegram",
                    "worker_loop_failed",
                    error_category="internal_error",
                    success=False,
                )
                self.stop_event.wait(0.5)

    def _enqueue(self, job: MessageJob) -> bool:
        with self._queue_lock:
            if job.update_id in self._queued_ids:
                return True
            try:
                self.jobs.put_nowait(job)
            except queue.Full:
                return False
            self._queued_ids.add(job.update_id)
            return True

    def _enqueue_persisted_jobs(self) -> None:
        if self.job_store is None:
            return
        for job in self.job_store.load_all():
            if not self._enqueue(job):
                break

    def _send_scheduled_notifications(self) -> None:
        if self.scheduled_notifications is None:
            return
        try:
            notifications = self.scheduled_notifications()
        except Exception:
            self.audit.emit(
                "scheduler",
                "check_failed",
                error_category="local_state_error",
                success=False,
            )
            return
        for notification in notifications:
            try:
                self._send_markdown(self.allowed_chat_id, notification.text)
            except TelegramError as exc:
                self.audit.emit(
                    "scheduler",
                    "send_failed",
                    error_category=exc.category,
                    success=False,
                )
                return
            if self.scheduled_delivered is not None:
                try:
                    self.scheduled_delivered(notification.key)
                except Exception:
                    self.audit.emit(
                        "scheduler",
                        "delivery_state_failed",
                        error_category="local_state_error",
                        success=False,
                    )
                    return
            self.audit.emit(
                "scheduler",
                "notification_sent",
                success=True,
            )

    def _cancel_current_and_pending(self) -> None:
        with self._current_lock:
            if self.current_cancel is not None:
                self.current_cancel.set()
        cleared_ids: set[int] = set()
        while True:
            try:
                job = self.jobs.get_nowait()
                self.jobs.task_done()
                with self._queue_lock:
                    self._queued_ids.discard(job.update_id)
                if self.job_store is not None:
                    self.job_store.delete(job.update_id)
                self._cleanup_job(job.update_id)
                cleared_ids.add(job.update_id)
            except queue.Empty:
                break
        persisted_jobs = self.job_store.list_all() if self.job_store is not None else []
        for job in persisted_jobs:
            self._cleanup_job(job.update_id)
        persisted = self.job_store.clear_all() if self.job_store is not None else 0
        self.audit.emit(
            "telegram",
            "queue_cancelled",
            queue_depth=len(cleared_ids) + persisted,
            success=True,
        )

    def _cleanup_job(self, update_id: int) -> None:
        if self.attachment_processor is not None:
            self.attachment_processor.cleanup_job(update_id)
        if self.visualization_renderer is not None:
            self.visualization_renderer.cleanup_job(update_id)

    def _send_markdown(self, chat_id: int, text: str) -> None:
        safe_text = text.strip() or self._text(
            "Agenten returnerte ikke noe svar.",
            "The agent returned no response.",
        )
        for chunk in chunk_markdown(safe_text):
            self.client.send_message(
                chat_id,
                markdown_to_telegram_html(chunk),
            )

    def _send_plain(self, chat_id: int, text: str) -> None:
        try:
            self.client.send_message(
                chat_id,
                markdown_to_telegram_html(text),
            )
        except TelegramError:
            self.audit.emit(
                "telegram",
                "send_failed",
                error_category="telegram_error",
                success=False,
            )

    def _text(self, norwegian: str, english: str) -> str:
        return english if self.language.lower().startswith("en") else norwegian
