"""Recognize Python type syntax for the source-bound secret scan only."""

from __future__ import annotations

import ast


def _builtin_annotation(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id in {"str", "bytes", "bool", "int", "float", "object"}
    if isinstance(node, ast.Constant):
        return node.value is None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _builtin_annotation(node.left) and _builtin_annotation(node.right)
    return False


def mask_builtin_annotations(text: str) -> str:
    """Mask proven annotation syntax and literal None defaults, retaining secret values.

    Incomplete snippets and unknown annotations retain the strict original scan.
    AST offsets are UTF-8 bytes, including when a line contains non-ASCII names.
    """

    if ":" not in text:
        return text
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return text
    raw = text.encode("utf-8")
    offsets = [0]
    for line in raw.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    edits = []
    empty_defaults = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.arguments):
            args = [*node.posonlyargs, *node.args]
            pairs = [*zip(args[-len(node.defaults):], node.defaults)] if node.defaults else []
            pairs += list(zip(node.kwonlyargs, node.kw_defaults))
            empty_defaults.update({id(arg): value for arg, value in pairs
                                   if isinstance(value, ast.Constant) and value.value is None})
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.annotation is not None:
            start = offsets[node.lineno - 1] + node.col_offset + len(node.arg.encode("utf-8"))
            annotation = node.annotation
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            start = offsets[node.target.end_lineno - 1] + node.target.end_col_offset
            annotation = node.annotation
        else:
            continue
        if _builtin_annotation(annotation):
            end = offsets[annotation.end_lineno - 1] + annotation.end_col_offset
            empty = empty_defaults.get(id(node)) if isinstance(node, ast.arg) else node.value
            if isinstance(empty, ast.Constant) and empty.value is None:
                target = node if isinstance(node, ast.arg) else node.target
                start = offsets[target.lineno - 1] + target.col_offset
                end = offsets[empty.end_lineno - 1] + empty.end_col_offset
            edits.append((start, end))
    pieces = []
    cursor = 0
    for start, end in sorted(edits):
        pieces.extend((raw[cursor:start], b" "))
        cursor = end
    pieces.append(raw[cursor:])
    return b"".join(pieces).decode("utf-8")
