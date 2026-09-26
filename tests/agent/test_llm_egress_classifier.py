from __future__ import annotations

from agent.llm_egress_classifier import _typed_payload
from agent.llm_egress_firewall import (
    GeneratedContextSegment,
    LiteralSegment,
    SanitizedSegment,
    ValidatedToolSyntaxSegment,
)


def test_typed_payload_classifies_protocol_literals_and_text() -> None:
    typed = _typed_payload(
        {"role": "user", "content": "hello"},
        (),
        {},
        sanitized_cap=128,
    )

    assert isinstance(typed["role"], LiteralSegment)
    assert typed["role"].text == "user"
    assert isinstance(typed["content"], SanitizedSegment)
    assert typed["content"].text == "hello"


def test_typed_payload_treats_anthropic_top_level_system_field_as_generated_context() -> None:
    # Anthropic's Messages API carries the system prompt under a top-level
    # "system" key (a list of content blocks), unlike the OpenAI/Codex
    # "instructions"/"system_prompt" naming already recognized here. Without
    # "system" in this set, Hermes's own system-prompt/tool-catalog text
    # (which routinely contains short all-caps acronyms like "MFCC" or "CRUD"
    # that coincidentally decode as canonical Base64) was classified as a
    # plain SanitizedSegment and scanned unmasked, blocking every Anthropic
    # request whose system prompt happened to contain such a word.
    typed = _typed_payload(
        {"system": [{"type": "text", "text": "Skill catalog: MFCC via CLI."}]},
        (),
        {},
        sanitized_cap=128,
        redact_generated_context=True,
    )

    block = typed["system"][0]
    text_segment = next(
        value for key, value in block.items() if getattr(key, "text", key) == "text"
    )
    assert isinstance(text_segment, GeneratedContextSegment)


def test_typed_payload_preserves_anthropic_tool_protocol_ids_without_trusting_content() -> None:
    opaque_id = "toolu_0123456789abcdefABCDEF"
    typed = _typed_payload(
        {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                    {
                        "type": "tool_use",
                        "id": opaque_id,
                        "name": "mcp__context_notes",
                        "input": {},
                    },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": opaque_id, "content": "ordinary result"},
                    ],
                },
            ],
        },
        (),
        {},
        sanitized_cap=128,
        allow_anthropic_thinking_replay=True,
    )

    tool_use = typed["messages"][0]["content"][0]
    tool_result = typed["messages"][1]["content"][0]
    assert isinstance(tool_use["id"], ValidatedToolSyntaxSegment)
    assert tool_use["id"].text == opaque_id
    assert isinstance(tool_use["name"], ValidatedToolSyntaxSegment)
    assert tool_use["name"].text == "mcp__context_notes"
    assert isinstance(tool_result["tool_use_id"], ValidatedToolSyntaxSegment)
    assert tool_result["tool_use_id"].text == opaque_id
    assert isinstance(tool_result["content"], SanitizedSegment)
    assert tool_result["content"].text == "ordinary result"
