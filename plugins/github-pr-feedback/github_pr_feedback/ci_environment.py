"""Bind CI imports to the verified worktree, independent of worker launch state."""
import os
from pathlib import Path


def ci_environment(worktree: Path, additions=None, *, include_worktree_roots=True):
    root = worktree.resolve()
    env = dict(os.environ)
    env.update(additions or {})
    if not include_worktree_roots:
        # The interpreter-version probe must not import repository startup
        # hooks before the worktree has passed its identity and cleanliness
        # checks. Remove inherited and caller-provided import paths entirely.
        env.pop("PYTHONPATH", None)
        return env
    roots = []
    scripts = (root / "scripts").resolve()
    if scripts.is_dir() and scripts.is_relative_to(root):
        roots.append(scripts)
    roots.append(root)  # root after scripts preserves sibling-helper precedence
    # Replace inherited paths from the controller or another worktree. This
    # explicitly admits only the checked repository and its CLI helper folder;
    # PYTHONSAFEPATH remains in force for every other implicit import location.
    env["PYTHONPATH"] = os.pathsep.join(str(path) for path in roots)
    return env
