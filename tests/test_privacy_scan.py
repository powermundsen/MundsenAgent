from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path


def _load_scanner():
    path = Path(__file__).resolve().parents[1] / "installer" / "privacy-scan.py"
    spec = importlib.util.spec_from_file_location("mundsen_agent_privacy_scan", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("privacy scanner could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestPrivacyScan(unittest.TestCase):
    def test_synthetic_token_does_not_hide_other_finding_on_same_line(self) -> None:
        scanner = _load_scanner()
        findings = scanner._scan_payload(
            b"123456789:abcdefghijklmnopqrstuvwxyzABCDE PRIVATE_PERSON",
            display_path="fixture.txt",
            repository_path="fixture.txt",
            patterns={
                "Telegram bot token": scanner.PATTERNS["Telegram bot token"],
                "private deny text": re.compile(b"PRIVATE_PERSON"),
            },
        )

        self.assertEqual(findings, ["fixture.txt: private deny text"])


if __name__ == "__main__":
    unittest.main()
