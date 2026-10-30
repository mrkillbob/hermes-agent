"""Prefix-family suffix separators retain redaction without changing plugin boundaries."""

import pytest

from agent import redact


@pytest.mark.parametrize("prefix", ["gh" + "p_", "h" + "f_", "np" + "m_"])
@pytest.mark.parametrize("suffix", ["_suffix", "-suffix", ".suffix"])
@pytest.mark.parametrize("mode", [{}, {"file_read": True}, {"code_file": True}])
def test_alphanumeric_family_masks_token_before_separator(prefix, suffix, mode):
    token = prefix + "A" * 40
    result = redact.redact_sensitive_text("output: " + token + suffix, force=True, **mode)
    assert token not in result
    assert result.endswith(suffix)


@pytest.mark.parametrize("control", ["\u200b", "\x1b", "\n"])
@pytest.mark.parametrize("suffix", ["_suffix", "-suffix"])
def test_control_split_token_masks_before_separator(control, suffix):
    prefix = "gh" + "p_"
    token = prefix + "A" * 5 + control + "B" * 35
    result = redact.redact_sensitive_text("output: " + token + suffix, force=True)
    assert token not in result
    assert "B" * 35 not in result
    assert result.endswith(suffix)


@pytest.mark.parametrize("left", ["x", "_", "-"])
def test_left_embedded_identifier_stays_unchanged(left):
    sample = left + "gh" + "p_" + "A" * 40 + "_suffix"
    assert redact.redact_sensitive_text(sample, force=True) == sample


def test_short_token_stays_unchanged():
    sample = "gh" + "p_" + "A" * 5 + "_suffix"
    assert redact.redact_sensitive_text(sample, force=True) == sample


def test_body_separator_family_keeps_greedy_body():
    token = "github" + "_pat_" + "A" * 40 + "_suffix"
    assert token not in redact.redact_sensitive_text(token, force=True)


def test_plugin_pattern_retains_existing_identifier_boundary():
    redact._reset_plugin_redaction_patterns()
    try:
        assert redact.register_redaction_patterns([r"fixturetoken-[A-Za-z0-9]{10,}"]) == 1
        token = "fixturetoken-" + "A" * 40
        assert token not in redact.redact_sensitive_text(token, force=True)
        assert redact.redact_sensitive_text(token + "_suffix", force=True) == token + "_suffix"
    finally:
        redact._reset_plugin_redaction_patterns()
