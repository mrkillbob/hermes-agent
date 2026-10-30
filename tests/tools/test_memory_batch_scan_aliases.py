"""Batch aliases share the same memory scanning contract."""

import pytest

from tools.memory_tool import MemoryStore


@pytest.mark.parametrize("field", ["content", "new_text"])
@pytest.mark.parametrize("action", ["add", "replace"])
def test_batch_scans_effective_content_before_mutation(tmp_path, monkeypatch, field, action):
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: tmp_path)
    store = MemoryStore(memory_char_limit=500, user_char_limit=300)
    store.load_from_disk()
    assert store.add("memory", "original fact")["success"]
    path = tmp_path / "MEMORY.md"
    original = path.read_bytes()
    marker = "inert scanner refusal fixture"
    monkeypatch.setattr("tools.memory_tool_store._scan_memory_content", lambda value: "Blocked fixture" if value == marker else None)
    op = {"action": action, field: marker, "old_text": "original fact"}
    for apply in (store.resolve_batch_entries, store.apply_batch):
        result = apply("memory", [op])
        assert result["success"] is False
        assert "Blocked fixture" in result["error"]
        assert store.memory_entries == ["original fact"]
        assert path.read_bytes() == original


@pytest.mark.parametrize("field", ["content", "new_text"])
def test_batch_alias_keeps_legitimate_replace(tmp_path, monkeypatch, field):
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: tmp_path)
    store = MemoryStore(memory_char_limit=500, user_char_limit=300)
    store.load_from_disk()
    assert store.add("memory", "original fact")["success"]
    result = store.apply_batch("memory", [{"action": "replace", "old_text": "original fact", field: "updated fact"}])
    assert result["success"]
    assert store.memory_entries == ["updated fact"]
