"""Real canonical owner/dispatcher under a one-claim hold; loopback fixture only."""
import asyncio
from contextlib import closing
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

from local_recovery_probe import daemon, websocket, rpc
from kanban_owner_probe import Model
from tests.waiting import wait_until


def main():
    root = Path(__file__).resolve().parents[3]
    home = Path(os.environ['HERMES_HOME'])
    home.mkdir(parents=True)
    Path(os.environ['HOME']).mkdir(parents=True)
    os.environ['HERMES_KANBAN_HOME'] = str(home)
    peer = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    peer.requests, peer.mode = [], 'budget'
    peer.blocked, peer.release = threading.Event(), threading.Event()
    threading.Thread(target=peer.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{peer.server_port}/v1'
    os.environ.update(OPENAI_API_KEY='loopback-only', OPENAI_BASE_URL=url)
    cfg = {'gateway': {'multiplex_profiles': False}, 'model': {'provider': 'custom', 'default': 'loop-model', 'base_url': url},
           'auxiliary': {'title_generation': {'enabled': False}}, 'platform_toolsets': {'cli': []},
           'sessions': {'auto_prune': True, 'retention_days': 90, 'vacuum_after_prune': False},
           'agent': {'api_max_retries': 0}, 'kanban': {'dispatch_in_gateway': True}}
    (home / 'config.yaml').write_text(json.dumps(cfg))
    workspace = home / 'workspace'
    workspace.mkdir()
    from hermes_cli import kanban_db as kb, kanban_db_dispatch as dispatch
    from hermes_cli.kanban_db_connect import connect
    from cron.jobs import create_job, claim_job_for_fire
    script = home / 'scripts' / 'unrelated.py'
    script.parent.mkdir()
    script.write_text('from pathlib import Path\nPath(' + repr(str(home / 'UNRELATED_CRON')) + ').touch()\n')
    job = create_job(prompt='', schedule='every 1m', script=str(script), no_agent=True)
    jobs_path = home / 'cron/jobs.json'
    jobs = json.loads(jobs_path.read_text())
    jobs['jobs'][0]['next_run_at'] = '2020-01-01T00:00:00+00:00'
    jobs_path.write_text(json.dumps(jobs))
    before = jobs_path.read_bytes()
    kb.create_board('owned')
    with closing(connect(board='owned')) as conn:
        tid = kb.create_task(conn, title='Bounded selected card', assignee='default', workspace_kind='dir', workspace_path=str(workspace))
        other = kb.create_task(conn, title='Must stay ready', assignee='default', workspace_kind='dir', workspace_path=str(workspace))
    dispatch._resolve_hermes_argv = lambda: [sys.executable, '-m', 'hermes_cli.main']
    from hermes_state import SessionDB
    import hermes_state_runtime as rt
    with closing(SessionDB(db_path=home / 'state.db')) as db:
        db.create_session('unrelated', source='test')
        epoch = rt.begin_runtime_epoch(db, instance_id='previous')
        rt.admit_session_input(db, epoch=epoch, principal_id='automation:old', session_id='unrelated',
                               request_id='old', payload={'text': 'NEVER_RECOVER'})
        rt.claim_session_input(db, epoch=epoch, session_id='unrelated')
        pending = tuple(db._conn.execute("SELECT * FROM session_admissions WHERE request_id='old'").fetchone())
    from gateway import drain_kanban as window
    (home / 'ESTOP').write_text('fixture hold')
    prior = '{"action":"drain","principal":"fixture prior"}\n'
    window.marker_path().write_text(prior)
    from types import SimpleNamespace
    from hermes_cli.gateway_kanban_window import execute
    value = execute(SimpleNamespace(drain_action='begin', board='owned', task_id=tid, seconds=900))
    env, children, receipt = dict(os.environ), [], {}
    original = subprocess.Popen
    def track(*args, **kwargs):
        child = original(*args, **kwargs)
        if 'hermes_cli.kanban_worker_client' in args[0]:
            children.append(child)
        return child
    def pending_row():
        import sqlite3
        with closing(sqlite3.connect((home / 'state.db').as_uri() + '?mode=ro', uri=True)) as db:
            return tuple(db.execute("SELECT * FROM session_admissions WHERE request_id='old'").fetchone())
    async def refused(desc):
        async with websocket(home, desc) as ws:
            result = await rpc(ws, 'session.create', request_id='unrelated', source='cli')
            assert result.get('error', {}).get('message') == 'runtime_draining', result
            result = await rpc(ws, 'setup.runtime_check', provider='custom')
            assert result.get('error', {}).get('message') == 'runtime_draining', result
    try:
        with daemon(root, home, env, barrier=False, expected_state='draining') as (_, desc):
            assert desc.get('drain_reason') == 'kanban_once', desc
            assert pending_row() == pending
            import sqlite3
            with closing(sqlite3.connect((home / 'state.db').as_uri() + '?mode=ro', uri=True)) as snapshot:
                assert not snapshot.execute("SELECT 1 FROM state_meta WHERE key='last_auto_prune'").fetchone()
            from urllib.request import urlopen
            from urllib.error import HTTPError
            try:
                urlopen(desc['api_origin'] + '/api/status', timeout=5)
            except HTTPError as exc:
                assert exc.code == 503
            else:
                raise AssertionError('REST unexpectedly admitted')
            from tests.gateway.test_normal_runtime_boot import control
            try:
                control(home, 'rescan-profiles')
            except AssertionError as exc:
                assert 'unknown verb' in str(exc)
            else:
                raise AssertionError('profile rescan unexpectedly admitted')
            asyncio.run(refused(desc))
            execute(SimpleNamespace(drain_action='arm', window_id=value['window_id']))
            (home / 'ESTOP').unlink()
            assert claim_job_for_fire(job['id'], force=True) is False
            subprocess.Popen = track
            try:
                with closing(connect(board='owned')) as conn:
                    budget = {'wall_seconds': 60, 'output_bytes': 65536, 'requests': 1}
                    denied = dispatch.dispatch_once(conn, board='owned', max_spawn=1)
                    assert not denied.spawned, denied
                    import argparse
                    from hermes_cli import kanban_parser, kanban_ops
                    from contextlib import redirect_stdout
                    import io
                    parser = argparse.ArgumentParser()
                    kanban_parser.build_parser(parser.add_subparsers(dest='command'))
                    args = parser.parse_args(['kanban', 'dispatch', '--task-id', tid, '--max', '1',
                                              '--wall-seconds', '60', '--output-bytes', '65536', '--requests', '1', '--json'])
                    # Same parser/handler as the product CLI, scoped to this fixture's board.
                    os.environ['HERMES_KANBAN_BOARD'] = 'owned'
                    captured = io.StringIO()
                    with redirect_stdout(captured):
                        rc = kanban_ops._cmd_dispatch(args)
                    assert rc == 0, captured.getvalue()
                    assert [row['task_id'] for row in json.loads(captured.getvalue())['spawned']] == [tid]
                    wait_until(lambda: peer.blocked.is_set() or children[0].poll() is not None, timeout=55, what='worker request or early refusal')
                    assert peer.blocked.is_set(), (home / 'logs/managed-worker.log').read_text()
                    task = kb.get_task(conn, tid)
                    replay_params = dict(task_id=tid, board='owned', run_id=task.current_run_id, claim_lock=task.claim_lock,
                                         execution_budget=budget, db=str(kb.kanban_db_path(board='owned'))) 
                    assert kb.get_task(conn, other).status == 'ready'
                    repeated = dispatch.dispatch_once(conn, board='owned', max_spawn=1, task_ids=[tid], execution_budget=budget)
                    assert not repeated.spawned, repeated
                    mode = os.environ['WINDOW_PROBE_MODE']
                    if mode == 'cancel':
                        execute(SimpleNamespace(drain_action='cancel', window_id=value['window_id']))
                    elif mode == 'expiry':
                        current, permission = window.state()
                        permission['expires_at'] = 1
                        permission['expires_monotonic'] = 1
                        window._save(current)  # elapse only this disposable fixture's permit
                    else:
                        peer.release.set()
                    wait_until(lambda: children[0].poll() is not None, timeout=35, what='owned submitter terminal')
                    receipt['worker_exit'] = children[0].returncode
                    assert children[0].returncode == 130, receipt
            finally:
                subprocess.Popen = original
            assert window.held()
            assert jobs_path.read_bytes() == before and not (home / 'UNRELATED_CRON').exists()
            (home / 'ESTOP').write_text('fixture hold')
        with daemon(root, home, env, barrier=False, expected_state='draining') as (_, new):
            assert new['instance_id'] != desc['instance_id']
            asyncio.run(refused(new))
            assert pending_row() == pending
            async def old_claim_refused():
                async with websocket(home, new) as ws:
                    reply = await rpc(ws, 'kanban.run', **replay_params)
                    assert reply.get('error', {}).get('message') == 'kanban_window_unavailable', reply
            asyncio.run(old_claim_refused())
            _, grant = window.state()
            receipt['restart_fenced'] = grant['owner_instance'] == desc['instance_id']
        execute(SimpleNamespace(drain_action='restore', window_id=value['window_id']))
        receipt.update(restored=window.marker_path().read_text() == prior, cron_unchanged=jobs_path.read_bytes() == before,
                       physical_requests=len(peer.requests))
        assert receipt['physical_requests'] == 1, receipt
    finally:
        peer.release.set()
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=10)
        peer.shutdown()
        peer.server_close()
        (home / 'receipt.json').write_text(json.dumps(receipt))
        print(json.dumps(receipt))


if __name__ == '__main__':
    main()
