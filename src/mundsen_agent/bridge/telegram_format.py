"""Safe Markdown-to-HTML formatting and chunking for Telegram."""

from __future__ import annotations

import html
import re

TABLE_SEPARATOR_RE = re.compile(
    r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$"
)
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
BOLD_RE = re.compile(r"\*\*([^\n*][^*\n]*?)\*\*")
ITALIC_RE = re.compile(r"(?<!\*)\*([^\n*][^*\n]*?)\*(?!\*)")
_HTML_ENTITY_RE = re.compile(
    r"&(?:#\d+|#x[0-9A-Fa-f]+|amp|lt|gt|quot|apos);",
    re.IGNORECASE,
)
_HTML_CANDIDATE_RE = re.compile(r"</?[A-Za-z][^<>]*>")
_SAFE_HTML_TAG_RE = re.compile(
    r"</?(?:b|strong|i|em|u|ins|s|strike|del|code|pre|tg-spoiler|blockquote)>",
    re.IGNORECASE,
)
_FENCED_CODE_RE = re.compile(r"(```[\s\S]*?```)")


def _escape_text_segment(text: str) -> str:
    """Escape text while preserving already valid HTML entities."""

    def escape_plain(value: str) -> str:
        return (
            value.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    output: list[str] = []
    position = 0
    for match in _HTML_ENTITY_RE.finditer(text):
        output.append(escape_plain(text[position:match.start()]))
        output.append(match.group(0))
        position = match.end()
    output.append(escape_plain(text[position:]))
    return "".join(output)


def escape_preserving_allowed(text: str) -> str:
    """Preserve simple Telegram tags and escape tags with attributes."""

    output: list[str] = []
    position = 0
    for match in _HTML_CANDIDATE_RE.finditer(text):
        output.append(_escape_text_segment(text[position:match.start()]))
        candidate = match.group(0)
        if _SAFE_HTML_TAG_RE.fullmatch(candidate):
            output.append(candidate)
        else:
            output.append(_escape_text_segment(candidate))
        position = match.end()
    output.append(_escape_text_segment(text[position:]))
    return "".join(output)


def split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def is_table_block(lines: list[str], index: int) -> bool:
    return (
        index + 1 < len(lines)
        and "|" in lines[index]
        and bool(TABLE_SEPARATOR_RE.match(lines[index + 1]))
    )


def render_table_block(lines: list[str]) -> str:
    rows = [
        split_table_row(line)
        for line in lines
        if line.strip() and not TABLE_SEPARATOR_RE.match(line)
    ]
    if not rows:
        return ""

    column_count = max(len(row) for row in rows)
    normalized = [row + [""] * (column_count - len(row)) for row in rows]
    widths = [
        max(len(row[index]) for row in normalized)
        for index in range(column_count)
    ]

    def format_row(row: list[str]) -> str:
        cells = " │ ".join(
            row[index].ljust(widths[index]) for index in range(column_count)
        )
        return f"│ {cells} │"

    top = "┌─" + "─┬─".join("─" * width for width in widths) + "─┐"
    middle = "├─" + "─┼─".join("─" * width for width in widths) + "─┤"
    bottom = "└─" + "─┴─".join("─" * width for width in widths) + "─┘"
    rendered = [top, format_row(normalized[0]), middle]
    rendered.extend(format_row(row) for row in normalized[1:])
    rendered.append(bottom)
    return "\n".join(rendered)


def replace_markdown_tables(text: str) -> str:
    lines = text.splitlines()
    output: list[str] = []
    index = 0
    while index < len(lines):
        if is_table_block(lines, index):
            block = [lines[index], lines[index + 1]]
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                block.append(lines[index])
                index += 1
            output.append(f"<pre>{html.escape(render_table_block(block))}</pre>")
            continue

        header = re.match(r"^\s*(#{1,6})\s+(.*\S)\s*$", lines[index])
        if header:
            output.append(f"<b>{html.escape(header.group(2))}</b>")
        else:
            output.append(escape_preserving_allowed(lines[index]))
        index += 1
    return "\n".join(output)


def apply_inline_formatting(text: str) -> str:
    text = INLINE_CODE_RE.sub(lambda match: f"<code>{match.group(1)}</code>", text)
    text = BOLD_RE.sub(lambda match: f"<b>{match.group(1)}</b>", text)
    return ITALIC_RE.sub(lambda match: f"<i>{match.group(1)}</i>", text)


def markdown_to_telegram_html(text: str) -> str:
    segments = _FENCED_CODE_RE.split(text)
    rendered: list[str] = []
    for segment in segments:
        if not segment:
            continue
        if segment.startswith("```") and segment.endswith("```"):
            raw_inner = segment[3:-3]
            if raw_inner.startswith("\n"):
                inner = raw_inner[1:]
            else:
                first_line, separator, remainder = raw_inner.partition("\n")
                if separator and re.fullmatch(r"[A-Za-z0-9_+.-]+", first_line):
                    inner = remainder
                else:
                    inner = raw_inner
            inner = inner.rstrip("\n")
            rendered.append(f"<pre>{html.escape(inner)}</pre>")
        else:
            rendered.append(apply_inline_formatting(replace_markdown_tables(segment)))
    return "".join(rendered)


def _largest_fitting_prefix(text: str, max_html: int) -> int:
    low = 1
    high = len(text)
    best = 0
    while low <= high:
        middle = (low + high) // 2
        if len(markdown_to_telegram_html(text[:middle])) <= max_html:
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    return best


def _split_plain_unit(unit: str, max_html: int) -> list[str]:
    parts: list[str] = []
    remainder = unit
    while remainder:
        split_at = _largest_fitting_prefix(remainder, max_html)
        if split_at <= 0:
            raise ValueError("max_html is too small for Telegram formatting")
        if split_at < len(remainder):
            whitespace = max(
                remainder.rfind(" ", 0, split_at),
                remainder.rfind("\n", 0, split_at),
            )
            if whitespace > 0 and split_at - whitespace < max(20, split_at // 3):
                split_at = whitespace + 1
        parts.append(remainder[:split_at])
        remainder = remainder[split_at:]
    return parts


def _split_fenced_unit(unit: str, max_html: int) -> list[str]:
    match = re.fullmatch(r"```([A-Za-z0-9_+.-]*)\n?([\s\S]*?)```", unit)
    if not match:
        return _split_plain_unit(unit, max_html)
    language = match.group(1)
    body = match.group(2)
    prefix = f"```{language}\n"
    suffix = "\n```"
    if not body:
        return [unit]

    parts: list[str] = []
    remainder = body
    while remainder:
        low = 1
        high = len(remainder)
        best = 0
        while low <= high:
            middle = (low + high) // 2
            candidate = prefix + remainder[:middle] + suffix
            if len(markdown_to_telegram_html(candidate)) <= max_html:
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        if best <= 0:
            raise ValueError("max_html is too small for a fenced code chunk")
        split_at = best
        if best < len(remainder):
            newline = remainder.rfind("\n", 0, best)
            if newline >= 0:
                split_at = newline + 1
        parts.append(prefix + remainder[:split_at] + suffix)
        remainder = remainder[split_at:]
    return parts


def _markdown_units(text: str) -> list[str]:
    units: list[str] = []
    for segment in _FENCED_CODE_RE.split(text):
        if not segment:
            continue
        if segment.startswith("```") and segment.endswith("```"):
            units.append(segment)
        else:
            units.extend(segment.splitlines(keepends=True))
    return units


def chunk_markdown(text: str, max_html: int = 4096) -> list[str]:
    """Split raw Markdown so every independently rendered chunk fits Telegram."""

    if max_html < 64:
        raise ValueError("max_html must be at least 64")
    if not text:
        return [""]

    chunks: list[str] = []
    current = ""
    for unit in _markdown_units(text):
        if len(markdown_to_telegram_html(unit)) > max_html:
            if current:
                chunks.append(current)
                current = ""
            splitter = (
                _split_fenced_unit
                if unit.startswith("```") and unit.endswith("```")
                else _split_plain_unit
            )
            chunks.extend(splitter(unit, max_html))
            continue
        if current and len(markdown_to_telegram_html(current + unit)) > max_html:
            chunks.append(current)
            current = unit
        else:
            current += unit
    if current:
        chunks.append(current)
    if any(len(markdown_to_telegram_html(chunk)) > max_html for chunk in chunks):
        raise AssertionError("internal chunking error")
    return chunks or [""]
