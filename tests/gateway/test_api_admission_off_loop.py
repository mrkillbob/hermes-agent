"""API admission leaves the owner loop responsive at a held SQLite writer boundary."""
import asyncio
from contextlib import contextmanager
import sqlite3
import threading

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

KEY = 'off-loop-admission-key-long-enough'
AUTH = {'Authorization': 'Bearer ' + KEY}
WATCHDOG_S = 10
DOORS = [
    ('chat', '/v1/chat/completions', {'messages': [{'role': 'user', 'content': 'hi'}]}, {}, 200),
    ('responses', '/v1/responses', {'input': 'hi'}, {'Idempotency-Key': 'resp-1'}, 200),
    ('runs', '/v1/runs', {'input': 'hi'}, {}, 202),
]


@contextmanager
def _held_writer(path):
    held, release, done, watchdog = (threading.Event() for _ in range(4))
    errors = []

    def hold():
        conn = None
        try:
            conn = sqlite3.connect(path, timeout=0, isolation_level=None)
            conn.execute('BEGIN IMMEDIATE')
            held.set()
            if not release.wait(WATCHDOG_S):
                watchdog.set()
            conn.execute('COMMIT')
        except sqlite3.Error as error:
            errors.append(error)
        finally:
            if conn is not None:
                conn.close()
            done.set()

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    try:
        yield held, release, done, watchdog
    finally:
        release.set()
        thread.join(WATCHDOG_S)
        assert not thread.is_alive(), 'SQLite holder did not unwind'
        assert not errors, errors


def _assert_off_loop(observed, loop_thread, watchdog):
    assert observed.get('thread') != loop_thread, 'admission write ran on owner loop'
    assert observed.get('progress_before_release'), 'owner loop did not progress while writer was held'
    assert not watchdog.is_set(), 'holder was released by the cleanup watchdog'


@pytest.mark.asyncio
@pytest.mark.parametrize('inline', [False, True], ids=['off-loop', 'inline-negative-control'])
@pytest.mark.parametrize('name,path,body,headers,expected', DOORS, ids=[d[0] for d in DOORS])
async def test_admission_waits_for_the_writer_off_the_owner_loop(api, owner, monkeypatch, inline,
                                                                name, path, body, headers, expected):
    from gateway.platforms import api_server_runs

    async def handle(event):
        from gateway.session_results import execution_result
        execution_result.get()['result'] = {'final_response': 'ok', 'messages': []}
        return 'ok'

    async def no_execution(*args, **kwargs):
        pass

    async def inline_to_thread(function, *args, **kwargs):
        return function(*args, **kwargs)

    owner.runner._handle_message = handle
    monkeypatch.setattr(api_server_runs, '_execute_run', no_execution)
    api._api_key = KEY
    app = web.Application()
    app.router.add_post(path, getattr(api, {'chat': '_handle_chat_completions', 'responses': '_handle_responses',
                                          'runs': '_handle_runs'}[name]))
    loop, loop_thread = asyncio.get_running_loop(), threading.get_ident()
    observed = {}
    async with TestClient(TestServer(app)) as client:
        with _held_writer(owner.db.db_path) as (held, release, done, watchdog):
            assert await asyncio.to_thread(held.wait, WATCHDOG_S), 'holder did not acquire writer'
            request = None

            def progress():
                observed['progress_before_release'] = (
                    held.is_set() and not release.is_set() and not done.is_set()
                    and request is not None and not request.done()
                )
                release.set()

            def trace(statement):
                if statement.strip().upper() != 'BEGIN IMMEDIATE' or 'thread' in observed:
                    return
                observed['thread'] = threading.get_ident()
                if observed['thread'] == loop_thread:
                    # The deliberate inline mutation must fail safely, not deadlock SQLite.
                    release.set()
                loop.call_soon_threadsafe(progress)

            owner.db._conn.set_trace_callback(trace)
            try:
                with monkeypatch.context() as mutation:
                    if inline:
                        mutation.setattr(asyncio, 'to_thread', inline_to_thread)
                    request = asyncio.create_task(client.post(path, json=body, headers={**AUTH, **headers}))
                    response = await asyncio.wait_for(request, WATCHDOG_S * 3)
                    assert response.status == expected, await response.text()
                if inline:
                    with pytest.raises(AssertionError, match='admission write ran on owner loop'):
                        _assert_off_loop(observed, loop_thread, watchdog)
                else:
                    _assert_off_loop(observed, loop_thread, watchdog)
            finally:
                release.set()
                owner.db._conn.set_trace_callback(None)
                if request is not None and not request.done():
                    request.cancel()
                    await asyncio.gather(request, return_exceptions=True)
