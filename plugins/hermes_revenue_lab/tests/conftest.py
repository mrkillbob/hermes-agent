"""Test-collection bootstrap for the in-tree Hermes Revenue Lab plugin.

HRL's own modules (ported from the standalone mrkillbob/HermesRevenueLab
repo) import each other as the top-level ``hermes_revenue_lab.*`` package,
unchanged from the standalone repo's ``src`` layout. The root
``tests/conftest.py`` already puts the hermes-agent repo root on
``sys.path`` (needed for HRL scripts/tests that import ``agent.*`` /
``hermes_cli.*`` directly); this conftest adds this plugin's own ``src/``
so ``hermes_revenue_lab.*`` resolves too, matching what
``plugins/hermes_revenue_lab/__init__.py`` does at normal plugin-load time.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_SRC = _PLUGIN_ROOT / "src"
_REPO_ROOT = _PLUGIN_ROOT.parents[1]

if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Several HRL tests shell out to HRL's own scripts (e.g. check_compliance.py,
# revenue_guard.py) as subprocesses via sys.executable, expecting to import
# `hermes_revenue_lab.*` themselves. subprocess.run() inherits os.environ,
# not this process's sys.path, so PYTHONPATH must be set explicitly here —
# mirrors the standalone repo's `PYTHONPATH=src:. pytest ...` invocation.
_pythonpath_prefix = f"{_SRC}{os.pathsep}{_REPO_ROOT}"
_existing_pythonpath = os.environ.get("PYTHONPATH", "")
os.environ["PYTHONPATH"] = (
    f"{_pythonpath_prefix}{os.pathsep}{_existing_pythonpath}"
    if _existing_pythonpath
    else _pythonpath_prefix
)
