"""Native persistent local filesystem identity of an already-open descriptor."""
import ctypes
import struct
import sys
import uuid


class _AttrList(ctypes.Structure):
    # Darwin <sys/attr.h>: ATTR_BIT_MAP_COUNT and five uint32 attribute groups.
    _fields_ = [('count', ctypes.c_uint16), ('reserved', ctypes.c_uint16),
                ('common', ctypes.c_uint32), ('volume', ctypes.c_uint32),
                ('directory', ctypes.c_uint32), ('file', ctypes.c_uint32),
                ('fork', ctypes.c_uint32)]


def _decode_volume(raw):
    if len(raw) != 24:
        raise OSError('incomplete native volume identity')
    size, flags = struct.unpack_from('=II', raw)
    identity = uuid.UUID(bytes=raw[8:])
    # <sys/mount.h> MNT_LOCAL; unsupported/zero UUIDs are never evidence.
    if size != len(raw) or not flags & 0x00001000 or not identity.int:
        raise OSError('native local volume identity unavailable')
    return str(identity)


def local_volume_identity(fd):
    """Darwin fgetattrlist, bound to fd rather than a mount-path lookup.

    ATTR_VOL_INFO | ATTR_VOL_MOUNTFLAGS | ATTR_VOL_UUID, in native attribute
    order. Older filesystems that refuse volume attributes on this descriptor
    fail closed. No path, environment, device-name or timestamp fallback.
    """
    if sys.platform != 'darwin':
        raise OSError('native volume identity unavailable')
    api = ctypes.CDLL(None, use_errno=True)
    api.fgetattrlist.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                ctypes.c_size_t, ctypes.c_ulong]
    api.fgetattrlist.restype = ctypes.c_int
    attributes = _AttrList(5, 0, 0, 0x80044000, 0, 0, 0)
    result = ctypes.create_string_buffer(24)
    if api.fgetattrlist(fd, ctypes.byref(attributes), result, len(result), 0):
        raise OSError(ctypes.get_errno(), 'native volume identity unavailable')
    return _decode_volume(result.raw)
