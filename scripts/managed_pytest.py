"""Known child entry point: retain inherited environment protection before pytest."""
from pathlib import Path
import runpy
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pm.lifetime_launch import adopt_arguments

if __name__ == '__main__':
    adopt_arguments(sys.argv)
    sys.argv[0] = 'pytest'
    runpy.run_module('pytest', run_name='__main__')
