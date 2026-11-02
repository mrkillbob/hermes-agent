"""Pre-cleanup observations for a command's private temp directory and process tree.

Npm events describe the last observed work, not proof of what is currently stuck.
Never retain raw logs, paths, package names, URLs, argv or environment values.
"""
import contextlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import threading

import psutil

_LIMIT = 16
_TAIL_BYTES = 65536
_CODES = {'ETIMEDOUT', 'ECONNRESET', 'ECONNREFUSED', 'ENOTFOUND', 'EAI_AGAIN',
          'ENOSPC', 'ENOMEM', 'EACCES', 'EPERM', 'EBUSY', 'ENOTEMPTY', 'ENOENT'}
_EVENTS = (
    ('fetch_manifest', re.compile(r'^\d+ silly fetch manifest ')),
    ('http_fetch', re.compile(r'^\d+ http fetch ')),
    ('tarball', re.compile(r'^\d+ silly tarball ')),
    ('reify', re.compile(r'^\d+ (?:silly|timing) reify[: ]')),
    ('lifecycle_end', re.compile(r'^\d+ info run \S+ (?:preinstall|install|postinstall|prepare) .* \{ code:')),
    ('lifecycle_start', re.compile(r'^\d+ info run \S+ (?:preinstall|install|postinstall|prepare) ')),
)


def _ordinary(path, *, directory=False):
    value = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(value.st_mode) or getattr(value, 'st_file_attributes', 0) & 0x400:
        raise OSError('not an ordinary diagnostic input')
    return value


def _log_tail(path):
    before = _ordinary(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino) or not stat.S_ISREG(opened.st_mode):
            raise OSError('diagnostic input changed')
        offset = max(0, opened.st_size - _TAIL_BYTES)
        os.lseek(fd, offset, os.SEEK_SET)
        data = os.read(fd, _TAIL_BYTES)
        # A prefix cut through a record must never be parsed as a complete record.
        if offset:
            data = data.partition(b'\n')[2]
        return data.decode('utf-8', errors='replace'), bool(offset)
    finally:
        os.close(fd)


def _log_summary(path):
    try:
        text, truncated = _log_tail(path)
    except OSError:
        return {'state': 'unreadable'}
    events, codes, last = {}, set(), 'unknown'
    for line in text.splitlines():
        for event, pattern in _EVENTS:
            if pattern.match(line):
                events[event] = events.get(event, 0) + 1
                last = event
                break
        code = re.fullmatch(r'\d+ (?:error|verbose) code (\S+)', line)
        if code:
            codes.add(code[1] if code[1] in _CODES else 'unknown')
    return {'state': 'readable', 'truncated': truncated, 'last_event': last,
            'events': events, 'error_codes': sorted(codes)}


def _entries(path):
    _ordinary(path, directory=True)
    with os.scandir(path) as entries:
        for index, entry in enumerate(entries):
            if index == 128:
                raise OSError('diagnostic directory enumeration limit')
            yield Path(entry.path)


def _npm_summary(temporary):
    result = {'state': 'readable', 'truncated': False, 'logs': []}
    try:
        for directory in _entries(temporary):
            if not directory.name.startswith('hermes-npm-logs-'):
                continue
            try:
                for path in _entries(directory):
                    if path.suffix != '.log':
                        continue
                    if len(result['logs']) == _LIMIT:
                        result['truncated'] = True
                        return result
                    result['logs'].append(_log_summary(path))
            except OSError:
                result['truncated'] = True
    except OSError:
        result.update(state='unavailable', truncated=True)
    return result


def bind_process(pid):
    try:
        # psutil retains the birth identity for is_running(), including PID reuse.
        return psutil.Process(pid)
    except psutil.Error:
        return None


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and 0 <= value < 2**63 else None


def _process_summary(owner):
    if owner is None:
        return {'state': 'unavailable'}
    result = {'state': 'sampled', 'sampled': 0, 'unreadable': 0,
              'truncated': False, 'cpu_seconds': 0, 'rss_bytes': 0}
    pending = [owner]
    try:
        if not owner.is_running():
            return {'state': 'owner_departed'}
        while pending and result['sampled'] + result['unreadable'] < 64:
            process = pending.pop()
            try:
                if not process.is_running():
                    result['unreadable'] += 1
                    continue
                cpu, memory = process.cpu_times(), process.memory_info()
                result['cpu_seconds'] += _number(cpu.user + cpu.system) or 0
                result['rss_bytes'] += _number(memory.rss) or 0
                result['sampled'] += 1
                children = process.children()
                available = max(0, 64 - len(pending) - result['sampled'] - result['unreadable'])
                result['truncated'] |= len(children) > available
                pending.extend(children[:available])
            except psutil.Error:
                result['unreadable'] += 1
        result['truncated'] |= bool(pending)
    except psutil.Error:
        result['state'] = 'unavailable'
    return result


def _collect(temporary, owner):
    host = {}
    with contextlib.suppress(OSError, psutil.Error):
        host['available_memory_bytes'] = _number(psutil.virtual_memory().available)
        host['free_disk_bytes'] = _number(shutil.disk_usage(temporary).free)
    return {'npm': _npm_summary(temporary), 'processes': _process_summary(owner), 'host': host}


def capture_timeout(path, temporary, owner, *, budget=2.0):
    # Native process/disk observations can block despite size limits. A daemon
    # may finish reading late, but only this caller can publish the receipt.
    observed = []

    def collect():
        try:
            observed.append(_collect(temporary, owner))
        except Exception:  # health: allow BLE001 -- Diagnostic errors never replace timeout cleanup.
            observed.append({'collection_failed': True})

    try:
        worker = threading.Thread(target=collect, name='timeout-evidence', daemon=True)
        worker.start()
        worker.join(timeout=budget)
        detail = {'schema': 1, 'observation': 'before_timeout_cleanup'}
        detail.update(observed[0] if observed else {'collection_timed_out': True})
        path.write_text(json.dumps(detail, indent=2) + '\n', encoding='utf-8')
    except Exception:  # health: allow BLE001 -- Evidence publication must never replace existing cleanup.
        pass
