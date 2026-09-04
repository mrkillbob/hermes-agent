"""Immutable evidence types shared by the HRL-0 inventory pipeline."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ObservationStatus = Literal["available", "unavailable", "blocked", "not_observed"]


@dataclass(frozen=True)
class Observation:
    """One evidence value with an explicit availability verdict."""

    name: str
    status: ObservationStatus
    value: Any
    reason: str | None = None

    @classmethod
    def unavailable(cls, name: str, reason: str) -> "Observation":
        return cls(name=name, status="unavailable", value=None, reason=reason)


@dataclass(frozen=True)
class CommandSpec:
    """An allowlisted argv-only command with a bounded runtime."""

    name: str
    argv: tuple[str, ...]
    timeout_seconds: float = 10.0
    required: bool = False


@dataclass(frozen=True)
class CommandResult:
    """Sanitized result from one inventory command."""

    name: str
    status: ObservationStatus
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float


@dataclass(frozen=True)
class InventoryContext:
    """Exact HRL and TradingBot paths used to enforce isolation."""

    workspace: Path
    hermes_home: Path
    tradingbot_path: Path

    def __post_init__(self) -> None:
        workspace = self.workspace.resolve()
        tradingbot = self.tradingbot_path.resolve()
        if workspace == tradingbot or tradingbot in workspace.parents:
            raise ValueError("Revenue Lab workspace must be outside TradingBotV18")
        if self.hermes_home.resolve() != workspace / ".hermes":
            raise ValueError("Hermes home must be <workspace>/.hermes")
