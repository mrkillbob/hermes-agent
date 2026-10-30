"""Choosing a channel in the Desktop selector supersedes a legacy custom-branch pin.

Branch precedence stays intact for ordinary checks (a pinned branch beats the channel); only the
explicit ``--set-channel`` selection resets the pin, otherwise "main" would keep following the old
branch and the selector would stay hidden behind a custom-branch status.
"""
import json

from hermes_cli.source_check import _drop_branch_override


def _write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def test_selecting_a_channel_resets_a_custom_branch_pin(tmp_path):
    config = tmp_path / "updates.json"
    _write(config, {"branch": "feature/legacy", "other": 1})
    _drop_branch_override(config)
    assert json.loads(config.read_text(encoding="utf-8")) == {"branch": "main", "other": 1}


def test_main_or_absent_pin_is_left_untouched(tmp_path):
    config = tmp_path / "updates.json"
    _drop_branch_override(config)  # missing file: nothing to reset, nothing created
    assert not config.exists()
    for data in ({"branch": "main"}, {"other": 1}):
        _write(config, data)
        before = config.stat().st_mtime_ns
        _drop_branch_override(config)
        assert json.loads(config.read_text(encoding="utf-8")) == data
        assert config.stat().st_mtime_ns == before
