"""Private, atomic persistence for provider session identifiers."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

from mundsen_agent.paths import ensure_private_directories
from mundsen_agent.router.models import RouterMode, RouterState


class RouterStateError(ValueError):
    """Raised when persisted router state is unsafe or invalid."""


class RouterStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> RouterState:
        if not self.path.exists():
            return RouterState()
        if not self.path.is_absolute() or self.path.is_symlink():
            raise RouterStateError("router state path is unsafe")
        file_stat = self.path.stat()
        if not stat.S_ISREG(file_stat.st_mode):
            raise RouterStateError("router state is not a regular file")
        if stat.S_IMODE(file_stat.st_mode) != 0o600:
            raise RouterStateError("router state must use mode 0600")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            mode = RouterMode(str(data.get("mode", "auto")))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            raise RouterStateError("router state is invalid") from exc
        return RouterState(
            mode=mode,
            claude_session_id=self._safe_session(data.get("claude_session_id")),
            codex_session_id=self._safe_session(data.get("codex_session_id")),
            last_agent=self._safe_agent(data.get("last_agent")),
            last_error_category=self._safe_category(
                data.get("last_error_category")
            ),
            claude_model=self._safe_model(data.get("claude_model")),
            codex_model=self._safe_model(data.get("codex_model")),
            codex_reasoning_effort=self._safe_reasoning(
                data.get("codex_reasoning_effort")
            ),
        )

    def save(self, state: RouterState) -> None:
        if not self.path.is_absolute() or self.path.is_symlink():
            raise RouterStateError("router state path is unsafe")
        ensure_private_directories((self.path.parent,))
        payload = {
            "mode": state.mode.value,
            "claude_session_id": self._safe_session(state.claude_session_id),
            "codex_session_id": self._safe_session(state.codex_session_id),
            "last_agent": self._safe_agent(state.last_agent),
            "last_error_category": self._safe_category(
                state.last_error_category
            ),
            "claude_model": self._safe_model(state.claude_model),
            "codex_model": self._safe_model(state.codex_model),
            "codex_reasoning_effort": self._safe_reasoning(
                state.codex_reasoning_effort
            ),
        }
        descriptor, temporary = tempfile.mkstemp(
            prefix=".router-state-",
            dir=self.path.parent,
        )
        temp_path = Path(temporary)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"))
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

    @staticmethod
    def _safe_session(value: object) -> str:
        text = str(value or "")
        if not text:
            return ""
        if len(text) > 160 or not all(
            character.isalnum() or character in "-_." for character in text
        ):
            raise RouterStateError("invalid provider session identifier")
        return text

    @staticmethod
    def _safe_agent(value: object) -> str:
        text = str(value or "")
        if text not in {"", "claude", "codex"}:
            raise RouterStateError("invalid last_agent")
        return text

    @staticmethod
    def _safe_category(value: object) -> str:
        text = str(value or "")
        if len(text) > 80 or not all(
            character.isalnum() or character in "-_." for character in text
        ):
            raise RouterStateError("invalid error category")
        return text

    @staticmethod
    def _safe_model(value: object) -> str:
        text = str(value or "")
        if len(text) > 120 or any(ord(character) < 32 for character in text):
            raise RouterStateError("invalid model identifier")
        return text

    @staticmethod
    def _safe_reasoning(value: object) -> str:
        text = str(value or "").lower()
        if text not in {"", "minimal", "low", "medium", "high", "xhigh"}:
            raise RouterStateError("invalid reasoning effort")
        return text
