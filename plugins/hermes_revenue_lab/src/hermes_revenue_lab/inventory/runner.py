"""Bounded, shell-free execution for allowlisted inventory commands."""

import subprocess
import time
from collections.abc import Mapping

from .redaction import sanitize_diagnostic
from .types import CommandResult, CommandSpec, ObservationStatus

_DIAGNOSTIC_LIMIT = 16 * 1024


def _result(
    spec: CommandSpec,
    status: ObservationStatus,
    exit_code: int | None,
    stdout: str,
    stderr: str,
    started: float,
) -> CommandResult:
    return CommandResult(
        name=spec.name,
        status=status,
        exit_code=exit_code,
        stdout=sanitize_diagnostic(stdout[:_DIAGNOSTIC_LIMIT]),
        stderr=sanitize_diagnostic(stderr[:_DIAGNOSTIC_LIMIT]),
        duration_seconds=time.monotonic() - started,
    )


def run_command(
    spec: CommandSpec,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    """Run one argv tuple without shell evaluation and with a hard timeout."""

    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(spec.argv),
            shell=False,
            text=True,
            capture_output=True,
            timeout=spec.timeout_seconds,
            env=None if env is None else dict(env),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _result(spec, "unavailable", None, "", "timeout", started)
    except PermissionError:
        return _result(spec, "blocked", None, "", "permission denied", started)
    except FileNotFoundError:
        return _result(spec, "unavailable", None, "", "not installed", started)

    status = "available" if completed.returncode == 0 else "unavailable"
    return _result(
        spec,
        status,
        completed.returncode,
        completed.stdout,
        completed.stderr,
        started,
    )
