"""Exercise descendant observations with inert process mocks, without launching LSP."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import psutil
import pytest

from tests.agent.lsp import test_client_e2e as harness


@pytest.fixture
def cancelled_client(tmp_path, monkeypatch):
    (tmp_path / "child.pid").write_text("123", encoding="utf-8")
    client = SimpleNamespace(
        _env={}, _proc=None, state="error", start=AsyncMock(side_effect=TimeoutError)
    )
    monkeypatch.setattr(harness, "_client", lambda *_args: client)
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["observation", "cleanup"])
@pytest.mark.parametrize("error", [psutil.NoSuchProcess, psutil.ZombieProcess])
async def test_descendant_disappearance_completes_cleanup(
    tmp_path, monkeypatch, cancelled_client, phase, error
):
    process = Mock()
    process.is_running.side_effect = [True, False] if phase == "observation" else [False, True]
    getattr(process, "status" if phase == "observation" else "kill").side_effect = error(123)
    monkeypatch.setattr(psutil, "Process", lambda _pid: process)

    await harness.test_cancelled_start_hard_kills_sigterm_ignoring_descendant(tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["observation", "cleanup"])
async def test_descendant_access_denied_remains_a_failure(
    tmp_path, monkeypatch, cancelled_client, phase
):
    process = Mock()
    process.is_running.side_effect = [True, False] if phase == "observation" else [False, True]
    getattr(process, "status" if phase == "observation" else "kill").side_effect = psutil.AccessDenied(123)
    monkeypatch.setattr(psutil, "Process", lambda _pid: process)

    with pytest.raises(psutil.AccessDenied):
        await harness.test_cancelled_start_hard_kills_sigterm_ignoring_descendant(tmp_path)
