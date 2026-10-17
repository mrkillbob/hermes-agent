"""Display and heartbeat phase of the request-local streaming monitor."""

import logging
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

from agent import chat_completion_wait_notice as wn
from agent.model_metadata import is_local_endpoint
from agent.proxy_bypass import is_loopback_host
from agent.sleep_clock import SleepWatch

logger = logging.getLogger(__name__)


class _CancellationState(dict):
    """Keep the monitor's flag transition coupled to shutdown of its checked-out response."""

    def __init__(self, initial, on_cancel):
        super().__init__(initial)
        self._on_cancel = on_cancel

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        if key == "value" and value:
            self._on_cancel()


class StreamingWaitMonitor:
    @staticmethod
    def _chunk_identity(diag, chunk, model_name, response_id, upstream_provider) -> tuple:
        """Model, response id and serving provider, as the stream reports them."""
        if hasattr(chunk, "model") and chunk.model:
            model_name = chunk.model
        if response_id is None and isinstance(getattr(chunk, "id", None), str) and chunk.id:
            response_id = chunk.id
        if upstream_provider is None and isinstance(getattr(chunk, "provider", None), str) and chunk.provider:
            upstream_provider = chunk.provider  # OpenRouter stamps who served
            diag["serving_provider"] = upstream_provider.strip()[:64]  # attribute a mid-stream drop (#90216)
        return model_name, response_id, upstream_provider

    def _visible_text_delivered(self, error, tool_in_flight: bool) -> bool:
        """True when recorded text proves the user saw output a retry would duplicate (#112419).

        ``deltas_were_sent`` flips on any content delta, including ones no consumer displayed, so a
        stream that dies after deltas but before visible text is reset to an undelivered failure.
        """
        if bool((getattr(self.agent, "_current_streamed_assistant_text", "") or "").strip()):
            return True
        if self.deltas_were_sent["yes"] and not tool_in_flight:
            logging.getLogger("agent.chat_completion_helpers").warning(
                "Stream died after deltas but before any visible text was delivered (0 chars, "
                "no tool call in flight); treating as an undelivered stream failure: %s", error)
            self._quiet(self.agent._reset_stream_delivery_tracking)
            self.deltas_were_sent["yes"] = False
            self.first_delta_fired["done"] = False
        return False

    @property
    def _request_cancelled(self):
        return self.__dict__.get("_request_cancelled")

    @_request_cancelled.setter
    def _request_cancelled(self, state):
        self.__dict__["_request_cancelled"] = _CancellationState(
            state,
            lambda: self._shutdown_stale_attempt_socket(
                getattr(self, "_attempt_stream_response", None)),
        )

    @property
    def _attempt_stream_response(self):
        return self.__dict__.get("_attempt_stream_response")

    @_attempt_stream_response.setter
    def _attempt_stream_response(self, response):
        self.__dict__["_attempt_stream_response"] = response
        if response is None:
            return
        cancelled = getattr(self, "_request_cancelled", {})
        attempt_state = getattr(self, "stream_attempt_state", {})
        current = attempt_state.get("current")
        late = (
            (isinstance(cancelled, dict) and cancelled.get("value"))
            or bool(getattr(getattr(self, "agent", None), "_interrupt_requested", False))
            or current in attempt_state.get("cancelled", set())
        )
        if late:
            self._shutdown_stale_attempt_socket(response)

    def _shutdown_stale_attempt_socket(self, response) -> int:
        """Unblock a checked-out HTTP response without closing its owner-thread FD."""
        if response is None:
            return 0
        from agent.agent_runtime_helpers_connections import (
            _connection_candidates,
            _shutdown_socket,
            _socket_from_candidate,
        )

        seen: set[int] = set()
        for root in (getattr(response, "stream", None), response):
            for candidate in _connection_candidates(root):
                sock = _socket_from_candidate(candidate)
                if sock is not None and id(sock) not in seen:
                    seen.add(id(sock))
                    _shutdown_socket(sock)
        return len(seen)

    def _poll_local_load_notice(self, now: float) -> bool:
        """Managed local server: surface a cold model's weight-load progress
        instead of the 60s neutral "waiting on <model>" notice. Polled ~1s only while no
        REAL chunk arrived for 2s+ (never during healthy token flow); in-memory,
        no network. True while loading = heartbeat liveness, skip the rest of
        this iteration (the stale detector's local floor dwarfs any load)."""
        from agent.chat_completion_local import _managed_local_load_notice

        m = self._mon
        if self._show_prompt_progress(now):
            return True
        if now - self.last_chunk_time["t"] < 2.0 or now - m.last_load_poll < 1.0:
            return False
        m.last_load_poll = now
        _load_notice = _managed_local_load_notice(self.agent, self.api_kwargs)
        if _load_notice is not None:
            m.wait_notice_started_ts = None  # The local loader now owns the display.
            m.wait_notice.reset()
            self.agent._emit_wait_notice(_load_notice)
            self.agent._touch_activity("local model loading")
            m.load_notice_shown, m.load_notice_misses, m.last_heartbeat = True, 0, now  # loading IS liveness
            return True
        if m.load_notice_shown:
            # One missed sample is routine (probe timeout under load); clearing on it strobed the line.
            m.load_notice_misses += 1
            if m.load_notice_misses >= 3:
                m.load_notice_shown, m.load_notice_misses = False, 0
                self.agent._emit_wait_notice("")
        return False

    def _show_prompt_progress(self, now: float) -> bool:
        """Prompt progress straight from the stream's own progress chunks (llama-server with
        ``return_progress``), at most once a second until the first token. Clears the line
        when the first token arrives. True when it showed progress."""
        from agent.local_prompt_progress import prompt_progress_percent

        m = self._mon
        state = getattr(self, "prompt_progress", None)
        diag = getattr(getattr(self, "clients", None), "diag", None)
        first_token = isinstance(diag, dict) and bool(diag.get("first_chunk_at"))
        if not state or state["progress"] is None:
            return False
        if first_token:
            if m.load_notice_shown:
                m.load_notice_shown, m.load_notice_misses = False, 0
                self.agent._emit_wait_notice("")
            return False
        if now - state["t"] > 60.0 or now - m.last_load_poll < 1.0:
            return False
        m.last_load_poll = now
        percent = prompt_progress_percent(state["progress"])
        m.wait_notice_started_ts = None  # The prompt-progress line owns the display.
        m.wait_notice.reset()
        self.agent._emit_wait_notice(
            f"⚙ processing prompt — {percent}%" if percent is not None else "⚙ processing prompt")
        m.load_notice_shown, m.load_notice_misses, m.last_heartbeat = True, 0, now
        return True

    def _skip_sleep(self, slept: float) -> None:
        """The computer slept and froze this request with a server on the same machine: move the
        stall clock past the sleep, so the request gets its full patience again after wake. Never
        past now: a chunk can land between the sleep reading and this write."""
        if slept <= 0:
            return
        now = time.time()
        self.last_chunk_time["t"] = min(self.last_chunk_time["t"] + slept, now)
        self._mon.last_heartbeat = min(self._mon.last_heartbeat + slept, now)
        logger.info("Computer slept for %.0fs during a model request; the stall timer skips that time.", slept)
        self.agent._touch_activity(f"resumed after the computer slept for {int(slept)}s")

    def _heartbeat(self, waiting_secs: int) -> None:
        """Gateway inactivity heartbeat: the start-to-first-chunk gap (thinking,
        local prefill) can exceed the gateway timeout."""
        if waiting_secs >= 60.0:
            # No chunks for 60s+: say WHAT the wait is and WHEN recovery kicks in —
            # once per silence, not every heartbeat (#92550).
            stale = self._stream_stale_timeout
            watchdog = ("stream stale", stale - waiting_secs) if stale is not None and stale != float("inf") else None
            diag = getattr(getattr(self, "clients", None), "diag", None)
            phase = "post_chunk" if isinstance(diag, dict) and diag.get("first_chunk_at") else "first_chunk"
            if not self._mon.wait_notice.should_emit(phase, watchdog):
                self.agent._touch_activity(f"waiting for stream response ({waiting_secs}s, {phase})")
                return
            self._mon.wait_notice_started_ts = self._mon.last_heartbeat
            self.agent._emit_wait_notice(wn.wait_notice_text(
                self.api_kwargs.get('model', 'the provider'), waiting_secs, phase, watchdog))
        else:
            # Chunks are flowing — keep the tracker fresh, leave the display alone.
            self.agent._touch_activity(f"waiting for stream response ({waiting_secs}s, no chunks yet)")

    def _monitor_loop(self) -> None:
        _HEARTBEAT_INTERVAL = 30.0  # seconds between gateway activity touches
        self._mon = SimpleNamespace(
            last_heartbeat=time.time(), last_load_poll=0.0,
            load_notice_shown=False, load_notice_misses=0, wait_notice_started_ts=None,
            wait_notice=wn.WaitNoticeState(),
        )
        _is_local_base = bool(self.agent.base_url) and is_local_endpoint(self.agent.base_url)
        # Only a server on this machine sleeps with it; a remote one kept working (or died).
        sleep_watch = (SleepWatch(interval=0.3) if self.agent.base_url
                       and is_loopback_host(urlsplit(self.agent.base_url).hostname) else None)
        while not self._call_done.is_set():
            self._call_done.wait(timeout=0.3)
            if sleep_watch is not None:
                self._skip_sleep(sleep_watch.slept())
            _hb_now = time.time()
            if _is_local_base and self._poll_local_load_notice(_hb_now):
                continue
            # Reasoning callbacks do not clear the classic CLI spinner. The empty
            # protocol payload resets status without adding synthetic reasoning.
            if (self._mon.wait_notice_started_ts is not None
                    and self.last_chunk_time["t"] > self._mon.wait_notice_started_ts):
                self.agent._emit_wait_notice("")
                self._mon.wait_notice_started_ts = None
                self._mon.wait_notice.reset()
            if _hb_now - self._mon.last_heartbeat >= _HEARTBEAT_INTERVAL:
                self._mon.last_heartbeat = _hb_now
                self._heartbeat(int(_hb_now - self.last_chunk_time["t"]))
            with self.stream_attempt_lock:
                _stale_attempt = int(self.stream_attempt_state["current"])
                _stale_attempt_active = (
                    _stale_attempt > 0
                    and _stale_attempt not in self.stream_attempt_state["cancelled"]
                )
            _stale_elapsed = time.time() - self.last_chunk_time["t"]
            if _stale_attempt_active and _stale_elapsed > self._stream_stale_timeout:
                self._mon.wait_notice_started_ts = None  # Reconnect status has its own owner.
                self._mon.wait_notice.reset()
                self._kill_stale_stream(_stale_elapsed, _stale_attempt)
            if self.agent._interrupt_requested:
                self._shutdown_stale_attempt_socket(getattr(self, "_attempt_stream_response", None))
                self._abort_for_interrupt(_stale_elapsed)
                return
