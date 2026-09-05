"""Opt-in extraction of inline visual blocks into private local artifacts."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from mundsen_agent.paths import MundsenAgentPaths, ensure_private_directories

MAX_VISUALIZATION_BYTES = 256 * 1024
MAX_VISUALIZATIONS = 4
_FENCE_RE = re.compile(r"```(svg|mermaid)\s*\n(.*?)```", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True, slots=True)
class RenderedResponse:
    text: str
    artifacts: tuple[Path, ...] = ()


class VisualizationRenderer:
    def __init__(
        self,
        paths: MundsenAgentPaths,
        *,
        language: str = "nb",
        mermaid_render_command: str = "",
    ) -> None:
        self.paths = paths
        self.language = language
        self.mermaid_render_command = mermaid_render_command

    def render(self, update_id: int, text: str) -> RenderedResponse:
        artifacts: list[Path] = []
        index = 0

        def replace(match: re.Match[str]) -> str:
            nonlocal index
            if len(artifacts) >= MAX_VISUALIZATIONS:
                return match.group(0)
            kind = match.group(1).lower()
            source = match.group(2).strip()
            if not source or len(source.encode("utf-8")) > MAX_VISUALIZATION_BYTES:
                return match.group(0)
            if kind == "svg" and not self._safe_svg(source):
                return match.group(0)
            index += 1
            artifact = self._write_artifact(update_id, index, kind, source)
            artifacts.append(artifact)
            label = "visualisering" if not self.language.lower().startswith("en") else "visualization"
            return f"[{kind.upper()} {label} {index}, se vedlegg]"

        rendered = _FENCE_RE.sub(replace, text)
        return RenderedResponse(rendered, tuple(artifacts))

    def cleanup_job(self, update_id: int) -> None:
        root = self.paths.attachments_dir
        target = root / f"job-{update_id}"
        if root.is_absolute() and target.parent == root and not target.is_symlink():
            if target.is_dir():
                shutil.rmtree(target)

    def _write_artifact(
        self,
        update_id: int,
        index: int,
        kind: str,
        source: str,
    ) -> Path:
        directory = self.paths.attachments_dir / f"job-{update_id}"
        ensure_private_directories((self.paths.attachments_dir, directory))
        suffix = ".svg" if kind == "svg" else ".mmd"
        target = directory / f"visualization-{index}{suffix}"
        self._atomic_write(target, source + "\n")
        if kind == "mermaid" and self.mermaid_render_command:
            rendered = directory / f"visualization-{index}.svg"
            if self._render_mermaid(target, rendered):
                return rendered
        return target

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix=".visual-", dir=path.parent)
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
            temp_path.unlink(missing_ok=True)

    def _render_mermaid(self, source: Path, output: Path) -> bool:
        command = Path(self.mermaid_render_command)
        if (
            not command.is_absolute()
            or command.is_symlink()
            or not command.is_file()
            or not os.access(command, os.X_OK)
        ):
            return False
        environment = {
            key: value
            for key, value in os.environ.items()
            if key in {"HOME", "LANG", "LC_ALL", "PATH", "TMPDIR"}
        }
        try:
            result = subprocess.run(
                [str(command), str(source), str(output)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=120,
                check=False,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        valid_file = (
            result.returncode == 0
            and output.is_file()
            and not output.is_symlink()
            and 0 < output.stat().st_size <= MAX_VISUALIZATION_BYTES
        )
        if not valid_file:
            output.unlink(missing_ok=True)
            return False
        try:
            rendered = output.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            output.unlink(missing_ok=True)
            return False
        if not self._safe_svg(rendered):
            output.unlink(missing_ok=True)
            return False
        os.chmod(output, 0o600)
        return True

    @staticmethod
    def _safe_svg(source: str) -> bool:
        lowered = source.lower()
        forbidden_fragments = (
            "<!doctype",
            "<!entity",
            "<?xml-stylesheet",
            "javascript:",
            "url(",
            "@import",
            "expression(",
        )
        if any(fragment in lowered for fragment in forbidden_fragments):
            return False
        try:
            root = ET.fromstring(source)
        except ET.ParseError:
            return False
        if not isinstance(root.tag, str):
            return False
        root_namespace, root_name = VisualizationRenderer._xml_name(root.tag)
        if root_name != "svg" or root_namespace not in {
            "",
            "http://www.w3.org/2000/svg",
        }:
            return False
        blocked_elements = {
            "a",
            "animate",
            "animatemotion",
            "animatetransform",
            "audio",
            "discard",
            "embed",
            "foreignobject",
            "iframe",
            "image",
            "object",
            "script",
            "set",
            "style",
            "use",
            "video",
        }
        for element in root.iter():
            if not isinstance(element.tag, str):
                continue
            namespace, name = VisualizationRenderer._xml_name(element.tag)
            if namespace not in {"", "http://www.w3.org/2000/svg"}:
                return False
            if name in blocked_elements:
                return False
            for raw_attribute in element.attrib:
                _, attribute = VisualizationRenderer._xml_name(raw_attribute)
                if attribute.startswith("on") or attribute in {"href", "src"}:
                    return False
        return True

    @staticmethod
    def _xml_name(value: str) -> tuple[str, str]:
        if value.startswith("{") and "}" in value:
            namespace, local = value[1:].split("}", 1)
            return namespace, local.lower()
        return "", value.lower()
