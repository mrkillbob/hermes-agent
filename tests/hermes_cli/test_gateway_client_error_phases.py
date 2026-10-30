"""A connected gateway context must not relabel caller failures as dial failures."""

from contextlib import asynccontextmanager
import asyncio

import pytest


def _remote_only(monkeypatch):
    from hermes_cli import gateway_runtime

    monkeypatch.setenv("HERMES_TUI_GATEWAY_URL", "ws://fixture.invalid/api/ws?token=private-value")

    def forbidden(*args, **kwargs):
        raise AssertionError("Explicit remote attempted local lifecycle")

    monkeypatch.setattr(gateway_runtime, "ensure_gateway_runtime", forbidden)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [OSError("private-dial-details"), TimeoutError("private-dial-details")])
async def test_dial_failures_remain_sanitized_without_local_fallback(monkeypatch, failure):
    from websockets.asyncio import client as transport
    from hermes_cli.gateway_client import connect_gateway, GatewayClientError

    _remote_only(monkeypatch)
    calls = []

    @asynccontextmanager
    async def dial(*args, **kwargs):
        calls.append((args, kwargs))
        raise failure
        yield

    monkeypatch.setattr(transport, "connect", dial)
    with pytest.raises(GatewayClientError, match="Gateway connection failed; no local fallback") as caught:
        async with connect_gateway():
            pytest.fail("Failed dial entered caller body")
    assert len(calls) == 1
    assert "private" not in str(caught.value)
    assert caught.value.__cause__ is failure


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["rpc_timeout", "caller_io"])
async def test_connected_body_errors_keep_their_phase_and_close_the_viewer(monkeypatch, mode):
    from websockets.asyncio import client as transport
    from hermes_cli.gateway_client import connect_gateway

    _remote_only(monkeypatch)
    calls, closed = [], []

    class Socket:
        async def send(self, value):
            calls.append(value)

        async def __aiter__(self):
            await asyncio.Event().wait()
            yield "{}"

    @asynccontextmanager
    async def dial(*args, **kwargs):
        try:
            yield Socket()
        finally:
            closed.append(True)

    monkeypatch.setattr(transport, "connect", dial)
    failure = OSError("caller-owned-failure")
    expected = TimeoutError if mode == "rpc_timeout" else OSError
    with pytest.raises(expected) as caught:
        async with connect_gateway() as client:
            if mode == "rpc_timeout":
                await client.rpc("runtime.describe", _timeout=0)
            else:
                raise failure
    if mode == "caller_io":
        assert caught.value is failure
    assert closed == [True]
    assert client.reader.done()
    assert not client.pending
    assert len(calls) == (1 if mode == "rpc_timeout" else 0)
