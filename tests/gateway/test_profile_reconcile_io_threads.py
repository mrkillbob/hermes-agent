"""Profile filesystem enumeration/signatures must not block the gateway loop."""
import asyncio
import threading

import pytest
from gateway import run_profile_reconcile as reconcile


@pytest.mark.parametrize('changed', [False, True])
def test_reconcile_filesystem_reads_run_off_loop(tmp_path, monkeypatch, changed):
    from hermes_cli import profiles
    loop_thread = threading.get_ident()
    homes = {'default': tmp_path, 'alpha': tmp_path / 'alpha'}
    observed = []

    def enumerate_profiles(*, multiplex):
        observed.append(('enumerate', threading.get_ident()))
        return list(homes.items())

    def signature(home):
        observed.append(('signature', threading.get_ident()))
        return ('new' if changed else 'old',)

    monkeypatch.setattr(profiles, 'profiles_to_serve', enumerate_profiles)
    monkeypatch.setattr(reconcile, 'profile_serve_signature', signature)

    class Runner(reconcile.GatewayProfileReconcileMixin):
        _running = True
        _served_profile_homes = homes
        _served_profile_signatures = {'alpha': ('old',)}

        def _multiplex_on(self):
            return True

        def _parked_profile_names(self):
            return set()

        async def _run_housekeeping_in_executor(self, func, *args):
            return await asyncio.to_thread(func, *args)

        async def _apply_profile_changes(self, current, added, removed, actual_changed, **kwargs):
            assert not added and not removed
            return actual_changed

    actual = asyncio.run(Runner().reconcile_served_profiles())
    assert actual == (['alpha'] if changed else [])
    assert {kind for kind, _ in observed} == {'enumerate', 'signature'}
    assert all(thread != loop_thread for _, thread in observed)
