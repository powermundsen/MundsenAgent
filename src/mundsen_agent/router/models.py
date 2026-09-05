"""Data models shared by router components."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RouterMode(str, Enum):
    AUTO = "auto"
    CLAUDE = "claude"
    CODEX = "codex"


@dataclass(frozen=True, slots=True)
class AgentResponse:
    text: str
    agent: str
    success: bool
    session_id: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    error_category: str = ""


@dataclass(slots=True)
class RouterState:
    mode: RouterMode = RouterMode.AUTO
    claude_session_id: str = ""
    codex_session_id: str = ""
    last_agent: str = ""
    last_error_category: str = ""
    claude_model: str = ""
    codex_model: str = ""
    codex_reasoning_effort: str = ""
