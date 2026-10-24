"""Seed and reset the real local install the Desktop update suite drives.

The install is made by the real entry points a Linux user runs (``scripts/install.sh`` and then
``hermes desktop --build-only``) against a local bare origin, inside the upgrade suite's sandbox
machinery (``tests/e2e/core/upgrade``). Building it takes minutes, so it is built once per run into
``<root>/sb`` and snapshotted; each spec restores the snapshot back into the SAME path (the install
bakes absolute paths into launchers and PM facts, so a copy elsewhere would not be the same install).

    python seed.py install <root>     -> JSON {home, hermesHome, checkout, origin, env, headSha}
    python seed.py restore <root>     -> the snapshot back in place (install + origin)
    python seed.py publish <root> <message> <relpath> <content-file>  -> sha of the new origin/main

HERMES_E2E_UPDATE_INSTALL_REF=<sha> installs that commit instead of HEAD (local A/B against an open
fix, or a sabotage commit, without touching the checkout the suite runs from).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from tests.e2e.core.upgrade import _helpers as H
from tests.e2e.core.upgrade import _install_helpers as I
from pm.lifetime import Gate, LifetimeUnavailable, gate


def _paths(root: Path) -> dict[str, Path]:
    return {
        "sb": root / "sb",
        "origin": root / "origin.git",
        "golden": root / "golden",
        "facts": root / "install.json",
    }


def _sandbox(root: Path) -> I.Sandbox:
    facts = json.loads(_paths(root)["facts"].read_text(encoding="utf-8"))
    return I.Sandbox(root=Path(facts["sandboxRoot"]), env=facts["env"])


_NPM_TOKENS = {
    "codes": {"unknown", "ENOTEMPTY", "ENOENT", "EACCES", "EPERM", "ENOSPC", "EBUSY", "ECONNRESET", "ECONNREFUSED",
              "ETIMEDOUT", "EAI_AGAIN", "ENOTFOUND", "EINTEGRITY", "EBADENGINE", "ERESOLVE", "ELIFECYCLE", "E401", "E403", "E404", "E429"},
    "syscalls": {"unknown", "rmdir", "unlink", "rename", "open", "mkdir", "scandir", "read", "write", "connect", "spawn"},
    "errnos": {"unknown", "-1", "-2", "-13", "-17", "-28", "-39", "-4048", "-4058", "-4082"},
}
_NPM_SIGNALS = {None, "unknown", "SIGTERM", "SIGKILL", "SIGINT", "SIGABRT", "SIGSEGV", "SIGBUS", "SIGXCPU", "SIGXFSZ", "SIGHUP"}


def _unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate diagnostic key")
    return result


def _npm_frame(raw: str) -> dict | None:
    if len(raw) > 4096:
        return None
    try:
        frame = json.loads(raw, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        return None
    keys = {"schema", "phase", "attempt", "status", "signal", "node_version", "npm_version", "elapsed_ms",
            "log_present", "log_readable", "log_truncated", *_NPM_TOKENS}
    if not isinstance(frame, dict) or frame.keys() != keys:
        return None
    if type(frame["schema"]) is not int or frame["schema"] != 1 or frame["phase"] != "npm_ci":
        return None
    if type(frame["attempt"]) is not int or frame["attempt"] not in (1, 2):
        return None
    if frame["status"] is not None and (type(frame["status"]) is not int or not 0 <= frame["status"] <= 2147483647):
        return None
    if not isinstance(frame["signal"], (str, type(None))) or frame["signal"] not in _NPM_SIGNALS:
        return None
    if type(frame["elapsed_ms"]) is not int or not 0 <= frame["elapsed_ms"] <= 9007199254740991:
        return None
    for key in ("node_version", "npm_version"):
        value = frame[key]
        if not isinstance(value, str) or (value != "unknown" and not re.fullmatch(r"\d{1,6}\.\d{1,6}\.\d{1,6}", value, re.ASCII)):
            return None
    if any(type(frame[key]) is not bool for key in ("log_present", "log_readable", "log_truncated")):
        return None
    for key, allowed in _NPM_TOKENS.items():
        values = frame[key]
        if not isinstance(values, list) or not 1 <= len(values) <= len(allowed):
            return None
        if any(not isinstance(value, str) or value not in allowed for value in values):
            return None
    return frame


def _save_npm_diagnostics(cp: subprocess.CompletedProcess, directory: Path) -> None:
    failures = []
    # Verbose builds inherit stderr; run_source_script's contained failure tail
    # merges stderr into stdout with its exact two-space + four-space indent.
    for output in (cp.stderr, cp.stdout):
        for line in (output or "").splitlines():
            for prefix in ("HERMES_NPM_FAILURE_V1 ", "      HERMES_NPM_FAILURE_V1 "):
                if line.startswith(prefix):
                    frame = _npm_frame(line.removeprefix(prefix))
                    if frame is not None and frame not in failures and len(failures) < 2:
                        failures.append(frame)
    if failures:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "node-dependency-failure.json"
        # The disposable sandbox owns this path. Never follow a pre-existing file/link.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"schema": 1, "failures": failures}, stream)
            stream.write("\n")


def _fail(what: str, cp: subprocess.CompletedProcess, *, diagnostics_dir: Path | None = None) -> None:
    if diagnostics_dir is not None:
        try:
            _save_npm_diagnostics(cp, diagnostics_dir)
        except OSError:
            # A failed evidence write must preserve the original failing build verdict.
            sys.stderr.write("npm dependency diagnostic retention unavailable\n")
    sys.stderr.write(f"{what} failed:\n{H.describe(cp)}\n")
    raise SystemExit(1)


def install(root: Path) -> None:
    p = _paths(root)
    root.mkdir(parents=True, exist_ok=True)
    p["facts"].unlink(missing_ok=True)
    for key in ("sb", "origin", "golden"):
        shutil.rmtree(p[key], ignore_errors=True)
    head = os.environ.get("HERMES_E2E_UPDATE_INSTALL_REF") or I.head_sha()
    origin = I.make_origin(root, head)
    # Serve full clones. When a CI runner's git honours the installer's --filter=tree:0 against a
    # local file:// origin, the clone's lazy tree fetches fan out into more than 1300 concurrent
    # upload-packs, and the runner is OOM-killed about 90 s into seeding. The Desktop cells need an
    # installed checkout, not a treeless one.
    I.git("config", "uploadpack.allowFilter", "false", cwd=origin)
    sb = I.new_sandbox(p["sb"], origin)
    # The installer's treeless clone still marks the checkout as a promisor, and on the CI
    # runner's git every missing-object probe then starts a lazy fetch whose own probe starts
    # another one (a chain of 78+ nested `fetch --filter=blob:none --stdin`). The clone is full, so
    # there is nothing to lazily fetch; stop the chain for the installer run only.
    cp = I.run_installer(I.Sandbox(root=sb.root, env={**sb.env, "GIT_NO_LAZY_FETCH": "1"}))
    if cp.returncode != 0:
        _fail("scripts/install.sh --non-interactive", cp)
    # publishUpstream() lands commits on main, never a release: follow main, not the stable default.
    I.pin_main_channel(sb)
    # The real way a CLI install gets the Desktop app on Linux: build + package into
    # apps/desktop/release/linux-unpacked, the tree the Desktop updater swaps in place.
    cp = sb.cli("desktop", "--build-only", timeout=1800)
    if cp.returncode != 0:
        _fail("hermes desktop --build-only", cp, diagnostics_dir=sb.hermes_home / "logs")
    facts = {
        "sandboxRoot": str(sb.root),
        "home": str(sb.home),
        "hermesHome": str(sb.hermes_home),
        "checkout": str(sb.checkout),
        "hermes": sb.hermes,
        "origin": str(origin),
        "env": sb.env,
        "headSha": head,
    }
    p["golden"].mkdir()
    for key in ("sb", "origin"):
        subprocess.run(["cp", "-a", str(p[key]), str(p["golden"] / key)], check=True)
    # Written last: its presence means the snapshot is complete (REUSE keys on it).
    p["facts"].write_text(json.dumps(facts, indent=1), encoding="utf-8")
    print(json.dumps(facts))


def restore(root: Path) -> None:
    p = _paths(root)
    for path in (p["sb"], p["origin"], p["golden"], p["golden"] / "sb", p["golden"] / "origin"):
        try:
            if not stat.S_ISDIR(path.lstat().st_mode):
                raise RuntimeError("fixture root is not a concrete directory; reseed required")
        except OSError as exc:
            raise RuntimeError("fixture root is missing; reseed required") from exc
    try:
        if not stat.S_ISREG(p["facts"].lstat().st_mode):
            raise RuntimeError("fixture facts are not a regular file; reseed required")
    except OSError as exc:
        raise RuntimeError("fixture facts are missing; reseed required") from exc
    facts = json.loads(p["facts"].read_text(encoding="utf-8"))
    sandbox = Path(facts["sandboxRoot"]).absolute()
    hermes_home = Path(facts["hermesHome"]).absolute()
    origin = Path(facts["origin"]).absolute()
    expected_sandbox = p["sb"].absolute()
    if (sandbox != expected_sandbox or not hermes_home.is_relative_to(sandbox)
            or Path(facts["env"]["HERMES_HOME"]).absolute() != hermes_home
            or origin != p["origin"].absolute()):
        raise RuntimeError("fixture facts escape the owned sandbox; reseed required")

    golden_sandbox = p["golden"] / "sb"
    sandbox_paths = (
        (sandbox, hermes_home),
        (golden_sandbox, golden_sandbox / hermes_home.relative_to(sandbox)),
    )
    for base, descendant in sandbox_paths:
        cursor = base
        for part in (*descendant.relative_to(base).parts, "installs", ".lifetimes"):
            cursor /= part
            try:
                if not stat.S_ISDIR(cursor.lstat().st_mode):
                    raise RuntimeError("fixture facts cross a non-directory path; reseed required")
            except OSError as exc:
                raise RuntimeError("fixture facts cross a missing path; reseed required") from exc

    # Qualification records bind physical identities. Validate and lock every managed install
    # domain before reconciling either tree; never let snapshot metadata certify a copied inode.
    install_root = hermes_home / "installs"
    domains = []
    coordination = install_root / ".lifetimes"
    expected_coordination = p["golden"] / "sb" / hermes_home.relative_to(sandbox) / "installs" / ".lifetimes"
    for path in (install_root, coordination, expected_coordination):
        try:
            if not stat.S_ISDIR(path.lstat().st_mode):
                raise RuntimeError("managed fixture coordination path is not concrete; reseed required")
        except OSError as exc:
            raise RuntimeError("managed fixture coordination path is missing; reseed required") from exc
    current_markers = sorted(coordination.glob("*/domain.json"))
    expected_markers = sorted(expected_coordination.glob("*/domain.json"))
    if [marker.parent.name for marker in current_markers] != [marker.parent.name for marker in expected_markers]:
        raise RuntimeError("managed fixture domains differ from snapshot; reseed required")
    for marker, expected_marker in zip(current_markers, expected_markers):
        namespace = marker.parent
        domain = install_root / namespace.name
        guard = Gate(domain)
        try:
            if (not stat.S_ISDIR(namespace.lstat().st_mode)
                    or not stat.S_ISDIR(guard.activities.lstat().st_mode)
                    or not stat.S_ISREG(guard.marker.lstat().st_mode)
                    or not stat.S_ISREG(expected_marker.lstat().st_mode)):
                raise RuntimeError("managed fixture identity is incomplete; reseed required")
            guard.domain.lstat()
            guard.activities.lstat()
            guard.marker.lstat()
            (namespace / "gate").lstat()
            relative_domain = domain.relative_to(sandbox)
            source_cursor = p["golden"] / "sb"
            target_cursor = p["sb"]
            for part in relative_domain.parts:
                source_cursor /= part
                target_cursor /= part
                if (not stat.S_ISDIR(source_cursor.lstat().st_mode)
                        or not stat.S_ISDIR(target_cursor.lstat().st_mode)):
                    raise RuntimeError("managed fixture directory cannot be reconciled in place; reseed required")
            if guard.marker.read_bytes() != expected_marker.read_bytes():
                raise RuntimeError("managed fixture qualification differs from snapshot; reseed required")
        except OSError as exc:
            raise RuntimeError("managed fixture identity is missing; reseed required") from exc
        domains.append(domain)

    with ExitStack() as stack:
        guards = []
        for domain in domains:
            try:
                guard = stack.enter_context(gate(domain, wait=False))
            except (OSError, LifetimeUnavailable) as exc:
                raise RuntimeError("cannot lock managed fixture identity; reseed required") from exc
            if guard is None or not guard.qualified:
                raise RuntimeError("managed fixture identity changed; reseed required")
            if guard.held():
                raise RuntimeError("managed fixture still has active readers; restore refused")
            guards.append(guard)

        for key in ("sb", "origin"):
            _reconcile_tree(p["golden"] / key, p[key], preserve_coordination=(key == "sb"))
        if any(not guard.qualified for guard in guards):
            raise RuntimeError("managed fixture identity changed during restore; reseed required")
    print(p["facts"].read_text(encoding="utf-8"))


def _remove_entry(path: Path) -> None:
    """Remove one fixture entry without following a symlink at the entry itself."""
    if path.is_symlink() or not path.is_dir():
        path.unlink(missing_ok=True)
    else:
        shutil.rmtree(path)


def _reconcile_tree(source: Path, target: Path, *, preserve_coordination: bool = False) -> None:
    """Restore owned fixture contents in place, retaining directory and lifetime-gate inodes."""
    if source.is_symlink() or not source.is_dir() or target.is_symlink() or not target.is_dir():
        raise RuntimeError("fixture root is not a concrete directory; reseed required")

    source_entries = {entry.name: entry for entry in source.iterdir()}
    target_entries = {entry.name: entry for entry in target.iterdir()}
    for name, old in target_entries.items():
        if preserve_coordination and name == ".lifetimes" and target.name == "installs":
            continue
        new = source_entries.get(name)
        if new is None or (old.is_dir() and not old.is_symlink()) != (new.is_dir() and not new.is_symlink()):
            _remove_entry(old)

    for name, new in source_entries.items():
        if preserve_coordination and name == ".lifetimes" and target.name == "installs":
            continue
        old = target / name
        if new.is_symlink():
            if old.exists() or old.is_symlink():
                _remove_entry(old)
            old.symlink_to(os.readlink(new), target_is_directory=new.is_dir())
        elif new.is_dir():
            if old.is_symlink() or (old.exists() and not old.is_dir()):
                _remove_entry(old)
            if not old.exists():
                old.mkdir()
            _reconcile_tree(new, old, preserve_coordination=preserve_coordination)
            shutil.copystat(new, old, follow_symlinks=False)
        elif new.is_file():
            # Write a fresh sibling and atomically replace, so destination hardlinks are never
            # modified through to an outside fixture path.
            fd, temporary_name = tempfile.mkstemp(prefix=f".{old.name}.restore-", dir=old.parent)
            os.close(fd)
            temporary = Path(temporary_name)
            try:
                shutil.copyfile(new, temporary, follow_symlinks=False)
                shutil.copystat(new, temporary, follow_symlinks=False)
                os.replace(temporary, old)
            finally:
                temporary.unlink(missing_ok=True)
        else:
            raise RuntimeError(f"unsupported fixture entry {new}; reseed required")


def publish(root: Path, message: str, rel: str, content_file: str) -> None:
    p = _paths(root)
    scratch = root / "publish"
    scratch.mkdir(exist_ok=True)
    text = Path(content_file).read_text(encoding="utf-8")
    print(I.publish_commit(p["origin"], scratch, message, {rel: text}))


def main(argv: list[str]) -> None:
    cmd, root = argv[0], Path(argv[1]).resolve()
    if cmd == "install":
        install(root)
    elif cmd == "restore":
        restore(root)
    elif cmd == "publish":
        publish(root, argv[2], argv[3], argv[4])
    else:
        raise SystemExit(f"unknown command {cmd!r}")


if __name__ == "__main__":
    main(sys.argv[1:])
