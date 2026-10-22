"""Fixed public protocol vocabulary is text; encoded neighbors remain denied."""

import pytest

from agent.llm_egress_firewall import SanitizedTextRejected, validate_sanitized_text
from agent.llm_egress_runtime import dispatch_authorized_agent_request
from tests.agent.test_llm_egress_runtime import _agent

ATOMS = ("PAPER_SAFETY_SENTINEL_OK", "HERMES_BACKEND_READY", "FAILURE", "YAML")


@pytest.mark.parametrize("atom", ATOMS)
@pytest.mark.parametrize("field", ["title", "body"])
def test_fixed_public_atom_in_task_sanitized_lane_keeps_bytes(
    tmp_path, monkeypatch, atom, field
):
    monkeypatch.setenv("HERMES_KANBAN_PROTECTED_REMOTE", "1")
    text = f"Task {field}: ({atom}), public assignment."
    assert validate_sanitized_text(text) == text
    body = {"model": "test-model", "messages": [{"role": "user", "content": text}]}
    sent = []
    dispatch_authorized_agent_request(_agent(tmp_path), body, sent.append)
    assert sent == [body]


@pytest.mark.parametrize("atom", ATOMS)
@pytest.mark.parametrize(
    "kind",
    ["prefix", "suffix", "near", "encoded", "chunked", "secret", "path", "unknown"],
)
def test_fixed_atom_never_admits_other_unsafe_bytes(atom, kind):
    variants = {
        "prefix": "AAAA" + atom,
        "suffix": atom + "AAAA",
        "near": "X" + atom[1:],
        "encoded": atom + " SGVsbG8gd29ybGQ=",
        "chunked": atom + " SGVs bG8g d29y bGQ=",
        "secret": atom + " sk-proj-" + "x" * 48,
        "path": atom + " /Users/private/credential.txt",
        "unknown": atom + " QWER",
    }
    with pytest.raises(SanitizedTextRejected):
        validate_sanitized_text(variants[kind])


@pytest.mark.parametrize("atom", ATOMS[:2])
def test_whitespace_wrapping_cannot_launder_known_protocol_marker(atom):
    wrapped = " ".join(atom[i : i + 4] for i in range(0, len(atom), 4))
    with pytest.raises(SanitizedTextRejected, match="base64_payload"):
        validate_sanitized_text(wrapped)


@pytest.mark.parametrize(
    "atom", ["_posixsubprocess", "_run_task_turns", "_account_crashes", "ROOT", "JOBS"]
)
def test_unreviewed_identifiers_and_words_remain_refused(atom):
    with pytest.raises(SanitizedTextRejected, match="base64_payload"):
        validate_sanitized_text(atom)


@pytest.mark.parametrize(
    "text",
    [
        "Use YAML for each job.",
        "Record FAILURE after a tool error.",
        "HERMES_BACKEND_READY is a backend marker.",
        "PAPER_SAFETY_SENTINEL_OK is just a marker.",
    ],
)
def test_fixed_words_in_natural_phrases_are_not_joined_as_payload(text):
    assert validate_sanitized_text(text) == text
