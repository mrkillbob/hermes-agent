"""A control-socket identity can precede the replacement gateway's HTTP listener."""
from types import SimpleNamespace

import pytest

from tests.e2e.core.upgrade.handoff import _handoff as X


def _recorded_poll(monkeypatch, observations):
    """Drive polling deterministically; no network, processes or wall-clock waits."""
    state = {}
    seen = []
    monkeypatch.setattr(X, 'identify', lambda home: state['identity'])
    monkeypatch.setattr(X, 'health', lambda port: state['health'])

    def poll(predicate, **kwargs):
        for identity, health in observations:
            state.update(identity=identity, health=health)
            seen.append((identity, health))
            if value := predicate():
                return value
        raise AssertionError('recorded gateway never became ready')

    monkeypatch.setattr(X, 'wait_for', poll)
    return seen


def test_settle_waits_for_the_new_gateway_http_listener(tmp_path, monkeypatch):
    inst = SimpleNamespace(hermes_home=tmp_path, target='target-commit', port=12345)
    replacement = {'pid': 22, 'code_sha': inst.target}
    seen = _recorded_poll(monkeypatch, [
        (replacement, None),                  # identify is up; HTTP still refuses connections
        (replacement, {'pid': 11}),           # another/old listener is not the replacement
        (replacement, {'pid': replacement['pid']}),
    ])
    assert X.settle_gateway(inst, old_pid=11) == replacement
    assert len(seen) == 3


@pytest.mark.parametrize('last_identity,last_health', [
    (None, None),                            # replacement dies before opening HTTP
    ({'pid': 11, 'code_sha': 'target-commit'}, {'pid': 11}),
    ({'pid': 22, 'code_sha': 'wrong-commit'}, {'pid': 22}),
    ({'pid': 22, 'code_sha': 'target-commit'}, {'status': 'ok'}),
])
def test_settle_rejects_incomplete_or_stale_relaunch(tmp_path, monkeypatch, last_identity, last_health):
    inst = SimpleNamespace(hermes_home=tmp_path, target='target-commit', port=12345)
    _recorded_poll(monkeypatch, [
        ({'pid': 22, 'code_sha': inst.target}, None),
        (last_identity, last_health),
    ])
    assert X.settle_gateway(inst, old_pid=11) is None
