"""Shutdown watchdog observations for the owned gateway runtime."""


class GatewayShutdownDiagnosticsMixin:
    def _shutdown_watchdog_snapshot(self, ctx) -> dict:
        """State dumped by the thread-based shutdown watchdog when teardown hangs."""
        from gateway.restart import effective_stop_drain_timeout
        from gateway.shutdown_watchdog import effective_shutdown_watchdog_delay
        return {
            "restart_requested": bool(self._restart_requested),
            "draining": bool(self._draining),
            "running": bool(self._running),
            "active_agents": self._running_agent_count(),
            "active_cron_jobs": self._active_cron_job_count(),
            "active_api_runs": self._active_api_run_count(),
            "active_deferred_agent_workers": ctx.deferred_count(),
            "restart_drain_timeout": self._restart_drain_timeout,
            "effective_drain_timeout": effective_stop_drain_timeout(self),
            "launchd_exit_timeout_s": getattr(self, "_launchd_exit_timeout_s", None),
            "watchdog_delay_s": effective_shutdown_watchdog_delay(self),
            "phase_elapsed_s": ctx.elapsed() if ctx.started_at is not None else None,
        }
