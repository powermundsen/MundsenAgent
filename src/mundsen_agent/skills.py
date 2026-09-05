"""Expose one safe Agent Skills source to Claude Code and Codex."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from mundsen_agent.paths import MundsenAgentPaths, ensure_private_directories

_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_REGISTRY = ".mundsen-agent-managed.json"


class SkillError(RuntimeError):
    """Raised when a shared skill source or discovery target is unsafe."""


@dataclass(frozen=True, slots=True)
class SkillSyncResult:
    names: tuple[str, ...]
    claude_directory: Path
    codex_directory: Path


def _skill_sources(paths: MundsenAgentPaths) -> dict[str, Path]:
    roots = (
        paths.current_release / "src" / "mundsen_agent" / "bundled_skills",
        paths.instance_dir / "skills",
    )
    found: dict[str, Path] = {}
    for root in roots:
        if not root.exists():
            continue
        if root.is_symlink() or not root.is_dir():
            raise SkillError("skill source directory is unsafe")
        for candidate in sorted(root.iterdir()):
            if not candidate.is_dir() or candidate.is_symlink():
                continue
            name = candidate.name
            if not _SKILL_NAME_RE.fullmatch(name):
                raise SkillError("skill directory has an invalid name")
            entrypoint = candidate / "SKILL.md"
            if entrypoint.is_symlink() or not entrypoint.is_file():
                raise SkillError(f"skill {name} has no safe SKILL.md")
            if name in found:
                raise SkillError(f"duplicate shared skill name: {name}")
            found[name] = candidate.resolve()
    return found


def _read_registry(root: Path) -> tuple[str, ...]:
    path = root / _REGISTRY
    if not path.exists():
        return ()
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise SkillError("managed skill registry is unsafe")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillError("managed skill registry is invalid") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise SkillError("managed skill registry is invalid")
    names = raw.get("skills")
    if not isinstance(names, list) or any(
        not isinstance(name, str) or not _SKILL_NAME_RE.fullmatch(name)
        for name in names
    ):
        raise SkillError("managed skill registry is invalid")
    return tuple(names)


def _write_registry(root: Path, names: tuple[str, ...]) -> None:
    path = root / _REGISTRY
    descriptor, temporary = tempfile.mkstemp(prefix=".skills-", dir=root)
    temp_path = Path(temporary)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(
                {"version": 1, "skills": list(names)},
                handle,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temp_path.unlink(missing_ok=True)


def _sync_target(root: Path, sources: dict[str, Path]) -> None:
    if root.is_symlink():
        raise SkillError("provider skill directory is unsafe")
    ensure_private_directories((root,))
    previous = set(_read_registry(root))
    wanted = set(sources)
    for name in sorted(previous - wanted):
        target = root / name
        if not target.is_symlink():
            raise SkillError("managed skill target was replaced")
        target.unlink()
    for name, source in sorted(sources.items()):
        target = root / name
        if target.exists() or target.is_symlink():
            if name not in previous or not target.is_symlink():
                raise SkillError(f"provider skill name is already occupied: {name}")
            if target.resolve() == source:
                continue
            target.unlink()
        target.symlink_to(source, target_is_directory=True)
    _write_registry(root, tuple(sorted(sources)))


def sync_skill_discovery(paths: MundsenAgentPaths) -> SkillSyncResult:
    """Link shared skills into both providers without copying personal files."""

    sources = _skill_sources(paths)
    claude = paths.provider_home / ".claude" / "skills"
    codex = paths.provider_home / ".agents" / "skills"
    _sync_target(claude, sources)
    _sync_target(codex, sources)
    return SkillSyncResult(tuple(sorted(sources)), claude, codex)


def skill_discovery_is_current(paths: MundsenAgentPaths) -> bool:
    try:
        sources = _skill_sources(paths)
        for root in (
            paths.provider_home / ".claude" / "skills",
            paths.provider_home / ".agents" / "skills",
        ):
            if set(_read_registry(root)) != set(sources):
                return False
            for name, source in sources.items():
                target = root / name
                if not target.is_symlink() or target.resolve() != source:
                    return False
        return True
    except (OSError, SkillError):
        return False
