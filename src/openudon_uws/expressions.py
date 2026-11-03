"""Port of the upstream reference expression parser (``expressions/parse.go``): grammar admission for UWS runtime
expressions (§5.6) with version and context gates."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

from .gostrings import go_trim_space

__all__ = [
    "MAX_EXPRESSION_BYTES",
    "ExpressionError",
    "ExpressionField",
    "JsonNumber",
    "ParsedExpression",
    "is_valid_expression",
    "parse_expression",
    "parse_pointer",
]

MAX_EXPRESSION_BYTES = 64 << 10

ExpressionField = Literal["value", "predicate", "wait", "batchSize"]
"""Grammar positions. ``predicate`` covers ``when`` and ``await`` waits; ``wait`` is a non-await delay."""

ErrorKind = Literal["syntax", "version", "context", "limit"]

_FIELDS = ("value", "predicate", "wait", "batchSize")
_OPERATORS = ("==", "!=", "<=", ">=", "<", ">")
_SEGMENT = re.compile(r"[A-Za-z0-9_-]+\Z", re.ASCII)
_HEX = frozenset(b"0123456789abcdefABCDEF")
_POINTER_PLAIN = frozenset(b"/abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")


class JsonNumber(str):
    """A JSON number literal kept as text, as Go's ``json.Number`` (no float conversion, no overflow)."""

    __slots__ = ()


@dataclass(frozen=True)
class ParsedExpression:
    source: str
    """Left-hand source expression; empty for a bare numeric literal."""
    operator: str = ""
    operand_source: str = ""
    """Right-hand source expression, when the operand is a ``$`` source."""
    literal: bool | str | JsonNumber | None = None
    """Right-hand JSON scalar literal, or the numeric literal itself."""
    is_number: bool = False


class ExpressionError(ValueError):
    def __init__(self, kind: ErrorKind) -> None:
        super().__init__(kind)
        self.kind: ErrorKind = kind


def parse_expression(text: str, *, version: str = "", field: str = "value", in_loop: bool = False) -> ParsedExpression:
    """Parses and admits one runtime expression (upstream ``expressions.Parse``).

    Raises :class:`ExpressionError` with ``kind`` ``syntax``, ``version``, ``context`` or ``limit``.
    """
    if len(text.encode("utf-8", errors="surrogatepass")) > MAX_EXPRESSION_BYTES:
        raise ExpressionError("limit")
    if text == "" or go_trim_space(text) != text or field not in _FIELDS:
        raise ExpressionError("syntax")
    version = version or "1.12.0"
    parsed = _version(version)
    if parsed is None:
        raise ExpressionError("version")
    minor = parsed
    if not text.startswith("$"):
        scalar = _parse_scalar(text)
        if not isinstance(scalar, JsonNumber):
            raise ExpressionError("syntax")
        if field not in ("wait", "batchSize"):
            raise ExpressionError("context")
        if minor < 11:
            raise ExpressionError("version")
        return ParsedExpression("", literal=scalar, is_number=True)
    left, sep, rest = text.partition(" ")
    _source(left, minor, in_loop)
    if sep and field in ("wait", "batchSize"):
        raise ExpressionError("context")
    if not sep:
        return ParsedExpression(left)
    for operator in _OPERATORS:
        if not rest.startswith(operator + " "):
            continue
        operand = rest[len(operator) + 1 :]
        if operand == "":
            raise ExpressionError("syntax")
        if operand.startswith("$"):
            _source(operand, minor, in_loop)
            return ParsedExpression(left, operator, operand_source=operand)
        return ParsedExpression(left, operator, literal=_parse_scalar(operand))
    raise ExpressionError("syntax")


def is_valid_expression(text: str, *, version: str = "", field: str = "value", in_loop: bool = False) -> bool:
    try:
        parse_expression(text, version=version, field=field, in_loop=in_loop)
    except ExpressionError:
        return False
    return True


def _source(text: str, minor: int, in_loop: bool) -> None:
    if text in ("$response.statusCode", "$response.body", "$inputs", "$trigger", "$item", "$index"):
        return
    if text == "$batchIndex":
        if minor < 11:
            raise ExpressionError("version")
        if not in_loop:
            raise ExpressionError("context")
        return
    if text.startswith("$response.headers."):
        if _segment(text[len("$response.headers.") :]):
            return
    elif text.startswith("$response.body#"):
        parse_pointer(text[len("$response.body") :])
        return
    elif text.startswith("$response.body."):
        if minor < 11:
            raise ExpressionError("version")
        if _path(text[len("$response.body.") :]):
            return
    elif text.startswith("$steps."):
        parts = text[len("$steps.") :].split(".")
        if (
            len(parts) >= 3
            and _segment(parts[0])
            and parts[1] == "outputs"
            and _segment(parts[2])
            and (len(parts) == 3 or _path(".".join(parts[3:])))
        ):
            return
    else:
        for prefix in ("$outputs.", "$variables.", "$inputs.", "$trigger.", "$item."):
            if text.startswith(prefix) and _path(text[len(prefix) :]):
                return
    raise ExpressionError("syntax")


def _segment(text: str) -> bool:
    return _SEGMENT.match(text) is not None


def _path(text: str) -> bool:
    return all(_segment(part) for part in text.split("."))


def _version(text: str) -> int | None:
    """Upstream ``version``: three canonical decimal parts with major 1; returns the minor."""
    if len(text.encode("utf-8", errors="surrogatepass")) > 32:
        return None
    parts = text.split(".")
    if len(parts) != 3 or parts[0] != "1":
        return None
    for part in parts:
        if not part or (len(part) > 1 and part[0] == "0") or not (part.isascii() and part.isdigit()):
            return None
    minor, patch = int(parts[1]), int(parts[2])
    if minor > (1 << 63) - 1 or patch > (1 << 63) - 1:  # strconv.Atoi overflow
        return None
    return minor


def _reject_constant(name: str) -> None:
    raise ValueError(name)


def _parse_scalar(text: str) -> bool | str | JsonNumber | None:
    """Upstream ``parseScalar``: exactly one JSON scalar (null, boolean, number or string)."""
    if text == "" or go_trim_space(text) != text:
        raise ExpressionError("syntax")
    try:
        value = json.loads(text, parse_int=JsonNumber, parse_float=JsonNumber, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as exc:
        raise ExpressionError("syntax") from exc
    if value is None or type(value) in (bool, str, JsonNumber):
        return value  # type: ignore[no-any-return]
    raise ExpressionError("syntax")


def parse_pointer(text: str) -> list[str]:
    """Validates and decodes a ``#``-prefixed JSON Pointer fragment (upstream ``expressions.Pointer``)."""
    raw = text.encode("utf-8", errors="surrogatepass")
    if len(raw) > MAX_EXPRESSION_BYTES:
        raise ExpressionError("limit")
    if not raw.startswith(b"#"):
        raise ExpressionError("syntax")
    fragment = raw[1:]
    if fragment and not fragment.startswith(b"/"):
        raise ExpressionError("syntax")
    i = 0
    while i < len(fragment):
        c = fragment[i]
        if c in _POINTER_PLAIN:
            pass
        elif c == 0x25:
            if i + 2 >= len(fragment) or fragment[i + 1] not in _HEX or fragment[i + 2] not in _HEX:
                raise ExpressionError("syntax")
            i += 2
        elif c == 0x7E:
            if i + 1 >= len(fragment) or fragment[i + 1] not in b"01":
                raise ExpressionError("syntax")
            i += 1
        else:
            raise ExpressionError("syntax")
        i += 1
    decoded = re.sub(rb"%([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), fragment)
    if decoded == b"":
        return []
    out: list[str] = []
    for part in decoded[1:].split(b"/"):
        token = bytearray()
        j = 0
        while j < len(part):
            if part[j] != 0x7E:
                token.append(part[j])
                j += 1
                continue
            if j + 1 >= len(part) or part[j + 1] not in b"01":
                raise ExpressionError("syntax")
            token.append(0x7E if part[j + 1] == 0x30 else 0x2F)
            j += 2
        out.append(token.decode("utf-8", errors="surrogateescape"))
    return out
