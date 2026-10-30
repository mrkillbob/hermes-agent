"""Descriptor-bound native volume identity; unsupported facts never become proof."""
import os
import struct
import uuid

import pytest


@pytest.mark.parametrize('raw', [b'', b'\0' * 24,
    struct.pack('=II', 23, 0x1000) + b'1' * 16,
    struct.pack('=II', 24, 0) + b'1' * 16,
    struct.pack('=II', 24, 0x1000) + b'\0' * 16,
    struct.pack('=II', 24, 0x1000) + b'1' * 17])
def test_invalid_or_nonlocal_volume_is_not_authority(raw):
    from hermes_platform.host.volume_identity import _decode_volume
    with pytest.raises(OSError):
        _decode_volume(raw)


def test_uuid_is_from_exact_complete_native_volume_result():
    from hermes_platform.host.volume_identity import _decode_volume
    identity = uuid.UUID('55555555-5555-4555-8555-555555555555')
    assert _decode_volume(struct.pack('=II', 24, 0x1000) + identity.bytes) == str(identity)


@pytest.mark.platforms('macos')
def test_native_directory_and_file_volume_are_descriptor_bound(tmp_path):
    from hermes_platform.host.volume_identity import local_volume_identity
    target = tmp_path / 'fixture'
    target.write_bytes(b'fixture')
    root_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    file_fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        first = local_volume_identity(root_fd)
        assert first == local_volume_identity(file_fd) == local_volume_identity(root_fd)
        assert uuid.UUID(first).int and str(uuid.UUID(first)) == first
        with pytest.raises(OSError):
            local_volume_identity(-1)
    finally:
        os.close(file_fd)
        os.close(root_fd)


@pytest.mark.platforms('macos')
def test_persistent_file_and_directory_ids_retain_inode_on_native_volume(tmp_path):
    from hermes_platform.host.volume_identity import local_directory_identity, local_file_identity
    target = tmp_path / 'fixture'
    target.write_bytes(b'fixture')
    fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        file_id = local_file_identity(fd)
        directory_id = local_directory_identity(tmp_path)
        assert file_id == [directory_id[0], target.stat().st_ino]
        assert directory_id[1] == tmp_path.stat().st_ino
        assert uuid.UUID(file_id[0]).int
    finally:
        os.close(fd)


@pytest.mark.platforms('not macos')
def test_unsupported_host_refuses_directory_identity_without_native_flags(tmp_path):
    from hermes_platform.host.volume_identity import local_directory_identity
    with pytest.raises(OSError, match='native directory identity unavailable'):
        local_directory_identity(tmp_path)


@pytest.mark.platforms('macos')
def test_directory_replacement_during_capture_is_refused(tmp_path, monkeypatch):
    from hermes_platform.host import volume_identity
    target = tmp_path / 'directory'
    target.mkdir()
    original = volume_identity.local_volume_identity
    def replaced(fd):
        value = original(fd)
        target.rename(tmp_path / 'previous')
        target.mkdir()
        return value
    monkeypatch.setattr(volume_identity, 'local_volume_identity', replaced)
    with pytest.raises(OSError, match='identity changed'):
        volume_identity.local_directory_identity(target)
