from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from mundsen_agent.router.models import RouterMode, RouterState
from mundsen_agent.router.state import RouterStateError, RouterStateStore


class TestRouterStateStore(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.path = Path(self.tempdir.name) / "state" / "router.json"
        self.store = RouterStateStore(self.path)

    def test_missing_state_returns_defaults(self) -> None:
        self.assertEqual(self.store.load(), RouterState())

    def test_round_trip_is_private_and_atomic(self) -> None:
        state = RouterState(
            mode=RouterMode.CODEX,
            claude_session_id="session-1",
            codex_session_id="thread-1",
            last_agent="codex",
            last_error_category="",
        )
        self.store.save(state)

        self.assertEqual(self.store.load(), state)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(
            stat.S_IMODE(self.path.parent.stat().st_mode),
            0o700,
        )

    def test_world_readable_state_is_rejected(self) -> None:
        self.store.save(RouterState())
        os.chmod(self.path, 0o644)

        with self.assertRaises(RouterStateError):
            self.store.load()

    def test_invalid_session_identifier_is_rejected(self) -> None:
        with self.assertRaises(RouterStateError):
            self.store.save(RouterState(claude_session_id="../private"))

    def test_symlink_state_is_rejected(self) -> None:
        real = Path(self.tempdir.name) / "real.json"
        real.write_text("{}\n", encoding="utf-8")
        os.chmod(real, 0o600)
        self.path.parent.mkdir(mode=0o700)
        self.path.symlink_to(real)

        with self.assertRaises(RouterStateError):
            self.store.load()


if __name__ == "__main__":
    unittest.main()
