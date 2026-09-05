from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from typing import Any

from mundsen_agent.bridge.telegram_client import TelegramClient, TelegramError

TOKEN = "123456789:abcdefghijklmnopqrstuvwxyzABCDE"


class RecordingTransport:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.urls: list[str] = []
        self.payloads: list[dict[str, Any]] = []

    def __call__(self, request: Any, timeout: float) -> bytes:
        self.urls.append(request.full_url)
        self.payloads.append(json.loads(request.data))
        return json.dumps(self.response).encode()


class TestTelegramClient(unittest.TestCase):
    def test_send_message_uses_html_and_returns_message_id(self) -> None:
        transport = RecordingTransport(
            {"ok": True, "result": {"message_id": 42}}
        )
        client = TelegramClient(TOKEN, transport=transport)

        message_id = client.send_message(1001, "<b>Hei</b>")

        self.assertEqual(message_id, 42)
        self.assertEqual(transport.payloads[0]["parse_mode"], "HTML")
        self.assertEqual(transport.payloads[0]["chat_id"], 1001)

    def test_http_error_never_contains_token(self) -> None:
        def failing_transport(request: Any, timeout: float) -> bytes:
            raise urllib.error.HTTPError(
                request.full_url,
                401,
                f"token={TOKEN}",
                {},
                None,
            )

        client = TelegramClient(TOKEN, transport=failing_transport)

        with self.assertRaises(TelegramError) as raised:
            client.get_me()
        self.assertNotIn(TOKEN, str(raised.exception))
        self.assertEqual(raised.exception.http_status, 401)

    def test_invalid_token_and_method_are_rejected_before_network(self) -> None:
        with self.assertRaises(ValueError):
            TelegramClient("not-a-token")
        client = TelegramClient(TOKEN, transport=RecordingTransport({"ok": True}))
        with self.assertRaises(ValueError):
            client.call("../getMe")

    def test_file_download_is_bounded(self) -> None:
        class FileTransport:
            def __call__(self, request: Any, timeout: float) -> bytes:
                del timeout
                if request.full_url.endswith("/getFile"):
                    return json.dumps(
                        {
                            "ok": True,
                            "result": {
                                "file_path": "voice/example.ogg",
                                "file_size": 3,
                            },
                        }
                    ).encode()
                return b"abc"

        client = TelegramClient(TOKEN, transport=FileTransport())
        path, size = client.get_file("example_file_id_123")

        self.assertEqual((path, size), ("voice/example.ogg", 3))
        self.assertEqual(client.download_file(path, maximum_bytes=3), b"abc")
        with self.assertRaises(TelegramError):
            client.download_file(path, maximum_bytes=2)

    def test_invalid_file_size_is_a_sanitized_telegram_error(self) -> None:
        def transport(request: Any, timeout: float) -> bytes:
            del request, timeout
            return json.dumps(
                {
                    "ok": True,
                    "result": {
                        "file_path": "voice/example.ogg",
                        "file_size": "not-a-number",
                    },
                }
            ).encode()

        client = TelegramClient(TOKEN, transport=transport)
        with self.assertRaises(TelegramError) as raised:
            client.get_file("example_file_id_123")
        self.assertEqual(raised.exception.category, "invalid_response")

    def test_send_document_uses_multipart_without_token_in_body(self) -> None:
        class MultipartTransport:
            def __init__(self) -> None:
                self.body = b""
                self.content_type = ""

            def __call__(self, request: Any, timeout: float) -> bytes:
                del timeout
                self.body = request.data
                self.content_type = request.headers["Content-type"]
                return json.dumps(
                    {"ok": True, "result": {"message_id": 42}}
                ).encode()

        with tempfile.TemporaryDirectory() as temp:
            document = Path(temp) / "diagram.svg"
            document.write_text("<svg/>", encoding="utf-8")
            transport = MultipartTransport()
            client = TelegramClient(TOKEN, transport=transport)

            message_id = client.send_document(1001, document)

        self.assertEqual(message_id, 42)
        self.assertIn("multipart/form-data", transport.content_type)
        self.assertIn(b"<svg/>", transport.body)
        self.assertNotIn(TOKEN.encode(), transport.body)

    def test_send_document_rejects_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            document = Path(temp) / "diagram.svg"
            document.write_text("<svg/>", encoding="utf-8")
            link = Path(temp) / "linked.svg"
            link.symlink_to(document)
            client = TelegramClient(TOKEN, transport=lambda request, timeout: b"")

            with self.assertRaises(ValueError):
                client.send_document(1001, link)


if __name__ == "__main__":
    unittest.main()
