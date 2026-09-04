"""Immutable public evidence types for local-model benchmarking."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Literal


BenchmarkTier = Literal["fast", "standard", "reasoning", "coding", "escalation"]
ProductionTier = Literal[
    "no_llm", "fast", "standard", "reasoning", "coding", "escalation"
]


@dataclass(frozen=True)
class BenchmarkTask:
    task_id: str
    family: str
    benchmark_tier: BenchmarkTier
    production_tier: ProductionTier
    fixture_count: int
    prompt: str
    expected_json: str
    output_schema_json: str
    thinking_allowed: bool = False
    tool_schema_json: str | None = None

    @property
    def expected(self) -> object:
        return json.loads(self.expected_json)

    @property
    def output_schema(self) -> object:
        return json.loads(self.output_schema_json)

    def canonical_record(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ModelCandidate:
    name: str
    digest: str
    parameters: str
    parameter_billions: float
    quantization: str
    size: str
    capabilities: tuple[str, ...]
    inventory_id: str


@dataclass(frozen=True)
class TaskEvaluation:
    structured_valid: bool
    correctness: float | None
    tool_call_correct: bool | None
    unnecessary_thinking: bool | None
    success: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class OllamaTaskResponse:
    response_text: str
    thinking_text: str
    tool_call: dict[str, object] | None
    wall_time_seconds: float
    time_to_first_token_seconds: float | None
    prompt_eval_count: int | None
    eval_count: int | None
    load_duration_seconds: float | None
    prompt_eval_duration_seconds: float | None
    eval_duration_seconds: float | None
    total_duration_seconds: float | None
    tokens_per_second: float | None
