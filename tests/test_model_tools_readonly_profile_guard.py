import json
import importlib


def test_readonly_profile_blocks_disallowed_tool(monkeypatch):
    monkeypatch.setenv("HERMES_MCP_READONLY", "1")
    # Reload model_tools to pick up env and any module-level decisions
    m = importlib.import_module("model_tools")
    importlib.reload(m)
    out = m.handle_function_call("write_file", {"path": "x.txt", "content": "hi"})
    body = json.loads(out)
    assert "error" in body and "not allowed" in body["error"].lower()


def test_readonly_profile_allows_allowlist_tools(monkeypatch):
    monkeypatch.setenv("HERMES_MCP_READONLY", "1")
    m = importlib.import_module("model_tools")
    importlib.reload(m)
    # web_search should pass the early guard; execution may still fail elsewhere but not by guard
    out = m.handle_function_call("web_search", {"query": "hermes agent"})
    body = json.loads(out)
    # Either a structured result or an error from the underlying tool, but not our profile_readonly error
    assert not ("error" in body and "profile" in body["error"].lower())
