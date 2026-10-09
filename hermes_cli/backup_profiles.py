"""Profile-directory probes shared by snapshot and strict health inventories."""
from pathlib import Path
import stat


def profile_directory(path: Path, *, strict: bool) -> bool:
    """A health inventory must distinguish unavailable metadata from an absent home."""
    if not strict:
        return path.is_dir()
    try:
        return stat.S_ISDIR(path.stat().st_mode)
    except FileNotFoundError:
        return False
