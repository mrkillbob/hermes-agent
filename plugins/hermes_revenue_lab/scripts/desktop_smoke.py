#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hermes_revenue_lab.inventory.redaction import assert_publication_safe
from hermes_revenue_lab.inventory.publish import update_desktop_verdict


LAB_ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "http://127.0.0.1:9120"
GATEWAY_NAME = "Hermes Revenue Lab"
ENV_KEY = "HERMES_DASHBOARD_SESSION_TOKEN"
DEFAULT_ENV_PATH = LAB_ROOT / ".hermes" / ".env"
DEFAULT_VERDICT_PATH = LAB_ROOT / "artifacts" / "bootstrap" / "desktop_connection_verdict.json"


def load_session_token(env_path: Path = DEFAULT_ENV_PATH) -> str:
    lines = env_path.read_text(encoding="utf-8").splitlines()
    prefix = f"{ENV_KEY}="
    matches = [line[len(prefix) :] for line in lines if line.startswith(prefix)]
    if len(lines) != 1 or len(matches) != 1 or not matches[0]:
        raise RuntimeError(f"{env_path} must contain exactly one non-empty session token")
    return matches[0]


def _require_loopback_endpoint(endpoint: str) -> None:
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port != 9120:
        raise ValueError("Desktop smoke endpoint must be exactly http://127.0.0.1:9120")
    if parsed.path or parsed.query or parsed.fragment:
        raise ValueError("Desktop smoke endpoint must not contain a path, query, or fragment")


def wait_for_status(
    endpoint: str, token: str, timeout_seconds: float = 30.0
) -> dict[str, object]:
    _require_loopback_endpoint(endpoint)
    deadline = time.monotonic() + timeout_seconds
    request = urllib.request.Request(
        f"{endpoint}/api/status",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    last_error = "not attempted"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(request, timeout=2.0) as response:
                if response.status != 200:
                    raise RuntimeError(f"unexpected HTTP status {response.status}")
                raw_body = response.read()
                body: Any = json.loads(raw_body) if raw_body else {}
                if not isinstance(body, dict):
                    raise RuntimeError("Hermes status response was not a JSON object")
                verdict = {
                    "status": "available",
                    "http_status": response.status,
                    "endpoint": endpoint,
                    "gateway_name": GATEWAY_NAME,
                    "auth_required": bool(body.get("auth_required", False)),
                }
                assert_publication_safe(verdict)
                return verdict
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
            last_error = type(exc).__name__
            time.sleep(0.25)
    raise RuntimeError(
        f"Hermes Revenue Lab did not become healthy within {timeout_seconds:g} seconds "
        f"(last error: {last_error})"
    )


def verify_token_auth(endpoint: str, token: str) -> dict[str, object]:
    """Prove the protected read-only config route denies anonymous access."""

    _require_loopback_endpoint(endpoint)
    url = f"{endpoint}/api/config"
    anonymous = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(anonymous, timeout=2.0):
            pass
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise RuntimeError(f"anonymous protected-route probe returned HTTP {exc.code}") from exc
    else:
        raise RuntimeError("anonymous protected-route probe was not denied")

    authenticated = urllib.request.Request(
        url,
        headers={"X-Hermes-Session-Token": token},
        method="GET",
    )
    try:
        with urllib.request.urlopen(authenticated, timeout=2.0) as response:
            if response.status != 200:
                raise RuntimeError(
                    f"authenticated protected-route probe returned HTTP {response.status}"
                )
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"authenticated protected-route probe returned HTTP {exc.code}"
        ) from exc
    return {"token_auth_verified": True}


def publish_verdict(verdict: dict[str, object], destination: Path = DEFAULT_VERDICT_PATH) -> None:
    assert_publication_safe(verdict)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.tmp"
    payload = json.dumps(verdict, indent=2, sort_keys=True) + "\n"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, payload.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    temporary.chmod(0o644)
    os.replace(temporary, destination)


def main() -> int:
    token = load_session_token()
    verdict = wait_for_status(ENDPOINT, token)
    verdict.update(verify_token_auth(ENDPOINT, token))
    verdict["verified_at"] = datetime.now(timezone.utc).isoformat()
    update_desktop_verdict(DEFAULT_VERDICT_PATH.parent, verdict)
    print(f"Desktop connection verdict: {verdict['status']} at {ENDPOINT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
