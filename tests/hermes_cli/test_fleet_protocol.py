import pytest

from hermes_cli.fleet_protocol import (
    FleetTask,
    ProtocolError,
    RunnerCapability,
    RunnerTelemetry,
    TaskRequirement,
    TaskTelemetry,
    decode_message,
    encode_message,
)


def test_task_telemetry_round_trips_and_derives_output_tps():
    telemetry = TaskTelemetry(output_tokens=240, duration_ms=4_000)

    assert telemetry.output_tps == 60.0
    assert TaskTelemetry.from_dict(telemetry.to_dict()) == telemetry


def test_task_telemetry_omits_tps_when_no_positive_measurement_exists():
    assert TaskTelemetry(output_tokens=0, duration_ms=4_000).output_tps is None
    assert TaskTelemetry(output_tokens=240, duration_ms=0).output_tps is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"output_tokens": -1, "duration_ms": 1},
        {"output_tokens": 1, "duration_ms": -1},
        {"output_tokens": 1, "duration_ms": 1, "output_tps": float("inf")},
        {"output_tokens": 1, "duration_ms": 1, "output_tps": -1.0},
    ],
)
def test_task_telemetry_rejects_invalid_measurements(kwargs):
    with pytest.raises(ProtocolError, match="telemetry"):
        TaskTelemetry(**kwargs)


def test_runner_telemetry_accepts_an_absent_last_measurement():
    telemetry = RunnerTelemetry.from_dict({"metrics_updated_at": 123.5})

    assert telemetry.last_output_tokens is None
    assert telemetry.last_output_tps is None
    assert telemetry.to_dict() == {"metrics_updated_at": 123.5}


def test_runner_capability_round_trips_through_wire_format():
    capability = RunnerCapability(
        node_id="mac",
        profile="coding-expert",
        models=("gpt-5", "gpt-5", "claude-sonnet"),
        tools=("terminal", "terminal", "browser"),
        projects=("LunaBot",),
        platform="darwin",
    )

    decoded = decode_message(encode_message(capability), expected_kind="runner_capability")

    assert RunnerCapability.from_dict(decoded) == RunnerCapability(
        node_id="mac",
        profile="coding-expert",
        models=("claude-sonnet", "gpt-5"),
        tools=("browser", "terminal"),
        projects=("LunaBot",),
        platform="darwin",
    )


def test_unknown_protocol_schema_is_rejected():
    with pytest.raises(ProtocolError, match="unsupported fleet protocol schema"):
        decode_message(b'{"schema":"hermes.fleet.v999","kind":"runner_capability"}')


def test_task_requirement_normalizes_duplicate_values():
    requirement = TaskRequirement(
        models=("local:7b", "local:7b"),
        tools=("git", "git", "terminal"),
        project="LunaBot",
        workspace_kind="worktree",
    )

    assert requirement.models == ("local:7b",)
    assert requirement.tools == ("git", "terminal")


def test_fleet_task_requires_a_safe_idempotency_key():
    with pytest.raises(ValueError, match="idempotency_key"):
        FleetTask(
            task_id="task-1",
            title="Build",
            body="Build the project",
            requirement=TaskRequirement(),
            idempotency_key="bad key with spaces",
        )


def test_oversized_messages_are_rejected():
    with pytest.raises(ProtocolError, match="message too large"):
        decode_message(b"{" + b"x" * (1024 * 1024) + b"}")
