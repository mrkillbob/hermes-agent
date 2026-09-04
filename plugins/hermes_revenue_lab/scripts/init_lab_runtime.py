#!/usr/bin/env python3
from __future__ import annotations

import os
import secrets
from pathlib import Path


LAB_ROOT = Path(__file__).resolve().parents[1]
HERMES_HOME = LAB_ROOT / ".hermes"
ENV_KEY = "HERMES_DASHBOARD_SESSION_TOKEN"


def _read_single_token(env_path: Path) -> str:
    lines = env_path.read_text(encoding="utf-8").splitlines()
    prefix = f"{ENV_KEY}="
    if len(lines) != 1 or not lines[0].startswith(prefix):
        raise RuntimeError(f"{env_path} must contain exactly one {ENV_KEY} entry")
    token = lines[0][len(prefix) :]
    if not token:
        raise RuntimeError(f"{env_path} contains an empty {ENV_KEY}")
    return token


def initialize_runtime(hermes_home: Path = HERMES_HOME) -> dict[str, object]:
    hermes_home = hermes_home.resolve()
    hermes_home.mkdir(parents=True, mode=0o700, exist_ok=True)
    hermes_home.chmod(0o700)
    env_path = hermes_home / ".env"

    if env_path.exists():
        _read_single_token(env_path)
    else:
        token = secrets.token_urlsafe(32)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(env_path, flags, 0o600)
        try:
            os.write(descriptor, f"{ENV_KEY}={token}\n".encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    env_path.chmod(0o600)
    return {
        "status": "available",
        "env_path": str(env_path),
        "token_persisted": True,
    }


def main() -> int:
    metadata = initialize_runtime()
    print(f"Revenue Lab runtime initialized at {metadata['env_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
