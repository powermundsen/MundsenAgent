"""Verified local release installation and atomic rollback."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from mundsen_agent.instance import SCHEMA_VERSION
from mundsen_agent.paths import MundsenAgentPaths, ensure_private_directories

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?$")
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_FILE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 256 * 1024 * 1024
RELEASE_METADATA_NAME = ".mundsen-agent-release.json"
MAX_PROTECTED_FILES = 10_000
MAX_PROTECTED_FILE_BYTES = 64 * 1024 * 1024


class ReleaseError(RuntimeError):
    """Raised when a release is invalid, incompatible, or unsafe."""


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    version: str
    archive: str
    sha256: str
    minimum_instance_schema: int
    maximum_instance_schema: int
    rollback_supported: bool
    modules_changed: tuple[str, ...]
    personal_data_impact: str
    migrations: tuple[dict[str, object], ...]


def load_manifest(path: str | Path) -> ReleaseManifest:
    target = Path(path)
    if target.is_symlink():
        raise ReleaseError("release manifest must not be a symlink")
    try:
        if target.stat().st_size > MAX_MANIFEST_BYTES:
            raise ReleaseError("release manifest is too large")
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseError("release manifest is unreadable or invalid") from exc
    if not isinstance(raw, dict) or raw.get("manifest_version") != 1:
        raise ReleaseError("unsupported release manifest")
    files = raw.get("files")
    if not isinstance(files, list) or len(files) != 1 or not isinstance(files[0], dict):
        raise ReleaseError("release manifest must contain exactly one archive")
    version = str(raw.get("version", ""))
    archive = str(files[0].get("path", ""))
    checksum = str(files[0].get("sha256", "")).lower()
    if not _VERSION_RE.fullmatch(version):
        raise ReleaseError("invalid release version")
    if (
        not archive
        or Path(archive).name != archive
        or not archive.endswith(".tar.gz")
        or not re.fullmatch(r"[a-f0-9]{64}", checksum)
    ):
        raise ReleaseError("invalid release archive metadata")
    minimum = raw.get("minimum_instance_schema")
    maximum = raw.get("maximum_instance_schema")
    rollback = raw.get("rollback_supported")
    modules = raw.get("modules_changed", [])
    impact = raw.get("personal_data_impact", "none")
    migrations = raw.get("migrations", [])
    if (
        isinstance(minimum, bool)
        or not isinstance(minimum, int)
        or isinstance(maximum, bool)
        or not isinstance(maximum, int)
        or minimum < 1
        or maximum < minimum
        or not isinstance(rollback, bool)
        or not isinstance(modules, list)
        or any(
            not isinstance(module, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,79}", module)
            for module in modules
        )
        or not isinstance(impact, str)
        or impact not in {"none", "metadata", "personal-files"}
        or not isinstance(migrations, list)
        or any(not isinstance(migration, dict) for migration in migrations)
    ):
        raise ReleaseError("invalid release compatibility metadata")
    return ReleaseManifest(
        version=version,
        archive=archive,
        sha256=checksum,
        minimum_instance_schema=minimum,
        maximum_instance_schema=maximum,
        rollback_supported=rollback,
        modules_changed=tuple(modules),
        personal_data_impact=impact,
        migrations=tuple(migrations),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _release_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    total_size = 0
    for path in sorted(root.rglob("*")):
        if path.name == RELEASE_METADATA_NAME:
            continue
        if path.is_symlink():
            raise ReleaseError("installed release contains a symlink")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ReleaseError("installed release contains an unsupported path")
        size = path.stat().st_size
        if size > MAX_ARCHIVE_FILE_BYTES:
            raise ReleaseError("installed release contains an oversized file")
        total_size += size
        if total_size > MAX_ARCHIVE_TOTAL_BYTES:
            raise ReleaseError("installed release is too large")
        relative = path.relative_to(root).as_posix()
        hashes[relative] = _sha256(path)
        if len(hashes) > MAX_ARCHIVE_MEMBERS:
            raise ReleaseError("installed release contains too many files")
    return hashes


def payload_matches_release(
    payload_dir: str | Path,
    release_dir: str | Path,
) -> bool:
    """Report whether a staged installer payload equals an installed release.

    The installer uses this to refuse a reinstall that would otherwise leave
    an older build in place under a version number that has been rebuilt.
    """

    payload = Path(payload_dir)
    installed = Path(release_dir)
    if (
        not payload.is_absolute()
        or not installed.is_absolute()
        or payload.is_symlink()
        or installed.is_symlink()
        or not payload.is_dir()
        or not installed.is_dir()
    ):
        raise ReleaseError("release comparison paths are unsafe")
    return _release_hashes(payload) == _release_hashes(installed)


def record_installed_release(
    release_dir: str | Path,
    *,
    version: str,
    minimum_instance_schema: int = SCHEMA_VERSION,
    maximum_instance_schema: int = SCHEMA_VERSION,
    rollback_supported: bool = True,
    archive_sha256: str = "source-installer",
) -> None:
    """Record a private integrity manifest inside one installed release."""

    root = Path(release_dir)
    if (
        not root.is_absolute()
        or root.is_symlink()
        or not root.is_dir()
        or root.name != version
        or not _VERSION_RE.fullmatch(version)
        or isinstance(minimum_instance_schema, bool)
        or isinstance(maximum_instance_schema, bool)
        or minimum_instance_schema < 1
        or maximum_instance_schema < minimum_instance_schema
    ):
        raise ReleaseError("installed release metadata is invalid")
    metadata = root / RELEASE_METADATA_NAME
    if metadata.is_symlink():
        raise ReleaseError("installed release metadata is unsafe")
    payload = {
        "metadata_version": 1,
        "version": version,
        "minimum_instance_schema": minimum_instance_schema,
        "maximum_instance_schema": maximum_instance_schema,
        "rollback_supported": bool(rollback_supported),
        "archive_sha256": archive_sha256,
        "files": _release_hashes(root),
    }
    descriptor, temporary = tempfile.mkstemp(
        prefix=".release-metadata-",
        dir=root,
    )
    temp_path = Path(temporary)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, metadata)
        os.chmod(metadata, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temp_path.unlink(missing_ok=True)


def installed_release_metadata(release_dir: str | Path) -> dict[str, object]:
    root = Path(release_dir)
    metadata = root / RELEASE_METADATA_NAME
    if (
        not root.is_absolute()
        or root.is_symlink()
        or not root.is_dir()
        or metadata.is_symlink()
    ):
        raise ReleaseError("installed release is unsafe")
    try:
        file_stat = metadata.stat()
        if (
            not stat.S_ISREG(file_stat.st_mode)
            or stat.S_IMODE(file_stat.st_mode) != 0o600
            or file_stat.st_size > MAX_MANIFEST_BYTES
        ):
            raise ReleaseError("installed release metadata is unsafe")
        raw = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseError("installed release metadata is missing or invalid") from exc
    if not isinstance(raw, dict) or raw.get("metadata_version") != 1:
        raise ReleaseError("installed release metadata is invalid")
    version = raw.get("version")
    minimum = raw.get("minimum_instance_schema")
    maximum = raw.get("maximum_instance_schema")
    rollback = raw.get("rollback_supported")
    archive_sha = raw.get("archive_sha256")
    files = raw.get("files")
    if (
        not isinstance(version, str)
        or not _VERSION_RE.fullmatch(version)
        or version != root.name
        or isinstance(minimum, bool)
        or not isinstance(minimum, int)
        or isinstance(maximum, bool)
        or not isinstance(maximum, int)
        or minimum < 1
        or maximum < minimum
        or not isinstance(rollback, bool)
        or not isinstance(archive_sha, str)
        or not archive_sha
        or not isinstance(files, dict)
        or any(
            not isinstance(name, str)
            or not isinstance(digest, str)
            or not re.fullmatch(r"[a-f0-9]{64}", digest)
            for name, digest in files.items()
        )
    ):
        raise ReleaseError("installed release metadata is invalid")
    if files != _release_hashes(root):
        raise ReleaseError("installed release integrity check failed")
    return raw


def verify_installed_release(
    release_dir: str | Path,
    *,
    require_compatible: bool = True,
) -> dict[str, object]:
    metadata = installed_release_metadata(release_dir)
    if require_compatible and not (
        int(metadata["minimum_instance_schema"])
        <= SCHEMA_VERSION
        <= int(metadata["maximum_instance_schema"])
    ):
        raise ReleaseError("installed release is incompatible with the instance schema")
    return metadata


def protected_snapshot(paths: MundsenAgentPaths) -> dict[str, str]:
    """Hash personal/configuration files without copying their contents."""

    roots = (
        ("instance", paths.instance_dir),
        ("config/runtime.env", paths.runtime_config_file),
        ("config/secrets.env", paths.secrets_file),
    )
    snapshot: dict[str, str] = {}
    for label, root in roots:
        if root.is_symlink():
            raise ReleaseError(f"protected path is a symlink: {label}")
        candidates = sorted(root.rglob("*")) if root.is_dir() else [root]
        for path in candidates:
            if not path.exists():
                continue
            if path.is_symlink():
                raise ReleaseError("protected personal files must not be symlinks")
            if path.is_dir():
                continue
            if not path.is_file() or path.stat().st_size > MAX_PROTECTED_FILE_BYTES:
                raise ReleaseError("protected personal file is unsafe")
            if root.is_dir():
                name = f"{label}/{path.relative_to(root).as_posix()}"
            else:
                name = label
            snapshot[name] = _sha256(path)
            if len(snapshot) > MAX_PROTECTED_FILES:
                raise ReleaseError("too many protected personal files")
    return snapshot


def create_upgrade_backup(
    paths: MundsenAgentPaths,
    *,
    current_version: str,
    snapshot: dict[str, str],
) -> Path:
    """Back up non-secret schema/config files before activation."""

    ensure_private_directories((paths.backups_dir,))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = paths.backups_dir / f"{stamp}-{current_version or 'none'}"
    suffix = 0
    while backup.exists():
        suffix += 1
        backup = paths.backups_dir / f"{stamp}-{current_version or 'none'}-{suffix}"
    backup.mkdir(mode=0o700)
    os.chmod(backup, 0o700)
    for source, name in (
        (paths.instance_dir / "instance.yaml", "instance.yaml"),
        (paths.runtime_config_file, "runtime.env"),
    ):
        if source.is_file() and not source.is_symlink():
            target = backup / name
            shutil.copyfile(source, target)
            os.chmod(target, 0o600)
    snapshot_file = backup / "protected-hashes.json"
    snapshot_file.write_text(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.chmod(snapshot_file, 0o600)
    return backup


def _validate_member(member: tarfile.TarInfo) -> PurePosixPath:
    relative = PurePosixPath(member.name)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or not relative.parts
        or member.issym()
        or member.islnk()
        or member.isdev()
        or member.size < 0
        or member.size > MAX_ARCHIVE_FILE_BYTES
    ):
        raise ReleaseError("release archive contains an unsafe entry")
    return relative


def _extract_archive(archive: Path, destination: Path) -> Path:
    top_levels: set[str] = set()
    seen_paths: set[PurePosixPath] = set()
    total_size = 0
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            members = bundle.getmembers()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise ReleaseError("release archive contains too many entries")
            for member in members:
                relative = _validate_member(member)
                if relative in seen_paths:
                    raise ReleaseError("release archive contains duplicate entries")
                seen_paths.add(relative)
                total_size += member.size
                if total_size > MAX_ARCHIVE_TOTAL_BYTES:
                    raise ReleaseError("release archive is too large")
                top_levels.add(relative.parts[0])
                output = destination.joinpath(*relative.parts)
                if member.isdir():
                    output.mkdir(mode=0o700, parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise ReleaseError("release archive contains an unsupported entry")
                output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source = bundle.extractfile(member)
                if source is None:
                    raise ReleaseError("release archive contains an unreadable file")
                with source, output.open("wb") as handle:
                    shutil.copyfileobj(source, handle)
                os.chmod(output, 0o700 if member.mode & 0o111 else 0o600)
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseError("release archive could not be extracted") from exc
    if len(top_levels) != 1:
        raise ReleaseError("release archive must have one top-level directory")
    release_root = destination / next(iter(top_levels))
    if not (release_root / "src" / "mundsen_agent" / "__init__.py").is_file():
        raise ReleaseError("release archive does not contain Mundsen Agent")
    return release_root


def _atomic_current(paths: MundsenAgentPaths, target: Path) -> None:
    if (
        target.parent != paths.releases_dir
        or target.is_symlink()
        or not target.is_dir()
        or target.resolve().parent != paths.releases_dir.resolve()
    ):
        raise ReleaseError("release target is outside the release directory")
    temporary = paths.home / ".current-next"
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    os.replace(temporary, paths.current_release)


def active_version(paths: MundsenAgentPaths) -> str:
    if not paths.current_release.is_symlink() or not paths.current_release.exists():
        return ""
    resolved = paths.current_release.resolve()
    return (
        resolved.name
        if resolved.parent == paths.releases_dir.resolve()
        else ""
    )


def activate_release(paths: MundsenAgentPaths, version: str) -> str:
    if not _VERSION_RE.fullmatch(version):
        raise ReleaseError("invalid release version")
    target = paths.releases_dir / version
    verify_installed_release(target)
    _atomic_current(paths, target)
    return version


def install_release(paths: MundsenAgentPaths, manifest_path: str | Path) -> str:
    supplied_manifest = Path(manifest_path)
    if supplied_manifest.is_symlink():
        raise ReleaseError("release manifest must not be a symlink")
    manifest = load_manifest(supplied_manifest)
    try:
        manifest_file = supplied_manifest.resolve(strict=True)
    except OSError as exc:
        raise ReleaseError("release manifest is unreadable or invalid") from exc
    if not (
        manifest.minimum_instance_schema
        <= SCHEMA_VERSION
        <= manifest.maximum_instance_schema
    ):
        raise ReleaseError("release is incompatible with the instance schema")
    if manifest.migrations:
        raise ReleaseError(
            "this Mundsen Agent version does not support release migrations"
        )
    archive = manifest_file.parent / manifest.archive
    if (
        archive.is_symlink()
        or not archive.is_file()
        or _sha256(archive) != manifest.sha256
    ):
        raise ReleaseError("release archive checksum mismatch")
    ensure_private_directories((paths.home, paths.releases_dir))
    target = paths.releases_dir / manifest.version
    if target.is_symlink():
        raise ReleaseError("release target must not be a symlink")
    if not target.exists():
        with tempfile.TemporaryDirectory(
            prefix=".release-", dir=paths.releases_dir
        ) as temp:
            extracted = _extract_archive(archive, Path(temp))
            staged = Path(temp) / manifest.version
            if extracted != staged:
                os.replace(extracted, staged)
            record_installed_release(
                staged,
                version=manifest.version,
                minimum_instance_schema=manifest.minimum_instance_schema,
                maximum_instance_schema=manifest.maximum_instance_schema,
                rollback_supported=manifest.rollback_supported,
                archive_sha256=manifest.sha256,
            )
            verify_installed_release(staged)
            os.replace(staged, target)
    else:
        metadata = verify_installed_release(target)
        if metadata.get("archive_sha256") != manifest.sha256:
            raise ReleaseError(
                "existing release version does not match the verified archive"
            )
    previous = paths.current_release.resolve() if paths.current_release.exists() else None
    try:
        _atomic_current(paths, target)
    except Exception:
        if previous is not None and previous.is_dir():
            _atomic_current(paths, previous)
        raise
    return manifest.version


def rollback_release(paths: MundsenAgentPaths, version: str = "") -> str:
    current = active_version(paths)
    if version:
        if not _VERSION_RE.fullmatch(version):
            raise ReleaseError("invalid rollback version")
        candidates = [paths.releases_dir / version]
    else:
        candidates = sorted(
            (
                path
                for path in paths.releases_dir.iterdir()
                if (
                    path.is_dir()
                    and not path.is_symlink()
                    and path.name != current
                    and _VERSION_RE.fullmatch(path.name)
                )
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    if not candidates or not candidates[0].is_dir():
        raise ReleaseError("no rollback release is available")
    target = candidates[0]
    metadata = verify_installed_release(target)
    if metadata.get("rollback_supported") is not True:
        raise ReleaseError("rollback release does not support rollback")
    _atomic_current(paths, target)
    return target.name
