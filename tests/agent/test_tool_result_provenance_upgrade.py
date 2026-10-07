"""Tool-result provenance survives an in-flight worker's cached pre-update builder."""

from hashlib import sha256
from types import SimpleNamespace

import pytest

from agent import source_provenance_tools, tool_executor
from agent.tool_dispatch_helpers import make_tool_result_message
from tools.budget_config import DEFAULT_BUDGET


@pytest.mark.parametrize("cached_legacy_builder", [True, False], ids=["cached-n1", "current"])
@pytest.mark.parametrize("has_provenance", [True, False], ids=["trusted-read", "no-provenance"])
def test_commit_retains_provenance_with_cached_builder(
    monkeypatch, cached_legacy_builder, has_provenance
):
    content = '{"content": "safe = True"}'
    envelope = {
        "request_id": "turn-upgrade:api:2",
        "source_grant_digests": ("a" * 64,),
        "content_sha256": sha256(content.encode("utf-8")).hexdigest(),
        "presentation_kind": "read_file_json_v1",
    } if has_provenance else None

    # N-1's cached helper accepts effect_disposition, but not source_provenance.
    def legacy_builder(name, value, tool_call_id, *, effect_disposition=None):
        return make_tool_result_message(
            name, value, tool_call_id, effect_disposition=effect_disposition
        )

    monkeypatch.setattr(
        tool_executor, "make_tool_result_message",
        legacy_builder if cached_legacy_builder else make_tool_result_message,
    )
    monkeypatch.setattr(tool_executor, "get_active_env", lambda _task: None)
    monkeypatch.setattr(tool_executor, "maybe_persist_tool_result", lambda **kwargs: kwargs["content"])
    monkeypatch.setattr(tool_executor, "note_tool_result", lambda *_args: None)
    monkeypatch.setattr(
        source_provenance_tools, "attach_trusted_source_provenance_metadata",
        lambda _agent, _name, *, content: envelope,
    )
    stages = []

    def flush(_agent, messages, *, stage):
        message = messages[-1]
        if has_provenance:
            assert message["_source_provenance"] == envelope
            assert message["_source_provenance"] is not envelope
        else:
            assert "_source_provenance" not in message
        stages.append("persisted")
        return True

    monkeypatch.setattr(tool_executor, "_flush_session_db_after_tool_progress", flush)
    agent = SimpleNamespace(
        _touch_activity=lambda *_args: None,
        _subdirectory_hints=SimpleNamespace(check_tool_call=lambda *_args: None),
        _tool_result_content_for_active_model=lambda _name, result: result,
        tool_result_metadata_callback=None,
        tool_progress_callback=lambda *_args, **_kwargs: stages.append("completed"),
    )
    messages = []
    ref = tool_executor._ToolCallRef("read_file", {"path": "source.py"}, "upgrade-task", "call-read", [])
    result = tool_executor._commit_tool_result(
        agent, messages, ref, content, budget=DEFAULT_BUDGET, tool_duration=0.0,
        is_error=False, blocked=False, effect_disposition="none",
    )

    assert result is not None
    assert stages == ["persisted", "completed"]
    assert len(messages) == 1
    message = messages[0]
    assert message["content"] == content
    assert message["tool_call_id"] == "call-read"
    assert message["name"] == message["tool_name"] == "read_file"
    assert message["effect_disposition"] == "none"
    sidecar = source_provenance_tools.build_source_provenance_sidecar(messages)
    if has_provenance:
        assert sidecar == [{**envelope, "message_index": 0, "tool_call_id": "call-read"}]
        envelope["content_sha256"] = "b" * 64
        assert message["_source_provenance"]["content_sha256"] == sha256(content.encode("utf-8")).hexdigest()
        assert source_provenance_tools.build_source_provenance_sidecar(messages) == sidecar
    else:
        assert sidecar == []
