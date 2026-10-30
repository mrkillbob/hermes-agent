"""Context-hint paths have one portable wire form without pretending to change host OS."""
from pathlib import PurePosixPath, PureWindowsPath

import pytest

from agent.subdirectory_hints import SubdirectoryHintTracker


@pytest.mark.parametrize('root,hint', [
    (PureWindowsPath('C:/repo'), PureWindowsPath('C:/repo/gateway/AGENTS.md')),
    (PurePosixPath('/repo'), PurePosixPath('/repo/gateway/AGENTS.md')),
])
def test_relative_hint_display_is_posix_path_data(root, hint):
    tracker = object.__new__(SubdirectoryHintTracker)
    tracker.working_dir = root
    assert tracker._display_path(hint) == 'gateway/AGENTS.md'


@pytest.mark.platforms('windows')
def test_native_windows_guidance_composite_passes_verified_source_presentation(tmp_path, monkeypatch):
    from agent.llm_egress_runtime import authorize_agent_sdk_kwargs
    from agent.source_provenance_tools import attach_trusted_source_provenance_metadata, source_provenance_activation
    from agent.tool_dispatch_helpers import make_tool_result_message
    from tests.agent.test_llm_egress_runtime import _agent
    from tools.file_tools import read_file_tool

    monkeypatch.setenv('HERMES_KANBAN_PROTECTED_REMOTE', '1')
    root = tmp_path / 'repo'
    area = root / 'gateway'
    area.mkdir(parents=True)
    source = area / 'module.py'
    source.write_text('value = 1\n', newline='')
    (area / 'AGENTS.md').write_text('Follow repository guidance.\n', newline='')
    agent = _agent(tmp_path / 'egress')
    agent._current_api_request_id = 'turn-1:api:1'
    tracker = SubdirectoryHintTracker(str(root))
    with source_provenance_activation(agent, 'read_file'):
        result = read_file_tool(str(source), task_id='native-windows-guidance')
        hints = tracker.check_tool_call('read_file', {'path': str(source)})
    assert hints == '\n\n[Subdirectory context discovered: gateway/AGENTS.md]\nFollow repository guidance.'
    combined = result + hints
    metadata = attach_trusted_source_provenance_metadata(agent, 'read_file', content=combined)
    message = make_tool_result_message('read_file', combined, 'native_guidance_call', source_provenance=metadata)
    agent._current_api_request_id = 'turn-1:api:2'
    authorized, receipt = authorize_agent_sdk_kwargs(agent, {'model': 'test-model', 'messages': [message]})
    assert authorized['messages'][0]['content'] == combined
    assert receipt.decision.source_grant_count == 2
    assert receipt.decision.source_segment_count == 2
