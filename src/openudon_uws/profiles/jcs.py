"""RFC 8785 canonical JSON (gowebpki/jcs), Go ``json.Marshal`` sizing and the mock-fixture request digest
(``mockruntime.CanonicalizeRequest`` / ``RequestDigest``)."""

from __future__ import annotations

import hashlib
import math
from decimal import Decimal
from typing import Any

from ..diagnostics import DecodeError

MAX_REQUEST_BYTES = 1 << 20
_STRICT_JSON_DEPTH = 100


class CanonicalizationError(ValueError):
    pass


def _go_float(value: Any) -> float:
    """A JSON number as Go's ``json.Unmarshal`` into ``any`` holds it (float64)."""
    try:
        f = float(value)
    except OverflowError as exc:
        raise CanonicalizationError("number is out of range for float64") from exc
    if math.isnan(f) or math.isinf(f):
        raise CanonicalizationError("number is not finite")
    return f


def es6_number(value: float) -> str:
    """ES6 Number-to-string, as gowebpki/jcs ``NumberToJSON`` (``-0`` is written ``0``)."""
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    digits_text = repr(abs(value))
    dec = Decimal(digits_text)
    _, digits_t, exponent = dec.as_tuple()
    digits = "".join(map(str, digits_t)).rstrip("0") or "0"
    # n: position of the decimal point relative to the start of `digits`.
    assert isinstance(exponent, int)
    n = len(digits_t) + exponent
    k = len(digits)
    if k <= n <= 21:
        out = digits + "0" * (n - k)
    elif 0 < n <= 21:
        out = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        out = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        mantissa = digits[0] + ("." + digits[1:] if k > 1 else "")
        out = f"{mantissa}e{'+' if e > 0 else '-'}{abs(e)}"
    return sign + out


def _jcs_string(s: str) -> str:
    out = ['"']
    for c in s:
        o = ord(c)
        if c == '"':
            out.append('\\"')
        elif c == "\\":
            out.append("\\\\")
        elif c == "\b":
            out.append("\\b")
        elif c == "\f":
            out.append("\\f")
        elif c == "\n":
            out.append("\\n")
        elif c == "\r":
            out.append("\\r")
        elif c == "\t":
            out.append("\\t")
        elif o < 0x20:
            out.append(f"\\u{o:04x}")
        elif 0xD800 <= o <= 0xDFFF:
            out.append("�")  # json.Marshal replaced it before JCS saw the text
        else:
            out.append(c)
    out.append('"')
    return "".join(out)


def _utf16_key(s: str) -> bytes:
    return s.encode("utf-16-be", errors="surrogatepass")


def canonical_json(value: Any) -> str:
    """RFC 8785 (JCS) text for a decoded JSON value. Numbers are float64, members sorted by UTF-16 code units."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if type(value) in (int, float):
        return es6_number(_go_float(value))
    if type(value) is str:
        return _jcs_string(value)
    if type(value) is list:
        return "[" + ",".join(canonical_json(v) for v in value) + "]"
    if type(value) is dict:
        keys = sorted(value, key=_utf16_key)
        return "{" + ",".join(f"{_jcs_string(k)}:{canonical_json(value[k])}" for k in keys) + "}"
    raise CanonicalizationError(f"unsupported JSON value of type {type(value).__name__}")


def _go_string_length(s: str) -> int:
    """Bytes ``json.Marshal`` writes for a string (HTML escaping, \\u2028/\\u2029, U+FFFD for bad bytes)."""
    n = 2
    for c in s:
        o = ord(c)
        if c in "<>&":
            n += 6
        elif c in '"\\\b\f\n\r\t':
            n += 2
        elif o < 0x20 or o in (0x2028, 0x2029):
            n += 6
        elif 0xD800 <= o <= 0xDFFF:
            n += 6  # �
        else:
            n += len(c.encode("utf-8"))
    return n


def go_marshal_length(value: Any) -> int:
    """Length of Go's ``json.Marshal`` output for a value decoded as Go ``any`` (numbers as float64)."""
    if value is None:
        return 4
    if value is True:
        return 4
    if value is False:
        return 5
    if type(value) in (int, float):
        f = _go_float(value)
        return 2 if (f == 0 and math.copysign(1.0, f) < 0) else len(es6_number(f))
    if type(value) is str:
        return _go_string_length(value)
    if type(value) is list:
        return 2 + sum(go_marshal_length(v) for v in value) + max(len(value) - 1, 0)
    if type(value) is dict:
        return (
            2 + sum(_go_string_length(k) + 1 + go_marshal_length(v) for k, v in value.items()) + max(len(value) - 1, 0)
        )
    raise CanonicalizationError(f"unsupported JSON value of type {type(value).__name__}")


def _depth(value: Any) -> int:
    deepest = 0
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        item, level = stack.pop()
        if type(item) in (list, dict):
            level += 1
            deepest = max(deepest, level)
            stack.extend((v, level) for v in (item.values() if type(item) is dict else item))
    return deepest


def canonicalize_request(request: Any) -> str:
    """Canonical request text for a fixture key (``mockruntime.CanonicalizeRequest``).

    ``request`` is the resolved request as decoded JSON; ``None`` stands for no request (``{}``).
    """
    if request is None:
        request = {}
    if go_marshal_length(request) > MAX_REQUEST_BYTES:
        raise CanonicalizationError(f"resolved request exceeds {MAX_REQUEST_BYTES} bytes")
    if _depth(request) > _STRICT_JSON_DEPTH:
        raise CanonicalizationError("validate resolved request JSON: JSON nesting exceeds depth 100")
    if type(request) is not dict:
        raise CanonicalizationError("resolved request must be a JSON object")
    return canonical_json(request)


def request_digest(request: Any) -> str:
    """``sha256:`` digest of the canonical request (``mockruntime.RequestDigest``)."""
    return "sha256:" + hashlib.sha256(canonicalize_request(request).encode("utf-8")).hexdigest()


__all__ = [
    "CanonicalizationError",
    "DecodeError",
    "canonical_json",
    "canonicalize_request",
    "es6_number",
    "go_marshal_length",
    "request_digest",
]
