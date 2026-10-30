"""A slow SQLite advisory read must not freeze unrelated gateway loop work."""
import asyncio
import threading

from hermes_cli.web_routers import status


def test_slow_advisory_database_keeps_event_loop_responsive(tmp_path, monkeypatch):
    import gateway.memory_status as memory
    import gateway.disk_status as disk
    import hermes_state
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    (tmp_path / 'state.db').touch()
    monkeypatch.setattr(memory, 'collect_memory_status', lambda *_: {})
    monkeypatch.setattr(disk, 'collect_disk_status', lambda *_: {})
    entered = threading.Event()
    release = threading.Event()
    heartbeat = threading.Event()
    observed = {}

    class SlowDatabase:
        def __init__(self, *, db_path, read_only):
            observed['home'] = db_path.parent
            observed['readonly'] = read_only
            observed['open_thread'] = threading.get_ident()
            entered.set()
            assert release.wait(5), 'test database was not released'

        def fts_rebuild_status(self):
            return {'state': 'idle'}

        def close(self):
            observed['close_thread'] = threading.get_ident()

    monkeypatch.setattr(hermes_state, 'SessionDB', SlowDatabase)

    async def exercise():
        loop = asyncio.get_running_loop()
        observed['loop_thread'] = threading.get_ident()
        def watchdog():
            if entered.wait(5):
                loop.call_soon_threadsafe(heartbeat.set)
                observed['responsive'] = heartbeat.wait(2)
            release.set()
        worker = threading.Thread(target=watchdog, daemon=True)
        worker.start()
        payload = {}
        try:
            await status._advisory_pressure(payload, tmp_path)
        finally:
            release.set()
            await asyncio.to_thread(worker.join, 5)
        assert observed.get('responsive') is True
        assert payload['fts_rebuild'] == {'state': 'idle'}
        assert observed['readonly'] is True
        assert observed['home'] == tmp_path
        assert observed['open_thread'] == observed['close_thread']
        assert observed['open_thread'] != observed['loop_thread']

    asyncio.run(exercise())
