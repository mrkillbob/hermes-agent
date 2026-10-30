"""Additional-memory bound and measurement: every unavailable input yields None, never RSS."""
from __future__ import annotations

import sys

import pytest

from hermes_cli.local_runtime import memory_accounting as ma
from hermes_cli.local_runtime.memory_accounting import (
    MemoryBound,
    MemoryMeasurement,
    MemorySampler,
    additional_memory_bound,
    child_bound,
    model_child,
)

MIB = 1 << 20


@pytest.fixture(autouse=True)
def _no_engine_exec(monkeypatch):
    monkeypatch.setattr(ma, "_engine_default_cache_ram_mib", lambda exe: 8192)


@pytest.mark.parametrize("argv", [
    ["llama-server", "--cache-ram", "2048"],
    ["llama-server", "-cram", "2048"],
    ["llama-server", "--cache-ram=2048"],
    ["llama-server", "--cache-ram", "999", "--cache-ram", "2048"],
])
def test_argv_flag_wins_over_env_and_default(argv):
    bound = additional_memory_bound(argv, {"LLAMA_ARG_CACHE_RAM": "1"}, "/x/llama-server")
    assert bound.limit_bytes == 2048 * MIB and bound.source == "argv"


def test_env_then_engine_default():
    assert additional_memory_bound(["s"], {"LLAMA_ARG_CACHE_RAM": "512"}, "/x").limit_bytes == 512 * MIB
    bound = additional_memory_bound(["s"], {}, "/x")
    assert bound.limit_bytes == 8192 * MIB and bound.source == "engine_default"


def test_cache_disabled_bound_is_zero():
    assert additional_memory_bound(["s", "--cache-ram", "0"], {}, "/x").limit_bytes == 0


@pytest.mark.parametrize("argv,environ", [
    (["s", "--cache-ram", "-1"], {}),            # unlimited: no ceiling exists
    (["s", "--cache-ram", "lots"], {}),          # unparseable
    (["s"], None),                               # env unreadable, flag absent: could be overridden
    (["s", "--cache-ram"], {}),                  # flag without value
])
def test_no_bound_when_not_determinable(argv, environ):
    bound = additional_memory_bound(argv, environ, "/x")
    assert bound.limit_bytes is None and not bound.available and bound.source == "unavailable"


def test_unreadable_engine_default_means_no_bound(monkeypatch):
    monkeypatch.setattr(ma, "_engine_default_cache_ram_mib", lambda exe: None)
    assert not additional_memory_bound(["s"], {}, "/x").available
    assert not additional_memory_bound(["s"], {}, None).available


def test_explicit_argv_survives_unreadable_environment():
    assert additional_memory_bound(["s", "--cache-ram", "64"], None, "/x").limit_bytes == 64 * MIB


class _Proc:
    def __init__(self, argv, env=None, *, children=(), fail=False):
        self._argv, self._env, self._children, self._fail = argv, env, children, fail

    def cmdline(self):
        if self._fail:
            raise OSError("gone")
        return self._argv

    def exe(self):
        return "/x/llama-server"

    def environ(self):
        if self._env is None:
            raise PermissionError("denied")
        return self._env

    def children(self, recursive=False):
        return list(self._children)


def test_child_bound_reads_running_process_and_fails_closed():
    assert child_bound(_Proc(["s", "--cache-ram", "1024"], {})).limit_bytes == 1024 * MIB
    assert not child_bound(_Proc(["s"], None)).available
    assert not child_bound(_Proc([], {}, fail=True)).available


def test_model_child_matching_is_exact_or_sole_resident():
    a = _Proc(["s", "--alias", "m1"])
    b = _Proc(["s", "--model", "/models/m2.gguf"])
    router = _Proc([], children=[a, b])
    assert model_child(router, "m1", ["m1"]) is a
    assert model_child(router, "m2", ["m2"]) is b
    assert model_child(router, "m3", ["m3"]) is None               # two children, no match
    sole = _Proc([], children=[_Proc(["s", "-x"])])
    assert model_child(sole, "m", ["m"]) is not None              # sole child + sole resident
    assert model_child(sole, "m", ["m", "other"]) is None         # residency not unique
    assert model_child(sole, "m", []) is None


def _sampler(values):
    it = iter(values)

    def reader(pid):
        return next(it)

    return MemorySampler(lambda: [1], reader=reader, interval_s=3600)


def test_measurement_reports_peak_rise_over_baseline():
    with _sampler([100, 400, 250]) as s:
        s._sample()  # one mid-run sample (the interval is too long for the thread to fire)
    m = s.result
    assert (m.baseline_bytes, m.peak_bytes, m.final_bytes) == (100, 400, 250)
    assert m.complete and m.additional_peak_bytes == 300
    assert m.within(MemoryBound(300, "argv", "")) is True
    assert m.within(MemoryBound(299, "argv", "")) is False
    assert m.within(MemoryBound(None, "unavailable", "")) is None


@pytest.mark.parametrize("values", [
    [None, 400, 250],      # baseline unreadable
    [100, None, 250],      # a mid sample failed: peak may be missed
    [100, 400, None],      # final unreadable
])
def test_any_failed_sample_voids_the_figure(values):
    with _sampler(values) as s:
        s._sample()
    assert s.result.additional_peak_bytes is None and not s.result.complete


def test_no_processes_or_raising_provider_is_unavailable():
    with MemorySampler(list, reader=lambda p: 1, interval_s=3600) as s:
        pass
    assert s.result.additional_peak_bytes is None
    with MemorySampler(lambda: (_ for _ in ()).throw(OSError('gone')), reader=lambda p: 1, interval_s=3600) as s:
        pass
    assert s.result.additional_peak_bytes is None


def test_never_negative_when_footprint_shrinks():
    m = MemoryMeasurement(500, 500, 100, 3, 0)
    assert m.additional_peak_bytes == 0


@pytest.mark.platforms("macos")
def test_phys_footprint_tracks_a_real_allocation_on_this_host():
    import os

    before = ma.phys_footprint(os.getpid())
    blob = bytearray(256 * MIB)
    for i in range(0, len(blob), 4096):
        blob[i] = 1
    after = ma.phys_footprint(os.getpid())
    assert before and after and after - before >= 200 * MIB
    del blob


@pytest.mark.platforms("linux")
def test_phys_footprint_is_unavailable_off_macos():
    import os

    assert sys.platform != "darwin" and ma.phys_footprint(os.getpid()) is None
