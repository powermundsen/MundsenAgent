"""Idempotent creation of a blank personal instance and local secrets."""

from __future__ import annotations

import getpass
import importlib.resources
import locale
import os
import secrets
import tempfile
from pathlib import Path
from typing import Callable

from mundsen_agent.bridge.telegram_client import TelegramClient, TelegramError
from mundsen_agent.instance import (
    InstanceSettings,
    load_instance,
    parse_instance_text,
    render_instance,
)
from mundsen_agent.paths import MundsenAgentPaths, ensure_private_directories
from mundsen_agent.skills import sync_skill_discovery

Input = Callable[[str], str]


def _default_language() -> str:
    language, _ = locale.getlocale()
    if not language:
        language = os.environ.get("LANG", "")
    tag = language.split(".", 1)[0].replace("_", "-")
    return tag.split("-", 1)[0].lower() if tag else "en"


def _default_timezone() -> str:
    configured = os.environ.get("TZ", "").strip()
    candidates = [configured] if configured else []
    timezone_file = Path("/etc/timezone")
    if timezone_file.is_file():
        try:
            candidates.append(timezone_file.read_text(encoding="utf-8").strip())
        except OSError:
            pass
    localtime = Path("/etc/localtime")
    if localtime.is_symlink():
        target = str(localtime.resolve())
        marker = "/zoneinfo/"
        if marker in target:
            candidates.append(target.split(marker, 1)[1])
    for candidate in candidates:
        if candidate and "/" in candidate:
            try:
                from zoneinfo import ZoneInfo

                ZoneInfo(candidate)
                return candidate
            except Exception:
                continue
    return "UTC"


def _atomic_private_write(path: Path, text: str, *, overwrite: bool = False) -> bool:
    if path.exists() and not overwrite:
        return False
    if not path.is_absolute() or path.is_symlink():
        raise ValueError(f"unsafe setup path: {path}")
    ensure_private_directories((path.parent,))
    descriptor, temporary = tempfile.mkstemp(prefix=".mundsen-agent-setup-", dir=path.parent)
    temp_path = Path(temporary)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
    return True


def _template(name: str, settings: InstanceSettings) -> str:
    source = (
        importlib.resources.files("mundsen_agent")
        .joinpath("templates")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )
    return source.replace(
        "{{ASSISTANT_NAME}}", settings.assistant_name
    ).replace("{{LANGUAGE}}", settings.language)


def create_instance(
    paths: MundsenAgentPaths,
    settings: InstanceSettings,
) -> list[Path]:
    """Create only missing personal files and return those created."""

    settings = parse_instance_text(render_instance(settings))
    ensure_private_directories(
        (
            *paths.private_runtime_directories(),
            paths.instance_dir,
            paths.instance_dir / "memory",
            paths.instance_dir / "skills",
        )
    )
    files = {
        paths.instance_dir / "instance.yaml": render_instance(settings),
        paths.instance_dir / "AGENTS.md": _template("AGENTS.md.tmpl", settings),
        paths.instance_dir / "CLAUDE.md": _template("CLAUDE.md.tmpl", settings),
        paths.instance_dir / "AUTONOMY.md": _template("AUTONOMY.md.tmpl", settings),
        paths.instance_dir / "MEMORY.md": "# Memory\n\n",
        paths.instance_dir / "TODO.md": "# TODO\n\n",
        paths.instance_dir / "reminders.md": (
            "# Reminders\n\n"
            "# Format: YYYY-MM-DD | warn_days | message\n"
        ),
        paths.instance_dir / "memory" / "user_profile.md": "# User profile\n\n",
        paths.instance_dir / "memory" / "open-threads.md": "# Open threads\n\n",
        paths.instance_dir / "memory" / "setup-wishlist.md": (
            "# Setup wishlist\n\n"
            "# Devices and services the owner wants connected, but that are\n"
            "# not set up yet. Format: YYYY-MM-DD | what they asked for | notes\n"
        ),
    }
    created: list[Path] = []
    for path, content in files.items():
        if _atomic_private_write(path, content):
            created.append(path)

    _atomic_private_write(
        paths.runtime_config_file,
        "\n".join(
            (
                "MUNDSEN_AGENT_TIMEOUT_SECONDS=900",
                "MUNDSEN_CLAUDE_MODEL=",
                "MUNDSEN_CLAUDE_MODEL_ALIASES=",
                "MUNDSEN_CODEX_MODEL=",
                "MUNDSEN_CODEX_MODEL_ALIASES=",
                "MUNDSEN_CODEX_REASONING_EFFORT=high",
                "MUNDSEN_FEATURES=",
                "MUNDSEN_CIRCUIT_BREAKER_THRESHOLD=3",
                "MUNDSEN_CIRCUIT_BREAKER_COOLDOWN_SECONDS=300",
                "MUNDSEN_TRANSCRIBE_COMMAND=",
                "MUNDSEN_MERMAID_RENDER_COMMAND=",
                "MUNDSEN_ATTACHMENT_MAX_BYTES=8388608",
                "MUNDSEN_PROGRESS_INTERVAL_SECONDS=45",
                "MUNDSEN_MODULES=",
                "MUNDSEN_CALENDAR_COMMAND=",
                "MUNDSEN_CALENDAR_NOTIFICATIONS=0",
                "MUNDSEN_MORNING_BRIEF_COMMAND=",
                "MUNDSEN_MORNING_BRIEF_NOTIFICATIONS=0",
                "MUNDSEN_OBSERVABILITY_COMMAND=",
                "MUNDSEN_OBSERVABILITY_NOTIFICATIONS=0",
                "MUNDSEN_SMART_HOME_COMMAND=",
                "MUNDSEN_SMART_HOME_NOTIFICATIONS=0",
                "MUNDSEN_UPDATE_CHECK=1",
                "MUNDSEN_UPDATE_REPOSITORY=powermundsen/MundsenAgent",
                "",
            )
        ),
    )
    sync_skill_discovery(paths)
    return created


def detect_private_chat_id(
    client: TelegramClient,
    *,
    pairing_code: str = "",
    attempts: int = 4,
    wait_for_user: Input = input,
) -> int:
    """Find exactly one private chat after the user messages the new bot."""

    offset = 0
    candidates: set[int] = set()
    for attempt in range(attempts):
        updates = client.get_updates(offset=offset, long_poll_seconds=2)
        for update in updates:
            update_id = int(update.get("update_id", -1) or -1)
            if update_id >= 0:
                offset = max(offset, update_id + 1)
            message = update.get("message")
            if not isinstance(message, dict):
                continue
            chat = message.get("chat")
            if not isinstance(chat, dict) or chat.get("type") != "private":
                continue
            if pairing_code:
                text = message.get("text")
                if not isinstance(text, str):
                    continue
                parts = text.strip().split()
                if (
                    len(parts) != 2
                    or not parts[0].lower().split("@", 1)[0] == "/start"
                    or not secrets.compare_digest(parts[1], pairing_code)
                ):
                    continue
            chat_id = int(chat.get("id", 0) or 0)
            if chat_id:
                candidates.add(chat_id)
        if len(candidates) == 1:
            return candidates.pop()
        if len(candidates) > 1:
            raise ValueError("more than one private chat contacted the bot")
        if attempt + 1 < attempts:
            command = (
                f"/start {pairing_code}" if pairing_code else "/start"
            )
            wait_for_user(
                f"Send {command} to the Telegram bot, then press Enter here: "
            )
    raise ValueError("no private Telegram chat was found")


def configure_telegram(
    paths: MundsenAgentPaths,
    *,
    token: str,
    chat_id: int,
    overwrite: bool = False,
) -> bool:
    if "\n" in token or chat_id == 0:
        raise ValueError("invalid Telegram configuration")
    return _atomic_private_write(
        paths.secrets_file,
        f"TELEGRAM_BOT_TOKEN={token}\nTELEGRAM_CHAT_ID={chat_id}\n",
        overwrite=overwrite,
    )


def interactive_setup(
    paths: MundsenAgentPaths,
    *,
    input_fn: Input = input,
    secret_input: Callable[[str], str] = getpass.getpass,
) -> InstanceSettings:
    instance_file = paths.instance_dir / "instance.yaml"
    if instance_file.exists():
        settings = load_instance(instance_file)
        create_instance(paths, settings)
    else:
        default_language = _default_language()
        default_timezone = _default_timezone()
        name = input_fn("Assistant name [Mundsen Agent]: ").strip() or "Mundsen Agent"
        language = (
            input_fn(f"Language tag [{default_language}]: ").strip()
            or default_language
        )
        timezone = (
            input_fn(f"Timezone [{default_timezone}]: ").strip()
            or default_timezone
        )
        tone = (
            input_fn("Tone [natural and concise]: ").strip()
            or "natural and concise"
        )
        technical_level = (
            input_fn("Technical level [general]: ").strip()
            or "general"
        )
        preferred = (
            input_fn("Preferred agent [auto/claude/codex] [auto]: ").strip()
            or "auto"
        )
        settings = InstanceSettings(
            schema_version=1,
            assistant_name=name,
            language=language,
            timezone=timezone,
            tone=tone,
            technical_level=technical_level,
            preferred_agent=preferred,
        )
        create_instance(paths, settings)
    if not paths.secrets_file.exists():
        input_fn(
            "Create a Telegram bot with @BotFather, then press Enter to continue: "
        )
        token = secret_input("Telegram bot token (hidden): ").strip()
        client = TelegramClient(token)
        client.get_me()
        pairing_code = secrets.token_hex(4)
        chat_id = detect_private_chat_id(
            client,
            pairing_code=pairing_code,
            wait_for_user=input_fn,
        )
        configure_telegram(paths, token=token, chat_id=chat_id)
    return settings
