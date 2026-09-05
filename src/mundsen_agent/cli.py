"""Command-line interface for setup, runtime, service, and releases."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sys
from dataclasses import replace
from pathlib import Path

from mundsen_agent import __version__
from mundsen_agent.app import build_bridge
from mundsen_agent.auth import authentication_status, interactive_login
from mundsen_agent.bridge.telegram_client import TelegramClient, TelegramError
from mundsen_agent.config import ConfigurationError, load_runtime_settings
from mundsen_agent.diagnostics import (
    DiagnosticsError,
    create_support_bundle,
    read_audit_events,
)
from mundsen_agent.health import run_health_checks
from mundsen_agent.instance import load_instance
from mundsen_agent.module_system import ModuleManager
from mundsen_agent.modules.local_health import LocalHealthError, LocalHealthModule
from mundsen_agent.onboarding import OnboardingState, OnboardingStore
from mundsen_agent.paths import MundsenAgentPaths, PathConfigurationError
from mundsen_agent.release import (
    ReleaseError,
    activate_release,
    active_version,
    create_upgrade_backup,
    install_release,
    load_manifest,
    protected_snapshot,
    rollback_release,
)
from mundsen_agent.service import ServiceError, ServiceManager
from mundsen_agent.setup import interactive_setup
from mundsen_agent.skills import SkillError
from mundsen_agent.updater import (
    DEFAULT_REPOSITORY,
    UpdateError,
    download_release,
    fetch_latest_release,
    fetch_release_manifest,
    version_is_newer,
)


def _absolute_root(raw: str) -> Path:
    expanded = Path(os.path.expanduser(raw))
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    return Path(os.path.abspath(expanded))


def _paths(raw: str | None) -> MundsenAgentPaths:
    selected = raw or os.environ.get("MUNDSEN_HOME") or str(Path.home() / "Mundsen Agent")
    return MundsenAgentPaths.from_root(_absolute_root(selected))


def _confirm(prompt: str, *, default: bool = False) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    answer = input(prompt + suffix).strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes", "j", "ja"}


def _cmd_setup(paths: MundsenAgentPaths, args: argparse.Namespace) -> int:
    settings = interactive_setup(paths)
    providers = args.providers
    if providers == "ask":
        default = (
            settings.preferred_agent
            if settings.preferred_agent in {"claude", "codex"}
            else "both"
        )
        providers = (
            input(f"Subscriptions to sign in [claude/codex/both] [{default}]: ")
            .strip()
            .lower()
            or default
        )
    if providers not in {"claude", "codex", "both"}:
        print("Choose claude, codex, or both.", file=sys.stderr)
        return 2
    selected = ["claude", "codex"] if providers == "both" else [providers]
    for provider in selected:
        status = authentication_status(paths, provider)
        if status.authenticated:
            print(f"{provider}: already signed in")
            continue
        print(f"{provider}: opening subscription sign-in")
        if not interactive_login(paths, provider):
            print(f"{provider}: sign-in was not completed", file=sys.stderr)
    authenticated = [
        provider
        for provider in ("claude", "codex")
        if authentication_status(paths, provider).authenticated
    ]
    if not authenticated:
        print("At least one subscription agent must be signed in.", file=sys.stderr)
        return 1

    onboarding_store = OnboardingStore(paths.onboarding_state_file)
    onboarding = onboarding_store.load()
    if (
        not onboarding.completed
        and not onboarding.opening_sent
        and not onboarding.opening_authorized
        and _confirm(
            "Allow Mundsen Agent to send the first Telegram message when the bridge starts?"
        )
    ):
        onboarding = replace(onboarding, opening_authorized=True)
        onboarding_store.save(onboarding)

    service_started = False
    if not args.no_service and _confirm(
        "Install and start the background service?", default=True
    ):
        ServiceManager(paths).install()
        service_started = True
        print("Background service is active.")

    onboarding = onboarding_store.load()
    if onboarding.completed:
        return 0
    if service_started:
        if onboarding.opening_authorized:
            print(
                f"{settings.assistant_name} will open the first conversation in "
                "Telegram shortly. Answer there in your own words; nothing else "
                "needs to be configured here."
            )
        else:
            print(
                "No first message was authorized. Send the bot a message when "
                "you want to begin."
            )
    else:
        print(
            f"Start the bridge with `mundsen run` or `mundsen service install`. "
            f"{settings.assistant_name} will only speak first if you authorized "
            "the first Telegram message."
        )
    return 0


def _cmd_onboarding(paths: MundsenAgentPaths, args: argparse.Namespace) -> int:
    store = OnboardingStore(paths.onboarding_state_file)
    if args.action == "reset":
        if not args.yes and not _confirm(
            "Run the first conversation again the next time the bridge starts?"
        ):
            print("Unchanged.")
            return 0
        store.save(OnboardingState())
        print(
            "The first conversation will run again. Existing memories and "
            "profile notes were not touched."
        )
        return 0
    if args.action == "skip":
        store.save(OnboardingState(completed=True, opening_sent=True))
        print("The first conversation is marked as done.")
        return 0
    if args.action == "start":
        state = store.load()
        if state.completed:
            print("The first conversation is already done.")
            return 0
        store.save(replace(state, opening_authorized=True))
        manager = ServiceManager(paths)
        if manager.status().installed:
            manager.restart()
            print("The first Telegram message is authorized and being started.")
        else:
            print(
                "The first Telegram message is authorized. Start the bridge "
                "when you are ready."
            )
        return 0
    state = store.load()
    print(
        f"first conversation: {'done' if state.completed else 'pending'} "
        f"({state.turns} exchanges), "
        f"opening={'sent' if state.opening_sent else 'authorized' if state.opening_authorized else 'not authorized'}"
    )
    return 0


def _cmd_auth(paths: MundsenAgentPaths, args: argparse.Namespace) -> int:
    selected = (
        ["claude", "codex"] if args.provider == "all" else [args.provider]
    )
    success = False
    for provider in selected:
        status = authentication_status(paths, provider)
        if status.authenticated:
            print(f"{provider}: signed in")
            success = True
        elif interactive_login(paths, provider):
            print(f"{provider}: signed in")
            success = True
        else:
            print(f"{provider}: sign-in incomplete", file=sys.stderr)
    return 0 if success else 1


def _cmd_run(paths: MundsenAgentPaths) -> int:
    bridge = build_bridge(paths)

    def stop_bridge(signum: int, frame: object) -> None:
        del signum, frame
        bridge.stop()

    signal.signal(signal.SIGTERM, stop_bridge)
    signal.signal(signal.SIGINT, stop_bridge)
    bridge.run_forever()
    return 0


def _cmd_service(paths: MundsenAgentPaths, action: str) -> int:
    manager = ServiceManager(paths)
    if action == "install":
        status = manager.install()
    elif action == "start":
        status = manager.start()
    elif action == "restart":
        status = manager.restart()
    elif action == "stop":
        manager.stop()
        status = manager.status()
    elif action == "remove":
        manager.uninstall_registration()
        status = manager.status()
    else:
        status = manager.status()
    print(
        f"{status.platform}: "
        f"{'active' if status.active else 'inactive'}, "
        f"{'installed' if status.installed else 'not installed'}"
    )
    return 0 if (action in {"stop", "remove"} or status.active) else 1


def _cmd_health(paths: MundsenAgentPaths, args: argparse.Namespace) -> int:
    checks = run_health_checks(
        paths,
        network=args.network,
        service=not args.no_service,
    )
    if args.json:
        print(json.dumps([check.as_dict() for check in checks], indent=2))
    else:
        for check in checks:
            print(f"{'OK' if check.ok else 'FAIL'}  {check.name}: {check.detail}")
    return 0 if all(check.ok for check in checks) else 1


def _restart_if_installed(paths: MundsenAgentPaths) -> bool:
    manager = ServiceManager(paths)
    if manager.status().installed:
        status = manager.restart()
        if not status.active:
            raise ServiceError("Mundsen Agent service did not become active")
        return True
    return False


def _require_healthy(paths: MundsenAgentPaths, *, service: bool) -> None:
    checks = run_health_checks(paths, network=False, service=service)
    failed = [check.name for check in checks if not check.ok]
    if failed:
        raise ReleaseError(
            f"activated release failed health checks: {', '.join(failed)}"
        )


def _service_is_installed(paths: MundsenAgentPaths) -> bool:
    return ServiceManager(paths).status().installed


def _restore_previous_release(
    paths: MundsenAgentPaths,
    *,
    previous: str,
    service: bool,
) -> None:
    if not previous:
        raise ReleaseError("no previous release is available for recovery")
    try:
        activate_release(paths, previous)
        if service:
            _restart_if_installed(paths)
        _require_healthy(paths, service=service)
    except (ReleaseError, ServiceError, OSError, ValueError):
        raise ReleaseError(
            "automatic recovery failed; use the preserved backup for manual recovery"
        ) from None


def _cmd_upgrade(paths: MundsenAgentPaths, args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
    changed = ", ".join(manifest.modules_changed) or "core"
    print(
        f"Release {manifest.version}; modules: {changed}; "
        f"personal-data impact: {manifest.personal_data_impact}; "
        f"migrations: {len(manifest.migrations)}."
    )
    if not args.yes and not _confirm(
        f"Install the verified release described by {Path(args.manifest).name}?"
    ):
        print("Upgrade cancelled.")
        return 0
    previous = active_version(paths)
    service = _service_is_installed(paths)
    _require_healthy(paths, service=service)
    before = protected_snapshot(paths)
    create_upgrade_backup(
        paths,
        current_version=previous,
        snapshot=before,
    )
    try:
        installed = install_release(paths, args.manifest)
        if service:
            _restart_if_installed(paths)
        _require_healthy(paths, service=service)
        if protected_snapshot(paths) != before:
            raise ReleaseError("protected personal files changed during upgrade")
    except (ReleaseError, ServiceError, OSError, ValueError):
        _restore_previous_release(
            paths,
            previous=previous,
            service=service,
        )
        raise ReleaseError(
            "upgrade failed validation; the previous release was restored and "
            "the pre-upgrade backup was preserved"
        ) from None
    print(f"Mundsen Agent {installed} is active.")
    return 0


def _cmd_rollback(paths: MundsenAgentPaths, args: argparse.Namespace) -> int:
    target = args.version or "the previous release"
    if not args.yes and not _confirm(f"Roll back to {target}?"):
        print("Rollback cancelled.")
        return 0
    previous = active_version(paths)
    service = _service_is_installed(paths)
    _require_healthy(paths, service=service)
    before = protected_snapshot(paths)
    create_upgrade_backup(
        paths,
        current_version=previous,
        snapshot=before,
    )
    try:
        version = rollback_release(paths, args.version)
        if service:
            _restart_if_installed(paths)
        _require_healthy(paths, service=service)
        if protected_snapshot(paths) != before:
            raise ReleaseError("protected personal files changed during rollback")
    except (ReleaseError, ServiceError, OSError, ValueError):
        _restore_previous_release(
            paths,
            previous=previous,
            service=service,
        )
        raise ReleaseError(
            "rollback failed validation; the original release was restored and "
            "the pre-rollback backup was preserved"
        ) from None
    print(f"Mundsen Agent {version} is active.")
    return 0


def _cmd_update(paths: MundsenAgentPaths, args: argparse.Namespace) -> int:
    runtime = load_runtime_settings(paths)
    repository = (
        args.repository
        or runtime.get("MUNDSEN_UPDATE_REPOSITORY")
        or DEFAULT_REPOSITORY
    )
    token = runtime.get("MUNDSEN_GITHUB_TOKEN")
    release = fetch_latest_release(repository, token=token)
    manifest = fetch_release_manifest(release, token=token)
    current = active_version(paths) or __version__
    available = version_is_newer(release.version, current)
    if args.json:
        print(
            json.dumps(
                {
                    "current": current,
                    "latest": release.version,
                    "update_available": available,
                    "title": release.title,
                    "published_at": release.published_at,
                    "url": release.page_url,
                    "modules_changed": list(manifest.modules_changed),
                    "personal_data_impact": manifest.personal_data_impact,
                    "migrations": list(manifest.migrations),
                },
                indent=2,
            )
        )
    else:
        status = "available" if available else "current"
        print(f"Mundsen Agent {current}; latest {release.version}; {status}.")
        changed = ", ".join(manifest.modules_changed) or "core"
        print(
            f"Modules: {changed}; personal-data impact: "
            f"{manifest.personal_data_impact}; migrations: {len(manifest.migrations)}."
        )
        if available and release.notes:
            print(release.notes)
        print(release.page_url)
    if args.action == "check" or not available:
        return 0
    destination = paths.cache_dir / "updates" / release.version
    manifest = download_release(
        release,
        destination,
        token=token,
    )
    if args.action == "download":
        print(f"Verified release downloaded to {manifest.parent}.")
        return 0
    return _cmd_upgrade(
        paths,
        argparse.Namespace(manifest=str(manifest), yes=args.yes),
    )


def _cmd_training(paths: MundsenAgentPaths, args: argparse.Namespace) -> int:
    runtime = load_runtime_settings(paths)
    instance = load_instance(paths.instance_dir / "instance.yaml")
    module = ModuleManager(paths, runtime, instance).require("local-health")
    if not isinstance(module, LocalHealthModule):
        raise ConfigurationError("local-health module is unavailable")
    if args.action == "import":
        result = module.import_apple_health(args.path)
        payload = {
            "already_imported": result.already_imported,
            "workouts_added": result.workouts_added,
            "samples_added": result.samples_added,
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        elif result.already_imported:
            print("This Apple Health export was already imported.")
        else:
            print(
                f"Imported {result.workouts_added} workouts and "
                f"{result.samples_added} selected health samples."
            )
        return 0
    if args.action == "summarize":
        summary = module.write_summary()
        print(summary)
        return 0
    checks = module.health()
    payload = [
        {"name": check.name, "ok": check.ok, "detail": check.detail}
        for check in checks
    ]
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for check in checks:
            print(f"{'OK' if check.ok else 'FAIL'}  {check.name}: {check.detail}")
    return 0 if all(check.ok for check in checks) else 1


def _cmd_logs(paths: MundsenAgentPaths, args: argparse.Namespace) -> int:
    events = read_audit_events(
        paths.audit_log_file,
        limit=args.limit,
        source=args.source,
        event=args.event,
    )
    if args.json:
        print(json.dumps(events, indent=2))
    else:
        for item in events:
            fields = " ".join(
                f"{key}={value}"
                for key, value in item.items()
                if key not in {"ts", "source", "event"}
            )
            print(
                f"{item.get('ts', '')} {item.get('source', '')}."
                f"{item.get('event', '')}{' ' + fields if fields else ''}"
            )
    return 0


def _cmd_uninstall(paths: MundsenAgentPaths, args: argparse.Namespace) -> int:
    manager = ServiceManager(paths)
    manager.uninstall_registration()
    if not args.purge_runtime:
        print("Service removed. Personal files and local runtime remain.")
        return 0
    if not _confirm(
        "Remove releases, provider binaries, tools, cache, and launch wrapper?"
    ):
        print("Runtime removal cancelled. Service remains removed.")
        return 0
    for target in (
        paths.releases_dir,
        paths.current_release,
        paths.provider_home,
        paths.provider_bin_dir,
        paths.codex_home,
        paths.tools_dir,
        paths.bin_dir,
        paths.cache_dir,
    ):
        if target.is_symlink() or target.is_file():
            target.unlink(missing_ok=True)
        elif target.is_dir():
            shutil.rmtree(target)
    print("Runtime removed. Instance, configuration, state, and logs were preserved.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mundsen-agent")
    parser.add_argument(
        "--home",
        help="Mundsen Agent root directory (defaults to MUNDSEN_HOME or ~/Mundsen Agent)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup = subparsers.add_parser("setup", help="create and configure an instance")
    setup.add_argument(
        "--providers",
        choices=("ask", "claude", "codex", "both"),
        default="ask",
    )
    setup.add_argument("--no-service", action="store_true")

    auth = subparsers.add_parser("auth", help="sign in with a subscription")
    auth.add_argument("provider", choices=("claude", "codex", "all"))

    subparsers.add_parser("run", help="run the bridge in the foreground")
    subparsers.add_parser("service-run", help=argparse.SUPPRESS)

    service = subparsers.add_parser("service", help="manage the background service")
    service.add_argument(
        "action",
        choices=("install", "start", "stop", "restart", "status", "remove"),
    )

    health = subparsers.add_parser("health", help="run local health checks")
    health.add_argument("--network", action="store_true")
    health.add_argument("--no-service", action="store_true")
    health.add_argument("--json", action="store_true")

    upgrade = subparsers.add_parser("upgrade", help="install a verified local release")
    upgrade.add_argument("manifest")
    upgrade.add_argument("--yes", action="store_true")

    rollback = subparsers.add_parser("rollback", help="activate an older release")
    rollback.add_argument("version", nargs="?", default="")
    rollback.add_argument("--yes", action="store_true")

    update = subparsers.add_parser(
        "update",
        help="check, download, or install the latest GitHub Release",
    )
    update.add_argument(
        "action",
        nargs="?",
        default="check",
        choices=("check", "download", "install"),
    )
    update.add_argument("--repository", default="")
    update.add_argument("--json", action="store_true")
    update.add_argument("--yes", action="store_true")

    training = subparsers.add_parser(
        "training", help="manage private local Apple Health training data"
    )
    training.add_argument(
        "action",
        nargs="?",
        choices=("status", "import", "summarize"),
        default="status",
    )
    training.add_argument("path", nargs="?", default="")
    training.add_argument("--json", action="store_true")

    logs = subparsers.add_parser("logs", help="show local operational metadata")
    logs.add_argument("--limit", type=int, default=100)
    logs.add_argument("--source", default="")
    logs.add_argument("--event", default="")
    logs.add_argument("--json", action="store_true")

    support = subparsers.add_parser(
        "support-bundle", help="create a private sanitized diagnostics archive"
    )
    support.add_argument("output")

    uninstall = subparsers.add_parser("uninstall", help="remove the service")
    uninstall.add_argument("--purge-runtime", action="store_true")
    subparsers.add_parser("version", help="show installed and active versions")

    onboarding = subparsers.add_parser(
        "onboarding", help="check or reset the first-conversation interview"
    )
    onboarding.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=("status", "start", "reset", "skip"),
    )
    onboarding.add_argument("--yes", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        paths = _paths(args.home)
        if args.command == "setup":
            return _cmd_setup(paths, args)
        if args.command == "auth":
            return _cmd_auth(paths, args)
        if args.command in {"run", "service-run"}:
            return _cmd_run(paths)
        if args.command == "service":
            return _cmd_service(paths, args.action)
        if args.command == "health":
            return _cmd_health(paths, args)
        if args.command == "upgrade":
            return _cmd_upgrade(paths, args)
        if args.command == "rollback":
            return _cmd_rollback(paths, args)
        if args.command == "update":
            return _cmd_update(paths, args)
        if args.command == "training":
            if args.action == "import" and not args.path:
                parser.error("training import requires an absolute export.xml path")
            return _cmd_training(paths, args)
        if args.command == "logs":
            return _cmd_logs(paths, args)
        if args.command == "support-bundle":
            print(create_support_bundle(paths, args.output))
            return 0
        if args.command == "uninstall":
            return _cmd_uninstall(paths, args)
        if args.command == "version":
            print(f"package={__version__} active={active_version(paths) or 'none'}")
            return 0
        if args.command == "onboarding":
            return _cmd_onboarding(paths, args)
    except (
        ConfigurationError,
        DiagnosticsError,
        FileNotFoundError,
        OSError,
        PathConfigurationError,
        ReleaseError,
        ServiceError,
        SkillError,
        LocalHealthError,
        TelegramError,
        UpdateError,
        ValueError,
    ) as exc:
        if args.command == "service-run":
            print("Mundsen Agent stopped after a local configuration error.", file=sys.stderr)
        else:
            print(f"Mundsen Agent: {exc}", file=sys.stderr)
        return 1
    return 2
