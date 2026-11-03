"""Ports of ``gopkg.in/yaml.v3`` scalar resolution (``resolve.go``) used by the YAML decoder.

Only the behavior that decides the JSON value of a scalar is ported: the resolver table, Go ``strconv``
integer/float parsing as yaml.v3 calls it, and yaml.v3's timestamp formats. Results are returned as Python
values whose JSON encoding matches what Go's ``json.Marshal`` writes for the decoded Go value.
"""

from __future__ import annotations

import base64
import binascii
import math
import re
from dataclasses import dataclass
from typing import Any

NULL_TAG = "!!null"
BOOL_TAG = "!!bool"
STR_TAG = "!!str"
INT_TAG = "!!int"
FLOAT_TAG = "!!float"
TIMESTAMP_TAG = "!!timestamp"
SEQ_TAG = "!!seq"
MAP_TAG = "!!map"
BINARY_TAG = "!!binary"
MERGE_TAG = "!!merge"

_LONG_PREFIX = "tag:yaml.org,2002:"


class ResolveError(ValueError):
    """yaml.v3 ``failf("cannot decode ...")``."""


@dataclass(frozen=True)
class GoFloat:
    """A float64 that ``json.Marshal`` refuses (NaN or ±Inf)."""

    value: float


def short_tag(tag: str) -> str:
    if tag.startswith(_LONG_PREFIX):
        return "!!" + tag[len(_LONG_PREFIX) :]
    return tag


# yaml.v3 resolves a plain ``<<`` to !!merge (verified against Go by the decode differential).
_HINT: dict[str, str] = {"+": "S", "-": "S", ".": ".", "<": "M"}
_HINT.update(dict.fromkeys("0123456789", "D"))
_HINT.update(dict.fromkeys("yYnNtTfFoO~", "M"))

_RESOLVE_MAP: dict[str, tuple[str, Any]] = {}
for _value, _tag, _words in (
    (True, BOOL_TAG, ("true", "True", "TRUE")),
    (False, BOOL_TAG, ("false", "False", "FALSE")),
    (None, NULL_TAG, ("", "~", "null", "Null", "NULL")),
    (GoFloat(math.nan), FLOAT_TAG, (".nan", ".NaN", ".NAN")),
    (GoFloat(math.inf), FLOAT_TAG, (".inf", ".Inf", ".INF", "+.inf", "+.Inf", "+.INF")),
    (GoFloat(-math.inf), FLOAT_TAG, ("-.inf", "-.Inf", "-.INF")),
    ("<<", MERGE_TAG, ("<<",)),
):
    for _word in _words:
        _RESOLVE_MAP[_word] = (_tag, _value)

_RESOLVABLE = {"", STR_TAG, BOOL_TAG, INT_TAG, FLOAT_TAG, NULL_TAG, TIMESTAMP_TAG}

_YAML_STYLE_FLOAT = re.compile(r"[-+]?(\.[0-9]+|[0-9]+(\.[0-9]*)?)([eE][-+]?[0-9]+)?", re.ASCII)
_GO_INT_BASE0 = re.compile(r"([+-]?)(0[xX][0-9a-fA-F]+|0[bB][01]+|0[oO][0-7]+|0[0-7]*|[1-9][0-9]*)", re.ASCII)
_DOT_FLOAT = re.compile(r"\.[0-9]+([eE][+-]?[0-9]+)?", re.ASCII)

_INT64_MIN, _INT64_MAX, _UINT64_MAX = -(1 << 63), (1 << 63) - 1, (1 << 64) - 1


def _parse_int_base0(text: str) -> int | None:
    """``strconv.ParseInt(text, 0, 64)``, falling back to ``ParseUint`` (no sign) as yaml.v3 does."""
    m = _GO_INT_BASE0.fullmatch(text)
    if m is None:
        return None
    sign, body = m.group(1), m.group(2)
    lower = body.lower()
    if lower.startswith("0x"):
        value = int(body[2:], 16)
    elif lower.startswith("0b"):
        value = int(body[2:], 2)
    elif lower.startswith("0o"):
        value = int(body[2:], 8)
    elif body.startswith("0") and len(body) > 1:
        value = int(body[1:], 8)
    else:
        value = int(body, 10)
    if sign == "-":
        value = -value
    if _INT64_MIN <= value <= _INT64_MAX:
        return value
    if sign == "" and value <= _UINT64_MAX:
        return value
    return None


def _parse_float(text: str) -> float | None:
    """``strconv.ParseFloat(text, 64)`` for the decimal forms yaml.v3 hands it; ``None`` on error or overflow."""
    try:
        value = float(text)
    except ValueError:
        return None
    if math.isinf(value):
        return None
    return value


def _float_value(value: float) -> Any:
    """The JSON value Go writes for a float64: integral values below 1e21 have no fraction or exponent."""
    if math.isnan(value) or math.isinf(value):
        return GoFloat(value)
    if value.is_integer() and abs(value) < 1e21:
        return int(value)
    return value


# --- timestamps -------------------------------------------------------------------------------------------------

_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _num12(s: str, i: int) -> tuple[int, int] | None:
    """Go ``getnum(value, false)``: one or two digits."""
    if i >= len(s) or not ("0" <= s[i] <= "9"):
        return None
    if i + 1 < len(s) and "0" <= s[i + 1] <= "9":
        return int(s[i : i + 2]), i + 2
    return int(s[i]), i + 1


def _leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def parse_timestamp(s: str) -> str | None:
    """yaml.v3 ``parseTimestamp``; returns the RFC 3339 text Go's ``time.Time.MarshalJSON`` writes, or ``None``."""
    i = 0
    while i < len(s) and "0" <= s[i] <= "9":
        i += 1
    if i != 4 or i == len(s) or s[i] != "-":
        return None
    for layout in ("T", "t", " ", "date"):
        out = _parse_layout(s, layout)
        if out is not None:
            return out
    return None


def _parse_layout(s: str, layout: str) -> str | None:
    year = int(s[0:4])
    pos = 5
    got = _num12(s, pos)
    if got is None:
        return None
    month, pos = got
    if pos >= len(s) or s[pos] != "-":
        return None
    got = _num12(s, pos + 1)
    if got is None:
        return None
    day, pos = got
    hour = minute = second = 0
    frac = ""
    offset: int | None = 0
    if layout == "date":
        if pos != len(s):
            return None
    else:
        if pos >= len(s) or s[pos] != layout:
            return None
        parts: list[int] = []
        pos += 1
        for n in range(3):
            got = _num12(s, pos)
            if got is None:
                return None
            value, pos = got
            parts.append(value)
            if n < 2:
                if pos >= len(s) or s[pos] != ":":
                    return None
                pos += 1
        hour, minute, second = parts
        if pos + 1 < len(s) and s[pos] in ".," and "0" <= s[pos + 1] <= "9":
            j = pos + 1
            while j < len(s) and "0" <= s[j] <= "9":
                j += 1
            frac = s[pos + 1 : j][:9]
            pos = j
        if layout == " ":
            if pos != len(s):
                return None
        else:
            if pos < len(s) and s[pos] == "Z":
                pos += 1
            elif pos + 6 <= len(s) and s[pos] in "+-" and s[pos + 3] == ":":
                hh, mm = s[pos + 1 : pos + 3], s[pos + 4 : pos + 6]
                if not (hh.isascii() and hh.isdigit() and mm.isascii() and mm.isdigit()):
                    return None
                if int(hh) > 24 or int(mm) > 60:
                    return None
                offset = (int(hh) * 60 + int(mm)) * (-1 if s[pos] == "-" else 1)
                pos += 6
            else:
                return None
            if pos != len(s):
                return None
    if not (1 <= month <= 12) or hour >= 24 or minute >= 60 or second >= 60:
        return None
    days = 29 if month == 2 and _leap(year) else _DAYS[month - 1]
    if not (1 <= day <= days):
        return None
    text = f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}"
    frac = frac.rstrip("0")
    if frac:
        text += "." + frac
    if not offset:
        return text + "Z"
    sign = "-" if offset < 0 else "+"
    return f"{text}{sign}{abs(offset) // 60:02d}:{abs(offset) % 60:02d}"


# --- resolve ------------------------------------------------------------------------------------------------------


def resolve(tag: str, value: str) -> tuple[str, Any]:
    """yaml.v3 ``resolve(tag, in)``. Raises :class:`ResolveError` where yaml.v3 fails."""
    tag = short_tag(tag)
    if tag not in _RESOLVABLE:
        return tag, value
    rtag, out = _resolve(tag, value)
    if tag in ("", rtag, STR_TAG, BINARY_TAG):
        return rtag, out
    if tag == FLOAT_TAG and rtag == INT_TAG and type(out) is int and _INT64_MIN <= out <= _INT64_MAX:
        return FLOAT_TAG, _float_value(float(out))
    raise ResolveError(f"cannot decode {rtag} `{value}` as a {tag}")


def _resolve(tag: str, value: str) -> tuple[str, Any]:
    hint = _HINT.get(value[0], "") if value else "N"
    if hint and tag not in (STR_TAG, BINARY_TAG):
        if value in _RESOLVE_MAP:
            return _RESOLVE_MAP[value]
        if hint == ".":
            if _DOT_FLOAT.fullmatch(value):
                parsed = _parse_float(value)
                if parsed is not None:
                    return FLOAT_TAG, _float_value(parsed)
        elif hint in ("D", "S"):
            if tag in ("", TIMESTAMP_TAG):
                stamp = parse_timestamp(value)
                if stamp is not None:
                    return TIMESTAMP_TAG, stamp
            plain = value.replace("_", "")
            as_int = _parse_int_base0(plain)
            if as_int is not None:
                return INT_TAG, as_int
            if _YAML_STYLE_FLOAT.fullmatch(plain):
                parsed = _parse_float(plain)
                if parsed is not None:
                    return FLOAT_TAG, _float_value(parsed)
    return STR_TAG, value


def decode_binary(value: str) -> str:
    """``base64.StdEncoding.DecodeString`` (CR/LF ignored); invalid UTF-8 becomes U+FFFD as in ``json.Marshal``."""
    cleaned = value.replace("\r", "").replace("\n", "")
    try:
        data = base64.b64decode(cleaned, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ResolveError("!!binary value contains invalid base64 data") from exc
    return data.decode("utf-8", errors="replace")
