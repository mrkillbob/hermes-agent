"""Canonical process capacity shared by Feishu's per-home plugin imports.

Import this module by its absolute canonical name: a relative import gives each
profile namespace a different pool. It owns no profile state or credentials.
"""

import threading


class _ReadCapacity:
    def __init__(self):
        self.lock = threading.Lock()
        self.active = 0


capacity = _ReadCapacity()
