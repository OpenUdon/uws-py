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
from typing import Any

from ..diagnostics import DecodeError

JsonValue = Any
"""A decoded JSON value: ``None``, ``bool``, ``int``, ``float``, ``str``, ``list`` or ``dict[str, ...]``."""

_SURROGATE_ESCAPE = re.compile(r"\\u[dD][89a-fA-F]")
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


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
    if type(value) is str:
        return _LONE_SURROGATE.sub("�", value)
    if type(value) is list:
        return [_fix_surrogates(item) for item in value]
    if type(value) is dict:
        return {_LONE_SURROGATE.sub("�", k): _fix_surrogates(v) for k, v in value.items()}
    return value


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
    except RecursionError as exc:
        raise DecodeError("JSON nesting is too deep") from exc
    if _SURROGATE_ESCAPE.search(text):
        value = _fix_surrogates(value)
    return value
