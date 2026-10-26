"""Native local-kernel identity for prospective reboot proofs; never wall-clock time."""
import ctypes
import sys
import uuid


class _Timespec(ctypes.Structure):
    _fields_ = [('seconds', ctypes.c_long), ('nanoseconds', ctypes.c_long)]


def _uuid(value):
    parsed = uuid.UUID(value)
    if parsed.int == 0:
        raise ValueError('empty UUID')
    return str(parsed)


def local_boot_identity():
    """Darwin only: native syscall host UUID plus read-only kernel boot-session UUID.

    XNU exposes kern.bootsessionuuid as CTLFLAG_RD, distinct from the APFS boot
    volume UUID. Unsupported/unknown facts are errors, never alternate evidence.
    """
    if sys.platform != 'darwin':
        raise OSError('native reboot proof is unavailable')
    api = ctypes.CDLL(None, use_errno=True)
    api.sysctlbyname.argtypes = [ctypes.c_char_p, ctypes.c_void_p,
                                ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
    api.sysctlbyname.restype = ctypes.c_int
    boot = ctypes.create_string_buffer(64)
    size = ctypes.c_size_t(len(boot))
    if api.sysctlbyname(b'kern.bootsessionuuid', boot, ctypes.byref(size), None, 0):
        raise OSError(ctypes.get_errno(), 'kernel boot identity unavailable')
    if size.value != 37 or boot.raw[36] != 0:
        raise OSError('malformed kernel boot identity')
    host = (ctypes.c_ubyte * 16)()
    api.__gethostuuid.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Timespec)]
    api.__gethostuuid.restype = ctypes.c_int
    if api.__gethostuuid(host, ctypes.byref(_Timespec(0, 0))):
        raise OSError(ctypes.get_errno(), 'native host identity unavailable')
    return dict(host=_uuid(str(uuid.UUID(bytes=bytes(host)))), boot=_uuid(boot.value.decode('ascii')))
