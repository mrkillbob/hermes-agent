"""Relay-side accumulator for the chat_completions streaming wire.

Relay invokes its collector for every post-intercept chunk and then its finalizer as soon
as the provider stream ends — concurrently with Hermes' consumer thread, which may not have
read the last chunk yet. The finalizer therefore builds Relay's recorded response from
collector-observed state only, never from the consumer loop's closures. Sibling of
``relay_llm.AnthropicStreamAccumulator``; Bedrock and Codex follow the same contract.
"""

from __future__ import annotations

from typing import Any

from agent.message_content import flatten_message_text
from agent.reasoning_summaries import separate_glued_reasoning_blocks

class RelayChatAccumulator:
    """Rebuild a chat.completion from Relay's post-intercept chunk dicts."""

    def __init__(self) -> None:
        self._content: list[str] = []
        self._reasoning: list[str] = []
        self._tool_calls: dict[int, dict[str, Any]] = {}
        self._active_slot_by_index: dict[int, int] = {}
        self._last_id_by_index: dict[int, str] = {}
        self._model = self._usage = self._finish_reason = None
        self._role = "assistant"

    def observe(self, chunk: Any) -> None:
        if not isinstance(chunk, dict):
            return
        self._model = chunk.get("model") or self._model
        if chunk.get("usage"):
            self._usage = chunk["usage"]
        choices = chunk.get("choices") or []
        choice = choices[0] if choices else None  # Hermes never requests n>1
        if not isinstance(choice, dict):
            return
        self._finish_reason = choice.get("finish_reason") or self._finish_reason
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            return
        if delta.get("role"):
            self._role = delta["role"]
        text = flatten_message_text(delta.get("content"), sep="")
        if text:
            self._content.append(text)
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if reasoning:
            self._reasoning.append(separate_glued_reasoning_blocks(
                self._reasoning[-1] if self._reasoning else "", reasoning))
        for tc_delta in delta.get("tool_calls") or []:
            if not isinstance(tc_delta, dict):
                continue
            raw_index = tc_delta.get("index")
            raw_index = raw_index if isinstance(raw_index, int) else 0
            delta_id = tc_delta.get("id") or ""
            slot = self._active_slot_by_index.setdefault(raw_index, raw_index)
            if delta_id and self._last_id_by_index.get(raw_index) not in (None, delta_id):
                slot = max(self._tool_calls, default=-1) + 1
                self._active_slot_by_index[raw_index] = slot
            if delta_id:
                self._last_id_by_index[raw_index] = str(delta_id)
            entry = self._tool_calls.setdefault(slot, {
                "id": str(delta_id) if delta_id is not None else "",
                "type": "function",
                "function": {"name": "", "arguments": ""},
                "extra_content": None,
            })
            if delta_id:
                entry["id"] = str(delta_id)
            function = tc_delta.get("function")
            if isinstance(function, dict):
                if function.get("name"):
                    entry["function"]["name"] = function["name"]
                if function.get("arguments"):
                    entry["function"]["arguments"] += function["arguments"]

    def finalize(self) -> dict[str, Any]:
        message = {"role": self._role, "content": "".join(self._content) or None,
            "reasoning_content": "".join(self._reasoning) or None,
            "tool_calls": [self._tool_calls[i] for i in sorted(self._tool_calls)] or None}
        # "stop" also covers Nous Portal ``lastOne`` usage frames, which carry no finish_reason.
        return {"model": self._model, "usage": self._usage,
            "choices": [{"message": message, "finish_reason": self._finish_reason or "stop"}]}
