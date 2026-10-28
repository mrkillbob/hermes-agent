"""Explicit no-op retries report feedback through the real agent callback."""

from copy import deepcopy

from agent.conversation_compression_manual import CompressRequest, compress_now
from tests.e2e.core.compaction._helpers import TurnSpec, db_rows
from tests.fakes.fake_llm_provider import Text


def test_manual_no_progress_retry_warns_and_preserves_durable_history(make_scenario):
    sc = make_scenario("good")
    marker = "U77x0xabcdef"
    sc.run_turn(TurnSpec(marker, marker, [Text("answer " + marker)], "chat"))
    history = deepcopy(sc.history)
    session_id = sc.agent.session_id
    rows = db_rows(sc.db_path, sc.session_id)
    summary_calls = len(sc.summary_calls)

    for expected_warnings in (1, 2):
        result = compress_now(sc.agent, sc.history, CompressRequest())

        warnings = [status for status in sc.statuses
                    if status[0] == "warn" and "Compression made no progress" in str(status[1])]
        assert len(warnings) == expected_warnings
        assert result.after_messages == history
        assert sc.history == history
        assert sc.agent.session_id == session_id
        assert db_rows(sc.db_path, sc.session_id) == rows
        assert len(sc.summary_calls) == summary_calls
        assert sc.committed_compactions() == 0
