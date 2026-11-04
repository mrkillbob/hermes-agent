"""Display and heartbeat phase of the request-local streaming monitor.

Also holds the stale-kill follow-up (re-arm, abandon a wedged worker, stop claiming liveness
for a wedged inline call) and the shared "no visible text was delivered" check
(``_forget_invisible_deltas``) used by the error and abandon paths.
"""

import contextlib
import logging
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

from agent import chat_completion_wait_notice as wn
from agent.model_metadata import is_local_endpoint
from agent.proxy_bypass import is_loopback_host
from agent.sleep_clock import SleepWatch
from agent.stream_max_lifetime import StreamLifetime, StreamLifetimeExceeded, resolve_stream_max_lifetime
logger = logging.getLogger(__name__)

# Kills of one dispatched attempt whose worker never unwinds before the monitor gives up on it.
_STALE_ABANDON_KILLS = 3


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

    # Stale-kill bookkeeping: consecutive kills of the SAME dispatched attempt, and the error
    # that ends a call whose worker those kills could not unwind.
    _last_killed_attempt = 0
    _same_attempt_kills = 0
    _abandon_error = None
    # Inline call wedged past the limit: the stale-timer re-arm time after which activity touches
    # are withheld (None = claim liveness as usual). A real chunk after it lifts the hold.
    _liveness_withheld_at = None
    _lifetime = StreamLifetime(0.0)  # opt-in agent.stream_max_lifetime; real clock set when the monitor starts
    _lifetime_kill = None  # (attempt id, elapsed s) of the latest cap kill
    _released_attempt = 0  # the attempt the worker's except path last caught (it let go of it)

    def _claims_liveness(self) -> bool:
        since = self._liveness_withheld_at
        return since is None or self.last_chunk_time["t"] > since

    def _start_stream_attempt(self) -> int:
        with self.stream_attempt_lock:
            if self._request_cancelled["value"]:
                raise InterruptedError("Stream request ended before retry")
            self.stream_attempt_state["current"] += 1
            attempt_id = int(self.stream_attempt_state["current"])
            self.last_chunk_time["t"] = time.time()
        self._stale_kill_requested = False
        # Attempt-local: a dead attempt's prompt progress must not mask the next load notice, and
        # its tool name (died before any text) must not label a later partial stub or retry.
        self.provider_tool_in_flight["yes"], self.prompt_progress["progress"] = False, None
        self.result["partial_tool_names"] = []
        self.deltas_were_sent["yes"] = False
        return attempt_id

    def _advance_cancelled_stall(self, elapsed: float, attempt_id: int) -> None:
        """Advance a cancelled current attempt without cancelling it or counting a new attempt."""
        # A cancelled worker may remain wedged after its socket was shut down. Advance
        # abandonment without cancelling again or crediting another circuit-breaker attempt.
        with self.stream_attempt_lock:
            cancelled_current = (self.stream_attempt_state["current"] == attempt_id
                                 and attempt_id in self.stream_attempt_state["cancelled"])
        if cancelled_current:
            logger.warning("Cancelled stream still has not unwound after %.0fs (attempt=%s).",
                           elapsed, attempt_id)
            self._rearm_or_abandon_after_kill(elapsed, expected_attempt_id=attempt_id)

    def _rearm_or_abandon_after_kill(self, elapsed: float, *, expected_attempt_id: int | None = None) -> None:
        """After a stale kill: re-arm the stale timer and announce the reconnect, unless the
        kill bookkeeping gave up on a wedged attempt (see ``_note_attempt_kill``)."""
        # Reset the timer so we don't kill repeatedly while the worker unwinds; the cap clock
        # too, so a cap at the stale timeout does not cut (and count) the same window again.
        with self.stream_attempt_lock:
            if expected_attempt_id is not None and self.stream_attempt_state["current"] != expected_attempt_id:
                return
            self.last_chunk_time["t"] = self._lifetime.started = time.time()
        if self._note_attempt_kill(elapsed, "stale", expected_attempt_id=expected_attempt_id):
            return
        if self._claims_liveness():  # the reconnect notice touches activity too (_emit_wait_notice)
            self.agent._emit_wait_notice(f"⚠ no output from provider for {int(elapsed)}s — reconnecting...")
            self.agent._touch_activity(f"stale stream detected after {int(elapsed)}s, reconnecting")

    def _note_attempt_kill(self, elapsed: float, kind: str, *, expected_attempt_id: int | None = None) -> bool:
        """Count a stale or ``stream_max_lifetime`` kill of the current attempt. Once the same
        dispatched attempt has been killed ``_STALE_ABANDON_KILLS`` times without unwinding,
        the request is parked where a socket shutdown cannot reach it, and re-killing it every
        window would stall the turn forever: a worker thread is abandoned (returns True); an
        inline call (cron, delegated children) runs on the caller's thread and cannot be, so
        the monitor stops touching activity and the owner's inactivity watchdog ends it."""
        with self.stream_attempt_lock:
            attempt = int(self.stream_attempt_state["current"])
            if expected_attempt_id is not None and attempt != expected_attempt_id:
                return True
        if attempt == self._released_attempt:  # retry backoff: the worker is not stuck in it
            return False
        rekill = bool(attempt) and attempt == self._last_killed_attempt
        self._last_killed_attempt = attempt
        self._same_attempt_kills = self._same_attempt_kills + 1 if rekill else 1
        if not rekill:
            self._liveness_withheld_at = None
        if not attempt or self._same_attempt_kills < _STALE_ABANDON_KILLS:
            return False
        if self.worker is not None:
            self._abandon_wedged_worker(elapsed, attempt, kind)
            return True
        if self._liveness_withheld_at is None:
            logger.error(
                "Inline streaming call did not unwind after %d %s kills of attempt %s; no longer "
                "reporting it as active, so the inactivity watchdog can end it. model=%s",
                self._same_attempt_kills, kind, attempt, self.api_kwargs.get("model", "unknown"))
        self._liveness_withheld_at = self.last_chunk_time["t"]
        return False

    def _forget_invisible_deltas(self, exc) -> bool:
        """Deltas fired but nothing visible reached a consumer (whitespace/think-only deltas,
        or no display consumer at all) and no tool call is in flight: from the user's and the
        model's point of view NOTHING was delivered. The "partial delivery" stub would be
        EMPTY and the loop would ask the model to continue from nowhere, so it repeats the
        lost step (#112419). Reset delivery so the failure is handled as undelivered (retry
        the unchanged request) instead. Returns whether a partial tool call is in flight."""
        tool_in_flight = bool(self.result.get("partial_tool_names")) or self.provider_tool_in_flight["yes"]
        self._visible_text_delivered(exc, tool_in_flight)
        return tool_in_flight

    def _abandon_wedged_worker(self, elapsed: float, attempt: int, kind: str = "stale") -> None:
        """Publish abandonment only while the sampled cancelled attempt still owns the request."""
        with self.stream_attempt_lock:
            if (self.stream_attempt_state["current"] != attempt
                    or attempt not in self.stream_attempt_state["cancelled"]):
                return
            if kind == "stale":
                self._abandon_error = TimeoutError(
                    f"Provider stream stalled: no chunks and the request worker did not unwind after "
                    f"{self._same_attempt_kills} stale-stream kills; request abandoned.")
            else:
                self._abandon_error = StreamLifetimeExceeded(elapsed, self._lifetime.cap)
            # Publish under the identity lock; socket shutdown stays outside it.
            dict.__setitem__(self._request_cancelled, "value", True)
            self._call_done.set()
        self._shutdown_stale_attempt_socket(getattr(self, "_attempt_stream_response", None))
        logger.error(
            "Streaming worker did not unwind after %d %s kills of attempt %s (%.0fs); "
            "abandoning the request. model=%s", self._same_attempt_kills, kind, attempt, elapsed,
            self.api_kwargs.get("model", "unknown"))

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
        local prefill) can exceed the gateway timeout. Withheld for a wedged inline call."""
        if not self._claims_liveness():
            return
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

    def _check_stream_lifetime(self) -> None:
        """Opt-in ``agent.stream_max_lifetime``: a stream that keeps sending chunks but never
        finishes never trips the stale check. Kill it like a stale kill (cancel the attempt,
        abort the request-local client) so the retry loop or the partial-delivery continuation
        takes over. Never counted in the cross-turn stale breaker: the provider WAS responding,
        and a streak of cap kills would refuse the next healthy request with nothing sent."""
        if not self._lifetime.cap or self._call_done.is_set():  # off (the default) or call over
            return
        with self.stream_attempt_lock:
            attempt = int(self.stream_attempt_state["current"])
        # A released attempt (retry backoff) has no stream left to cut.
        elapsed = self._lifetime.exceeded(attempt) if attempt and attempt != self._released_attempt else None
        if elapsed is None:
            return
        model = self.api_kwargs.get("model", "unknown")
        logger.warning("Stream still running after %.0fs (stream_max_lifetime %.0fs) — killing connection. "
                       "model=%s", elapsed, self._lifetime.cap, model)
        self.agent._buffer_diagnostic_status(
            f"⚠️ Response still streaming after {int(elapsed)}s (stream_max_lifetime "
            f"{int(self._lifetime.cap)}s, model: {model}). Aborting...")
        killed = self._attempt_stream_response
        with self.stream_attempt_lock:
            if (self.stream_attempt_state["current"] != attempt or attempt == self._released_attempt):
                return
            self._lifetime_kill = (attempt, elapsed)  # the woken worker reads it at once
        if self._cancel_current_stream_attempt("stream_max_lifetime_kill", expected_attempt_id=attempt):
            with contextlib.suppress(Exception):
                self.clients.close_once("stream_max_lifetime_kill")
        with self.stream_attempt_lock:
            if self.stream_attempt_state["current"] != attempt:
                return
            self.last_chunk_time["t"] = time.time()
        self._shutdown_stale_attempt_socket(killed)
        # Accounted, never bumped: a stale re-kill or /stop while it unwinds must not strike it either.
        self._stale_counted_attempts.add(attempt)
        # A worker the abort cannot unwind would be re-cut every window forever (the stale
        # detector may be off): same give-up as a stale kill, without its reconnect notice.
        self._note_attempt_kill(elapsed, "stream_max_lifetime", expected_attempt_id=attempt)

    def _as_lifetime_error(self, e: Exception) -> Exception:
        """The worker's transport error for an attempt the cap killed, replaced by one that names
        ``agent.stream_max_lifetime`` (the retry, exhausted and partial-delivery lines show it)."""
        kill = self._lifetime_kill
        if kill is None or isinstance(e, StreamLifetimeExceeded):  # no cap kill: touch nothing else
            return e
        with self.stream_attempt_lock:
            if kill[0] != int(self.stream_attempt_state["current"]):
                return e
        err = StreamLifetimeExceeded(kill[1], self._lifetime.cap)
        err.__cause__ = e
        return err

    def _monitor_loop(self) -> None:
        try:
            self._poll_until_done()
        except Exception:
            # A dying watchdog otherwise ends stale/cap/interrupt monitoring with no trace.
            logger.error("stream watchdog monitor loop aborted abnormally", exc_info=True)
            raise

    def _poll_until_done(self) -> None:
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
        self._lifetime = StreamLifetime(resolve_stream_max_lifetime(self._stream_stale_timeout))
        logger.debug("stream watchdog armed: stale=%.0fs cap=%.0fs model=%s", self._stream_stale_timeout,
                     self._lifetime.cap, self.api_kwargs.get("model", "unknown"))
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
                _stale_attempt_dispatched = _stale_attempt > 0
            _stale_elapsed = time.time() - self.last_chunk_time["t"]
            if _stale_attempt_dispatched and _stale_elapsed > self._stream_stale_timeout:
                self._mon.wait_notice_started_ts = None  # Reconnect status has its own owner.
                self._mon.wait_notice.reset()
                self._kill_stale_stream(_stale_elapsed, _stale_attempt)
            self._check_stream_lifetime()
            if self.agent._interrupt_requested:
                self._shutdown_stale_attempt_socket(getattr(self, "_attempt_stream_response", None))
                self._abort_for_interrupt(_stale_elapsed)
                return
