"""A held bootstrap may enumerate provider metadata without reading private dotenv files."""


def test_load_env_defers_private_read_until_hold_clears(monkeypatch, tmp_path):
    from agent import secret_scope
    from hermes_cli import config, startup_hold

    held = [True]
    path = tmp_path / ".env"
    reads = []
    monkeypatch.setattr(startup_hold, "defer_maintenance", lambda: held[0])
    monkeypatch.setattr(config, "_worker_config_snapshot", lambda: None)
    monkeypatch.setattr(config, "get_env_path", lambda: path)
    monkeypatch.setattr(secret_scope, "load_env_file",
                        lambda requested: reads.append(requested) or {"RAMP_ROUTER_BASE_URL": "https://fixture"})

    assert config.load_env() == {}
    assert reads == []
    held[0] = False
    assert config.load_env()["RAMP_ROUTER_BASE_URL"] == "https://fixture"
    assert reads == [path]
