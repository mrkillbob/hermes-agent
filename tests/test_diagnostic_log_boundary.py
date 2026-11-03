"""Hermes log creation/queue boundaries, without exporting diagnostic payloads."""
import logging
import sys
from queue import SimpleQueue
from types import SimpleNamespace

import pytest

from agent.diagnostic_reference_policy import (
    DiagnosticPolicy, bind_agent, release_turn_context, sealed_output_boundary,
)
from agent.local_text_policy import local_text_only
import hermes_logging


@pytest.mark.parametrize('sealed', [False, True])
@pytest.mark.parametrize('fail', [False, True])
def test_factory_and_queue_withhold_private_message_exception_and_late_extras(sealed, fail):
    records = []
    queue = SimpleQueue()
    queued = hermes_logging._NonFormattingQueueHandler(queue)

    class Recorder(logging.Handler):
        def emit(self, record):
            records.append(logging.Formatter().format(record))

    logger = logging.getLogger('fixture.reference.logs')
    direct = Recorder()
    logger.addHandler(direct)
    logger.addHandler(queued)
    agent = SimpleNamespace(session_id='fixture')
    policy = DiagnosticPolicy('fixed host request', '{}', 'fixture')
    if sealed:
        bind_agent(agent, policy)

    @sealed_output_boundary
    def run(owner):
        try:
            raise ValueError('PRIVATE-LOG-ERROR')
        except ValueError:
            logger.exception('PRIVATE-LOG-MESSAGE %s', 'PRIVATE-LOG-ARG',
                             extra={'request_payload': {'body': 'PRIVATE-LOG-EXTRA'}})
        if fail:
            raise ValueError('PRIVATE-OUTER-ERROR')
        release_turn_context(owner, policy if sealed else None)
        return {'completed': True}

    try:
        if fail and not sealed:
            with pytest.raises(ValueError, match='PRIVATE-OUTER-ERROR'):
                run(agent)
        else:
            result = run(agent)
            if fail:
                assert result['failed'] is True
        prepared = queue.get_nowait()
        if sealed:
            assert 'PRIVATE-LOG' not in '\n'.join(records)
            assert not hasattr(prepared, 'request_payload')
            assert prepared.args == () and prepared.exc_info is None and prepared.exc_text is None
        else:
            assert 'PRIVATE-LOG-MESSAGE' in records[0] and 'PRIVATE-LOG-ERROR' in records[0]
            assert prepared.request_payload == {'body': 'PRIVATE-LOG-EXTRA'}
        assert local_text_only() is False
        logger.warning('ordinary-after-scope')
        assert records[-1] == 'ordinary-after-scope'
    finally:
        logger.removeHandler(direct)
        logger.removeHandler(queued)


def test_queue_drops_payload_extra_even_when_message_itself_is_harmless():
    queue = SimpleQueue()
    handler = hermes_logging._NonFormattingQueueHandler(queue)
    logger = logging.getLogger('fixture.reference.extra')
    logger.addHandler(handler)
    agent = SimpleNamespace(session_id='fixture-extra')
    policy = DiagnosticPolicy('fixed host request', '{}', agent.session_id)
    bind_agent(agent, policy)

    @sealed_output_boundary
    def run(owner):
        logger.warning('harmless message', extra={'private_payload': {'body': 'PRIVATE-LATE-EXTRA'}})
        release_turn_context(owner, policy)
        return {}

    try:
        run(agent)
        prepared = queue.get_nowait()
        assert not hasattr(prepared, 'private_payload')
        assert prepared.getMessage() == '[diagnostic execution log withheld]'
    finally:
        logger.removeHandler(handler)


def test_logging_remains_available_before_agent_policy_module_is_loaded(monkeypatch):
    monkeypatch.delitem(sys.modules, 'agent.diagnostic_reference_policy')
    record = logging.getLogger('fixture.bootstrap').makeRecord(
        'fixture.bootstrap', logging.WARNING, '', 0, 'ordinary bootstrap', (), None)
    prepared = hermes_logging._NonFormattingQueueHandler(SimpleQueue()).prepare(record)
    assert prepared.getMessage() == 'ordinary bootstrap'
    assert 'agent.diagnostic_reference_policy' not in sys.modules


def test_private_context_does_not_grant_execution_to_a_different_agent():
    agent, foreign = SimpleNamespace(session_id='fixture'), SimpleNamespace(session_id='foreign')
    bind_agent(agent, DiagnosticPolicy('fixed host request', '{}', agent.session_id))
    called = []

    @sealed_output_boundary
    def other(owner):
        called.append(owner)
        return {}

    @sealed_output_boundary
    def run(owner):
        return other(foreign)

    result = run(agent)
    assert result['failed'] is True and called == []
    assert local_text_only() is False
