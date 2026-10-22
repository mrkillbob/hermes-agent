"""Bounded encoding and calendar syntax classification; this module grants no source authority."""

import base64
import binascii
from datetime import date
import re


_ISO_CALENDAR_WEEK = re.compile(r"[0-9]{4}-W[0-9]{2}\Z")


def is_iso_calendar_week(candidate: str) -> bool:
    """Recognize a complete canonical ISO week, including actual week-53 validity."""
    if _ISO_CALENDAR_WEEK.fullmatch(candidate) is None:
        return False
    try:
        date.fromisocalendar(int(candidate[:4]), int(candidate[6:]), 1)
    except ValueError:
        return False
    return True


def is_canonical_base64_encoding(candidate: str) -> bool:
    """Decode/re-encode an already bounded ASCII candidate without syntax exemptions."""
    unpadded = candidate.rstrip("=")
    if "=" in unpadded or len(unpadded) % 4 == 1:
        return False
    padded = unpadded + "=" * (-len(unpadded) % 4)
    encoded = padded.encode("ascii")
    for altchars in (None, b"-_"):
        try:
            decoded = base64.b64decode(encoded, altchars=altchars, validate=True)
        except (binascii.Error, ValueError):
            continue
        canonical_padded = (
            base64.b64encode(decoded)
            if altchars is None
            else base64.urlsafe_b64encode(decoded)
        ).decode("ascii")
        if candidate in {canonical_padded, canonical_padded.rstrip("=")} and decoded:
            return True
    return False
