"""Shared strict GitHub timestamp decoding for list/detail and feedback reads."""

from datetime import datetime


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp is required")
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("timestamp is invalid") from error
    if timestamp.tzinfo is None:
        raise ValueError("timestamp requires a timezone")
    return timestamp
