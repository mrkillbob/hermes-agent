"""Bounded launch identities: numeric parentage and path fingerprints, never private paths."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys

import psutil


_IMAGE_NAMES = frozenset({
    "python", "python3", "python.exe", "pythonw.exe", "hermes.exe", "hermes.cmd",
    "node", "node.exe", "cmd.exe", "powershell.exe", "pwsh.exe",
})


def _normalized(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _digest(path: str | Path) -> str:
    return hashlib.sha256(_normalized(path).encode("utf-8", errors="surrogatepass")).hexdigest()


def _image(path: str, install_root: Path) -> dict[str, object]:
    normalized = Path(_normalized(path))
    name = normalized.name.lower()
    return {"name": name if name in _IMAGE_NAMES else "other", "path_sha256": _digest(path),
            "within_install": normalized.is_relative_to(_normalized(install_root))}


def _process(pid: int, install_root: Path) -> dict[str, object]:
    if pid > 0:
        try:
            image = psutil.Process(pid).exe()
            if image:
                return {"pid": pid, "image": _image(image, install_root)}
        except (psutil.Error, OSError):
            pass  # Process liveness/access can change between parent discovery and the image query.
    return {"pid": pid, "unavailable": True}


def process_launch_context(executable: str, cwd: Path) -> dict[str, object]:
    """Read only this process and its immediate parent; fingerprints identify spelling, not trust.

    No argv, environment, file contents, process scan or native handle escapes. A disappearing or
    inaccessible process is explicitly unknown, never evidence of an absent job or a safe fallback.
    """
    parent_pid = os.getppid()
    client = _process(os.getpid(), cwd)
    client["parent_pid"] = parent_pid
    return {"client": client, "python_executable": _image(sys.executable, cwd),
            "parent": _process(parent_pid, cwd), "requested_image": _image(executable, cwd),
            "cwd_sha256": _digest(cwd)}
