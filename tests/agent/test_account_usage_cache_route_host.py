"""Fork-only regression (see FORK_PATCHES.md): per-credential usage snapshots are keyed by route
host, so one account's two hosts never share a refresh claim or a quota reading."""

from types import SimpleNamespace

from agent import account_usage_cache as cache


def _snap(identity, used):
    return SimpleNamespace(windows=[SimpleNamespace(used_percent=used)], identity=identity)


def test_two_hosts_for_one_identity_keep_separate_snapshots():
    cache.remember_account_usage("openai-codex", _snap("acct", 10), identity_id="acct", base_url="https://a.example/v1")
    cache.remember_account_usage("openai-codex", _snap("acct", 90), identity_id="acct", base_url="https://b.example/v1")

    a = cache.cached_account_usage("openai-codex", identity_id="acct", base_url="https://a.example/v1")
    b = cache.cached_account_usage("openai-codex", identity_id="acct", base_url="https://b.example/v1")

    assert a.windows[0].used_percent == 10
    assert b.windows[0].used_percent == 90


def test_refresh_claims_are_per_host(monkeypatch):
    monkeypatch.setattr(cache.threading.Thread, "start", lambda self: None)
    reqs = [{"provider": "openai-codex", "identity_id": "acct-claim", "base_url": u, "api_key": "k"}
            for u in ("https://a.example/v1", "https://b.example/v1")]

    threads = cache.refresh_account_usage_entries_async(reqs)

    assert len(threads) == 1
    assert len(threads[0]._args[1]) == 2  # both hosts were queued, not just the first
