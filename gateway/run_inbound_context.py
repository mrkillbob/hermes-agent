"""Inbound context-reference expansion and its request-bound provenance."""
from __future__ import annotations
import asyncio
import logging
import os
from typing import Optional
from agent.i18n import t
from gateway.session import SessionSource
logger = logging.getLogger("gateway.run")


class GatewayInboundContextMixin:
    async def _expand_inbound_context_references(
        self, source: SessionSource, session_key: str, message_text: str
    ) -> Optional[str]:
        """Expand ``@`` context references; returns None when the injection was refused (user notified)."""
        try:
            from agent.context_references import preprocess_context_references_async
            from agent.source_provenance import provenance_kwargs_for_agent

            try:
                from tools.terminal_scope import terminal_env as _ts_env
            except ImportError:
                _ts_env = os.environ.get
            _msg_cwd = _ts_env("TERMINAL_CWD") or await asyncio.to_thread(os.path.expanduser, "~")
            _msg_ctx_len = await self._inbound_model_context_length(source, session_key)
            state = self._peek_session_state(session_key) if session_key else None
            live_agent = getattr(getattr(state, "turn", None), "agent", None)
            _ctx_result = await preprocess_context_references_async(
                message_text,
                cwd=_msg_cwd,
                context_length=_msg_ctx_len,
                allowed_root=_msg_cwd,
                **provenance_kwargs_for_agent(live_agent),
            )
            if _ctx_result.blocked:
                _adapter = self._delivery_adapter_for(source)
                if _adapter:
                    await _adapter.send(
                        source.chat_id,
                        "\n".join(_ctx_result.warnings) or t("gateway.notify.context_injection_refused"),
                    )
                return None
            if _ctx_result.expanded:
                message_text = _ctx_result.message
        except Exception as exc:
            logger.warning("@ context reference expansion failed: %s", exc)
            logger.debug("@ context reference expansion failure detail", exc_info=True)
        return message_text
