#!/usr/bin/env python3
"""Prove Revenue Lab writes are confined without mutating TradingBotV18."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys


class IsolationError(RuntimeError):
    """Raised when the sandbox isolation contract is not proven."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_status_hash(repository: Path) -> str:
    result = subprocess.run(
        ("git", "status", "--porcelain=v1"),
        cwd=repository,
        text=True,
        capture_output=True,
        check=True,
    )
    return hashlib.sha256(result.stdout.encode()).hexdigest()


def _policy_text(workspace: Path, external_root: Path) -> str:
    return "\n".join(
        (
            "(version 1)",
            "(allow default)",
            "(deny file-write*)",
            f"(allow file-write* (subpath {json.dumps(str(workspace.resolve()))}))",
            f"(deny file-write* (subpath {json.dumps(str(external_root.resolve()))}))",
            "",
        )
    )


def _sandbox_python(policy: Path, code: str, target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("/usr/bin/sandbox-exec", "-f", str(policy), sys.executable, "-c", code, str(target)),
        text=True,
        capture_output=True,
        check=False,
    )


def is_policy_denial(result: subprocess.CompletedProcess[str]) -> bool:
    """Distinguish a child write denial from failure to apply the sandbox."""

    return (
        result.returncode != 0
        and "sandbox_apply" not in result.stderr
        and "PermissionError" in result.stderr
    )


def verify_isolation(
    workspace: Path,
    tradingbot: Path,
    probe_file: Path,
) -> dict[str, object]:
    """Allow a lab sentinel and deny a byte-free write-only external open."""

    workspace = workspace.resolve()
    tradingbot = tradingbot.resolve()
    probe_file = probe_file.resolve()
    policy_dir = workspace / "tmp"
    policy_dir.mkdir(parents=True, exist_ok=True)
    policy = policy_dir / "isolation-probe.sb"
    policy.write_text(_policy_text(workspace, tradingbot), encoding="utf-8")
    sentinel = policy_dir / "inside-write-sentinel"

    inside_code = (
        "import os,sys; fd=os.open(sys.argv[1], os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600); "
        "os.write(fd, b'ok'); os.close(fd)"
    )
    inside = _sandbox_python(policy, inside_code, sentinel)
    inside_exists = sentinel.exists()
    inside_payload = sentinel.read_bytes() if inside_exists else b""
    inside_allowed = inside.returncode == 0 and inside_payload == b"ok"
    if inside_exists:
        sentinel.unlink()

    before_hash = _sha256(probe_file)
    before_status = _git_status_hash(tradingbot)
    outside_code = "import os,sys; fd=os.open(sys.argv[1], os.O_WRONLY); os.close(fd)"
    outside = _sandbox_python(policy, outside_code, probe_file)
    after_hash = _sha256(probe_file)
    after_status = _git_status_hash(tradingbot)
    policy.unlink()

    verdict = {
        "status": "available",
        "inside_write_allowed": inside_allowed,
        "tradingbot_write_denied": is_policy_denial(outside),
        "probe_hash_unchanged": before_hash == after_hash,
        "git_status_unchanged": before_status == after_status,
    }
    if not all(value is True for key, value in verdict.items() if key != "status"):
        failed = sorted(key for key, value in verdict.items() if key != "status" and value is not True)
        detail = (
            f"inside_rc={inside.returncode}, inside_exists={inside_exists}, "
            f"inside_stderr={inside.stderr.strip()!r}, outside_rc={outside.returncode}"
        )
        raise IsolationError(f"Revenue Lab write isolation failed: {', '.join(failed)}; {detail}")
    return verdict


def main() -> int:
    # HRL now lives in-tree; TradingBotV18 is an unrelated external project
    # and its path is inherently machine-specific — override with
    # HRL_TRADINGBOT_ROOT if you need to re-run this proof locally.
    import os

    lab_root = Path(__file__).resolve().parents[1]
    tradingbot_root = Path(
        os.environ.get("HRL_TRADINGBOT_ROOT", str(Path.home() / "TradingBotV18"))
    )
    verdict = verify_isolation(
        lab_root,
        tradingbot_root,
        tradingbot_root / "README.md",
    )
    print(json.dumps(verdict, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
