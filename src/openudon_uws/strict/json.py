"""Strict JSON decoding with Go ``encoding/json`` verdicts.

Go decodes core documents with ``json.Unmarshal`` (into structs) and the schema layer re-reads the same bytes
with ``json.Decoder.UseNumber``. Both accept exactly one value, let the last duplicate member win, refuse a byte
order mark, replace invalid UTF-8 and unpaired surrogate escapes with U+FFFD, and refuse numbers that do not fit a
float64 wherever they are decoded into Go numbers.
"""

from __future__ import annotations

import json
import math
import re
from json.decoder import scanstring  # type: ignore[attr-defined]
from typing import Any

from ..diagnostics import DecodeError

JsonValue = Any
"""A decoded JSON value: ``None``, ``bool``, ``int``, ``float``, ``str``, ``list`` or ``dict[str, ...]``."""

_SURROGATE_ESCAPE = re.compile(r"\\u[dD][89a-fA-F]")
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")
_REPLACEMENT = "\ufffd"


def decode_utf8(data: bytes) -> str:
    """Decodes bytes as UTF-8, replacing invalid sequences with U+FFFD as Go ``encoding/json`` does."""
    return data.decode("utf-8", errors="replace")


def _reject_constant(name: str) -> Any:
    raise DecodeError(f"invalid JSON literal {name}")


def _parse_float(text: str) -> float:
    value = float(text)
    if math.isinf(value):
        raise DecodeError(f"JSON number {text} is out of range for float64")
    return value


def _parse_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:  # exceeds Python's integer string conversion limit
        raise DecodeError("JSON number is out of range for float64") from exc
    try:
        float(value)
    except OverflowError as exc:
        raise DecodeError(f"JSON number {text[:40]} is out of range for float64") from exc
    return value


def _fix_surrogates(value: JsonValue) -> JsonValue:
    """Replaces unpaired surrogates with U+FFFD everywhere (iterative: values may nest 10000 deep)."""
    if type(value) is str:
        return _LONE_SURROGATE.sub(_REPLACEMENT, value)
    stack: list[Any] = [value]
    while stack:
        item = stack.pop()
        if type(item) is list:
            for i, v in enumerate(item):
                if type(v) is str:
                    item[i] = _LONE_SURROGATE.sub(_REPLACEMENT, v)
                elif type(v) in (list, dict):
                    stack.append(v)
        elif type(item) is dict:
            for k in list(item):
                v = item.pop(k)
                if type(v) is str:
                    v = _LONE_SURROGATE.sub(_REPLACEMENT, v)
                elif type(v) in (list, dict):
                    stack.append(v)
                item[_LONE_SURROGATE.sub(_REPLACEMENT, k)] = v
    return value


# encoding/json's scanner refuses nesting deeper than this.
MAX_DEPTH = 10000
_WS = frozenset(" \t\n\r")
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(\.[0-9]+)?([eE][-+]?[0-9]+)?")


def _depth(value: JsonValue) -> int:
    deepest = 0
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        item, level = stack.pop()
        if type(item) in (list, dict):
            level += 1
            deepest = max(deepest, level)
            stack.extend((v, level) for v in (item.values() if type(item) is dict else item))
    return deepest


def _parse_iterative(text: str, reject_duplicates: bool) -> JsonValue:
    """A non-recursive JSON parser with Go's depth limit.

    Used when the C scanner hits Python's recursion limit: Python 3.11 bounds it by the interpreter recursion depth,
    while Go accepts nesting up to 10000.
    """
    n = len(text)

    def skip(i: int) -> int:
        while i < n and text[i] in _WS:
            i += 1
        return i

    def fail(msg: str, at: int) -> DecodeError:
        return DecodeError(f"invalid JSON: {msg} at offset {at}")

    def read_key(i: int, frame: list[Any]) -> int:
        if i >= n or text[i] != '"':
            raise fail("object member name is not a string", i)
        try:
            key, i = scanstring(text, i + 1, True)
        except ValueError as exc:
            raise fail(str(exc), i) from exc
        i = skip(i)
        if i >= n or text[i] != ":":
            raise fail("expected ':'", i)
        frame[1] = key
        return skip(i + 1)

    stack: list[list[Any]] = []  # frames: [container, pending member name]
    i = skip(0)
    while True:
        if i >= n:
            raise fail("unexpected end of input", i)
        c = text[i]
        value: Any
        opened = False
        if c in "{[":
            if len(stack) >= MAX_DEPTH:
                raise DecodeError(f"invalid JSON: exceeded max depth of {MAX_DEPTH}")
            value = {} if c == "{" else []
            opened = True
            i = skip(i + 1)
        elif c == '"':
            try:
                value, i = scanstring(text, i + 1, True)
            except ValueError as exc:
                raise fail(str(exc), i) from exc
        elif text.startswith("true", i):
            value, i = True, i + 4
        elif text.startswith("false", i):
            value, i = False, i + 5
        elif text.startswith("null", i):
            value, i = None, i + 4
        else:
            m = _NUMBER.match(text, i)
            if m is None:
                raise fail("unexpected character", i)
            literal = m.group(0)
            value = _parse_float(literal) if (m.group(1) or m.group(2)) else _parse_int(literal)
            i = m.end()
        if stack:
            frame = stack[-1]
            if type(frame[0]) is dict:
                if reject_duplicates and frame[1] in frame[0]:
                    raise DecodeError(f"JSON object contains duplicate member {json.dumps(frame[1])}")
                frame[0][frame[1]] = value
            else:
                frame[0].append(value)
        else:
            root = value
        if opened:
            stack.append([value, None])
            closer = "}" if type(value) is dict else "]"
            if i < n and text[i] == closer:
                i += 1
                stack.pop()
            else:
                if type(value) is dict:
                    i = read_key(i, stack[-1])
                continue
        while True:
            i = skip(i)
            if not stack:
                if i != n:
                    raise fail("trailing data", i)
                return root
            frame = stack[-1]
            closer = "}" if type(frame[0]) is dict else "]"
            if i < n and text[i] == ",":
                i = skip(i + 1)
                if type(frame[0]) is dict:
                    i = read_key(i, frame)
                break
            if i < n and text[i] == closer:
                i += 1
                stack.pop()
                continue
            raise fail(f"expected ',' or {closer!r}", i)


def parse_strict_json(text: str, *, reject_duplicates: bool = False) -> JsonValue:
    """Parses exactly one JSON value.

    Malformed JSON, trailing values, ``NaN``/``Infinity`` literals, a leading byte order mark and numbers outside
    float64 range are refused. Duplicate member names keep the last value unless ``reject_duplicates`` is set.
    """

    def pairs(items: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
        out: dict[str, JsonValue] = {}
        for key, value in items:
            if reject_duplicates and key in out:
                raise DecodeError(f"JSON object contains duplicate member {json.dumps(key)}")
            out[key] = value
        return out

    if text.startswith("﻿"):
        raise DecodeError("JSON document starts with a byte order mark")
    if text.strip(" \t\r\n") == "":
        raise DecodeError("JSON document is empty")
    try:
        value = json.loads(
            text,
            parse_constant=_reject_constant,
            parse_float=_parse_float,
            parse_int=_parse_int,
            object_pairs_hook=pairs,
        )
    except json.JSONDecodeError as exc:
        raise DecodeError(f"invalid JSON: {exc}") from exc
    except RecursionError:
        value = _parse_iterative(text, reject_duplicates)
    else:
        if text.count("[") + text.count("{") > MAX_DEPTH and _depth(value) > MAX_DEPTH:
            raise DecodeError(f"invalid JSON: exceeded max depth of {MAX_DEPTH}")
    if _SURROGATE_ESCAPE.search(text):
        value = _fix_surrogates(value)
    return value
