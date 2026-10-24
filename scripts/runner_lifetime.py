"""Spawn a protected file worker and capture its group before the leader can exit."""
import os
from pathlib import Path
import sys

from pm.lifetime_launch import popen


def spawn_pytest(command, **options):
    # The managed entry consumes private lifetime arguments before pytest sees them.
    command = [command[0], str(Path(__file__).with_name('managed_pytest.py')), *command[3:]]
    process = popen(command, managed=True, **options)
    pgid = None
    if sys.platform != 'win32':
        try:
            pgid = os.getpgid(process.pid)
        except (ProcessLookupError, PermissionError):
            pgid = None
    return process, pgid
