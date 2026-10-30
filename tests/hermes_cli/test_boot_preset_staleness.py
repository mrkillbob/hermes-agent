"""Every staged model must launch with a policy decision, never stock fit.

The managed server autoloads any GGUF in its models dir; a model missing
from the preset INI loads with llama-server defaults (f16 KV at max
context, no placement) — on Windows/WDDM that silently demotes VRAM and
decodes at a crawl. A live owner must refresh its own policy; consumers must not replace it with a parallel router."""

from __future__ import annotations

import pytest


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _stage(home, name):
    mdir = home / "models"
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / f"{name}.gguf").write_bytes(b"GGUF" + b"\x00" * 32)


def _write_presets(home, *model_ids):
    pdir = home / "runtimes" / "llamacpp"
    pdir.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"[{m}]\nmodel = {home / 'models' / (m + '.gguf')}\nctx-size = 65536\n" for m in model_ids)
    (pdir / "presets.ini").write_text(body, encoding="utf-8")


def test_presets_stale_when_a_staged_model_has_no_section(hermes_home):
    from hermes_cli.local_runtime.bootstrap import _presets_stale

    _stage(hermes_home, "model-a")
    _stage(hermes_home, "model-b")
    _write_presets(hermes_home, "model-a")
    assert _presets_stale() is True


def test_presets_current_when_every_staged_model_is_covered(hermes_home):
    from hermes_cli.local_runtime.bootstrap import _presets_stale

    _stage(hermes_home, "model-a")
    _write_presets(hermes_home, "model-a")
    assert _presets_stale() is False


def test_legacy_presets_without_model_paths_are_regenerated(hermes_home):
    from hermes_cli.local_runtime.bootstrap import _presets_stale

    _stage(hermes_home, "model-a")
    _write_presets(hermes_home, "model-a")
    ini = hermes_home / "runtimes/llamacpp/presets.ini"
    ini.write_text("[model-a]\nctx-size = 65536\n")
    assert _presets_stale()


def test_no_models_is_never_stale(hermes_home):
    from hermes_cli.local_runtime.bootstrap import _presets_stale

    _write_presets(hermes_home, "model-a")
    assert _presets_stale() is False


def test_stale_presets_are_deferred_to_the_live_owner(hermes_home, monkeypatch):
    from hermes_cli.local_runtime import bootstrap as boot, recovery
    from hermes_cli.local_runtime.ownership import RuntimeOwner

    _stage(hermes_home, "model-a")
    _stage(hermes_home, "model-b")
    _write_presets(hermes_home, "model-a")
    path = hermes_home / "runtimes/llamacpp/presets.ini"
    before = path.read_bytes()
    monkeypatch.setattr(boot, "_SUPERVISOR", None)
    monkeypatch.setattr(recovery, "read_state", lambda: {"owner_pid": -1, "create_time": 1})
    monkeypatch.setattr(recovery, "_owner_is_dead", lambda state: False)
    monkeypatch.setattr(recovery, "stop_recorded_orphan", lambda: pytest.fail("stopped a live owner"))
    monkeypatch.setattr("hermes_cli.local_runtime.binaries.installed_engine",
                        lambda *args: pytest.fail("started a parallel replacement"))
    assert boot.ensure_local_runtime({"local_runtime": {"enabled": True}},
                                     owner=RuntimeOwner(hermes_home)) is None
    assert path.read_bytes() == before


def test_refresh_defers_to_another_process_owner(hermes_home, monkeypatch):
    from hermes_cli.local_runtime import bootstrap as boot

    monkeypatch.setattr(boot, "_SUPERVISOR", None)
    monkeypatch.setattr(boot, "ensure_local_runtime", lambda *args, **kwargs: pytest.fail("acquired ownership"))
    assert boot.refresh_local_runtime() is False


def test_refresh_no_server_anywhere_is_a_noop(hermes_home, monkeypatch):
    import hermes_cli.local_runtime.bootstrap as boot

    monkeypatch.setattr(boot, "_SUPERVISOR", None)
    monkeypatch.setattr(
        "hermes_cli.local_runtime.endpoint._state_endpoint", lambda: None)
    assert boot.refresh_local_runtime() is False
