"""Desktop auth/host role isolation and ledger-proven orphan owner reclaim.

Regressions for #96490, #119644, #119824 and #121964. All tests use the shared
hermes_cli conftest isolation; no fixture from test_web_server.py is required.
"""

import sys
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Desktop-owned loopback backends are not gated by dashboard.public_url (#96490)
# ---------------------------------------------------------------------------


class TestDesktopLoopbackAuthExemption:
    """``_desktop_loopback_auth_exempt`` decides the #96490 exemption."""

    def test_exempt_with_desktop_env_and_session_token_on_loopback(self, monkeypatch):
        import hermes_cli.web_server as web_server

        monkeypatch.setenv("HERMES_DESKTOP", "1")
        monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", "desktop-minted")
        assert web_server._desktop_loopback_auth_exempt("127.0.0.1") is True
        assert web_server._desktop_loopback_auth_exempt("::1") is True

    def test_exempt_via_ssh_spawn_credentials_without_env_token(self, monkeypatch):
        import hermes_cli.web_server as web_server

        monkeypatch.setenv("HERMES_DESKTOP", "1")
        monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)
        assert web_server._desktop_loopback_auth_exempt(
            "127.0.0.1", ssh_session_token="tok"
        )
        assert web_server._desktop_loopback_auth_exempt(
            "127.0.0.1", ssh_owner_nonce="nonce"
        )

    def test_not_exempt_without_desktop_env(self, monkeypatch):
        import hermes_cli.web_server as web_server

        monkeypatch.delenv("HERMES_DESKTOP", raising=False)
        monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", "tok")
        assert web_server._desktop_loopback_auth_exempt("127.0.0.1") is False

    def test_not_exempt_without_any_credential(self, monkeypatch):
        import hermes_cli.web_server as web_server

        # HERMES_DESKTOP=1 alone is not enough: a plain serve with the env var
        # exported must stay gated.
        monkeypatch.setenv("HERMES_DESKTOP", "1")
        monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)
        assert web_server._desktop_loopback_auth_exempt("127.0.0.1") is False

    def test_not_exempt_on_non_loopback_bind(self, monkeypatch):
        import hermes_cli.web_server as web_server

        monkeypatch.setenv("HERMES_DESKTOP", "1")
        monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", "tok")
        assert web_server._desktop_loopback_auth_exempt("0.0.0.0") is False
        assert web_server._desktop_loopback_auth_exempt("192.168.1.10") is False

    def test_public_url_engages_gate_for_non_desktop_loopback(self, monkeypatch):
        import hermes_cli.web_server as web_server

        # Sanity: the base behaviour is untouched — a non-Desktop loopback
        # serve with a public_url configured stays ticket-gated.
        monkeypatch.delenv("HERMES_DESKTOP", raising=False)
        assert web_server.should_require_dashboard_auth(
            "127.0.0.1", frozenset({"dash.example.com"})
        ) is True


class TestDesktopHostRendezvousIsolation:
    """Desktop pool children have a private lifecycle, not a host ownership role."""

    def test_desktop_backend_does_not_claim_the_host_serve_record(self, monkeypatch, tmp_path):
        """A Desktop child must not block a separately supervised public dashboard, yet a
        terminal `hermes plugins install` on a Desktop-only box must still find it (#119644):
        it publishes under its OWN role, which the attach ladder never reads."""
        import io
        import urllib.request
        from gateway import host_rendezvous as hr
        import hermes_cli.web_server as web_server
        from hermes_cli.main_dashboard import _host_backend_attachment
        from hermes_cli.plugins_activation import notify_serve_backend

        monkeypatch.setenv("HERMES_GATEWAY_LOCK_DIR", str(tmp_path / "locks"))
        monkeypatch.setenv("HERMES_DESKTOP", "1")
        monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", "desktop-spawn-token")
        monkeypatch.setattr(web_server, "_SESSION_TOKEN", "desktop-spawn-token")
        monkeypatch.setattr(hr, "cleanup_on_exit", lambda role: None)
        dialed = []

        class _Reply(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def _fake_urlopen(request, timeout=None):
            dialed.append((request.full_url, request.get_header("X-hermes-session-token")))
            return _Reply(b'{"ok": true}')

        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
        try:
            web_server._publish_host_rendezvous("127.0.0.1", 9231)

            # Not a host owner: the supervised public dashboard's attach ladder sees nobody.
            assert hr.read_record(hr.ROLE_SERVE) is None
            assert _host_backend_attachment() is None
            # ...but a terminal `hermes plugins install` still lights up its open chats.
            assert notify_serve_backend("demo", tmp_path) == {"ok": True}
            assert dialed == [("http://127.0.0.1:9231/api/dashboard/agent-plugins/activate",
                               "desktop-spawn-token")]
        finally:
            hr.clear_record(hr.ROLE_DESKTOP_SERVE)
            hr.release_host_lock(hr.ROLE_DESKTOP_SERVE)

    def test_standalone_backend_still_claims_the_host_serve_record(self, monkeypatch):
        """The Desktop exclusion must not alter standalone dashboard discovery — including a
        supervised service whose shell merely inherited HERMES_DESKTOP=1 without the token."""
        from gateway import host_rendezvous as hr
        import hermes_cli.web_server as web_server

        monkeypatch.setenv("HERMES_DESKTOP", "1")
        monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)
        claimed = []
        published = []
        monkeypatch.setattr(
            hr,
            "claim_host_lock",
            lambda role: (claimed.append(role) or (hr.HostLockOutcome.ACQUIRED, None)),
        )
        monkeypatch.setattr(hr, "publish_record", lambda *args, **kwargs: published.append((args, kwargs)))
        monkeypatch.setattr(hr, "cleanup_on_exit", lambda role: None)

        web_server._publish_host_rendezvous("0.0.0.0", 9119)

        assert claimed == [hr.ROLE_SERVE]
        assert published[0][0] == (hr.ROLE_SERVE,)
        assert published[0][1]["host"] == "0.0.0.0"
        assert published[0][1]["port"] == 9119


class TestOrphanedOwnerReclaim:
    """HELD_BY_OTHER against a dead session's orphan must re-claim, not loop observe-only (#121964).

    The conflicting owner is alive (re-parented to init), so ``record_is_stale()`` never fires
    and the old code returned observe-only forever: every attach retried against the same orphan.
    """

    def _owner_record(self, pid=555, create_time=55.0):
        from gateway import host_rendezvous as hr

        return hr.HostRecord(
            role=hr.ROLE_SERVE, pid=pid, create_time=create_time, host="0.0.0.0", port=9119,
            protocol_version=hr.HOST_PROTOCOL_VERSION, token_fingerprint="", profiles=("default",),
            updated_at="2026-09-24T00:00:00+00:00")

    def _owner_entry(self, pid=555, create_time=55.0, spawner_pid=700, spawner_create=7.0):
        from hermes_cli import process_identity as pi
        from pathlib import Path as _Path

        return {
            "pid": pid, "create_time": create_time, "purpose": "serve",
            "install": pi.install_id(_Path("/x/install")),
            "spawner_pid": spawner_pid, "spawner_create": spawner_create,
            "registered_at": 0.0, "argv": "",
        }

    def _run_publish(self, monkeypatch, tmp_path, *, entry, procs):
        """Drive ``_publish_host_rendezvous`` through HELD_BY_OTHER with a faked owner world.

        First claim fails (the orphan holds the flock), the retry succeeds (it died); returns
        ``(claimed, published, fake_procs)``.
        """
        import types
        from gateway import host_rendezvous as hr
        import hermes_cli.web_server as web_server
        from hermes_cli import process_identity as pi

        monkeypatch.setenv("HERMES_GATEWAY_LOCK_DIR", str(tmp_path / "locks"))
        monkeypatch.setattr(web_server, "is_desktop_owned_backend", lambda: False)
        claims = iter([hr.HostLockOutcome.HELD_BY_OTHER, hr.HostLockOutcome.ACQUIRED])
        claimed, published = [], []
        monkeypatch.setattr(
            hr, "claim_host_lock",
            lambda role: (claimed.append(role) or (next(claims), None)),
        )
        monkeypatch.setattr(hr, "read_record", lambda role, **kw: self._owner_record())
        monkeypatch.setattr(hr, "publish_record", lambda *a, **k: published.append((a, k)))
        monkeypatch.setattr(hr, "cleanup_on_exit", lambda role: None)
        monkeypatch.setattr(pi, "ledger_entries", lambda **kw: [entry])

        made = {}

        def _process(pid):
            if pid not in procs:
                raise fake_psutil.NoSuchProcess(pid)
            if pid not in made:
                proc = MagicMock()
                proc.pid = pid
                proc.create_time.return_value = procs[pid]
                made[pid] = proc
            return made[pid]

        fake_psutil = types.SimpleNamespace(
            Process=_process,
            NoSuchProcess=type("NoSuchProcess", (Exception,), {}),
            TimeoutExpired=type("TimeoutExpired", (Exception,), {}),
            STATUS_ZOMBIE="zombie",
        )
        monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

        web_server._publish_host_rendezvous("0.0.0.0", 9119)
        return claimed, published, made

    def test_dead_spawner_owner_is_reaped_and_lock_reclaimed(self, monkeypatch, tmp_path):
        """Spawner provably gone: the orphan is terminated and this backend publishes."""
        entry = self._owner_entry(spawner_pid=700, spawner_create=7.0)  # 700 not alive
        claimed, published, made = self._run_publish(
            monkeypatch, tmp_path, entry=entry, procs={555: 55.0})

        assert made[555].terminate.called
        assert claimed == ["serve", "serve"]  # conflict, then re-claim after the reap
        assert published and published[0][0][0] == "serve"

    def test_live_spawner_owner_stays_observe_only(self, monkeypatch, tmp_path):
        """Spawner alive: never touch, never re-claim — the old observe-only stands."""
        entry = self._owner_entry(spawner_pid=500, spawner_create=5.0)
        claimed, published, made = self._run_publish(
            monkeypatch, tmp_path, entry=entry, procs={555: 55.0, 500: 5.0})

        assert 555 not in made  # no signal attempted
        assert claimed == ["serve"]  # no retry: nothing was reaped
        assert published == []

    def test_unprovable_owner_is_untouched(self, monkeypatch, tmp_path):
        """Null spawner whose parent is alive: unprovable means never touch."""
        from hermes_cli import dashboard_procs

        entry = self._owner_entry(spawner_pid=None, spawner_create=None)
        monkeypatch.setattr(dashboard_procs, "_process_ppid", lambda pid: 1234)
        claimed, published, made = self._run_publish(
            monkeypatch, tmp_path, entry=entry, procs={555: 55.0})

        assert 555 not in made
        assert claimed == ["serve"]
        assert published == []
