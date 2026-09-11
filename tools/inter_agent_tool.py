"""Inter-agent messaging tool for cross-conversation communication.

Sends, receives, and lists messages between Hermes agents running in
different conversations on the same machine. Messages persist via a
local HTTP broker (see tools/comms/broker.py).
"""
import json
import sys
import urllib.request
from typing import Callable, Optional

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
    },
}

BROKER_URL = "http://127.0.0.1:8765"


def _broker_call(path: str, data: Optional[dict] = None, timeout: int = 5) -> dict:
    """Make a call to the broker."""
    url = f"{BROKER_URL}{path}"
    body = None
    if data is not None:
        body = json.dumps(data).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"} if body else {},
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


def receive_messages(to: str, since: int = 0) -> str:
    """Receive messages for an agent."""
    result = _broker_call(f"/receive?to={to}&since={since}", timeout=30)
    if isinstance(result, dict) and "error" in result:
        return tool_error(f"Failed to receive: {result['error']}")
    return json.dumps(result, indent=2)


def list_history(with_: Optional[str] = None) -> str:
    """List message history."""
    path = "/history"
    if with_:
        path += f"?with_={with_}"
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
    callback: Optional[Callable] = None,
) -> str:
    """Inter-agent messaging: send, receive, or list history."""
    if action == "send":
        if not to or not body:
            return tool_error("send requires 'to' and 'body'")
        return send_message(to, body, from_ or "hermes-agent")
    elif action == "receive":
        if not to:
            return tool_error("receive requires 'to'")
        return receive_messages(to, since)
    elif action == "history":
        return list_history(with_)
    else:
        return tool_error(f"Unknown action: {action}")


registry.register(
    name="inter_agent",
    toolset=TOOLSET,
    schema={
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
        },
        "required": ["action"],
    },
    handler=lambda args, **kw: inter_agent_tool(
        action=args.get("action", "send"),
        to=args.get("to"),
        body=args.get("body"),
        from_=args.get("from_"),
        since=args.get("since", 0),
        with_=args.get("with_"),
        callback=kw.get("callback"),
    ),
    emoji="💬",
)
