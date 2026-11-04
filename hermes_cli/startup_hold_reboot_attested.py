"""Consume a committed schema-3 reboot checkpoint after its boot, by exact operator-attested bytes.

The strict proof binds each writer reference by its full file version, including ctime. ctime is
an OS-maintained timestamp that any metadata touch moves, so the proof can be lost to it with no
writer changed. With the checkpoint's exact sha256 supplied, the same facts are bound by inode,
size, mtime, link count and content hash instead, and the recovery source recorded in the proof
must still exist at its recorded path with its recorded hash. Everything else stays strict: host,
native volume, root identity, the full ledger version, the later native boot, live-writer refusal
and closure of every selected row. The attested path never writes, deletes or settles a record.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat

from hermes_constants import get_default_hermes_root
from hermes_cli.startup_hold_writer_checkpoint import WriterSnapshot


def _stable(version):
    """(ino, size, mtime_ns, nlink) of a (dev, ino, size, mtime_ns, ctime_ns, nlink) version."""
    return [version[1], version[2], version[3], version[5]]


class _AttestedSnapshot(WriterSnapshot):
    """Within-run consistency without ctime, so a metadata touch is not mistaken for a change."""

    def check(self):
        from hermes_cli.startup_hold_reconcile import _version
        from hermes_cli.startup_hold_writer_checkpoint import _refuse, _secure
        if self.version is None:
            if self.directory.exists() or self.directory.is_symlink():
                _refuse()
            return {'directory': None, 'files': []}
        info = self.directory.lstat()
        _secure(info, directory=True)
        if (_stable(_version(info)) != _stable(self.version)
                or sorted(p.name for p in self.directory.iterdir()) != sorted(self.files)):
            _refuse()
        rows = []
        for name, (fd, version, raw) in sorted(self.files.items()):
            named = (self.directory / name).lstat()
            _secure(named)
            os.lseek(fd, 0, os.SEEK_SET)
            current = os.read(fd, 1025)
            if (_stable(_version(named)) != _stable(version)
                    or _stable(_version(os.fstat(fd))) != _stable(version) or current != raw):
                _refuse()
            rows.append({'name': name, 'version': list(version), 'sha256': hashlib.sha256(raw).hexdigest()})
        return {'directory': list(self.version), 'files': rows}


@contextmanager
def writers():
    from hermes_cli.startup_hold_writer_checkpoint import capture_writers
    with capture_writers(_AttestedSnapshot) as snapshot:
        yield snapshot.covered(), snapshot


def _writers_match(recorded, snapshot):
    """Recorded writer references versus held ones: same set, same stable identity, same bytes."""
    from hermes_cli.startup_hold_reboot import _refuse
    rows = {row['name']: row for row in recorded['files']}
    if set(rows) != set(snapshot.files):
        _refuse()
    if (recorded['directory'] is None) != (snapshot.version is None) or (
            snapshot.version is not None and _stable(recorded['directory']) != _stable(snapshot.version)):
        _refuse()
    for name, (_, version, raw) in snapshot.files.items():
        row = rows[name]
        if _stable(row['version']) != _stable(version) or row['sha256'] != hashlib.sha256(raw).hexdigest():
            _refuse()


def _make_proof():
    from hermes_cli import startup_hold_reboot as reboot

    class _AttestedProof(reboot._Proof):
        def check(self):
            from hermes_cli.startup_hold_reconcile import _version
            from hermes_cli.startup_hold_writers import refuse_live_writers
            reboot._paused()
            refuse_live_writers()
            path = get_default_hermes_root() / reboot._FILENAME
            if (_stable(_version(os.fstat(self.fd))) != self.version
                    or _stable(_version(path.lstat())) != self.version):
                reboot._refuse()
            try:
                recorded = self.record['binding']
                retained = recorded['source']
                recorded_writers = recorded['writers']
                if reboot._source_binding(Path(retained['path'])) != retained:
                    reboot._refuse()
                _writers_match(recorded_writers, self.writers)
            except (KeyError, TypeError, ValueError, OSError):
                reboot._refuse()
            binding, entries = reboot._binding(schema=3, writers=self.writers)
            self.rows = reboot.validate(
                self.record, dict(binding, source=retained, writers=recorded_writers),
                entries, reboot._native_identity())

    return _AttestedProof


@contextmanager
def exclusion(*, writers, attested_sha256):
    from hermes_cli import startup_hold_reboot as reboot
    from hermes_cli.startup_hold_reconcile import _version
    from pm.lifetime_handles import open_hold
    if not reboot._digest(attested_sha256):
        reboot._refuse()
    path = get_default_hermes_root() / reboot._FILENAME
    try:
        named = path.lstat()
    except FileNotFoundError:
        reboot._refuse()
    if (os.name == 'nt' or not stat.S_ISREG(named.st_mode) or named.st_nlink != 1
            or named.st_uid != reboot._uid() or stat.S_IMODE(named.st_mode) != 0o600):
        reboot._refuse()
    fd = open_hold(path)
    if fd is None:
        reboot._refuse()
    try:
        if _stable(_version(os.fstat(fd))) != _stable(_version(named)) or named.st_size > reboot._LIMIT:
            reboot._refuse()
        raw = b''
        while len(raw) <= reboot._LIMIT:
            chunk = os.read(fd, reboot._LIMIT + 1 - len(raw))
            if not chunk:
                break
            raw += chunk
        if len(raw) != named.st_size or hashlib.sha256(raw).hexdigest() != attested_sha256:
            reboot._refuse()
        try:
            record = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeError):
            reboot._refuse()
        if not isinstance(record, dict) or record.get('schema') != 3:
            reboot._refuse()
        proof = _make_proof()(record, fd, _stable(_version(named)), writers)
        proof.check()
        yield proof
        proof.check()
    finally:
        os.close(fd)
