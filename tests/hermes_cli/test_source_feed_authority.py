"""Fork source channels use the owning profile's configured archive."""
from pathlib import Path
import importlib.util

import pytest

pytestmark = pytest.mark.real_release_channels


@pytest.fixture
def multiplexed():
    from agent.secret_scope import is_multiplex_active, set_multiplex_active
    previous = is_multiplex_active()
    set_multiplex_active(True)
    try:
        yield
    finally:
        set_multiplex_active(previous)

_spec = importlib.util.spec_from_file_location("source_feed_http_fixture", Path(__file__).parents[1] / "scripts/test_release_channels.py")
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)


def test_profiles_select_their_own_fork_feed_without_cross_repository_fallback(tmp_path, monkeypatch, multiplexed):
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from agent.secret_scope import set_secret_scope, reset_secret_scope
    from hermes_cli.config import atomic_config_write
    from hermes_cli.release_channels import ChannelError
    from hermes_cli.release_channels import canonical_json
    from hermes_cli.source_releases import resolve_source_target
    from scripts.releases.channels import ChannelPublisher, R2ChannelStore

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "launch"))
    with _fixture.object_server() as (url_a, objects_a, _ha, requests_a, _fa), _fixture.object_server() as (url_b, objects_b, _hb, requests_b, _fb):
        homes = []
        for name, url, objects, branch in (("a", url_a, objects_a, "main"), ("b", url_b, objects_b, "preview")):
            home = tmp_path / name
            home.mkdir()
            atomic_config_write(home / "config.yaml", {"updates": {"source_feed_base_url": url + "/bucket"}})
            pub = ChannelPublisher(R2ChannelStore({"access_key_id": "fixture", "secret_key": "fixture"}, url, "bucket"), "mrkillbob/hermes-agent", url + "/bucket", authorize=lambda *_: None)
            record = pub.create("main")
            record.update(policy="source-branch", identity=None, delivery={"kind": "source-branch", "branch": branch})
            objects["releases/channels/main.json"] = canonical_json(record)
            homes.append(home)
        for home, branch in ((homes[0], "main"), (homes[1], "preview"), (homes[0], "main")):
            scope = set_hermes_home_override(home)
            secrets = set_secret_scope({}, profile_home=str(home))
            try:
                target = resolve_source_target("main", repository="mrkillbob/hermes-agent")
                assert target.branch == branch
                assert target.repository == "mrkillbob/hermes-agent"
            finally:
                reset_hermes_home_override(scope)
                reset_secret_scope(secrets)
        scope = set_hermes_home_override(homes[0])
        try:
            # Same feed, different repository: the existing authority check must refuse it.
            with pytest.raises(ChannelError, match="repository"):
                resolve_source_target("main", repository="another/hermes-agent")
            objects_a.pop("releases/channels/main.json")
            assert resolve_source_target("main", repository="mrkillbob/hermes-agent").branch == "main"
        finally:
            reset_hermes_home_override(scope)
        assert sum(path.endswith("/releases/channels/main.json") for method, path in requests_a if method == "GET") >= 3
        assert any(path.endswith("/releases/channels/main.json") for method, path in requests_b if method == "GET")


def test_missing_or_invalid_fork_feed_fails_before_network(tmp_path, monkeypatch):
    from hermes_cli.config import atomic_config_write
    from hermes_cli.release_channels import ChannelError
    from hermes_cli.source_releases import resolve_source_target
    from hermes_cli.config_defaults import DEFAULT_CONFIG

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert "source_feed_base_url" in DEFAULT_CONFIG["updates"]
    with pytest.raises(ChannelError, match="source_feed_base_url"):
        resolve_source_target("main", repository="mrkillbob/hermes-agent")
    for value in ("http://example.invalid", "https://user:password@example.invalid", 42):
        atomic_config_write(home / "config.yaml", {"updates": {"source_feed_base_url": value}})
        with pytest.raises(ChannelError):
            resolve_source_target("main", repository="mrkillbob/hermes-agent")
    (home / "config.yaml").write_text("updates: [broken")
    with pytest.raises(ChannelError, match="configuration"):
        resolve_source_target("main", repository="mrkillbob/hermes-agent")
