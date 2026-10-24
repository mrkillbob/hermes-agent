"""Stdlib entry selected by a bootstrap outside collectible state."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pm.lifetime_launch import main

if __name__ == '__main__':
    raise SystemExit(main())
