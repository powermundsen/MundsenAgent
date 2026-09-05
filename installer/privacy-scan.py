#!/usr/bin/env python3
"""Conservative current-tree privacy and secret gate for Mundsen Agent."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
import unicodedata
from pathlib import Path


PATTERNS = {
    "absolute macOS user path": re.compile(rb"/Users/[A-Za-z0-9._-]+/"),
    "absolute Linux user path": re.compile(
        rb"(?<![A-Za-z0-9_.-])/home/[A-Za-z0-9._-]+/"
    ),
    "GitHub token": re.compile(rb"(?:github_pat_|ghp_)[A-Za-z0-9_]{12,}"),
    "Telegram bot token": re.compile(rb"\b\d{8,}:[A-Za-z0-9_-]{20,}\b"),
    "private IPv4 address": re.compile(
        rb"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
    ),
    "private runtime file": re.compile(
        rb"(?:^|/)(?:secrets\.env|audit\.jsonl|health\.sqlite3|export\.xml)$"
    ),
}


MAX_SCAN_BYTES = 8 * 1024 * 1024
_SYNTHETIC_FIXTURES = (
    b"123456789:abcdefghijklmnopqrstuvwxyzABCDE",
    b"999999999:abcdefghijklmnopqrstuvwxyzABCDE",
)
_PRIVATE_IDENTIFIER_HASHES = frozenset(
    {
        "7838cb6e2b2f5ecbc12a2e6b06e9cfa568ad13f13e35d7c3e3436d756d665cd4",
        "b2a2af3b9e82592079d3bc5b94c1a726a3c3259a542521065d0a55597f2e4d98",
    }
)


def working_tree_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        check=True,
        stdout=subprocess.PIPE,
    )
    return [Path(value) for value in result.stdout.decode().split("\0") if value]


def _scan_payload(
    data: bytes,
    *,
    display_path: str,
    repository_path: str,
    patterns: dict[str, re.Pattern[bytes]],
) -> list[str]:
    findings: list[str] = []
    sanitized = data
    for fixture in _SYNTHETIC_FIXTURES:
        sanitized = sanitized.replace(fixture, b"[synthetic-telegram-token]")
    eligible_lines = sanitized.splitlines()
    for label, pattern in patterns.items():
        if label == "private runtime file":
            matched = pattern.search(repository_path.encode())
        else:
            matched = any(pattern.search(line) for line in eligible_lines)
        if matched:
            findings.append(f"{display_path}: {label}")
    normalized = unicodedata.normalize(
        "NFKC",
        b"\n".join(eligible_lines).decode("utf-8", errors="ignore"),
    ).casefold()
    for token in re.findall(r"[^\W_]+", normalized):
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        if digest in _PRIVATE_IDENTIFIER_HASHES:
            findings.append(f"{display_path}: reserved private identifier")
            break
    return findings


def _history_blobs() -> list[tuple[str, str]]:
    result = subprocess.run(
        ["git", "rev-list", "--objects", "--all"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    candidates: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        object_id, separator, path = line.partition(" ")
        if separator and path:
            candidates.append((object_id, path))
    return candidates


def _scan_history(patterns: dict[str, re.Pattern[bytes]]) -> list[str]:
    findings: list[str] = []
    seen: set[tuple[str, str]] = set()
    for object_id, path in _history_blobs():
        identity = (object_id, path)
        if identity in seen or path == "installer/privacy-scan.py":
            continue
        seen.add(identity)
        kind = subprocess.run(
            ["git", "cat-file", "-t", object_id],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        if kind != "blob":
            continue
        size = int(
            subprocess.run(
                ["git", "cat-file", "-s", object_id],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout
        )
        if size > MAX_SCAN_BYTES:
            findings.append(f"history:{object_id[:12]}:{path}: oversized file")
            continue
        data = subprocess.run(
            ["git", "cat-file", "blob", object_id],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout
        findings.extend(
            _scan_payload(
                data,
                display_path=f"history:{object_id[:12]}:{path}",
                repository_path=path,
                patterns=patterns,
            )
        )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--deny-text", action="append", default=[])
    args = parser.parse_args(argv)
    patterns = dict(PATTERNS)
    for index, value in enumerate(args.deny_text, start=1):
        normalized = value.strip()
        if not normalized or len(normalized) > 200 or "\x00" in normalized:
            parser.error("--deny-text must be 1 to 200 visible characters")
        patterns[f"private deny text {index}"] = re.compile(
            re.escape(normalized.encode("utf-8")),
            re.IGNORECASE,
        )

    findings: list[str] = []
    for path in working_tree_files():
        if not path.is_file() or path.is_symlink():
            continue
        if path == Path("installer/privacy-scan.py"):
            continue
        if path.stat().st_size > MAX_SCAN_BYTES:
            findings.append(f"{path}: oversized file")
            continue
        findings.extend(
            _scan_payload(
                path.read_bytes(),
                display_path=str(path),
                repository_path=str(path),
                patterns=patterns,
            )
        )
    if args.history:
        findings.extend(_scan_history(patterns))
    if findings:
        print("Privacy scan failed:", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        return 1
    scope = "working tree and Git history" if args.history else "working tree"
    print(f"Privacy scan passed for {scope}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
