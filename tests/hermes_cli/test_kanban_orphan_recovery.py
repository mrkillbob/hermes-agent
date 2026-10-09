"""Exact orphan maintenance against real disposable SQLite boards."""
from __future__ import annotations

from contextlib import closing, contextmanager
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc


def api():
    from hermes_cli import kanban_orphan_recovery
    return kanban_orphan_recovery


def open_connection(path):
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def connect(path):
    with closing(open_connection(path)) as conn:
        yield conn


def row(path, table, key):
    with connect(path) as c:
        return dict(c.execute(f'SELECT * FROM {table} WHERE id=?', (key,)).fetchone())


@pytest.fixture
def boards(tmp_path, request):
    home = tmp_path / 'home'
    selection = []
    paths = {}
    # Deliberately overlapping run IDs: board identity must disambiguate them.
    for board in ('default', 'tradingbot-burndown'):
        path = home / 'kanban.db' if board == 'default' else home / 'kanban/boards' / board / 'kanban.db'
        path.parent.mkdir(parents=True, exist_ok=True)
        kbc.init_db(path)
        with connect(path) as c:
            run_ids = range(1, 6) if getattr(request, 'param', False) and board != 'default' else (1, 2)
            for run_id in run_ids:
                task = f't_{board}_{run_id}'
                c.execute("INSERT INTO tasks(id,title,body,assignee,status,priority,created_by,created_at,workspace_path,workspace_base_sha) VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (task, 'retained terminal card', '原文', 'owner', 'done' if board == 'default' else 'archived', 1, 'operator', 1, 'preserved/wip', 'base'))
                c.execute("INSERT INTO task_runs(id,task_id,status,profile,claim_lock,claim_expires,worker_pid,worker_started_at,started_at,last_heartbeat_at,metadata,summary,error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (run_id, task, 'running', 'original-profile', f'{socket.gethostname()}:42', 2, 99999999, '|12300', 1, 2, '{ "retained": "é" }', 'retain summary', 'retain error'))
                c.execute("INSERT INTO task_comments(task_id,author,body,created_at) VALUES(?,?,?,?)", (task, 'author', 'retain comment', 1))
                selection.append({'board': board, 'run_id': run_id, 'task_id': task})
            c.execute("INSERT INTO tasks(id,title,status,priority,created_by,created_at) VALUES('unrelated','foreign card','ready',1,'foreign',1)")
            # Existing closed successor history is reviewed, retained and bound.
            c.execute("INSERT INTO task_runs(id,task_id,status,started_at,ended_at,outcome) VALUES(?,?, 'done',3,4,'completed')", (1001 if len(run_ids)>2 else 3, f't_{board}_1'))
        paths[board] = path
    return home, selection, paths, tmp_path / 'receipts'


def prepared(boards):
    home, selection, _, _ = boards
    return api().prepare(home, selection)


def apply(plan, out):
    return api().apply(plan, out, expected_digest=api().digest(plan))


def test_exact_closure_preserves_terminal_card_and_raw_provenance(boards):
    home, selection, paths, out = boards
    before = {board: row(path, 'tasks', f't_{board}_1') for board, path in paths.items()}
    runs = {board: row(path, 'task_runs', 1) for board, path in paths.items()}
    if importlib.util.find_spec('hermes_cli.kanban_orphan_recovery') is None:
        # Existing closure follows task.current_run_id and cannot close this orphan.
        with connect(paths['default']) as c:
            kb._end_run(c, selection[0]['task_id'], outcome='reclaimed', status='reclaimed')
    else:
        apply(prepared(boards), out)
    for board, path in paths.items():
        after = row(path, 'task_runs', 1)
        assert after['status'] == 'reclaimed', 'An exact orphan run needs administrative closure'
        assert after['outcome'] == 'reclaimed'
        assert after['ended_at'] >= time.time() - 10
        for k in runs[board]:
            if k not in ('status', 'outcome', 'ended_at'):
                assert after[k] == runs[board][k]
        assert row(path, 'tasks', f't_{board}_1') == before[board]
        assert row(path, 'tasks', 'unrelated')['status'] == 'ready'
        with connect(path) as c:
            assert c.execute('SELECT body FROM task_comments').fetchall()[0][0] == 'retain comment'
            assert c.execute("SELECT count(*) FROM task_events WHERE kind='orphan_run_reconciled'").fetchone()[0] == 2


def test_dry_run_is_read_only_and_explicit_boards_ignore_ambient_pin(boards, monkeypatch):
    home, selection, paths, _ = boards
    before = {b: p.read_bytes() for b, p in paths.items()}
    monkeypatch.setenv('HERMES_KANBAN_DB', str(paths['default']))
    monkeypatch.setenv('HERMES_KANBAN_BOARD', 'default')
    plan = prepared(boards)
    assert len({v['database']['path'] for v in plan['boards']}) == 2
    assert before == {b: p.read_bytes() for b, p in paths.items()}
    assert len(plan['boards'][0]['rows']) == 2


@pytest.mark.parametrize('change', ['running_card', 'task_claim', 'current_run', 'task_pid', 'lease', 'foreign_host', 'bad_birth', 'open_successor'])
def test_unsafe_selection_refused_without_any_changes(boards, change):
    _, _, paths, _ = boards
    with connect(paths['default']) as c:
        changes = {
            'running_card': "UPDATE tasks SET status='running' WHERE id='t_default_1'",
            'task_claim': "UPDATE tasks SET claim_lock='new-owner' WHERE id='t_default_1'",
            'current_run': "UPDATE tasks SET current_run_id=3 WHERE id='t_default_1'",
            'task_pid': "UPDATE tasks SET worker_pid=123 WHERE id='t_default_1'",
            'lease': f"UPDATE task_runs SET claim_expires={int(time.time())+1000} WHERE id=1",
            'foreign_host': "UPDATE task_runs SET claim_lock='foreign.invalid:42' WHERE id=1",
            'bad_birth': "UPDATE task_runs SET worker_started_at='unverified' WHERE id=1",
            'open_successor': "UPDATE task_runs SET ended_at=NULL,status='running' WHERE id=3",
        }
        c.execute(changes[change])
    with pytest.raises(api().RecoveryRefused):
        prepared(boards)
    assert row(paths['tradingbot-burndown'], 'task_runs', 1)['status'] == 'running'


@pytest.mark.parametrize('state', ['live', 'unknown'])
def test_worker_probe_refuses_live_or_unknown_identity(boards, state):
    home, selection, _, _ = boards
    with pytest.raises(api().RecoveryRefused):
        api().prepare(home, selection, worker_probe=lambda run: state)


def test_real_current_process_identity_is_refused(boards):
    _, _, paths, _ = boards
    from hermes_cli.kanban_db_dispatch import _process_fingerprint
    with connect(paths['default']) as c:
        c.execute('UPDATE task_runs SET worker_pid=?,worker_started_at=? WHERE id=1',
                  (os.getpid(), _process_fingerprint(os.getpid())))
    with pytest.raises(api().RecoveryRefused):
        prepared(boards)


@pytest.mark.parametrize('change', ['run', 'card', 'successor', 'event', 'replacement'])
def test_preimage_or_successor_cas_change_refuses_both_boards(boards, change):
    _, _, paths, out = boards
    plan = prepared(boards)
    p = paths['tradingbot-burndown']
    if change == 'replacement':
        moved = p.with_name('old.sqlite3');p.rename(moved)
        with connect(moved) as source, connect(p) as destination:
            source.backup(destination)
    else:
        with connect(p) as c:
            if change == 'run': c.execute("UPDATE task_runs SET metadata='foreign' WHERE id=1")
            elif change == 'card': c.execute("UPDATE tasks SET assignee='foreign' WHERE id='t_tradingbot-burndown_1'")
            elif change == 'successor': c.execute("INSERT INTO task_runs(id,task_id,status,started_at,ended_at) VALUES(4,'t_tradingbot-burndown_1','done',5,6)")
            else: c.execute("INSERT INTO task_events(task_id,run_id,kind,payload,created_at) VALUES('t_tradingbot-burndown_1',1,'foreign',NULL,6)")
    with pytest.raises(api().RecoveryRefused): apply(plan, out)
    assert row(paths['default'], 'task_runs', 1)['status'] == 'running'


def test_board_aliases_refused(boards):
    home, selection, paths, _ = boards
    paths['tradingbot-burndown'].unlink()
    os.link(paths['default'], paths['tradingbot-burndown'])
    with pytest.raises(api().RecoveryRefused): api().prepare(home, selection)


def test_fenced_context_and_triggers_refused(boards, monkeypatch):
    monkeypatch.setenv('HERMES_DELEGATED_CHILD_CONTEXT', '/fenced/root')
    with pytest.raises(api().RecoveryRefused): prepared(boards)
    monkeypatch.delenv('HERMES_DELEGATED_CHILD_CONTEXT')
    with connect(boards[2]['default']) as c:
        c.execute("CREATE TRIGGER foreign_trigger AFTER UPDATE ON task_runs BEGIN UPDATE tasks SET assignee='foreign'; END")
    with pytest.raises(api().RecoveryRefused): prepared(boards)


def test_backups_include_committed_wal_and_preimage_journal(boards):
    _, _, paths, out = boards
    writer = open_connection(paths['default'])
    try:
        writer.execute('PRAGMA journal_mode=WAL');writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute("UPDATE tasks SET title='committed in WAL' WHERE id='unrelated'")
        assert Path(str(paths['default'])+'-wal').stat().st_size > 0
        plan = prepared(boards); result = apply(plan, out)
        journal = json.loads(Path(result['journal']).read_text(encoding='utf-8-sig'))
        assert len(journal['backups']) == 2
        backup = journal['backups']['default']['path']
        assert row(backup, 'tasks', 'unrelated')['title'] == 'committed in WAL'
        assert row(backup, 'task_runs', 1)['status'] == 'running'
        assert journal['plan'] == plan
    finally: writer.close()


def test_transaction_failure_rolls_back_board_and_is_resumable(boards, monkeypatch):
    _, _, paths, out = boards
    plan = prepared(boards); original = kb._append_event
    def fail(*a, **kw): raise RuntimeError('injected audit failure')
    monkeypatch.setattr(kb, '_append_event', fail)
    with pytest.raises(RuntimeError): apply(plan, out)
    assert row(paths['default'], 'task_runs', 1)['status'] == 'running'
    assert row(paths['tradingbot-burndown'], 'task_runs', 1)['status'] == 'running'
    monkeypatch.setattr(kb, '_append_event', original)
    assert apply(plan, out)['states'] == {'default': 'applied', 'tradingbot-burndown': 'applied'}


def test_partial_two_board_failure_is_explicit_and_resume_does_not_duplicate(boards, monkeypatch):
    _, _, paths, out = boards
    plan = prepared(boards); original = kb._append_event
    def fail_second(conn, *a, **kw):
        if a[0].startswith('t_tradingbot'): raise RuntimeError('injected second board failure')
        return original(conn, *a, **kw)
    monkeypatch.setattr(kb, '_append_event', fail_second)
    with pytest.raises(RuntimeError): apply(plan, out)
    assert row(paths['default'], 'task_runs', 1)['status'] == 'reclaimed'
    assert row(paths['tradingbot-burndown'], 'task_runs', 1)['status'] == 'running'
    monkeypatch.setattr(kb, '_append_event', original); apply(plan, out)
    with connect(paths['default']) as c:
        assert c.execute('SELECT count(*) FROM task_events').fetchone()[0] == 2


def test_filesystem_journal_failure_after_commit_is_recoverable(boards, monkeypatch):
    _, _, paths, out = boards
    plan = prepared(boards);original = api()._save_journal;calls = []
    def fail_after_first_commit(*a):
        calls.append(1)
        if len(calls) == 2: raise OSError('injected journal fsync failure')
        return original(*a)
    monkeypatch.setattr(api(), '_save_journal', fail_after_first_commit)
    with pytest.raises(OSError): apply(plan, out)
    assert row(paths['default'], 'task_runs', 1)['status'] == 'reclaimed'
    assert row(paths['tradingbot-burndown'], 'task_runs', 1)['status'] == 'running'
    monkeypatch.setattr(api(), '_save_journal', original);apply(plan, out)
    with connect(paths['default']) as c:
        assert c.execute('SELECT count(*) FROM task_events').fetchone()[0] == 2


def test_apply_idempotence_and_guarded_undo_preserve_foreign_changes(boards):
    _, _, paths, out = boards
    plan = prepared(boards); first = apply(plan, out); first_run = row(paths['default'], 'task_runs', 1)
    assert apply(plan, out)['states'] == first['states']
    assert row(paths['default'], 'task_runs', 1) == first_run
    with connect(paths['default']) as c: c.execute("UPDATE tasks SET title='later foreign write' WHERE id='unrelated'")
    result = api().undo(Path(first['journal']), expected_digest=api().digest(plan))
    assert result['states'] == {'default':'undone','tradingbot-burndown':'undone'}
    assert row(paths['default'], 'task_runs', 1)['ended_at'] is None
    assert row(paths['default'], 'tasks', 'unrelated')['title'] == 'later foreign write'
    api().undo(Path(first['journal']), expected_digest=api().digest(plan))
    with connect(paths['default']) as c: assert c.execute('SELECT count(*) FROM task_events').fetchone()[0] == 4
    with pytest.raises(api().RecoveryRefused): apply(plan, out)


def test_undo_refuses_changed_postimage_without_overwriting_other_board(boards):
    _, _, paths, out = boards
    plan = prepared(boards); result = apply(plan, out)
    with connect(paths['tradingbot-burndown']) as c: c.execute("UPDATE task_runs SET summary='later foreign write' WHERE id=1")
    with pytest.raises(api().RecoveryRefused): api().undo(Path(result['journal']), expected_digest=api().digest(plan))
    assert row(paths['default'], 'task_runs', 1)['status'] == 'reclaimed'


def test_manifest_digest_mismatch_refused(boards):
    plan = prepared(boards)
    with pytest.raises(api().RecoveryRefused): api().apply(plan, boards[3], expected_digest='0'*64)


@pytest.mark.parametrize('encoding', ['utf8', 'utf-8-sig'])
def test_command_path_dry_run_apply_undo_on_disposable_home(boards, tmp_path, encoding):
    home, selection, paths, out = boards
    selection_path = tmp_path/'selection.json'; selection_path.write_text(json.dumps(selection),encoding=encoding)
    plan_path = tmp_path/'plan.json'
    argv = [sys.executable, '-m', 'hermes_cli.kanban_orphan_recovery']
    env = dict(os.environ, HERMES_HOME=str(home), PYTHONDONTWRITEBYTECODE='1')
    preview = subprocess.run([*argv,'dry-run','--home',str(home),'--selection',str(selection_path)],capture_output=True,text=True,encoding='utf8',env=env)
    assert preview.returncode == 0, preview.stderr
    plan_path.write_text(preview.stdout,encoding=encoding);plan=json.loads(preview.stdout)
    done = subprocess.run([*argv,'apply','--plan',str(plan_path),'--plan-sha256',api().digest(plan),'--run-dir',str(out)],capture_output=True,text=True,encoding='utf8',env=env)
    assert done.returncode == 0, done.stderr
    journal=json.loads(done.stdout)['journal']
    if encoding == 'utf-8-sig':
        content = Path(journal).read_text(encoding='utf-8-sig')
        Path(journal).write_text(content, encoding=encoding)
    restored = subprocess.run([*argv,'undo','--journal',journal,'--plan-sha256',api().digest(plan)],capture_output=True,text=True,encoding='utf8',env=env)
    assert restored.returncode == 0, restored.stderr
    assert row(paths['default'],'task_runs',1)['status']=='running'


@pytest.mark.parametrize('delta', [-100, -1, 1, 100])
def test_process_birth_reading_drift_is_conservatively_live(monkeypatch, delta):
    from hermes_cli import kanban_db_dispatch as dispatch
    original = dispatch._process_fingerprint(os.getpid())
    epoch, start = original.rsplit('|', 1)
    monkeypatch.setattr(dispatch, '_process_fingerprint', lambda pid: f'{epoch}|{int(start)+delta}')
    assert api().worker_state({'worker_pid': os.getpid(), 'worker_started_at': original}) == 'live'


def test_unreadable_live_process_fingerprint_remains_unknown(monkeypatch):
    from hermes_cli import kanban_db_dispatch as dispatch
    monkeypatch.setattr(dispatch, '_process_fingerprint', lambda pid: None)
    assert api().worker_state({'worker_pid': os.getpid(), 'worker_started_at': '|12300'}) == 'unknown'


def test_apply_reprobes_worker_without_mutation(boards):
    plan=prepared(boards)
    with pytest.raises(api().RecoveryRefused):
        api().apply(plan, boards[3], expected_digest=api().digest(plan), worker_probe=lambda run: 'unknown')
    assert row(boards[2]['default'],'task_runs',1)['status']=='running'


@pytest.mark.parametrize('boards', [True], indirect=True)
def test_exact_seven_run_selection_closes_only_selected_records(boards):
    for board, ids in [('default', [1]), ('tradingbot-burndown', [1,2])]:
        with connect(boards[2][board]) as c:
            for i in ids:c.execute('UPDATE tasks SET worker_started_at=? WHERE id=?', ('|45600', f't_{board}_{i}'))
    plan=prepared(boards);result=apply(plan,boards[3])
    assert sum(len(e['rows']) for e in plan['boards'])==7
    for entry in plan['boards']:
        for view in entry['rows']:assert row(boards[2][entry['board']], 'tasks', view['task']['id'])==view['task']
    assert set(result['states'].values())=={'applied'}
    for board,path in boards[2].items():
        with connect(path) as conn:
            for r in conn.execute('SELECT * FROM task_runs'):
                if r['id'] in (3,1001) and r['outcome']=='completed':
                    assert r['status']=='done'
                else: assert r['status']=='reclaimed'


def test_post_commit_invariant_failure_recognizes_durable_audit(boards, monkeypatch):
    plan=prepared(boards);original=kbc._check_file_length_invariant;calls=[]
    def fail_after_commit(conn):
        calls.append(1)
        if len(calls)==1: raise RuntimeError('injected post-COMMIT invariant failure')
        return original(conn)
    monkeypatch.setattr(kbc,'_check_file_length_invariant',fail_after_commit)
    with pytest.raises(RuntimeError):apply(plan,boards[3])
    assert row(boards[2]['default'],'task_runs',1)['status']=='reclaimed'
    assert row(boards[2]['tradingbot-burndown'],'task_runs',1)['status']=='running'
    monkeypatch.setattr(kbc,'_check_file_length_invariant',original)
    apply(plan,boards[3])
    with connect(boards[2]['default']) as c:
        assert c.execute('SELECT count(*) FROM task_events').fetchone()[0]==2


@pytest.mark.parametrize('missing', ['all','one'])
def test_incomplete_backup_coverage_refuses_resume_before_mutation(boards,monkeypatch,missing):
    plan=prepared(boards);original=api()._execute
    def interrupted(*a): raise RuntimeError('stopped after durable intent before SQL')
    monkeypatch.setattr(api(),'_execute',interrupted)
    with pytest.raises(RuntimeError):apply(plan,boards[3])
    journal_path=boards[3]/'journal.json'; journal=json.loads(journal_path.read_text(encoding='utf-8-sig'))
    if missing=='all': journal['backups']={}
    else:journal['backups'].pop('tradingbot-burndown')
    journal_path.write_text(json.dumps(journal),encoding='utf8')
    monkeypatch.setattr(api(),'_execute',original)
    with pytest.raises(api().RecoveryRefused):apply(plan,boards[3])
    assert row(boards[2]['default'],'task_runs',1)['status']=='running'


@pytest.mark.parametrize('birth', ['garbage|123', 'bad|epoch|123', ':|123', '123.0', True, None])
def test_malformed_birth_cannot_supply_reuse_evidence(birth):
    assert api().worker_state({'worker_pid': os.getpid(), 'worker_started_at': birth}) == 'unknown'


def test_missing_current_epoch_witness_is_unknown(monkeypatch):
    from hermes_cli import kanban_db_dispatch as dispatch
    monkeypatch.setattr(dispatch, '_process_fingerprint', lambda pid: '|12300')
    recorded='00000000-0000-0000-0000-000000000001:42|12300'
    assert api().worker_state({'worker_pid': os.getpid(), 'worker_started_at': recorded}) == 'unknown'


@pytest.mark.parametrize('change', ['missing_file', 'wrong_path', 'changed_snapshot'])
def test_invalid_backup_snapshot_refuses_resume(boards, monkeypatch, change):
    plan=prepared(boards);original=api()._execute
    def interrupted(*a): raise RuntimeError('stopped before SQL')
    monkeypatch.setattr(api(), '_execute', interrupted)
    with pytest.raises(RuntimeError):apply(plan,boards[3])
    journal_path=boards[3]/'journal.json'
    journal=json.loads(journal_path.read_text(encoding='utf-8-sig'))
    backup=Path(journal['backups']['default']['path'])
    if change=='missing_file':backup.unlink()
    elif change=='wrong_path':journal['backups']['default']['path']=journal['backups']['tradingbot-burndown']['path']
    else:
        import hashlib
        with connect(backup) as c:c.execute("UPDATE task_runs SET summary='changed retained snapshot' WHERE id=1")
        journal['backups']['default']['sha256']=hashlib.sha256(backup.read_bytes()).hexdigest()
    journal_path.write_text(json.dumps(journal),encoding='utf8')
    monkeypatch.setattr(api(), '_execute', original)
    with pytest.raises(api().RecoveryRefused):apply(plan,boards[3])
    assert row(boards[2]['default'],'task_runs',1)['status']=='running'


def test_initial_journal_failure_prevents_all_sql_changes(boards,monkeypatch):
    plan=prepared(boards)
    def fail(*a):raise OSError('initial journal durability failure')
    monkeypatch.setattr(api(), '_save_journal', fail)
    with pytest.raises(OSError):apply(plan,boards[3])
    for path in boards[2].values():assert row(path,'task_runs',1)['status']=='running'


def test_undo_requires_complete_backups(boards):
    plan=prepared(boards);result=apply(plan,boards[3]);p=Path(result['journal'])
    journal=json.loads(p.read_text(encoding='utf-8-sig'));journal['backups']={}
    p.write_text(json.dumps(journal),encoding='utf8')
    with pytest.raises(api().RecoveryRefused):api().undo(p,expected_digest=api().digest(plan))
    assert row(boards[2]['default'],'task_runs',1)['status']=='reclaimed'


def test_partial_undo_resumes_without_duplicate_audits(boards,monkeypatch):
    plan=prepared(boards);result=apply(plan,boards[3]);original=kb._append_event
    def fail_second(conn,*a,**kw):
        if a[0].startswith('t_tradingbot'):raise RuntimeError('second board undo failure')
        return original(conn,*a,**kw)
    monkeypatch.setattr(kb,'_append_event',fail_second)
    with pytest.raises(RuntimeError):api().undo(Path(result['journal']),expected_digest=api().digest(plan))
    assert row(boards[2]['default'],'task_runs',1)['status']=='running'
    assert row(boards[2]['tradingbot-burndown'],'task_runs',1)['status']=='reclaimed'
    monkeypatch.setattr(kb,'_append_event',original)
    api().undo(Path(result['journal']),expected_digest=api().digest(plan))
    for path in boards[2].values():
        with connect(path) as c:assert c.execute('SELECT count(*) FROM task_events').fetchone()[0]==4



def test_actual_sqlite_busy_backup_is_bounded_without_any_board_changes(boards,monkeypatch,tmp_path):
    plan=prepared(boards);entry=plan['boards'][0];path=boards[2]['default']
    monkeypatch.setattr(api(),'BACKUP_TIMEOUT_SECONDS',0.05)
    with connect(path) as writer, closing(api()._connect(path,'ro')) as source:
        # WAL permits concurrent readers; rollback-journal mode exercises real BUSY.
        writer.execute('PRAGMA journal_mode=DELETE')
        writer.execute('BEGIN EXCLUSIVE')
        started=time.monotonic()
        try:
            with pytest.raises(api().RecoveryRefused,match='bounded deadline'):
                api()._backup(source,tmp_path/'busy.sqlite3',entry,plan,int(time.time()),api().worker_state)
            assert time.monotonic()-started < 5
        finally:writer.rollback()
    assert row(path,'task_runs',1)['status']=='running'


def test_backup_failure_prevents_all_sql_changes(boards,monkeypatch):
    plan=prepared(boards)
    def fail(*a):raise OSError('injected online backup failure')
    monkeypatch.setattr(api(),'_backup',fail)
    with pytest.raises(OSError):apply(plan,boards[3])
    for path in boards[2].values():assert row(path,'task_runs',1)['status']=='running'
    assert not (boards[3]/'journal.json').exists()


def test_allocation_durability_failure_prevents_all_sql_changes(boards,monkeypatch):
    plan=prepared(boards)
    def fail(*a):raise OSError('allocation parent fsync failure')
    monkeypatch.setattr(api(),'_sync_directory',fail)
    with pytest.raises(OSError):apply(plan,boards[3])
    for path in boards[2].values():assert row(path,'task_runs',1)['status']=='running'


@pytest.mark.parametrize('change',['card','successor'])
def test_undo_refuses_later_card_or_successor_changes(boards,change):
    plan=prepared(boards);result=apply(plan,boards[3])
    with connect(boards[2]['tradingbot-burndown']) as c:
        if change=='card':c.execute("UPDATE tasks SET assignee='later-owner' WHERE id='t_tradingbot-burndown_1'")
        else:c.execute("INSERT INTO task_runs(id,task_id,status,started_at,ended_at,outcome) VALUES(4,'t_tradingbot-burndown_1','done',10,11,'completed')")
    with pytest.raises(api().RecoveryRefused):api().undo(Path(result['journal']),expected_digest=api().digest(plan))
    assert row(boards[2]['default'],'task_runs',1)['status']=='reclaimed'



@pytest.mark.parametrize('marker', [12300, '12300', '|12300'])
def test_retained_terminal_task_birth_is_bound_and_preserved(boards,marker):
    path=boards[2]['default']
    with connect(path) as c:c.execute("UPDATE tasks SET worker_started_at=? WHERE id='t_default_1'",(marker,))
    before=row(path,'tasks','t_default_1');plan=prepared(boards)
    result=apply(plan,boards[3]);assert row(path,'tasks','t_default_1')==before
    api().undo(Path(result['journal']),expected_digest=api().digest(plan))
    assert row(path,'tasks','t_default_1')==before


def test_malformed_terminal_task_birth_refuses(boards):
    with connect(boards[2]['default']) as c:c.execute("UPDATE tasks SET worker_started_at='unverified' WHERE id='t_default_1'")
    with pytest.raises(api().RecoveryRefused):prepared(boards)


@pytest.mark.parametrize('pid', [99999999, os.getpid()])
def test_closed_history_with_worker_pid_refuses(boards,pid):
    with connect(boards[2]['default']) as c:c.execute("UPDATE task_runs SET worker_pid=?,worker_started_at='|12300' WHERE id=3",(pid,))
    with pytest.raises(api().RecoveryRefused):prepared(boards)
