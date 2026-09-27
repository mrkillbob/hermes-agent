"""Profile config validation for protected Kanban worker environments."""

from __future__ import annotations

import pytest

from hermes_cli.kanban_worker_environment import validate_profile_config


def test_validate_profile_config_requires_valid_mapping(tmp_path):
    config = tmp_path / "config.yaml"

    for contents in ("[not, a, mapping]", "model: [unterminated"):
        config.write_text(contents, encoding="utf-8")
        with pytest.raises(ValueError, match="invalid profile config"):
            validate_profile_config(str(tmp_path))

    config.write_text("model:\n  provider: openai\n", encoding="utf-8")
    validate_profile_config(str(tmp_path))
