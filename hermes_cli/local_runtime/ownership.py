"""Boot authority belongs to the long-lived root gateway, never an inference consumer."""

from __future__ import annotations

import logging
import threading
from contextvars import copy_context
from pathlib import Path

logger = logging.getLogger(__name__)
_OWNER = None


class RuntimeOwner:
    """A process-local capability, closed before its gateway starts draining."""

    def __init__(self, home: Path):
        self.home = home
        self.stopping = threading.Event()
        self._boot_lock = threading.Lock()
        self._context = copy_context()

    def ensure(self, *, force: bool = False, boot_generation: int | None = None):
        from hermes_cli.local_runtime.bootstrap import current_boot_generation

        if boot_generation is None:
            boot_generation = current_boot_generation()
        # A routed caller's secret scope must not expand the owner's ${VAR} settings.
        return self._context.copy().run(self._ensure, force=force, boot_generation=boot_generation)

    def _ensure(self, *, force: bool, boot_generation: int):
        from hermes_cli.config import load_config_readonly
        from hermes_cli.local_runtime.bootstrap import ensure_local_runtime
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override

        if self.stopping.is_set():
            return None
        # Only runtime settings are read here; no profile secrets or model assignments are copied.
        token = set_hermes_home_override(self.home)
        try:
            return ensure_local_runtime(load_config_readonly(), force=force, owner=self,
                                        boot_generation=boot_generation)
        finally:
            reset_hermes_home_override(token)

    def request_boot(self) -> bool:
        from agent.memory_provider import spawn_context_thread
        from hermes_cli.local_runtime.bootstrap import current_boot_generation

        if self.stopping.is_set():
            return False
        if not self._boot_lock.acquire(blocking=False):
            return True  # This owner already has a queued or active boot request.
        generation = current_boot_generation()

        def boot() -> None:
            try:
                self.ensure(boot_generation=generation)
            except Exception:
                logger.exception("Managed local runtime owner boot failed")
            finally:
                self._boot_lock.release()

        try:
            spawn_context_thread(boot, name="gateway-local-runtime-boot").start()
            return True
        except Exception:
            self._boot_lock.release()
            raise

    def close(self) -> None:
        from hermes_cli.local_runtime.bootstrap import cancel_pending_boot

        self.stopping.set()
        cancel_pending_boot(self)


def start_gateway_owner(*, request_boot: bool = True, on_registered=None) -> RuntimeOwner | None:
    """Called only after the durable gateway wins its lock; named gateways remain consumers."""
    global _OWNER
    from hermes_constants import get_default_hermes_root, get_hermes_home
    from agent.safe_worker_policy import worker_config_snapshot

    home = get_hermes_home()
    if worker_config_snapshot() is not None or home.resolve() != get_default_hermes_root(home=home).resolve():
        return None
    candidate = _OWNER
    if candidate is None or candidate.stopping.is_set():
        candidate = RuntimeOwner(home)
    elif candidate.home.resolve() != home.resolve():
        logger.warning("This process already carries another root gateway's local runtime")
        return None
    # Bind lifecycle cleanup before exposing this capability to concurrent consumers.
    if on_registered is not None:
        on_registered(candidate)
    _OWNER = candidate
    if request_boot:
        candidate.request_boot()
    return candidate


def current_owner(*, for_scope: bool = False) -> RuntimeOwner | None:
    from hermes_constants import get_hermes_home

    owner = _OWNER
    if owner is None or owner.stopping.is_set():
        return None
    if for_scope and get_hermes_home().resolve() != owner.home.resolve():
        return None
    return owner
