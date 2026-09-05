"""Composition root for the local Mundsen Agent runtime."""

from __future__ import annotations

from mundsen_agent import __version__
from mundsen_agent.auth import authentication_status
from mundsen_agent.audit import AuditLogger
from mundsen_agent.bridge.attachments import AttachmentProcessor
from mundsen_agent.bridge.job_store import PersistentJobStore
from mundsen_agent.bridge.runtime import OffsetStore, TelegramBridge
from mundsen_agent.bridge.telegram_client import TelegramClient
from mundsen_agent.bridge.visualizations import VisualizationRenderer
from mundsen_agent.config import ConfigurationError, load_runtime_settings
from mundsen_agent.context import build_instance_context
from mundsen_agent.features import FeatureSet, parse_model_aliases
from mundsen_agent.instance import load_instance
from mundsen_agent.module_system import ModuleManager
from mundsen_agent.onboarding import OnboardingRouter, OnboardingStore
from mundsen_agent.paths import MundsenAgentPaths
from mundsen_agent.reminders import ReminderEngine
from mundsen_agent.router.agents import ClaudeAdapter, CodexAdapter
from mundsen_agent.router.router import AgentRouter
from mundsen_agent.router.state import RouterStateStore
from mundsen_agent.skills import sync_skill_discovery
from mundsen_agent.updater import DEFAULT_REPOSITORY, UpdateNotificationEngine


def _positive_int(value: str, *, name: str, default: int) -> int:
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if parsed < 10 or parsed > 7200:
        raise ConfigurationError(f"{name} must be between 10 and 7200")
    return parsed


def _bounded_int(
    value: str,
    *,
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise ConfigurationError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return parsed


def build_bridge(paths: MundsenAgentPaths) -> TelegramBridge:
    sync_skill_discovery(paths)
    instance = load_instance(paths.instance_dir / "instance.yaml")
    runtime = load_runtime_settings(
        paths,
        required_names=("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
    )
    try:
        chat_id = int(runtime.require("TELEGRAM_CHAT_ID"))
    except ValueError as exc:
        raise ConfigurationError("TELEGRAM_CHAT_ID must be an integer") from exc
    if chat_id == 0:
        raise ConfigurationError("TELEGRAM_CHAT_ID must not be zero")
    timeout = _positive_int(
        runtime.get("MUNDSEN_AGENT_TIMEOUT_SECONDS"),
        name="MUNDSEN_AGENT_TIMEOUT_SECONDS",
        default=900,
    )
    audit = AuditLogger(paths.audit_log_file)
    features = FeatureSet(paths, runtime)
    modules = ModuleManager(paths, runtime, instance)
    extended_commands = features.enabled("extended-commands")
    claude_model_aliases = (
        parse_model_aliases(
            runtime.get("MUNDSEN_CLAUDE_MODEL_ALIASES"),
            setting="MUNDSEN_CLAUDE_MODEL_ALIASES",
        )
        if extended_commands
        else {}
    )
    codex_model_aliases = (
        parse_model_aliases(
            runtime.get("MUNDSEN_CODEX_MODEL_ALIASES"),
            setting="MUNDSEN_CODEX_MODEL_ALIASES",
        )
        if extended_commands
        else {}
    )
    duplicate_aliases = sorted(
        set(claude_model_aliases) & set(codex_model_aliases)
    )
    if duplicate_aliases:
        raise ConfigurationError(
            "Claude and Codex model aliases overlap: "
            + ", ".join(duplicate_aliases)
        )
    preferred = instance.preferred_agent
    if preferred == "auto":
        if authentication_status(paths, "claude").authenticated:
            preferred = "claude"
        elif authentication_status(paths, "codex").authenticated:
            preferred = "codex"
    router = AgentRouter(
        claude=ClaudeAdapter(
            paths,
            model=runtime.get("MUNDSEN_CLAUDE_MODEL"),
            timeout_seconds=timeout,
        ),
        codex=CodexAdapter(
            paths,
            model=runtime.get("MUNDSEN_CODEX_MODEL"),
            reasoning_effort=runtime.get("MUNDSEN_CODEX_REASONING_EFFORT"),
            timeout_seconds=timeout,
        ),
        state_store=RouterStateStore(paths.router_state_file),
        audit=audit,
        preferred_agent=preferred,
        language=instance.language,
        context_provider=lambda: _combined_context(
            paths,
            instance.timezone,
            modules,
            features,
        ),
        extended_commands=extended_commands,
        version_text=f"Mundsen Agent {__version__}",
        claude_model_aliases=claude_model_aliases,
        codex_model_aliases=codex_model_aliases,
        circuit_breaker_threshold=(
            _bounded_int(
                runtime.get("MUNDSEN_CIRCUIT_BREAKER_THRESHOLD"),
                name="MUNDSEN_CIRCUIT_BREAKER_THRESHOLD",
                default=3,
                minimum=1,
                maximum=20,
            )
            if features.enabled("circuit-breaker")
            else 0
        ),
        circuit_breaker_cooldown_seconds=(
            _positive_int(
                runtime.get("MUNDSEN_CIRCUIT_BREAKER_COOLDOWN_SECONDS"),
                name="MUNDSEN_CIRCUIT_BREAKER_COOLDOWN_SECONDS",
                default=300,
            )
            if features.enabled("circuit-breaker")
            else 300
        ),
    )
    onboarding = OnboardingRouter(
        inner=router,
        store=OnboardingStore(paths.onboarding_state_file),
        settings=instance,
    )
    reminders = ReminderEngine(
        reminders_file=paths.instance_dir / "reminders.md",
        state_file=paths.reminder_state_file,
        timezone=instance.timezone,
        language=instance.language,
    )
    updates = UpdateNotificationEngine(
        state_file=paths.update_state_file,
        repository=runtime.get("MUNDSEN_UPDATE_REPOSITORY") or DEFAULT_REPOSITORY,
        current_version=__version__,
        token=runtime.get("MUNDSEN_GITHUB_TOKEN"),
        enabled=runtime.get("MUNDSEN_UPDATE_CHECK", "1") != "0",
        language=instance.language,
    )
    client = TelegramClient(runtime.require("TELEGRAM_BOT_TOKEN"))
    attachment_processor = (
        AttachmentProcessor(
            client=client,
            paths=paths,
            transcribe_command=(
                runtime.get("MUNDSEN_TRANSCRIBE_COMMAND")
                if features.enabled("local-transcription")
                else ""
            ),
            maximum_bytes=_bounded_int(
                runtime.get("MUNDSEN_ATTACHMENT_MAX_BYTES"),
                name="MUNDSEN_ATTACHMENT_MAX_BYTES",
                default=8 * 1024 * 1024,
                minimum=1024,
                maximum=8 * 1024 * 1024,
            ),
        )
        if features.enabled("attachments")
        else None
    )
    visualization_renderer = (
        VisualizationRenderer(
            paths,
            language=instance.language,
            mermaid_render_command=runtime.get("MUNDSEN_MERMAID_RENDER_COMMAND"),
        )
        if features.enabled("inline-visualizations")
        else None
    )
    return TelegramBridge(
        client=client,
        router=onboarding,
        allowed_chat_id=chat_id,
        offset_store=OffsetStore(paths.telegram_offset_file),
        audit=audit,
        language=instance.language,
        opening_message=onboarding.open_conversation,
        opening_delivered=onboarding.mark_opening_sent,
        job_store=PersistentJobStore(paths.queue_dir),
        scheduled_notifications=lambda: [
            *reminders.pending(),
            *updates.pending(),
            *modules.pending_notifications(),
        ],
        scheduled_delivered=lambda key: (
            reminders.mark_sent(key)
            if key.startswith("reminder:")
            else updates.mark_sent(key)
            if key.startswith("update:")
            else modules.mark_notification_sent(key)
        ),
        attachment_processor=attachment_processor,
        visualization_renderer=visualization_renderer,
        live_progress=features.enabled("live-progress"),
        progress_interval_seconds=(
            _bounded_int(
                runtime.get("MUNDSEN_PROGRESS_INTERVAL_SECONDS"),
                name="MUNDSEN_PROGRESS_INTERVAL_SECONDS",
                default=45,
                minimum=10,
                maximum=300,
            )
            if features.enabled("live-progress")
            else 45
        ),
    )


def _combined_context(
    paths: MundsenAgentPaths,
    timezone: str,
    modules: ModuleManager,
    features: FeatureSet,
) -> str:
    base = build_instance_context(
        paths,
        timezone=timezone,
        extra_files=features.context_files(),
    )
    extension = modules.context()
    if not extension:
        return base
    return f"{base}\n\n<mundsen_agent_module_context>\n{extension}\n</mundsen_agent_module_context>"
