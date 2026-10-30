"""Safe managed destinations for secret-bearing profile exports.

Runtime dependencies late-bind through the profiles facade, retaining its patch seams.
"""

from pathlib import Path
from typing import Optional

def _inside_git_checkout(path: Path) -> bool:
    """True when *path* lies inside a Git checkout. Walks the path's OWN resolved ancestry
    (not cwd) so the check holds when HERMES_HOME sits in a checkout but the process runs
    elsewhere (cron, service manager). Resolution failure reports True (fail closed)."""
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):  # RuntimeError: symlink loops on Python <= 3.12
        return True
    return any((candidate / ".git").exists() for candidate in (resolved, *resolved.parents))


def _profile_export_directory() -> Path:
    """Choose an export directory that cannot become source-tree input."""
    from hermes_cli.profiles import Path, _get_default_hermes_home, _inside_git_checkout, os
    import tempfile
    export_dir = _get_default_hermes_home() / "profile-exports"
    if not _inside_git_checkout(export_dir):
        return export_dir

    # A custom deployment may point HERMES_HOME at its source checkout: use a sibling store,
    # falling back to the OS temp dir only when the user's home itself is a checkout (dotfiles
    # repo). Per-uid temp name: a fixed /tmp/hermes-profile-exports is a predictable shared
    # path another local user could pre-create (or symlink) first.
    uid_suffix = f"-{os.getuid()}" if hasattr(os, "getuid") else ""
    candidates = (
        Path.home() / ".hermes-profile-exports", Path(tempfile.gettempdir()) / f"hermes-profile-exports{uid_suffix}"
    )
    for candidate in candidates:
        if not _inside_git_checkout(candidate):
            return candidate
    # Fail closed: writing a secret-bearing archive into a source tree is the incident this
    # helper prevents; a stderr warning would not stop a scripted export.
    raise ValueError(
        # See #92457.
        "No safe automatic export destination: every candidate directory is "
        "inside a Git checkout. Provide an explicit output path outside the "
        "checkout (CLI: -o /path/outside/repo/profile.tar.gz)."
    )


def get_profile_export_path(name: str, *, timestamp: Optional[str] = None) -> Path:
    """Managed destination for an export with no explicit output — outside the cwd and every
    profile, since a ``<name>.tar.gz`` default in a source checkout got committed by accident."""
    from hermes_cli.profiles import _canon_valid, _profile_export_directory, os, time
    canon = _canon_valid(name)
    export_dir = _profile_export_directory()
    export_dir.mkdir(parents=True, exist_ok=True)
    # exist_ok=True silently accepts a directory (or symlink) another local user pre-created
    # at a predictable path; refuse to write a secret-bearing archive anywhere we don't own.
    if export_dir.is_symlink():
        raise ValueError(
            f"Export directory {export_dir} is a symlink; refusing to write "
            "a profile archive through it. Provide an explicit output path."
        )
    if hasattr(os, "getuid") and export_dir.stat().st_uid != os.getuid():
        raise ValueError(
            f"Export directory {export_dir} is owned by another user; "
            "refusing to write a profile archive there. Provide an explicit output path."
        )
    stamp = timestamp or time.strftime("%Y%m%d-%H%M%S")
    return export_dir / f"{canon}-{stamp}.tar.gz"
