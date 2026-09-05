from __future__ import annotations

import tempfile
import time
import unittest
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from mundsen_agent.audit import AuditLogger
from mundsen_agent.bridge.job_store import MessageJob, PersistentJobStore
from mundsen_agent.bridge.telegram_client import TelegramError
from mundsen_agent.bridge.runtime import OffsetStore, TelegramBridge
from mundsen_agent.bridge.visualizations import VisualizationRenderer
from mundsen_agent.paths import MundsenAgentPaths
from mundsen_agent.router.models import AgentResponse


class FakeClient:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []
        self.typing: list[int] = []
        self.edits: list[tuple[int, int, str]] = []
        self.deleted: list[tuple[int, int]] = []
        self.documents: list[tuple[int, str]] = []

    def send_message(self, chat_id: int, html_text: str) -> int:
        self.messages.append((chat_id, html_text))
        return len(self.messages)

    def send_typing(self, chat_id: int) -> None:
        self.typing.append(chat_id)

    def edit_message(self, chat_id: int, message_id: int, html_text: str) -> None:
        self.edits.append((chat_id, message_id, html_text))

    def delete_message(self, chat_id: int, message_id: int) -> None:
        self.deleted.append((chat_id, message_id))

    def send_document(self, chat_id: int, path: Path) -> int:
        self.documents.append((chat_id, path.name))
        return len(self.documents)


class FakeRouter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Event | None]] = []

    def route(
        self,
        message: str,
        *,
        cancel_event: Event | None = None,
    ) -> AgentResponse:
        self.calls.append((message, cancel_event))
        return AgentResponse("**Svar**", "claude", True)


class BrokenRouter:
    def route(
        self,
        message: str,
        *,
        cancel_event: Event | None = None,
    ) -> AgentResponse:
        del message, cancel_event
        raise RuntimeError("secret internal detail")


class VisualRouter:
    def route(
        self,
        message: str,
        *,
        cancel_event: Event | None = None,
    ) -> AgentResponse:
        del message, cancel_event
        return AgentResponse(
            "```svg\n<svg><circle cx='1' cy='1' r='1'/></svg>\n```",
            "claude",
            True,
        )

class FailingSendClient(FakeClient):
    def send_message(self, chat_id: int, html_text: str) -> int:
        del chat_id, html_text
        raise TelegramError("network_error")

class FlakyClient(FakeClient):
    def __init__(self, *, fail_on_call: int) -> None:
        super().__init__()
        self.fail_on_call = fail_on_call
        self.calls = 0

    def send_message(self, chat_id: int, html_text: str) -> int:
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise TelegramError("network_error")
        return super().send_message(chat_id, html_text)


@dataclass(frozen=True)
class FakeNotification:
    key: str
    text: str


class TestTelegramRuntime(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        self.client = FakeClient()
        self.router = FakeRouter()
        self.bridge = TelegramBridge(
            client=self.client,  # type: ignore[arg-type]
            router=self.router,
            allowed_chat_id=1001,
            offset_store=OffsetStore(root / "state" / "offset.json"),
            audit=AuditLogger(root / "logs" / "audit.jsonl"),
        )

    def test_unknown_chat_is_rejected_before_queue(self) -> None:
        self.bridge.process_update(
            {"message": {"chat": {"id": 9999}, "text": "private"}}
        )

        self.assertTrue(self.bridge.jobs.empty())
        self.assertEqual(self.router.calls, [])
        self.assertEqual(self.client.messages, [])

    def test_allowed_text_is_queued_routed_and_formatted(self) -> None:
        self.bridge.process_update(
            {"message": {"chat": {"id": 1001}, "text": "Hei"}}
        )
        processed = self.bridge.process_one_job()

        self.assertTrue(processed)
        self.assertEqual(self.router.calls[0][0], "Hei")
        self.assertEqual(self.client.messages, [(1001, "<b>Svar</b>")])
        self.assertEqual(self.client.typing, [1001])

    def test_kill_sets_active_cancel_and_clears_queue(self) -> None:
        self.bridge.process_update(
            {"message": {"chat": {"id": 1001}, "text": "første"}}
        )
        self.bridge.process_update(
            {"message": {"chat": {"id": 1001}, "text": "fortsett"}}
        )
        active = Event()
        self.bridge.current_cancel = active

        self.bridge.process_update(
            {"message": {"chat": {"id": 1001}, "text": "/kill"}}
        )

        self.assertTrue(active.is_set())
        self.assertTrue(self.bridge.jobs.empty())
        self.assertIn("stoppes", self.client.messages[-1][1])

    def test_kill_clears_deferred_persisted_jobs(self) -> None:
        root = Path(self.tempdir.name)
        store = PersistentJobStore(root / "state" / "queue")
        bridge = TelegramBridge(
            client=self.client,  # type: ignore[arg-type]
            router=self.router,
            allowed_chat_id=1001,
            offset_store=self.bridge.offset_store,
            audit=self.bridge.audit,
            job_store=store,
        )
        bridge.process_update(
            {
                "update_id": 42,
                "message": {"chat": {"id": 1001}, "text": "utsatt"},
            }
        )
        store.defer(42, now=time.time())

        bridge.process_update(
            {
                "update_id": 43,
                "message": {"chat": {"id": 1001}, "text": "/kill"},
            }
        )

        self.assertEqual(store.load_all(now=time.time() + 1000), [])

    def test_stop_cancels_queue_and_requests_both_session_closures(self) -> None:
        self.bridge.process_update(
            {"message": {"chat": {"id": 1001}, "text": "første"}}
        )
        self.bridge.process_update(
            {"message": {"chat": {"id": 1001}, "text": "fortsett"}}
        )
        active = Event()
        self.bridge.current_cancel = active

        self.bridge.process_update(
            {"message": {"chat": {"id": 1001}, "text": "/stop"}}
        )

        self.assertTrue(active.is_set())
        self.assertTrue(self.bridge.jobs.empty())
        self.assertEqual(self.router.calls[-1][0], "/stop")
        self.assertIn("begge agent-sesjonene lukkes", self.client.messages[-1][1])

    def test_non_text_message_gets_short_supported_type_response(self) -> None:
        self.bridge.process_update(
            {"message": {"chat": {"id": 1001}, "photo": [{}]}}
        )

        self.assertIn("tekstmeldinger", self.client.messages[-1][1])

    def test_live_progress_is_opt_in_and_removed_after_response(self) -> None:
        root = Path(self.tempdir.name)
        bridge = TelegramBridge(
            client=self.client,  # type: ignore[arg-type]
            router=self.router,
            allowed_chat_id=1001,
            offset_store=OffsetStore(root / "state" / "progress-offset.json"),
            audit=self.bridge.audit,
            live_progress=True,
            progress_interval_seconds=10,
        )
        bridge.process_update(
            {"update_id": 44, "message": {"chat": {"id": 1001}, "text": "Hei"}}
        )

        bridge.process_one_job()

        self.assertIn("Tenker", self.client.messages[0][1])
        self.assertEqual(self.client.deleted, [(1001, 1)])
        self.assertIn("Svar", self.client.messages[-1][1])

    def test_inline_visualization_is_sent_and_cleaned(self) -> None:
        root = Path(self.tempdir.name)
        renderer = VisualizationRenderer(
            MundsenAgentPaths.from_root(root / "Mundsen Agent"),
            language="nb",
        )
        bridge = TelegramBridge(
            client=self.client,  # type: ignore[arg-type]
            router=VisualRouter(),
            allowed_chat_id=1001,
            offset_store=OffsetStore(root / "state" / "visual-offset.json"),
            audit=self.bridge.audit,
            visualization_renderer=renderer,
        )
        bridge.process_update(
            {"update_id": 45, "message": {"chat": {"id": 1001}, "text": "Vis"}}
        )

        bridge.process_one_job()

        self.assertEqual(self.client.documents, [(1001, "visualization-1.svg")])
        self.assertIn("se vedlegg", self.client.messages[-1][1])
        self.assertFalse((renderer.paths.attachments_dir / "job-45").exists())

    def test_offset_store_is_private(self) -> None:
        store = self.bridge.offset_store
        store.save(42)

        self.assertEqual(store.load(), 42)
        self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)

    def test_router_exception_is_sanitized_and_next_job_can_run(self) -> None:
        self.bridge.router = BrokenRouter()  # type: ignore[assignment]
        self.bridge.process_update(
            {"message": {"chat": {"id": 1001}, "text": "første"}}
        )

        self.assertTrue(self.bridge.process_one_job())
        self.assertIn("teknisk feil", self.client.messages[-1][1])
        self.assertNotIn("secret", self.client.messages[-1][1])

        self.bridge.router = self.router
        self.bridge.process_update(
            {"message": {"chat": {"id": 1001}, "text": "fortsett"}}
        )
        self.assertTrue(self.bridge.process_one_job())
        self.assertEqual(self.router.calls[-1][0], "fortsett")

    def test_opening_is_acknowledged_only_after_successful_send(self) -> None:
        delivered: list[bool] = []
        bridge = TelegramBridge(
            client=self.client,  # type: ignore[arg-type]
            router=self.router,
            allowed_chat_id=1001,
            offset_store=self.bridge.offset_store,
            audit=self.bridge.audit,
            opening_message=lambda: AgentResponse(
                "Hei", "claude", True
            ),
            opening_delivered=lambda: delivered.append(True),
        )

        self.assertTrue(bridge.send_opening_message())
        self.assertEqual(delivered, [True])

        failed = TelegramBridge(
            client=FailingSendClient(),  # type: ignore[arg-type]
            router=self.router,
            allowed_chat_id=1001,
            offset_store=self.bridge.offset_store,
            audit=self.bridge.audit,
            opening_message=lambda: AgentResponse(
                "Hei", "claude", True
            ),
            opening_delivered=lambda: delivered.append(False),
        )
        self.assertFalse(failed.send_opening_message())
        self.assertEqual(delivered, [True])

    def test_persisted_inbox_survives_bridge_restart(self) -> None:
        root = Path(self.tempdir.name)
        store = PersistentJobStore(root / "state" / "queue")
        first = TelegramBridge(
            client=self.client,  # type: ignore[arg-type]
            router=self.router,
            allowed_chat_id=1001,
            offset_store=self.bridge.offset_store,
            audit=self.bridge.audit,
            job_store=store,
        )
        first.process_update(
            {
                "update_id": 42,
                "message": {"chat": {"id": 1001}, "text": "Hei"},
            }
        )
        self.assertIsNotNone(store.get(42))

        restarted_router = FakeRouter()
        restarted = TelegramBridge(
            client=self.client,  # type: ignore[arg-type]
            router=restarted_router,
            allowed_chat_id=1001,
            offset_store=self.bridge.offset_store,
            audit=self.bridge.audit,
            job_store=store,
        )
        self.assertTrue(restarted.process_one_job())
        self.assertEqual(restarted_router.calls[0][0], "Hei")
        self.assertIsNone(store.get(42))

    def test_persisted_owner_message_preempts_opening_after_restart(self) -> None:
        root = Path(self.tempdir.name)
        store = PersistentJobStore(root / "state" / "queue")
        store.put_if_absent(MessageJob(42, 1001, "Hei"))
        opening_calls: list[bool] = []
        restarted = TelegramBridge(
            client=self.client,  # type: ignore[arg-type]
            router=self.router,
            allowed_chat_id=1001,
            offset_store=self.bridge.offset_store,
            audit=self.bridge.audit,
            job_store=store,
            opening_message=lambda: opening_calls.append(True)
            or AgentResponse("Uoppfordret", "claude", True),
        )
        restarted.stop_event.set()

        restarted._worker_loop()

        self.assertEqual(opening_calls, [])
        self.assertEqual(self.client.messages, [])

    def test_failed_send_retries_saved_response_without_rerouting(self) -> None:
        root = Path(self.tempdir.name)
        store = PersistentJobStore(root / "state" / "queue")
        router = FakeRouter()
        failed = TelegramBridge(
            client=FlakyClient(fail_on_call=1),  # type: ignore[arg-type]
            router=router,
            allowed_chat_id=1001,
            offset_store=self.bridge.offset_store,
            audit=self.bridge.audit,
            job_store=store,
        )
        failed.process_update(
            {
                "update_id": 43,
                "message": {"chat": {"id": 1001}, "text": "Hei"},
            }
        )
        self.assertTrue(failed.process_one_job())
        saved = store.get(43)
        self.assertIsNotNone(saved)
        self.assertTrue(saved.response_ready)
        self.assertEqual(len(router.calls), 1)

        store.defer(43, now=0)
        no_reroute = FakeRouter()
        delivered = TelegramBridge(
            client=self.client,  # type: ignore[arg-type]
            router=no_reroute,
            allowed_chat_id=1001,
            offset_store=self.bridge.offset_store,
            audit=self.bridge.audit,
            job_store=store,
        )
        self.assertTrue(delivered.process_one_job())
        self.assertEqual(no_reroute.calls, [])
        self.assertIsNone(store.get(43))

    def test_partial_multichunk_send_resumes_at_next_chunk(self) -> None:
        root = Path(self.tempdir.name)
        store = PersistentJobStore(root / "state" / "queue")

        class LongRouter(FakeRouter):
            def route(
                self,
                message: str,
                *,
                cancel_event: Event | None = None,
            ) -> AgentResponse:
                self.calls.append((message, cancel_event))
                return AgentResponse("A" * 7000, "claude", True)

        router = LongRouter()
        failed_client = FlakyClient(fail_on_call=2)
        failed = TelegramBridge(
            client=failed_client,  # type: ignore[arg-type]
            router=router,
            allowed_chat_id=1001,
            offset_store=self.bridge.offset_store,
            audit=self.bridge.audit,
            job_store=store,
        )
        failed.process_update(
            {
                "update_id": 44,
                "message": {"chat": {"id": 1001}, "text": "Langt svar"},
            }
        )
        failed.process_one_job()
        saved = store.get(44)
        self.assertIsNotNone(saved)
        self.assertEqual(saved.next_chunk, 1)

        store.defer(44, now=0)
        restarted = TelegramBridge(
            client=self.client,  # type: ignore[arg-type]
            router=FakeRouter(),
            allowed_chat_id=1001,
            offset_store=self.bridge.offset_store,
            audit=self.bridge.audit,
            job_store=store,
        )
        restarted.process_one_job()
        self.assertEqual(len(self.client.messages), 1)
        self.assertIsNone(store.get(44))

    def test_scheduler_marks_only_successful_delivery(self) -> None:
        delivered: list[str] = []
        bridge = TelegramBridge(
            client=self.client,  # type: ignore[arg-type]
            router=self.router,
            allowed_chat_id=1001,
            offset_store=self.bridge.offset_store,
            audit=self.bridge.audit,
            scheduled_notifications=lambda: [
                FakeNotification("reminder:1:due", "Påminnelse")
            ],
            scheduled_delivered=delivered.append,
        )
        bridge._send_scheduled_notifications()
        self.assertEqual(delivered, ["reminder:1:due"])


if __name__ == "__main__":
    unittest.main()
