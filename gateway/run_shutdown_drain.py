"""Reversible external drain state and its existing owner-controlled watcher."""
import asyncio
import logging

logger = logging.getLogger('gateway.run')


class GatewayExternalDrainMixin:
    # External drain control: the dashboard writes/removes ``.drain_request.json`` (gateway/drain_control.py);
    # the watcher flips between accepting and refusing NEW turns WITHOUT exiting (reversible).
    def _enter_external_drain(self) -> None:
        """Begin external drain: refuse NEW turns (in-flight ones are NOT interrupted). Idempotent."""
        if self._external_drain_active:
            return
        self._external_drain_active = True
        logger.info(
            "External drain ENGAGED (.drain_request.json present) — refusing "
            "new turns; %d in-flight turn(s) will finish. Process stays up.", self._active_work_count(),
        )
        # Persist "draining" so /api/status tracks it; active_agents is read-merged, only state changes.
        self._update_runtime_status("draining")

    def _exit_external_drain(self) -> None:
        """Cancel external drain: re-accept new turns. Idempotent; never resurrects a stopping gateway."""
        if not self._external_drain_active:
            return
        self._external_drain_active = False
        if self._draining or not self._running:
            logger.info(
                "External drain marker cleared during shutdown — not reverting "
                "to running (shutdown takes precedence)."
            )
            return
        logger.info(
            "External drain RELEASED (.drain_request.json removed) — "
            "re-accepting new turns; gateway_state -> %s.", self._serving_state(),
        )
        self._update_runtime_status(self._serving_state())

    async def _drain_control_watcher(self, interval: float = 1.0) -> None:
        """Poll ``.drain_request.json`` at 1s: present -> enter drain, absent -> exit; a stale epoch = absent."""
        from gateway.drain_control import drain_requested
        while self._running:
            try:
                # Off-thread: a synchronous marker read at 1s cadence can stall 30s+ under host I/O
                # pressure and take every platform heartbeat down.
                from gateway.drain_kanban import held, enforce_claim
                if held(self) or await asyncio.to_thread(drain_requested):
                    await enforce_claim(self)
                    self._enter_external_drain()
                    # API and cron work live outside messaging's _running_agents map; refresh the
                    # aggregate while an external caller polls this reversible drain state.
                    self._persist_active_agents()
                else:
                    self._exit_external_drain()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("Drain-control watcher tick error: %s", exc, exc_info=True)
            await asyncio.sleep(interval)

