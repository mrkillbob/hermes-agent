"""An idle expiry sweep must not serialize authenticated HTTP acceptance."""
import sqlite3
import threading

from hermes_cli.fleet_client import FleetClient
from hermes_cli.fleet_server import FleetServer
from hermes_cli.fleet_store import FleetStore


def test_health_accepts_while_idle_sweep_waits_for_sqlite_writer(tmp_path, monkeypatch):
    store = FleetStore(tmp_path / 'fleet.db')
    writer = sqlite3.connect(store.path)
    writer.execute('BEGIN IMMEDIATE')
    entered = threading.Event()
    original = store.reclaim_expired
    def reclaim(**kwargs):
        entered.set()
        return original(**kwargs)
    monkeypatch.setattr(store, 'reclaim_expired', reclaim)
    server = FleetServer(store, token='inert')
    try:
        server.start()
        assert entered.wait(5)
        # The real SQLite write transaction stays held through this request.
        assert FleetClient(server.url, token='inert', timeout=2).health() == {'ok': True}
    finally:
        writer.rollback()
        writer.close()
        server.close()


def test_maintenance_shutdown_waits_for_inflight_sweep_and_does_not_overlap(tmp_path, monkeypatch):
    store = FleetStore(tmp_path / 'fleet.db')
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    active = [0]
    calls = []
    def reclaim(**_kwargs):
        active[0] += 1
        calls.append(active[0])
        entered.set()
        assert release.wait(20)
        active[0] -= 1
        return 0
    monkeypatch.setattr(store, 'reclaim_expired', reclaim)
    server = FleetServer(store, token='inert')
    server.start()
    closer = None
    try:
        assert entered.wait(5)
        # Repeated accepted requests cannot start a second maintenance sweep.
        client = FleetClient(server.url, token='inert', timeout=2)
        assert client.health()['ok']
        assert client.health()['ok']
        def close():
            server.close()
            closed.set()
        closer = threading.Thread(target=close)
        closer.start()
        assert not closed.wait(2)
        release.set()
        assert closed.wait(5)
        assert active == [0]
        assert calls == [1]
    finally:
        release.set()
        if closer:
            closer.join(5)
        server.close()


def test_transient_sqlite_sweep_error_is_retried_without_stopping_http(tmp_path, monkeypatch):
    store = FleetStore(tmp_path / 'fleet.db')
    recovered = threading.Event()
    original = store.reclaim_expired
    calls = []
    def reclaim(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError('inert busy database')
        result = original(**kwargs)
        recovered.set()
        return result
    monkeypatch.setattr(store, 'reclaim_expired', reclaim)
    server = FleetServer(store, token='inert')
    try:
        server.start()
        assert recovered.wait(5)
        assert FleetClient(server.url, token='inert', timeout=2).health()['ok']
    finally:
        server.close()


def test_maintenance_runs_in_the_starting_profile_context(tmp_path, monkeypatch):
    import contextvars
    owner = contextvars.ContextVar('inert-owner', default='wrong-profile')
    store = FleetStore(tmp_path / 'fleet.db')
    observed = []
    done = threading.Event()
    def reclaim(**_kwargs):
        observed.append(owner.get())
        done.set()
        return 0
    monkeypatch.setattr(store, 'reclaim_expired', reclaim)
    server = FleetServer(store, token='inert')
    token = owner.set('owning-profile')
    try:
        server.start()
    finally:
        owner.reset(token)
    try:
        assert done.wait(5)
        assert observed and set(observed) == {'owning-profile'}
    finally:
        server.close()
