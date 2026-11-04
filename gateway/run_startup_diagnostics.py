"""Startup warnings for retired configuration and stale service timing."""
from __future__ import annotations

import logging

from gateway.restart import DEFAULT_GATEWAY_CRON_DRAIN_TIMEOUT
from gateway.run_shutdown import _log_suppressed

logger = logging.getLogger("gateway.run")


class GatewayStartupDiagnosticsMixin:
    """Operator-facing configuration diagnostics during startup."""

    def _start_log_retired_session_reset(self) -> None:
        """Warn per served profile whose config still declares an idle/daily ``session_reset``,
        unless the plugin that honours it is enabled. Never raises."""
        with _log_suppressed(logging.DEBUG, "retired session_reset check failed", exc_info=True):
            from gateway.config_loader import read_yaml_layers
            from hermes_cli.profiles import profiles_to_serve
            from hermes_cli.session_reset_retirement import format_notice, reset_plugin_enabled, retired_reset_policy
            hits = [(name, found) for name, home in profiles_to_serve(bool(self.config.multiplex_profiles))
                    if (found := retired_reset_policy(read_yaml_layers(home)))]
            if hits and not reset_plugin_enabled():
                for name, (path, mode) in hits:
                    logger.warning("Profile %s: %s", name, format_notice(path, mode))

    def _start_log_systemd_timing_alignment(self) -> None:
        """Warn when systemd's TimeoutStopSec does not cover the drain window (a unit file from before
        an upgrade may encode the old default, so SIGKILL hits mid-drain). Never raises."""
        with _log_suppressed(logging.DEBUG, "check_systemd_timing_alignment failed: %s"):
            from gateway.shutdown_forensics import check_systemd_timing_alignment
            _alignment = check_systemd_timing_alignment(
                self._restart_drain_timeout,
                getattr(self, "_cron_drain_timeout", DEFAULT_GATEWAY_CRON_DRAIN_TIMEOUT),
            )
            if _alignment is not None and _alignment.get("mismatch"):
                logger.warning(
                    "Stale systemd unit detected: %s has TimeoutStopSec=%.0fs but drain_timeout=%.0fs "
                    "cron_drain_timeout=%.0fs (expected >=%.0fs). systemd may SIGKILL the gateway "
                    "mid-drain. Run `hermes gateway install --force` to regenerate the unit, or shorten "
                    "agent.restart_drain_timeout / agent.cron_drain_timeout.",
                    _alignment.get("unit", "(unknown)"), _alignment["timeout_stop_sec"],
                    _alignment["drain_timeout"],
                    _alignment.get("cron_drain_timeout", DEFAULT_GATEWAY_CRON_DRAIN_TIMEOUT),
                    _alignment["expected_min"],
                )
