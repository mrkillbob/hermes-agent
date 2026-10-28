"""Prospective generation evidence; reservation alone never authorizes a launch."""
from contextvars import ContextVar
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import threading

from hermes_cli.local_runtime import reconciliation_io as io
from hermes_cli.local_runtime.runtime_lock import require_owned

_BOOT_TICKET = ContextVar('local_runtime_generation_ticket', default=None)


def _in_owned_boot_context():
    from hermes_cli.local_runtime.runtime_lock import _HELD

    held = _HELD.get()
    return held is not None and held[:2] == (os.getpid(), threading.get_ident())


def _source_identity():
    root = Path(__file__).parent
    names = ('generation_evidence.py', 'binaries.py', 'supervisor.py', 'processes.py',
             'hardware.py', 'devices.py')
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}
    hashes['utils.py'] = hashlib.sha256((root.parent.parent / 'utils.py').read_bytes()).hexdigest()
    return dict(base=io.source_identity(), prospective=hashlib.sha256(io.encoded(hashes)).hexdigest())


def _root(root, volume_identity):
    root = Path(root)
    if root.absolute() != root.resolve(strict=True):
        raise RuntimeError('runtime_path_alias')
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != io._uid() or info.st_mode & 0o022:
        raise RuntimeError('runtime_directory_unqualified')
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        current = os.fstat(fd)
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise RuntimeError('runtime_directory_changed')
        volume = io.uuid_text(volume_identity(fd))
        named = root.lstat()
        if (named.st_dev, named.st_ino) != (current.st_dev, current.st_ino):
            raise RuntimeError('runtime_directory_changed')
    finally:
        os.close(fd)
    return dict(root=str(root), root_inode=info.st_ino, uid=info.st_uid, volume=volume)


def _predecessor(root):
    path = root / 'server.json'
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    return hashlib.sha256(io.private_read(path)[0]).hexdigest()


def reserve_for_engine_probe():
    """The owned boot path calls this before backend selection can launch a probe."""
    from hermes_cli.local_runtime import bootstrap, reconciliation
    from hermes_cli.local_runtime.binaries import runtimes_root

    if (bootstrap._BOOT_OWNER is None or bootstrap._SUPERVISOR is not None or
            not _in_owned_boot_context()):
        return
    root = runtimes_root()
    require_owned(root / 'server.json')
    ticket = reserve(root, _predecessor(root), reconciliation._native_identity,
                     reconciliation._volume_identity)
    authorize(root, ticket, reconciliation._native_identity, reconciliation._volume_identity)
    _BOOT_TICKET.set((str(root), ticket))


def current_boot_ticket(root):
    """Only the lock-owning boot thread may hand its reservation to the supervisor."""
    from hermes_cli.local_runtime import bootstrap

    if bootstrap._BOOT_OWNER is None or not _in_owned_boot_context():
        return None
    require_owned(Path(root) / 'server.json')
    saved = _BOOT_TICKET.get()
    if saved is None or saved[0] != str(root):
        raise RuntimeError('generation_reservation_missing')
    return saved[1]


def _exclusive_atomic_state(root, ticket, raw, volume_identity, binding):
    """Darwin no-clobber rename: the canonical name is always absent or complete."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    stage = '.generation-stage-' + ticket
    try:
        opened = os.fstat(fd)
        if (binding != _root(root, volume_identity) or
                opened.st_ino != binding['root_inode'] or opened.st_uid != binding['uid'] or
                io.uuid_text(volume_identity(fd)) != binding['volume']):
            raise RuntimeError('runtime_directory_changed')
        staged = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=fd)
        with os.fdopen(staged, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        require_owned(Path(root) / 'server.json')
        if (binding != _root(root, volume_identity) or
                os.fstat(fd).st_ino != binding['root_inode'] or
                io.uuid_text(volume_identity(fd)) != binding['volume']):
            raise RuntimeError('runtime_directory_changed')
        api = ctypes.CDLL(None, use_errno=True)
        rename = api.renameatx_np
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        # AT_FDCWD is avoided: both names are relative to the verified directory fd.
        if rename(fd, stage.encode(), fd, b'server.json', 0x00000004):  # RENAME_EXCL
            raise OSError(ctypes.get_errno(), 'exclusive router-state publication refused')
        os.fsync(fd)
    finally:
        os.close(fd)


def publish_intent(root, ticket, native_identity, volume_identity):
    """Bind and durably install the pre-spawn state before any executable probe."""
    from hermes_cli.local_runtime.reconciliation import replace_retired_record

    root = Path(root)
    path = root / 'server.json'
    intent = dict(tree_custody='unverified', spawn_ticket=secrets.token_hex(16),
                  owner_pid=os.getpid())
    raw = json.dumps(intent, indent=2, ensure_ascii=False).encode()
    bind_publication(root, ticket, raw, native_identity, volume_identity)
    directory = root / ('.generation-' + ticket)
    _, reservation = io.read_json(directory / 'reservation.json')
    try:
        path.lstat()
    except FileNotFoundError:
        _exclusive_atomic_state(root, ticket, raw, volume_identity, reservation['binding'])
    else:
        replace_retired_record(path, intent)
    verify_current_publication(root, ticket, raw, 1, native_identity, volume_identity)
    return intent


def verify_current_publication(root, ticket, raw, step, native_identity, volume_identity):
    """Recheck the complete custody chain after the canonical state is replaced."""
    root = Path(root)
    path = root / 'server.json'
    directory = root / ('.generation-' + ticket)
    require_owned(path)
    if step not in (1, 2) or set(root.glob('.generation-*')) != {
            directory, root / '.generation-current.json'}:
        raise RuntimeError('generation_link_ambiguous')
    io.private_directory(directory)
    reservation_raw, current = io.read_json(directory / 'reservation.json')
    expected = dict(schema=1, ticket=ticket,
                    reservation_sha256=hashlib.sha256(reservation_raw).hexdigest())
    link_raw, link = io.read_json(root / '.generation-current.json')
    authorization_raw, authorization = io.read_json(directory / 'authorized.json')
    chain = _publications(directory, ticket, expected['reservation_sha256'],
                          current.get('predecessor_sha256'))
    if (current.get('schema') != 1 or current.get('ticket') != ticket or
            link != expected or authorization != expected or
            current['binding'] != _root(root, volume_identity) or
            current['identity'] != io.boot_identity(native_identity()) or
            current['source'] != _source_identity() or len(chain) != step or
            chain[-1][2]['intended_sha256'] != hashlib.sha256(raw).hexdigest() or
            io.private_read(path)[0] != raw):
        raise RuntimeError('generation_intent_changed')
    require_owned(path)
    if (io.private_read(root / '.generation-current.json')[0] != link_raw or
            io.private_read(directory / 'authorized.json')[0] != authorization_raw or
            io.private_read(directory / 'reservation.json')[0] != reservation_raw or
            _publications(directory, ticket, expected['reservation_sha256'],
                          current.get('predecessor_sha256')) != chain or
            io.private_read(path)[0] != raw):
        raise RuntimeError('generation_intent_changed')
    require_owned(path)


def _publications(directory, ticket, reservation_sha256, predecessor_sha256):
    """Read the complete, bounded publication chain; a gap or extra receipt refuses."""
    names = ('publication.json', 'publication-2.json')
    present = {path.name for path in directory.glob('publication*')}
    if present - set(names) or ('publication-2.json' in present and 'publication.json' not in present):
        raise RuntimeError('generation_evidence_changed')
    chain = []
    previous_state = predecessor_sha256
    previous_receipt = None
    for name in names:
        if name not in present:
            break
        try:
            raw, value = io.read_json(directory / name)
        except ValueError as exc:
            raise RuntimeError('generation_evidence_changed') from exc
        expected = dict(schema=1, ticket=ticket, reservation_sha256=reservation_sha256,
                        predecessor_sha256=previous_state, previous_publication_sha256=previous_receipt,
                        intended_sha256=value.get('intended_sha256'))
        if (value != expected or not isinstance(value['intended_sha256'], str) or
                re.fullmatch('[0-9a-f]{64}', value['intended_sha256']) is None or
                value['intended_sha256'] == previous_state):
            raise RuntimeError('generation_evidence_changed')
        chain.append((name, raw, value))
        previous_state = value['intended_sha256']
        previous_receipt = hashlib.sha256(raw).hexdigest()
    return chain


def reserve(root, predecessor_sha256, native_identity, volume_identity):
    root = Path(root)
    require_owned(root / 'server.json')
    binding = _root(root, volume_identity)
    if predecessor_sha256 != _predecessor(root):
        raise RuntimeError('predecessor_changed')
    if list(root.glob('.generation-*')):
        raise RuntimeError('prior_generation_requires_inspection')
    identity = io.boot_identity(native_identity())
    ticket = secrets.token_hex(16)
    directory = root / ('.generation-' + ticket)
    directory.mkdir(mode=0o700)
    io.sync_directory(root)
    reservation = dict(schema=1, ticket=ticket, predecessor_sha256=predecessor_sha256,
                       binding=binding, identity=identity, source=_source_identity())
    data = io.encoded(reservation)
    io.exclusive_write(directory / 'reservation.json', data)
    if (binding != _root(root, volume_identity) or identity != io.boot_identity(native_identity()) or
            predecessor_sha256 != _predecessor(root)):
        raise RuntimeError('generation_facts_changed')
    link = dict(schema=1, ticket=ticket, reservation_sha256=hashlib.sha256(data).hexdigest())
    io.exclusive_write(root / '.generation-current.json', io.encoded(link))
    return ticket


def authorize(root, ticket, native_identity, volume_identity):
    root = Path(root)
    require_owned(root / 'server.json')
    if not isinstance(ticket, str) or not re.fullmatch('[0-9a-f]{32}', ticket):
        raise RuntimeError('generation_ticket_invalid')
    directory = root / ('.generation-' + ticket)

    def validate():
        require_owned(root / 'server.json')
        if set(root.glob('.generation-*')) != {directory, root / '.generation-current.json'}:
            raise RuntimeError('generation_link_ambiguous')
        io.private_directory(directory)
        _, link = io.read_json(root / '.generation-current.json')
        raw, reservation = io.read_json(directory / 'reservation.json')
        if (link != dict(schema=1, ticket=ticket, reservation_sha256=hashlib.sha256(raw).hexdigest()) or
                reservation.get('schema') != 1 or reservation.get('ticket') != ticket or
                reservation.get('binding') != _root(root, volume_identity) or
                reservation.get('identity') != io.boot_identity(native_identity()) or
                reservation.get('source') != _source_identity() or
                reservation.get('predecessor_sha256') != _predecessor(root)):
            raise RuntimeError('generation_evidence_changed')
        return raw

    raw = validate()
    authorization = io.encoded(dict(schema=1, ticket=ticket, reservation_sha256=hashlib.sha256(raw).hexdigest()))
    path = directory / 'authorized.json'
    io.exclusive_write(path, authorization)
    if io.private_read(path)[0] != authorization or validate() != raw:
        raise RuntimeError('generation_authorization_changed')


def bind_publication(root, ticket, intended, native_identity, volume_identity):
    """Bind exact bytes before publication; does not write the router state."""
    root = Path(root)
    require_owned(root / 'server.json')
    if not isinstance(ticket, str) or not re.fullmatch('[0-9a-f]{32}', ticket):
        raise RuntimeError('generation_ticket_invalid')
    if not isinstance(intended, bytes) or len(intended) > io.LIMIT:
        raise RuntimeError('publication_invalid')
    directory = root / ('.generation-' + ticket)

    def validate():
        require_owned(root / 'server.json')
        if set(root.glob('.generation-*')) != {directory, root / '.generation-current.json'}:
            raise RuntimeError('generation_link_ambiguous')
        io.private_directory(directory)
        link_raw, link = io.read_json(root / '.generation-current.json')
        raw, reservation = io.read_json(directory / 'reservation.json')
        authorization_raw, authorization = io.read_json(directory / 'authorized.json')
        expected = dict(schema=1, ticket=ticket, reservation_sha256=hashlib.sha256(raw).hexdigest())
        chain = _publications(directory, ticket, hashlib.sha256(raw).hexdigest(),
                              reservation.get('predecessor_sha256'))
        previous_state = chain[-1][2]['intended_sha256'] if chain else reservation.get('predecessor_sha256')
        if (link != expected or authorization != expected or reservation.get('schema') != 1 or
                reservation.get('ticket') != ticket or reservation.get('binding') != _root(root, volume_identity) or
                reservation.get('identity') != io.boot_identity(native_identity()) or
                reservation.get('source') != _source_identity() or
                previous_state != _predecessor(root)):
            raise RuntimeError('generation_evidence_changed')
        return raw, reservation, link_raw, authorization_raw, chain

    raw, reservation, link_raw, authorization_raw, chain = validate()
    if len(chain) >= 2:
        raise RuntimeError('generation_publication_limit')
    previous_state = chain[-1][2]['intended_sha256'] if chain else reservation['predecessor_sha256']
    intended_sha256 = hashlib.sha256(intended).hexdigest()
    if intended_sha256 == previous_state:
        raise RuntimeError('publication_state_unchanged')
    publication = io.encoded(dict(schema=1, ticket=ticket, reservation_sha256=hashlib.sha256(raw).hexdigest(),
                                  predecessor_sha256=previous_state,
                                  previous_publication_sha256=hashlib.sha256(chain[-1][1]).hexdigest() if chain else None,
                                  intended_sha256=intended_sha256))
    path = directory / ('publication-2.json' if chain else 'publication.json')
    io.exclusive_write(path, publication)
    # The intended state is not yet installed. Recheck the exact predecessor and custody facts.
    require_owned(root / 'server.json')
    later_chain = _publications(directory, ticket, hashlib.sha256(raw).hexdigest(),
                                reservation['predecessor_sha256'])
    if (io.private_read(path)[0] != publication or
            later_chain != chain + [(path.name, publication, io.read_json(path)[1])] or
            io.private_read(root / '.generation-current.json')[0] != link_raw or
            io.private_read(directory / 'reservation.json')[0] != raw or
            io.private_read(directory / 'authorized.json')[0] != authorization_raw or
            reservation['binding'] != _root(root, volume_identity) or
            reservation['identity'] != io.boot_identity(native_identity()) or
            reservation['source'] != _source_identity() or
            _predecessor(root) != (chain[-1][2]['intended_sha256'] if chain else
                                   reservation['predecessor_sha256'])):
        raise RuntimeError('generation_publication_changed')
    require_owned(root / 'server.json')
    if _publications(directory, ticket, hashlib.sha256(raw).hexdigest(),
                     reservation['predecessor_sha256']) != later_chain:
        raise RuntimeError('generation_publication_changed')


def verify_interrupted(root, ticket, native_identity, volume_identity):
    """Read-only proof for unchanged predecessors, never authorize retirement."""
    root = Path(root)
    require_owned(root / 'server.json')
    if not isinstance(ticket, str) or not re.fullmatch('[0-9a-f]{32}', ticket):
        raise RuntimeError('generation_ticket_invalid')
    directory = root / ('.generation-' + ticket)
    if set(root.glob('.generation-*')) != {directory, root / '.generation-current.json'}:
        raise RuntimeError('generation_link_ambiguous')
    io.private_directory(directory)
    link_raw, link = io.read_json(root / '.generation-current.json')
    raw, reservation = io.read_json(directory / 'reservation.json')
    auth_raw, auth = io.read_json(directory / 'authorized.json')
    expected = dict(schema=1, ticket=ticket, reservation_sha256=hashlib.sha256(raw).hexdigest())
    predecessor = _predecessor(root)
    chain = _publications(directory, ticket, hashlib.sha256(raw).hexdigest(),
                          reservation.get('predecessor_sha256'))
    allowed_states = {reservation.get('predecessor_sha256')}
    allowed_states.update(value['intended_sha256'] for _, _, value in chain)
    if (link != expected or auth != expected or reservation.get('schema') != 1 or
            reservation.get('ticket') != ticket or reservation.get('binding') != _root(root, volume_identity) or
            reservation.get('source') != _source_identity() or
            predecessor not in allowed_states):
        raise RuntimeError('generation_evidence_changed')
    saved = io.boot_identity(reservation.get('identity'))
    current = io.boot_identity(native_identity())
    if current['host'] != saved['host']:
        raise RuntimeError('host_changed')
    if current['boot'] == saved['boot']:
        raise RuntimeError('kernel_boundary_required')
    require_owned(root / 'server.json')
    if (io.private_read(root / '.generation-current.json')[0] != link_raw or
            io.private_read(directory / 'reservation.json')[0] != raw or
            io.private_read(directory / 'authorized.json')[0] != auth_raw or
            reservation.get('binding') != _root(root, volume_identity) or
            predecessor != _predecessor(root) or
            current != io.boot_identity(native_identity())):
        raise RuntimeError('generation_evidence_changed')
    if _publications(directory, ticket, hashlib.sha256(raw).hexdigest(),
                     reservation.get('predecessor_sha256')) != chain:
        raise RuntimeError('generation_evidence_changed')
    require_owned(root / 'server.json')
    return dict(outcome='verified_only', ticket=ticket, retired=False)
