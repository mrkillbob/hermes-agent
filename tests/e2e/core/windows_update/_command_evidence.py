"""Allowlisted observations of one fixture command, copied with its transcript.

Elapsed times describe observation, not npm progress or a diagnosis. No argv,
environment, PID, raw output, exception messages or npm debug logs are retained.
"""

import contextlib
import json
import threading
import time
from pathlib import Path


_PHASE_MARKERS = (
    ("dependencies_finished", "[OK] dependencies installed"),
    ("source_build_started", "-> Building the hermes command and apps"),
    ("isolated_runtime_started", "Preparing the isolated Hermes runtime"),
    ("node_dependencies_started", "Preparing Node dependencies"),
    ("npm_ci_started", "node-deps: installing workspace dependencies with npm ci"),
    ("npm_ci_retry_started", "node-deps: npm ci hit ENOTEMPTY"),
    ("node_dependencies_reused", "node-deps: reusing completed install"),
    ("tui_build_started", "Building the TUI"),
)


class CommandEvidence:
    def __init__(self, path: Path, started: float, timeout: float):
        self.path, self.started, self.timeout = path, started, timeout
        self._lock = threading.Lock()
        self._phases: dict[str, float] = {}
        self._last_output_s: float | None = None
        self.timeout_reached_s: float | None = None
        self.observed_returncode_before_cleanup: int | None = None
        self.termination_started = False
        self.taskkill_returncode: int | None = None

    def observe_line(self, line: str, elapsed: float) -> None:
        with self._lock:
            self._last_output_s = round(elapsed, 3)
            for phase, marker in _PHASE_MARKERS:
                if marker in line and phase not in self._phases:
                    self._phases[phase] = round(elapsed, 3)

    def save(self, *, returncode: int | None, reader_finished: bool) -> None:
        with self._lock:
            detail = {
                "schema": 1,
                "timeout_s": self.timeout,
                "elapsed_s": round(time.monotonic() - self.started, 3),
                "last_output_s": self._last_output_s,
                "phases": [{"phase": phase, "elapsed_s": elapsed} for phase, elapsed in self._phases.items()],
                "timed_out": self.timeout_reached_s is not None,
                "timeout_reached_s": self.timeout_reached_s,
                "observed_returncode_before_cleanup": self.observed_returncode_before_cleanup,
                "termination_started": self.termination_started,
                "taskkill_returncode": self.taskkill_returncode,
                "returncode": returncode,
                "reader_finished": reader_finished,
            }
        # An artifact write must not replace the command's result or cleanup failure.
        with contextlib.suppress(OSError):
            self.path.write_text(json.dumps(detail, indent=2) + "\n", encoding="utf-8")
