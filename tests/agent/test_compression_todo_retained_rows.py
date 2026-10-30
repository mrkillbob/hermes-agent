"""Refreshing a derived todo exchange cannot duplicate its surrounding human turn."""

import copy
import json
from unittest.mock import patch

import pytest

from agent.conversation_compression import _fold_todo_snapshot, compress_context
from agent.compression_todo_reference import is_todo_state_result
from agent.context_compressor import SUMMARY_PREFIX
from hermes_state import SessionDB
from tests.agent.test_in_place_compaction import _make_agent


@pytest.mark.parametrize("mixed_call", [False, True])
def test_repeated_todo_compaction_rewinds_the_complete_original_tail(tmp_path, mixed_call):
    db = SessionDB(db_path=tmp_path / "state.db")
    sid = "todo-retained-tail"
    db.create_session(sid, "cli", model="test/model")
    agent = _make_agent(db, sid, in_place=True)
    agent._compression_feasibility_checked = True
    agent._todo_store.write([{"id": "1", "content": "saved work", "status": "pending"}])
    tail = [{"role": "user", "content": "actual redirect"},
            {"role": "assistant", "content": "delivered reply"}]
    _fold_todo_snapshot(agent, tail)
    if mixed_call:
        call_row = next(m for m in tail if m.get("tool_calls"))
        call_row["tool_calls"].append({"id": "other", "type": "function",
                                       "function": {"name": "terminal", "arguments": "{}"}})
        tail.insert(-1, {"role": "tool", "tool_call_id": "other", "content": "other result"})
    for message in [{"role": "user", "content": "earlier history " * 2000},
                    {"role": "assistant", "content": "earlier reply"}, *tail]:
        db.append_message(sid, message["role"], message.get("content"),
                          tool_calls=message.get("tool_calls"), tool_call_id=message.get("tool_call_id"),
                          tool_name=message.get("tool_name"))
    original = db.get_messages_as_conversation(sid, include_row_ids=True)
    kept = copy.deepcopy(original[-len(tail):])
    for message in kept:
        message["_compaction_tail"] = True
    agent.context_compressor.compress = lambda *args, **kwargs: [
        {"role": "user", "content": SUMMARY_PREFIX + " earlier conversation"}, *copy.deepcopy(kept)]
    with patch("agent.conversation_compression._rebuild_system_prompt_at_boundary", return_value="sys"):
        compressed, _ = compress_context(agent, original, "sys", approx_tokens=100_000)
    assert agent._last_compaction_in_place
    assert sum(m.get("content") == "actual redirect" for m in compressed) == 1
    display = db.get_messages_as_conversation(sid, include_row_ids=True, include_compacted=True)
    assert sum(m.get("content") == "actual redirect" for m in display) == 1
    assert sum(m.get("content") == "delivered reply" for m in display) == 1
    active = db.get_messages_as_conversation(sid, include_row_ids=True)
    live_user = next(m for m in active if m.get("content") == "actual redirect")
    displayed_user = next(m for m in display if m.get("content") == "actual redirect")
    assert displayed_user["_row_id"] == live_user["_row_id"]
    originals = db._conn.execute("SELECT active, compacted FROM messages WHERE session_id=? AND content=? ORDER BY id", (sid, "actual redirect")).fetchall()
    assert [tuple(row) for row in originals] == [(0, 0), (1, 0)]
    result = next(m for m in compressed if is_todo_state_result(m))
    assert json.loads(result["content"])["todos"] == agent._todo_store.snapshot()["todos"]
    db.close()
