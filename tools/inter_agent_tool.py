"""Inter-agent messaging tool for cross-conversation communication.

Sends, receives, and lists messages between Hermes agents running in
different conversations on the same machine. Messages persist via a
local HTTP broker (see tools/comms/broker.py).
"""
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlencode

from hermes_constants import get_hermes_home
from tools.registry import registry, tool_error

TOOLSET = "inter_agent"

SEND_SCHEMA = {
    "type": "object",
    "properties": {
        "to": {
            "type": "string",
            "description": "Recipient agent name",
        },
        "body": {
            "type": "string",
            "description": "Message body",
        },
        "from_": {
            "type": "string",
            "description": "Sender agent name (default: current agent)",
        },
    },
    "required": ["to", "body"],
}

RECEIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "to": {
            "type": "string",
            "description": "Agent name to receive messages for",
        },
        "since": {
            "type": "integer",
            "description": "Message ID to start from (default: 0)",
        },
        "limit": {
            "type": "integer",
            "description": "Maximum messages to return (default: 100)",
        },
    },
    "required": ["to"],
}

HISTORY_SCHEMA = {
    "type": "object",
    "properties": {
        "with_": {
            "type": "string",
            "description": "Filter by agent name",
        },
        "limit": {
            "type": "integer",
            "description": "Maximum messages to return (default: 100)",
        },
    },
}

_DEFAULT_HISTORY_LIMIT = 100


def _state_path(name: str) -> Path:
    return get_hermes_home() / name


def _broker_token() -> str:
    home = get_hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    path = _state_path("inter-agent-broker.token")
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        token = secrets.token_urlsafe(32)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return path.read_text(encoding="utf-8").strip()
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(token)
        return token


def _broker_endpoint() -> Optional[dict[str, str]]:
    try:
        endpoint = json.loads(
            _state_path("inter-agent-broker.json").read_text(encoding="utf-8")
        )
        port = int(endpoint["port"])
        broker_id = endpoint["broker_id"]
    except (
        FileNotFoundError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
    ):
        return None
    if not 1 <= port <= 65535 or not isinstance(broker_id, str) or not broker_id:
        return None
    return {"url": f"http://127.0.0.1:{port}", "broker_id": broker_id}


def _broker_url() -> Optional[str]:
    endpoint = _broker_endpoint()
    return endpoint["url"] if endpoint is not None else None


def _request_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_broker_token()}"}


def _broker_is_ready() -> bool:
    endpoint = _broker_endpoint()
    if endpoint is None:
        return False
    try:
        request = urllib.request.Request(
            f"{endpoint['url']}/health", headers=_request_headers()
        )
        with urllib.request.urlopen(request, timeout=0.25) as resp:
            payload = json.loads(resp.read())
            return (
                resp.status == 200
                and payload.get("ok") is True
                and payload.get("broker_id") == endpoint["broker_id"]
            )
    except Exception:
        return False


def _ensure_broker() -> None:
    """Start the shared loopback broker on first use, if it is not already running."""
    if _broker_is_ready():
        return
    subprocess.Popen(
        [sys.executable, "-m", "tools.comms.broker"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if _broker_is_ready():
            return
        time.sleep(0.05)


def _broker_call(path: str, data: Optional[dict] = None, timeout: int = 5) -> dict:
    """Make a call to the broker."""
    _ensure_broker()
    base_url = _broker_url()
    if base_url is None:
        return {"error": "broker did not publish an endpoint"}
    url = f"{base_url}{path}"
    body = None
    if data is not None:
        body = json.dumps(data).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            **_request_headers(),
            **({"Content-Type": "application/json"} if body else {}),
        },
        method="POST" if body else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


def send_message(to: str, body: str, from_: str = "hermes-agent") -> str:
    """Send a message to another agent."""
    result = _broker_call("/send", {"from": from_, "to": to, "body": body})
    if "error" in result:
        return tool_error(f"Failed to send: {result['error']}")
    return json.dumps({"ok": True, "id": result.get("id")})


def receive_messages(
    to: str, since: int = 0, limit: int = _DEFAULT_HISTORY_LIMIT
) -> str:
    """Receive messages for an agent."""
    query = urlencode({"to": to, "since": since, "limit": limit})
    result = _broker_call(f"/receive?{query}", timeout=30)
    if isinstance(result, dict) and "error" in result:
        return tool_error(f"Failed to receive: {result['error']}")
    return json.dumps(result, indent=2)


def list_history(
    with_: Optional[str] = None, limit: int = _DEFAULT_HISTORY_LIMIT
) -> str:
    """List message history."""
    path = "/history"
    query = {"limit": limit}
    if with_:
        query["with_"] = with_
    path += f"?{urlencode(query)}"
    result = _broker_call(path)
    if isinstance(result, dict) and "error" in result:
        return tool_error(f"Failed to get history: {result['error']}")
    return json.dumps(result, indent=2)


def inter_agent_tool(
    action: str = "send",
    to: Optional[str] = None,
    body: Optional[str] = None,
    from_: Optional[str] = None,
    since: int = 0,
    with_: Optional[str] = None,
    limit: int = _DEFAULT_HISTORY_LIMIT,
    sender: Optional[str] = None,
    callback: Optional[Callable] = None,
) -> str:
    """Inter-agent messaging: send, receive, or list history."""
    if action == "send":
        if not to or not body:
            return tool_error("send requires 'to' and 'body'")
        return send_message(to, body, from_ or sender or "hermes-agent")
    elif action == "receive":
        if not to:
            return tool_error("receive requires 'to'")
        return receive_messages(to, since, limit)
    elif action == "history":
        return list_history(with_, limit)
    else:
        return tool_error(f"Unknown action: {action}")


registry.register(
    name="inter_agent",
    toolset=TOOLSET,
    schema={
        "description": "Send persistent messages between Hermes agents.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["send", "receive", "history"],
                    "description": "Action to perform",
                },
                "to": {
                    "type": "string",
                    "description": "Recipient agent name (for send/receive)",
                },
                "body": {
                    "type": "string",
                    "description": "Message body (for send)",
                },
                "from_": {
                    "type": "string",
                    "description": "Sender agent name (default: hermes-agent)",
                },
                "since": {
                    "type": "integer",
                    "description": "Message ID to start from (for receive)",
                },
                "with_": {
                    "type": "string",
                    "description": "Filter by agent name (for history)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum messages to return (default: 100)",
                },
            },
            "required": ["action"],
        },
    },
    handler=lambda args, **kw: inter_agent_tool(
        action=args.get("action", "send"),
        to=args.get("to"),
        body=args.get("body"),
        from_=args.get("from_"),
        since=args.get("since", 0),
        with_=args.get("with_"),
        limit=args.get("limit", _DEFAULT_HISTORY_LIMIT),
        sender=kw.get("session_id") or kw.get("task_id"),
        callback=kw.get("callback"),
    ),
    emoji="💬",
)
