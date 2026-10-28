"""Bounded authenticated ancestors for a separately retired router generation."""
import hashlib
import os
from pathlib import Path
import re

from hermes_cli.local_runtime import reconciliation_io as io
from hermes_cli.local_runtime.runtime_lock import require_owned

MAX_GENERATIONS = 64


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _ticket(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{32}', value) is None:
        raise RuntimeError('generation_ticket_invalid')
    return value


def _retired_ancestor(root, state_digest, reference, parent, child):
    receipt_raw, receipt = io.read_json(root / ('.router-retirement-' + state_digest + '.json'))
    if _digest(receipt_raw) != reference['retirement_sha256']:
        raise RuntimeError('generation_retirement_changed')
    checkpoint_id = _ticket(receipt.get('checkpoint_id'))
    checkpoint_raw, checkpoint = io.read_json(root / ('.router-checkpoint-' + state_digest + '.json'))
    binding = checkpoint.get('binding', {})
    prior = io.boot_identity(parent.get('identity'))
    retired = io.boot_identity(receipt.get('retired_identity'))
    later = io.boot_identity(child.get('identity'))
    if (receipt.get('schema') != 1 or checkpoint.get('schema') != 1
            or checkpoint.get('checkpoint_id') != checkpoint_id
            or receipt.get('checkpoint_sha256') != _digest(checkpoint_raw)
            or receipt.get('source') != io.source_identity()
            or checkpoint.get('source') != receipt['source']
            or receipt.get('original_binding') != binding or binding.get('sha256') != state_digest
            or any(binding.get(k) != parent['binding'][k] for k in parent['binding'])
            or io.boot_identity(checkpoint.get('identity')) != prior
            or io.boot_identity(receipt.get('prior_identity')) != prior
            or retired['host'] != prior['host'] or retired['boot'] == prior['boot']
            or later['host'] != prior['host'] or later['boot'] == prior['boot']):
        raise RuntimeError('generation_retirement_unqualified')
    backup = root / ('.router-recovery-' + checkpoint_id) / 'server.json'
    if receipt.get('recovery_path') != str(backup):
        raise RuntimeError('generation_recovery_changed')
    directory = io.private_directory(backup.parent)
    if (_digest(io.private_read(backup)[0]) != state_digest
            or io.private_directory(backup.parent) != directory):
        raise RuntimeError('generation_recovery_changed')


def _parent(root, directory, child):
    from hermes_cli.local_runtime import generation_evidence as g

    reference = child.get('prior_generation')
    if not isinstance(reference, dict) or set(reference) != {
            'ticket', 'reservation_sha256', 'link_sha256', 'retirement_sha256'}:
        raise RuntimeError('generation_history_unqualified')
    ticket = _ticket(reference['ticket'])
    old_link_raw, old_link = io.read_json(directory / 'predecessor-link.json')
    expected = dict(schema=1, ticket=ticket,
                    reservation_sha256=reference['reservation_sha256'])
    if _digest(old_link_raw) != reference['link_sha256'] or old_link != expected:
        raise RuntimeError('generation_history_link_changed')
    parent_dir = root / ('.generation-' + ticket)
    io.private_directory(parent_dir)
    raw, parent = io.read_json(parent_dir / 'reservation.json')
    _, authorization = io.read_json(parent_dir / 'authorized.json')
    chain = g._publications(parent_dir, ticket, _digest(raw), parent.get('predecessor_sha256'))
    state_digest = child.get('predecessor_sha256')
    if (_digest(raw) != reference['reservation_sha256'] or authorization != expected
            or len(chain) != 2 or chain[-1][2]['intended_sha256'] != state_digest):
        raise RuntimeError('generation_history_incomplete')
    _retired_ancestor(root, state_digest, reference, parent, child)
    return ticket, parent


def assert_layout(root, ticket, volume_identity, prepared=()):
    """Only the current generation and its authenticated ancestor chain may exist."""
    from hermes_cli.local_runtime import generation_evidence as g

    root = Path(root)
    require_owned(root / 'server.json')
    binding, source = g._root(root, volume_identity), g._source_identity()
    expected = {root / '.generation-current.json'}
    io.private_directory(root / ('.generation-' + _ticket(ticket)))
    _, current = io.read_json(root / ('.generation-' + ticket) / 'reservation.json')
    for _ in range(MAX_GENERATIONS):
        directory = root / ('.generation-' + ticket)
        if directory in expected:
            raise RuntimeError('generation_history_cycle')
        io.private_directory(directory)
        expected.add(directory)
        if (current.get('ticket') != ticket or current.get('binding') != binding
                or current.get('source') != source):
            raise RuntimeError('generation_history_changed')
        if current.get('schema') == 1 and 'prior_generation' not in current:
            break
        if current.get('schema') != 2:
            raise RuntimeError('generation_history_unqualified')
        ticket, current = _parent(root, directory, current)
    else:
        raise RuntimeError('generation_history_limit')
    if set(root.glob('.generation-*')) != expected | set(prepared):
        raise RuntimeError('generation_link_ambiguous')
    require_owned(root / 'server.json')
    return len(expected) - 1


def predecessor(root, native_identity, volume_identity, prepared=()):
    """The maintained live-record proof remains mandatory before any advancement."""
    from hermes_cli.local_runtime import generation_evidence as g, reconciliation

    root = Path(root)
    raw, link = io.read_json(root / '.generation-current.json')
    ticket = _ticket(link.get('ticket'))
    if assert_layout(root, ticket, volume_identity, prepared) >= MAX_GENERATIONS:
        raise RuntimeError('generation_history_limit')
    directory = root / ('.generation-' + ticket)
    reservation_raw, saved = io.read_json(directory / 'reservation.json')
    _, authorized = io.read_json(directory / 'authorized.json')
    expected = dict(schema=1, ticket=ticket, reservation_sha256=_digest(reservation_raw))
    chain = g._publications(directory, ticket, _digest(reservation_raw), saved.get('predecessor_sha256'))
    digest = g._predecessor(root)
    now, prior = io.boot_identity(native_identity()), io.boot_identity(saved.get('identity'))
    if (link != expected or authorized != expected or len(chain) != 2
            or chain[-1][2]['intended_sha256'] != digest
            or now['host'] != prior['host'] or now['boot'] == prior['boot']
            or not reconciliation.retirement_matches(root / 'server.json')):
        raise RuntimeError('prior_generation_requires_inspection')
    receipt_raw, _ = io.read_json(root / ('.router-retirement-' + digest + '.json'))
    return raw, dict(ticket=ticket, reservation_sha256=_digest(reservation_raw),
                     link_sha256=_digest(raw), retirement_sha256=_digest(receipt_raw))


def _directory_current(root, fd, binding, volume_identity):
    from hermes_cli.local_runtime import generation_evidence as g

    opened = os.fstat(fd)
    if (g._root(root, volume_identity) != binding or opened.st_ino != binding['root_inode']
            or opened.st_uid != binding['uid']
            or io.uuid_text(volume_identity(fd)) != binding['volume']):
        raise RuntimeError('runtime_directory_changed')
    require_owned(root / 'server.json')


def replace_link(root, ticket, data, previous, native_identity, volume_identity):
    """A torn successor stays fenced; preserve old evidence and the directory fd."""
    from hermes_cli.local_runtime import generation_evidence as g

    root = Path(root)
    require_owned(root / 'server.json')
    binding = g._root(root, volume_identity)
    prepared = (root / ('.generation-' + ticket),)
    if predecessor(root, native_identity, volume_identity, prepared)[0] != previous:
        raise RuntimeError('generation_predecessor_changed')
    stage_name = '.generation-next-' + ticket + '.json'
    stage = root / stage_name
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _directory_current(root, fd, binding, volume_identity)
        staged = os.open(stage_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=fd)
        with os.fdopen(staged, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(fd)
        if (predecessor(root, native_identity, volume_identity, (*prepared, stage))[0] != previous
                or io.private_read(stage)[0] != data):
            raise RuntimeError('generation_predecessor_changed')
        _directory_current(root, fd, binding, volume_identity)
        os.replace(stage_name, '.generation-current.json', src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
        _directory_current(root, fd, binding, volume_identity)
        if io.private_read(root / '.generation-current.json')[0] != data:
            raise RuntimeError('generation_link_changed')
    finally:
        os.close(fd)
