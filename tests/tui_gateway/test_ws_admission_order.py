"""Queued and awaited socket sends preserve admission order and cancellation cleanup."""
import asyncio
import json

import pytest

from tui_gateway.ws import WSTransport


class RecordingSocket:
    def __init__(self):
        self.frames = []

    async def send_text(self, line):
        self.frames.append(json.loads(line))


def frame(seq):
    return {"jsonrpc": "2.0", "method": "event", "params": {
        "session_id": "ordering", "type": "status.update", "seq": seq,
        "payload": {"status": "idle"}}}


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("timer", [False, True])
def test_awaited_send_does_not_overtake_already_admitted_loop_write(batch, timer):
    async def run():
        socket = RecordingSocket()
        transport = WSTransport(socket, asyncio.get_running_loop(), peer="test")
        earlier = [frame(5), frame(6)]
        if timer:
            for event in earlier:
                event["params"]["type"] = "message.delta"
        for event in earlier:
            assert transport.write(event)
        if timer:
            # Fire the actual timer callback without a timing-dependent sleep.
            transport._flush_tokens()
        if batch:
            assert await transport.write_many_if_current_async([frame(7)], lambda: True)
        else:
            assert await transport.write_async(frame(7))
        assert await transport.write_async({"id": "barrier", "result": {}})
        await asyncio.sleep(0)
        observed = [f["params"]["seq"] for f in socket.frames if f.get("method") == "event"]
        assert observed == [5, 6, 7]
        transport.close()

    asyncio.run(run())


class CleaningSocket:
    def __init__(self):
        self.entered = asyncio.Event()
        self.cleaning = asyncio.Event()
        self.release_cleanup = asyncio.Event()

    async def send_text(self, line):
        self.entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cleaning.set()
            await self.release_cleanup.wait()


@pytest.mark.parametrize("batch", [False, True])
def test_cancelled_async_send_joins_socket_cleanup(batch):
    async def run():
        socket = CleaningSocket()
        transport = WSTransport(socket, asyncio.get_running_loop(), peer="test")
        sending = transport.write_many_async([frame(1)]) if batch else transport.write_async(frame(1))
        caller = asyncio.create_task(sending)
        try:
            await asyncio.wait_for(socket.entered.wait(), timeout=2)
            caller.cancel()
            await asyncio.wait_for(socket.cleaning.wait(), timeout=2)
            # Give completion propagation a turn; socket cleanup is deliberately still pending.
            await asyncio.sleep(0)
            returned_before_cleanup = caller.done()
        finally:
            socket.release_cleanup.set()
            await asyncio.gather(caller, return_exceptions=True)
            transport.close()
        assert not returned_before_cleanup
        assert caller.cancelled()

    asyncio.run(run())


class GatedSocket(RecordingSocket):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def send_text(self, line):
        self.entered.set()
        await self.release.wait()
        await super().send_text(line)


def test_cancelled_waiters_do_not_strand_later_sends():
    async def run():
        socket = GatedSocket()
        transport = WSTransport(socket, asyncio.get_running_loop(), peer="test")
        first = asyncio.create_task(transport.write_async(frame(1)))
        waiters = []
        last = None
        try:
            await asyncio.wait_for(socket.entered.wait(), timeout=2)
            waiters = [asyncio.create_task(transport.write_async(frame(n))) for n in range(2, 514)]
            await asyncio.sleep(0)  # admit each caller behind the gated first send
            for task in waiters:
                task.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)
            last = asyncio.create_task(transport.write_async(frame(514)))
            socket.release.set()
            assert await asyncio.wait_for(first, timeout=5)
            assert await asyncio.wait_for(last, timeout=5)
            assert [f["params"]["seq"] for f in socket.frames] == [1, 514]
        finally:
            socket.release.set()
            tasks = [first, *waiters] + ([last] if last is not None else [])
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            transport.close()

    asyncio.run(run())
