"""Inert authorization regressions for ordinary task prose; no provider dispatch."""

import base64
import json

import pytest

from agent.llm_egress_firewall import EgressBlocked
from tests.agent.test_llm_egress_firewall import firewall, _route, _sanitized_request


@pytest.mark.parametrize("word", ["IDLE", "Cross-department"])
def test_task_prose_is_authorized_without_changing_bytes(tmp_path, word):
    text = f"The {word} label appears in this ordinary context."
    authorized = firewall(tmp_path).authorize(_sanitized_request(text), _route())
    assert authorized.allowed
    assert json.loads(authorized.payload_bytes)["messages"][0]["content"] == text


@pytest.mark.parametrize("urlsafe", [False, True])
@pytest.mark.parametrize("padded", [False, True])
def test_task_prose_words_do_not_exempt_adjacent_encoded_payload(tmp_path, urlsafe, padded):
    encode = base64.urlsafe_b64encode if urlsafe else base64.b64encode
    encoded = encode(b"private source bytes\xfb\xff").decode("ascii")
    if not padded:
        encoded = encoded.rstrip("=")
    with pytest.raises(EgressBlocked) as exc_info:
        firewall(tmp_path).preflight(
            _sanitized_request(f"Cross-department status: IDLE. Payload: {encoded}"), _route()
        )
    assert "base64_payload" in exc_info.value.decision.reason_codes


@pytest.mark.parametrize("text", ["IDLF", "Cross-departmenU", "Cross-departmentAA=="])
def test_task_prose_exceptions_do_not_allow_similar_base64_blobs(tmp_path, text):
    with pytest.raises(EgressBlocked) as exc_info:
        firewall(tmp_path).preflight(_sanitized_request(text), _route())
    assert "base64_payload" in exc_info.value.decision.reason_codes


