"""Session overlays participate in both real tool selection caches."""
from concurrent.futures import ThreadPoolExecutor
import pytest


@pytest.mark.parametrize("warm_inside", [False, True])
def test_session_catalog_caches_follow_nested_and_concurrent_overlays(tmp_path, warm_inside):
    import model_tools
    import toolsets
    from hermes_constants import get_hermes_home
    from tools.registry import registry, session_tool_scope

    (get_hermes_home() / "config.yaml").write_text('tools:\n  tool_search:\n    enabled: "off"\n')
    scope = str(tmp_path / "editor-overlay")
    wrong = str(tmp_path / "other-overlay")
    toolset = "mcp-cache-fixture"
    names = {f"mcp__cache_fixture__operation_{i}" for i in range(4)}
    for name in sorted(names):
        registry.register(name, toolset, {"name": name, "parameters": {"type": "object", "properties": {}}},
                          lambda args, **kwargs: "unused", scope=scope)

    def snapshot(active):
        with session_tool_scope(active):
            resolved = set(toolsets.resolve_toolset(toolset))
            definitions = model_tools.get_tool_definitions(enabled_toolsets=[toolset], quiet_mode=True)
            return resolved, {tool["function"]["name"] for tool in definitions}

    try:
        expected = (names, names)
        empty = (set(), set())
        assert snapshot(scope if warm_inside else None) == (expected if warm_inside else empty)
        assert snapshot(None if warm_inside else scope) == (empty if warm_inside else expected)
        with session_tool_scope(scope):
            assert snapshot(wrong) == empty
            assert snapshot(scope) == expected
            with session_tool_scope(wrong):
                assert snapshot(None) == empty
            assert set(toolsets.resolve_toolset(toolset)) == names
        assert snapshot(None) == empty
        with ThreadPoolExecutor(max_workers=3) as pool:
            assert list(pool.map(snapshot, [scope, wrong, None])) == [expected, empty, empty]
    finally:
        for name in names:
            registry.deregister(name, scope=scope)
