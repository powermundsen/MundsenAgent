"""Read-only GitHub Release discovery and bounded asset download."""

from __future__ import annotations

import json
import hashlib
import os
import re
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from mundsen_agent.paths import ensure_private_directories
from mundsen_agent.release import (
    MAX_ARCHIVE_TOTAL_BYTES,
    MAX_MANIFEST_BYTES,
    ReleaseError,
    ReleaseManifest,
    load_manifest,
)

DEFAULT_REPOSITORY = "powermundsen/MundsenAgent"
MAX_RELEASE_METADATA_BYTES = 1024 * 1024
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?$")
_ALLOWED_REDIRECT_HOSTS = frozenset(
    {
        "api.github.com",
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }
)


class UpdateError(RuntimeError):
    """Raised when release discovery or download is unsafe or unavailable."""


@dataclass(frozen=True, slots=True)
class GitHubRelease:
    repository: str
    version: str
    tag: str
    title: str
    notes: str
    page_url: str
    published_at: str
    manifest_asset_url: str
    archive_asset_url: str
    archive_name: str


@dataclass(frozen=True, slots=True)
class UpdateNotification:
    key: str
    text: str


JsonFetcher = Callable[[str, str], bytes]
AssetDownloader = Callable[[str, Path, str, int], None]
ManifestFetcher = Callable[..., ReleaseManifest]


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        response: object,
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> urllib.request.Request | None:
        parsed = urlparse(new_url)
        if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_REDIRECT_HOSTS:
            raise UpdateError("GitHub redirected a release request to an untrusted host")
        redirected = super().redirect_request(
            request,
            response,
            code,
            message,
            headers,
            new_url,
        )
        if redirected is not None and parsed.hostname != "api.github.com":
            redirected.remove_header("Authorization")
        return redirected


def _open(request: urllib.request.Request, *, timeout: float):
    return urllib.request.build_opener(_SafeRedirectHandler()).open(
        request,
        timeout=timeout,
    )


def _headers(token: str, *, binary: bool = False) -> dict[str, str]:
    if "\n" in token or "\r" in token or len(token) > 1024:
        raise UpdateError("invalid GitHub credential")
    headers = {
        "Accept": (
            "application/octet-stream"
            if binary
            else "application/vnd.github+json"
        ),
        "User-Agent": "MundsenAgent-update-check",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _fetch_json(url: str, token: str) -> bytes:
    request = urllib.request.Request(url, headers=_headers(token))
    try:
        with _open(request, timeout=20) as response:
            data = response.read(MAX_RELEASE_METADATA_BYTES + 1)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise UpdateError("GitHub release metadata is unavailable") from exc
    if len(data) > MAX_RELEASE_METADATA_BYTES:
        raise UpdateError("GitHub release metadata is too large")
    return data


def _download_asset(url: str, target: Path, token: str, limit: int) -> None:
    request = urllib.request.Request(url, headers=_headers(token, binary=True))
    descriptor, temporary = tempfile.mkstemp(prefix=".download-", dir=target.parent)
    temp_path = Path(temporary)
    total = 0
    try:
        os.fchmod(descriptor, 0o600)
        try:
            response = _open(request, timeout=60)
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            raise UpdateError("GitHub release asset is unavailable") from exc
        with response, os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise UpdateError("GitHub release asset is too large")
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
        os.chmod(target, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temp_path.unlink(missing_ok=True)


def _asset_api_url(raw: object, *, repository: str) -> str:
    if not isinstance(raw, str):
        raise UpdateError("GitHub release contains an invalid asset URL")
    parsed = urlparse(raw)
    prefix = f"/repos/{repository}/releases/assets/"
    if parsed.scheme != "https" or parsed.netloc != "api.github.com" or not parsed.path.startswith(prefix):
        raise UpdateError("GitHub release contains an untrusted asset URL")
    return raw


def _page_url(raw: object, *, repository: str) -> str:
    if not isinstance(raw, str):
        raise UpdateError("GitHub release contains an invalid page URL")
    parsed = urlparse(raw)
    prefix = f"/{repository}/releases/tag/"
    if parsed.scheme != "https" or parsed.netloc != "github.com" or not parsed.path.startswith(prefix):
        raise UpdateError("GitHub release contains an untrusted page URL")
    return raw


def fetch_latest_release(
    repository: str = DEFAULT_REPOSITORY,
    *,
    token: str = "",
    fetch_json: JsonFetcher = _fetch_json,
) -> GitHubRelease:
    """Fetch and validate the latest stable GitHub Release metadata."""

    if not _REPOSITORY_RE.fullmatch(repository):
        raise UpdateError("invalid GitHub repository")
    api_url = f"https://api.github.com/repos/{repository}/releases/latest"
    try:
        raw = json.loads(fetch_json(api_url, token).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise UpdateError("GitHub release metadata is invalid") from exc
    if not isinstance(raw, dict) or raw.get("draft") is True or raw.get("prerelease") is True:
        raise UpdateError("GitHub did not return a stable release")
    tag = raw.get("tag_name")
    if not isinstance(tag, str):
        raise UpdateError("GitHub release tag is missing")
    version = tag[1:] if tag.startswith("v") else tag
    if not _VERSION_RE.fullmatch(version):
        raise UpdateError("GitHub release version is invalid")
    assets = raw.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("GitHub release assets are missing")
    archive_name = f"mundsen-agent-{version}.tar.gz"
    selected: dict[str, str] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        if name not in {"release-manifest.json", archive_name}:
            continue
        if name in selected:
            raise UpdateError("GitHub release contains duplicate required assets")
        selected[name] = _asset_api_url(asset.get("url"), repository=repository)
    if set(selected) != {"release-manifest.json", archive_name}:
        raise UpdateError("GitHub release is missing required assets")
    title = raw.get("name") if isinstance(raw.get("name"), str) else tag
    notes = raw.get("body") if isinstance(raw.get("body"), str) else ""
    published = raw.get("published_at") if isinstance(raw.get("published_at"), str) else ""
    return GitHubRelease(
        repository=repository,
        version=version,
        tag=tag,
        title=" ".join(title.split())[:160],
        notes=notes.strip()[:8000],
        page_url=_page_url(raw.get("html_url"), repository=repository),
        published_at=published[:64],
        manifest_asset_url=selected["release-manifest.json"],
        archive_asset_url=selected[archive_name],
        archive_name=archive_name,
    )


def version_is_newer(candidate: str, current: str) -> bool:
    """Compare stable semantic versions without importing package tooling."""

    def parse(value: str) -> tuple[int, int, int]:
        base = value.split("-", 1)[0]
        if not _VERSION_RE.fullmatch(value):
            raise UpdateError("invalid release version")
        return tuple(int(part) for part in base.split("."))  # type: ignore[return-value]

    return parse(candidate) > parse(current)


def fetch_release_manifest(
    release: GitHubRelease,
    *,
    token: str = "",
    downloader: AssetDownloader = _download_asset,
) -> ReleaseManifest:
    """Fetch only the small release manifest and validate it against the tag."""

    with tempfile.TemporaryDirectory(prefix="mundsen-agent-manifest-") as temporary:
        path = Path(temporary) / "release-manifest.json"
        downloader(
            release.manifest_asset_url,
            path,
            token,
            MAX_MANIFEST_BYTES,
        )
        try:
            manifest = load_manifest(path)
        except ReleaseError as exc:
            raise UpdateError("downloaded release manifest is invalid") from exc
    if manifest.version != release.version or manifest.archive != release.archive_name:
        raise UpdateError("GitHub release assets do not match the release tag")
    return manifest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_release(
    release: GitHubRelease,
    destination: str | Path,
    *,
    token: str = "",
    downloader: AssetDownloader = _download_asset,
) -> Path:
    """Download a manifest and archive into a private version directory."""

    root = Path(destination)
    if not root.is_absolute() or root.is_symlink():
        raise UpdateError("release download directory is unsafe")
    ensure_private_directories((root,))
    manifest_path = root / "release-manifest.json"
    archive_path = root / release.archive_name
    for path in (manifest_path, archive_path):
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise UpdateError("release download target is unsafe")
    downloader(
        release.manifest_asset_url,
        manifest_path,
        token,
        MAX_MANIFEST_BYTES,
    )
    try:
        manifest = load_manifest(manifest_path)
    except ReleaseError as exc:
        raise UpdateError("downloaded release manifest is invalid") from exc
    if manifest.version != release.version or manifest.archive != release.archive_name:
        raise UpdateError("GitHub release assets do not match the release tag")
    downloader(
        release.archive_asset_url,
        archive_path,
        token,
        MAX_ARCHIVE_TOTAL_BYTES,
    )
    if _sha256(archive_path) != manifest.sha256:
        archive_path.unlink(missing_ok=True)
        raise UpdateError("downloaded release archive checksum mismatch")
    return manifest_path


class UpdateNotificationEngine:
    """Perform a weekly metadata-only update check with bounded retries."""

    def __init__(
        self,
        *,
        state_file: str | Path,
        repository: str,
        current_version: str,
        token: str = "",
        enabled: bool = True,
        language: str = "nb",
        now: Callable[[], datetime] | None = None,
        fetch_release: Callable[..., GitHubRelease] = fetch_latest_release,
        fetch_manifest: ManifestFetcher = fetch_release_manifest,
        check_interval: timedelta = timedelta(days=7),
        retry_interval: timedelta = timedelta(hours=12),
    ) -> None:
        self.state_file = Path(state_file)
        self.repository = repository
        self.current_version = current_version
        self.token = token
        self.enabled = enabled
        self.language = language
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.fetch_release = fetch_release
        self.fetch_manifest = fetch_manifest
        self.check_interval = check_interval
        self.retry_interval = retry_interval
        if check_interval.total_seconds() <= 0 or retry_interval.total_seconds() <= 0:
            raise ValueError("update check intervals must be positive")

    def pending(self) -> list[UpdateNotification]:
        if not self.enabled:
            return []
        state = self._load_state()
        clock = self.now()
        if clock.tzinfo is None:
            clock = clock.replace(tzinfo=timezone.utc)
        clock = clock.astimezone(timezone.utc)
        last_success = self._state_time(state, "checked_at")
        last_attempt = self._state_time(state, "attempted_at")
        if last_success is not None and clock - last_success < self.check_interval:
            return []
        if last_attempt is not None and clock - last_attempt < self.retry_interval:
            return []
        state["attempted_at"] = clock.isoformat()
        self._save_state(state)
        try:
            release = self.fetch_release(self.repository, token=self.token)
            manifest = self.fetch_manifest(release, token=self.token)
        except UpdateError:
            return []
        state["checked_at"] = clock.isoformat()
        self._save_state(state)
        if not version_is_newer(release.version, self.current_version):
            return []
        if state.get("notified_version") == release.version:
            return []
        english = self.language.lower().startswith("en")
        modules = ", ".join(manifest.modules_changed) or "core"
        migrations = len(manifest.migrations)
        text = (
            f"Mundsen Agent {release.version} is available. Modules: {modules}. "
            f"Personal-data impact: {manifest.personal_data_impact}. "
            f"Migrations: {migrations}. Run `mundsen update check` to review it. "
            "Installation still requires explicit approval."
            if english
            else f"Mundsen Agent {release.version} er tilgjengelig. Moduler: {modules}. "
            f"Persondataeffekt: {manifest.personal_data_impact}. "
            f"Migreringer: {migrations}. Kjør `mundsen update check` for detaljer. "
            "Installasjon krever fortsatt eksplisitt godkjenning."
        )
        return [UpdateNotification(f"update:{release.version}", text)]

    def mark_sent(self, key: str) -> None:
        if not key.startswith("update:"):
            raise UpdateError("invalid update notification key")
        version = key.split(":", 1)[1]
        if not _VERSION_RE.fullmatch(version):
            raise UpdateError("invalid update notification key")
        state = self._load_state()
        state["notified_version"] = version
        self._save_state(state)

    def _load_state(self) -> dict[str, str]:
        if not self.state_file.exists():
            return {}
        if (
            not self.state_file.is_absolute()
            or self.state_file.is_symlink()
            or not self.state_file.is_file()
            or (self.state_file.stat().st_mode & 0o077)
        ):
            raise UpdateError("update state is unsafe")
        try:
            value = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpdateError("update state is invalid") from exc
        if not isinstance(value, dict) or any(
            not isinstance(key, str) or not isinstance(item, str)
            for key, item in value.items()
        ):
            raise UpdateError("update state is invalid")
        return value

    @staticmethod
    def _state_time(state: dict[str, str], key: str) -> datetime | None:
        raw = state.get(key)
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise UpdateError("update state is invalid") from exc
        if parsed.tzinfo is None:
            raise UpdateError("update state is invalid")
        return parsed.astimezone(timezone.utc)

    def _save_state(self, state: dict[str, str]) -> None:
        if not self.state_file.is_absolute() or self.state_file.is_symlink():
            raise UpdateError("update state is unsafe")
        ensure_private_directories((self.state_file.parent,))
        descriptor, temporary = tempfile.mkstemp(
            prefix=".update-state-", dir=self.state_file.parent
        )
        temp_path = Path(temporary)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                json.dump(state, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.state_file)
            os.chmod(self.state_file, 0o600)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temp_path.unlink(missing_ok=True)
