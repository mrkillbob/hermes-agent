"""Supersession evidence refusal and durable audit rollback, without completion hooks."""
import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_db_connect import connect_closing
from hermes_cli.kanban_task_revision import task_revision
from hermes_cli.kanban_transfer import _selected_task_rows
from hermes_cli.kanban_source_supersession import archive_source_supersession
from tests.hermes_cli.test_kanban_source_supersession import _make_case, decision


@pytest.fixture
def case(tmp_path, monkeypatch):
    return _make_case(tmp_path, monkeypatch)


@pytest.mark.parametrize('overrides', [
    {'repository': '../private'}, {'pull_request': True}, {'pull_request': 0},
    {'merge_sha': 'a' * 39}, {'reason_code': 'runtime_completed'},
    {'finding_comment_id': True}, {'finding_comment_id': -1},
])
def test_source_supersession_refuses_malformed_evidence(case, overrides):
    from dataclasses import replace

    with connect_closing(board='source-review') as conn:
        before = _selected_task_rows(conn, [case])
        revision = task_revision('source-review', case, before)
        with pytest.raises(ValueError):
            archive_source_supersession(conn, case, board='source-review',
                                        expected_revision=revision,
                                        evidence=replace(decision(), **overrides))
        assert _selected_task_rows(conn, [case]) == before


def test_source_supersession_rolls_back_status_if_audit_append_fails(case, monkeypatch):
    def refuse_append(*args, **kwargs):
        raise RuntimeError('synthetic audit failure')

    with connect_closing(board='source-review') as conn:
        before = _selected_task_rows(conn, [case])
        revision = task_revision('source-review', case, before)
        monkeypatch.setattr(kb, '_append_event', refuse_append)
        with pytest.raises(RuntimeError, match='synthetic audit failure'):
            archive_source_supersession(conn, case, board='source-review',
                                        expected_revision=revision, evidence=decision())
        assert _selected_task_rows(conn, [case]) == before
