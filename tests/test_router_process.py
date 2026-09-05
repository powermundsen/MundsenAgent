from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest

from mundsen_agent.router.process import run_command


class TestControlledProcess(unittest.TestCase):
    def test_passes_stdin_without_a_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = run_command(
                [
                    sys.executable,
                    "-c",
                    "import sys; print(sys.stdin.read())",
                ],
                cwd=temp,
                env=os.environ,
                timeout_seconds=2,
                input_text="$(touch should-not-exist)",
            )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.returncode, 0)
        self.assertIn("$(touch should-not-exist)", result.stdout)

    def test_timeout_terminates_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = run_command(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                cwd=temp,
                env=os.environ,
                timeout_seconds=1,
            )

        self.assertEqual(result.status, "timeout")
        self.assertNotEqual(result.returncode, 0)

    def test_cancel_event_terminates_process(self) -> None:
        event = threading.Event()
        timer = threading.Timer(0.1, event.set)
        timer.start()
        self.addCleanup(timer.cancel)
        with tempfile.TemporaryDirectory() as temp:
            started = time.monotonic()
            result = run_command(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                cwd=temp,
                env=os.environ,
                timeout_seconds=4,
                cancel_event=event,
            )

        self.assertEqual(result.status, "cancelled")
        self.assertLess(time.monotonic() - started, 2)


if __name__ == "__main__":
    unittest.main()
