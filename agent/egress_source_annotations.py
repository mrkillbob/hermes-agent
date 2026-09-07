
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
    defaults = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.arguments):
            args = [*node.posonlyargs, *node.args]
            pairs = [*zip(args[-len(node.defaults):], node.defaults)] if node.defaults else []
            pairs += [(arg, value) for arg, value in zip(node.kwonlyargs, node.kw_defaults)
                      if value is not None]
            defaults.update({id(arg): value for arg, value in pairs})
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
            edits.append((start, end))
            empty = defaults.get(id(node)) if isinstance(node, ast.arg) else node.value
            if isinstance(empty, ast.Constant) and empty.value is None:
                if isinstance(node, ast.arg):
                    target_start = offsets[node.lineno - 1] + node.col_offset
                    target_end = target_start + len(node.arg.encode("utf-8"))
                else:
                    target_start = offsets[node.target.lineno - 1] + node.target.col_offset
                    target_end = target_start + len(node.target.id.encode("utf-8"))
                edits.extend(((target_start, target_end),
                              (offsets[empty.lineno - 1] + empty.col_offset,
                               offsets[empty.lineno - 1] + empty.col_offset + 4)))
    pieces = []
    cursor = 0
    for start, end in sorted(edits):
        pieces.extend((raw[cursor:start], b" "))
        cursor = end
    pieces.append(raw[cursor:])
    return b"".join(pieces).decode("utf-8")
