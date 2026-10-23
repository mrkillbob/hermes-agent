"""Bounded-memory parsing of archived Kanban board identity markers."""

from __future__ import annotations

import json
from typing import TextIO


_CAPTURE_TOO_LONG = object()
_INVALID_SLUG_VALUE = object()
_MAX_JSON_DEPTH = 400
_MAX_SLUG_NUMBER_CHARS = 4300


class _JSONStream:
    """Small-buffer JSON syntax reader that captures only a short identity field."""

    def __init__(self, stream: TextIO, chunk_size: int) -> None:
        self._stream = stream
        self._chunk_size = chunk_size
        self._buffer = ""
        self._position = 0

    def peek(self) -> str | None:
        if self._position >= len(self._buffer):
            self._buffer = self._stream.read(self._chunk_size)
            self._position = 0
        return self._buffer[self._position] if self._buffer else None

    def take(self) -> str:
        char = self.peek()
        if char is None:
            raise ValueError("unexpected end of JSON")
        self._position += 1
        return char

    def whitespace(self) -> None:
        while self.peek() in (" ", "\t", "\r", "\n"):
            self.take()

    def expect(self, expected: str) -> None:
        if self.take() != expected:
            raise ValueError("invalid JSON token")

    def is_digit(self) -> bool:
        char = self.peek()
        return char is not None and char in "0123456789"

    def _string_char(self) -> str | None:
        """Return one decoded JSON string character, or None at its closing quote."""
        char = self.take()
        if char == '"':
            return None
        if ord(char) < 0x20:
            raise ValueError("control character in JSON string")
        if char != "\\":
            return char
        escape = self.take()
        simple = {
            '"': '"',
            "\\": "\\",
            "/": "/",
            "b": "\b",
            "f": "\f",
            "n": "\n",
            "r": "\r",
            "t": "\t",
        }
        if escape in simple:
            return simple[escape]
        if escape != "u":
            raise ValueError("invalid JSON escape")
        digits: list[str] = []
        for _ in range(4):
            digit = self.take()
            if digit not in "0123456789abcdefABCDEF":
                raise ValueError("invalid JSON unicode escape")
            digits.append(digit)
        return chr(int("".join(digits), 16))

    def string(self, capture_limit: int | None = None, *, slug_value: bool = False):
        self.expect('"')
        if slug_value:
            # Apply str.strip() semantics as characters arrive. Arbitrarily long
            # leading/trailing Unicode whitespace need not be retained; internal
            # whitespace and overlong normalized values are invalid slugs.
            captured: list[str] = []
            started = False
            pending_space = False
            invalid = False
            while True:
                char = self._string_char()
                if char is None:
                    break
                if char.isspace():
                    if started:
                        pending_space = True
                    continue
                if pending_space:
                    invalid = True
                started = True
                if not invalid:
                    captured.append(char)
                    if len(captured) > 64:
                        invalid = True
                        captured.clear()
            if invalid:
                return _INVALID_SLUG_VALUE
            return "".join(captured).lower()

        if capture_limit is None:
            while self._string_char() is not None:
                pass
            return None
        captured: list[str] = []
        too_long = False
        while True:
            char = self._string_char()
            if char is None:
                break
            if not too_long:
                captured.append(char)
                if len(captured) > capture_limit:
                    too_long = True
                    captured.clear()
        return _CAPTURE_TOO_LONG if too_long else "".join(captured)

    def value(self, depth: int, *, capture_slug: bool = False):
        if depth > _MAX_JSON_DEPTH:
            raise ValueError("JSON nesting is too deep")
        self.whitespace()
        char = self.peek()
        if char == '"':
            return self.string(slug_value=True) if capture_slug else self.string()
        if char == "{":
            self.object(depth + 1, capture_slug=False)
            return _INVALID_SLUG_VALUE if capture_slug else None
        if char == "[":
            self.array(depth + 1)
            return _INVALID_SLUG_VALUE if capture_slug else None
        if char in ("t", "f", "n"):
            literal = {"t": "true", "f": "false", "n": "null"}[char]
            for expected in literal:
                self.expect(expected)
            if not capture_slug:
                return None
            return {"t": True, "f": False, "n": None}[char]
        if char in ("N", "I"):
            literal = "NaN" if char == "N" else "Infinity"
            for expected in literal:
                self.expect(expected)
            if not capture_slug:
                return None
            return float("nan") if char == "N" else float("inf")
        if char == "-":
            self.take()
            if self.peek() == "I":
                for expected in "Infinity":
                    self.expect(expected)
                return -float("inf") if capture_slug else None
            return self.number(capture_slug=capture_slug, negative=True)
        return self.number(capture_slug=capture_slug)

    def object(self, depth: int, *, capture_slug: bool):
        self.expect("{")
        self.whitespace()
        slug = None
        if self.peek() == "}":
            self.take()
            return None
        while True:
            self.whitespace()
            if self.peek() != '"':
                raise ValueError("JSON object key must be a string")
            key = self.string(24)
            self.whitespace()
            self.expect(":")
            if capture_slug and key == "slug":
                # Match json.loads(dict) last-key-wins behavior. Invalid values
                # are remembered but do not prevent a later duplicate from
                # replacing them.
                slug = self.value(depth, capture_slug=True)
            else:
                self.value(depth)
            self.whitespace()
            separator = self.take()
            if separator == "}":
                return slug
            if separator != ",":
                raise ValueError("invalid JSON object separator")

    def array(self, depth: int) -> None:
        self.expect("[")
        self.whitespace()
        if self.peek() == "]":
            self.take()
            return
        while True:
            self.value(depth)
            self.whitespace()
            separator = self.take()
            if separator == "]":
                return
            if separator != ",":
                raise ValueError("invalid JSON array separator")

    def number(self, *, capture_slug: bool, negative: bool = False):
        captured: list[str] | None = (
            (["-"] if negative else []) if capture_slug else None
        )
        length = 1 if negative else 0

        def record(char: str) -> None:
            nonlocal length, captured
            length += 1
            if captured is not None:
                if length <= _MAX_SLUG_NUMBER_CHARS:
                    captured.append(char)
                else:
                    captured = None

        first = self.take()
        record(first)
        if first == "0":
            if self.is_digit():
                raise ValueError("leading zero in JSON number")
        elif first in "123456789":
            while self.is_digit():
                record(self.take())
        else:
            raise ValueError("invalid JSON value")
        if self.peek() == ".":
            record(self.take())
            if not self.is_digit():
                raise ValueError("invalid JSON fraction")
            while self.is_digit():
                record(self.take())
        if self.peek() in ("e", "E"):
            record(self.take())
            if self.peek() in ("+", "-"):
                record(self.take())
            if not self.is_digit():
                raise ValueError("invalid JSON exponent")
            while self.is_digit():
                record(self.take())
        if not capture_slug:
            return None
        if captured is None:
            return _INVALID_SLUG_VALUE
        return json.loads("".join(captured))


def read_board_slug(stream: TextIO, *, chunk_size: int = 8192) -> object:
    """Validate one JSON object and return its top-level slug value.

    Other string values are consumed without retention. Slug strings are
    stripped/lowercased with Python string semantics as they stream; scalar
    slug values retain the prior ``json.loads`` + ``_require_slug`` behavior.
    This lets archived boards keep large display metadata while keeping parser
    memory bounded by the read chunk, JSON depth, and a bounded slug token.
    Total bytes read and parse time remain linear in marker size because the
    writer does not cap display fields.
    """
    parser = _JSONStream(stream, chunk_size)
    parser.whitespace()
    if parser.peek() != "{":
        raise ValueError("board marker must be a JSON object")
    slug = parser.object(1, capture_slug=True)
    parser.whitespace()
    if parser.peek() is not None:
        raise ValueError("trailing data after board marker")
    if slug is None:
        raise ValueError("board marker has no slug")
    return slug
