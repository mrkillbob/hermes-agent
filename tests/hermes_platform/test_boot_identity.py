"""The native read is local, stable within one boot, and not a reboot operation."""
import uuid

import pytest


@pytest.mark.platforms('macos')
def test_native_local_boot_identity_is_stable():
    from hermes_platform.host.boot_identity import local_boot_identity
    first = local_boot_identity()
    assert first == local_boot_identity()
    assert set(first) == {'host', 'boot'}
    assert all(str(uuid.UUID(value)) == value and uuid.UUID(value).int for value in first.values())
