from __future__ import annotations

import os
import tempfile

import tools.delegate_tool as dt


class _FakeCompressor:
    def __init__(self, context_length, max_tokens):
        self.context_length = context_length
        self.max_tokens = max_tokens


class _FakeParent:
    def __init__(self):
        self.context_compressor = _FakeCompressor(context_length=100_000, max_tokens=8_000)
        self._last_turn_usage = {"prompt_tokens": 99_900}  # ~no headroom


def test_summary_budget_cap_enforced_zero_headroom_sets_truncated_flag(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("HERMES_HOME", os.path.join(td, ".hermes"))
        big = "X" * 100_000
        results = [{"task_index": 0, "summary": big, "status": "completed"}]
        dt._apply_summary_budget(results, _FakeParent())
        assert results[0].get("summary_truncated") is True
        assert len(results[0]["summary"]) < len(big)
        assert results[0].get("summary_full_path")
