"""Small Telegram Bot API client that never exposes the bot token."""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{20,}$")


class TelegramError(RuntimeError):
    """Sanitized Telegram failure suitable for local categorization."""

    def __init__(self, category: str, http_status: int = 0) -> None:
        super().__init__(category)
        self.category = category
        self.http_status = http_status


Transport = Callable[[urllib.request.Request, float], bytes]


def _default_transport(request: urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise TelegramError("response_too_large")
    return data


class TelegramClient:
    def __init__(
        self,
        token: str,
        *,
        timeout_seconds: float = 20,
        transport: Transport = _default_transport,
    ) -> None:
        if not _TOKEN_RE.fullmatch(token):
            raise ValueError("invalid Telegram bot token format")
        self.__token = token
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def call(
        self,
        method: str,
        payload: Mapping[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{1,63}", method):
            raise ValueError("invalid Telegram method")
        url = f"https://api.telegram.org/bot{self.__token}/{method}"
        body = json.dumps(dict(payload or {}), separators=(",", ":")).encode()
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            raw = self.transport(
                request,
                self.timeout_seconds if timeout_seconds is None else timeout_seconds,
            )
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            raise TelegramError("http_error", status) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise TelegramError("network_error") from None
        try:
            response = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise TelegramError("invalid_response") from None
        if not isinstance(response, dict) or not response.get("ok"):
            status = int(response.get("error_code", 0) or 0) if isinstance(response, dict) else 0
            raise TelegramError("api_error", status)
        return response.get("result")

    def get_me(self) -> dict[str, Any]:
        result = self.call("getMe")
        if not isinstance(result, dict):
            raise TelegramError("invalid_response")
        return result

    def get_updates(
        self,
        *,
        offset: int,
        long_poll_seconds: int = 25,
    ) -> list[dict[str, Any]]:
        result = self.call(
            "getUpdates",
            {
                "offset": offset,
                "timeout": long_poll_seconds,
                "allowed_updates": ["message"],
            },
            timeout_seconds=long_poll_seconds + 10,
        )
        if not isinstance(result, list):
            raise TelegramError("invalid_response")
        return [item for item in result if isinstance(item, dict)]

    def send_message(self, chat_id: int, html_text: str) -> int:
        result = self.call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": html_text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )
        if not isinstance(result, dict):
            raise TelegramError("invalid_response")
        return int(result.get("message_id", 0) or 0)

    def send_typing(self, chat_id: int) -> None:
        self.call(
            "sendChatAction",
            {"chat_id": chat_id, "action": "typing"},
        )

    def edit_message(self, chat_id: int, message_id: int, html_text: str) -> None:
        self.call(
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": html_text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )

    def delete_message(self, chat_id: int, message_id: int) -> None:
        self.call(
            "deleteMessage",
            {"chat_id": chat_id, "message_id": message_id},
        )

    def get_file(self, file_id: str) -> tuple[str, int]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,256}", file_id):
            raise ValueError("invalid Telegram file identifier")
        result = self.call("getFile", {"file_id": file_id})
        if not isinstance(result, dict):
            raise TelegramError("invalid_response")
        file_path = str(result.get("file_path") or "")
        try:
            size = int(result.get("file_size", 0) or 0)
        except (TypeError, ValueError):
            raise TelegramError("invalid_response") from None
        if (
            not re.fullmatch(r"[A-Za-z0-9_./-]{1,512}", file_path)
            or file_path.startswith("/")
            or ".." in file_path.split("/")
            or size < 0
        ):
            raise TelegramError("invalid_response")
        return file_path, size

    def download_file(self, file_path: str, *, maximum_bytes: int) -> bytes:
        if (
            maximum_bytes <= 0
            or maximum_bytes > MAX_RESPONSE_BYTES
            or not re.fullmatch(r"[A-Za-z0-9_./-]{1,512}", file_path)
            or file_path.startswith("/")
            or ".." in file_path.split("/")
        ):
            raise ValueError("invalid Telegram download request")
        request = urllib.request.Request(
            f"https://api.telegram.org/file/bot{self.__token}/{file_path}",
            method="GET",
        )
        try:
            data = self.transport(request, self.timeout_seconds)
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            raise TelegramError("http_error", status) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise TelegramError("network_error") from None
        if len(data) > maximum_bytes:
            raise TelegramError("file_too_large")
        return data

    def send_document(
        self,
        chat_id: int,
        path: str | os.PathLike[str],
        *,
        caption: str = "",
    ) -> int:
        target = os.fspath(path)
        filename = os.path.basename(target)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", filename):
            raise ValueError("invalid Telegram document name")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(target, flags)
        except OSError:
            raise ValueError("invalid Telegram document") from None
        try:
            file_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(file_stat.st_mode)
                or file_stat.st_size > MAX_RESPONSE_BYTES
            ):
                raise ValueError("invalid Telegram document")
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                content = handle.read(MAX_RESPONSE_BYTES + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if len(content) > MAX_RESPONSE_BYTES:
            raise ValueError("Telegram document is too large")
        boundary = f"mundsen-{secrets.token_hex(12)}"
        fields = [
            ("chat_id", str(chat_id)),
            ("caption", caption[:1024]),
        ]
        body = bytearray()
        for name, value in fields:
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
            )
            body.extend(value.encode("utf-8"))
            body.extend(b"\r\n")
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            (
                'Content-Disposition: form-data; name="document"; '
                f'filename="{filename}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode()
        )
        body.extend(content)
        body.extend(f"\r\n--{boundary}--\r\n".encode())
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self.__token}/sendDocument",
            data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            raw = self.transport(request, self.timeout_seconds)
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            raise TelegramError("http_error", status) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise TelegramError("network_error") from None
        try:
            response = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise TelegramError("invalid_response") from None
        if not isinstance(response, dict) or not response.get("ok"):
            raise TelegramError("api_error")
        result = response.get("result")
        if not isinstance(result, dict):
            raise TelegramError("invalid_response")
        return int(result.get("message_id", 0) or 0)
