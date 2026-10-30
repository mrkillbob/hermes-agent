"""The existing exact claim binds and cancels the optional text restriction."""
import pytest


def test_text_restriction_cannot_be_removed_from_window_claim(tmp_path, monkeypatch):
    from gateway import drain_kanban as window
    monkeypatch.setenv('HERMES_HOME',str(tmp_path))
    (tmp_path/'ESTOP').write_text('hold')
    target = dict(board='default',db=str(tmp_path/'kanban.db'),task_id='t_public',profile_id=str(tmp_path))
    value = window.begin(target,seconds=900)
    window.arm('owner',window_id=value['window_id'])
    budget = dict(wall_seconds=60,output_bytes=8192,requests=1,local_text_only=True)
    claim = dict(target,run_id=1,claim_lock='claim',execution_budget=budget)
    window.bind_claim(claim)
    assert window.permitted(claim,instance_id='owner')
    weakened = dict(claim,execution_budget={k:v for k,v in budget.items() if k != 'local_text_only'})
    assert not window.permitted(weakened,instance_id='owner')
    assert not window.permitted(claim,instance_id='other')
    window.cancel(window_id=value['window_id'])
    assert window.held() and not window.permitted(claim,instance_id='owner')
    assert (tmp_path/'ESTOP').read_text() == 'hold'


def test_restricted_kanban_policy_always_uses_managed_worker(monkeypatch):
    from types import SimpleNamespace
    from gateway.session_managed_worker import managed_policy
    from gateway import session_policy
    policy = SimpleNamespace(ignore_user_config=True,kanban_json='{}')
    monkeypatch.setattr(session_policy,'policy_for_source',lambda *args:policy)
    authority = SimpleNamespace(runner=None,sessions={'s':SimpleNamespace(source=None)})
    assert managed_policy(authority,SimpleNamespace(session_id='s')) is policy
