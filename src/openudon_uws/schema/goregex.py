"""Translation of JSON Schema ``pattern`` text to Python ``re`` with Go RE2 semantics.

santhosh-tekuri/jsonschema compiles patterns with Go's ``regexp`` (RE2). Python's ``re`` differs on the
constructs the vendored schemas use: ``\\d`` matches any Unicode digit, ``\\s``/``\\S`` use Unicode whitespace and
``$`` also matches before a trailing newline. The translation rewrites those to their RE2 meaning (ASCII digits,
``[\\t\\n\\f\\r ]``, end of text). Constructs outside the supported subset raise :class:`UnsupportedPattern`, and a
test asserts every vendored pattern translates.
"""

from __future__ import annotations

import re
from functools import lru_cache

_RE2_SPACE = "\\t\\n\\f\\r "


class UnsupportedPattern(ValueError):
    pass


def translate(pattern: str) -> str:
    out: list[str] = []
    i = 0
    in_class = False
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "\\":
            if i + 1 >= n:
                raise UnsupportedPattern(f"trailing backslash in {pattern!r}")
            e = pattern[i + 1]
            i += 2
            if e == "d":
                out.append("0-9" if in_class else "[0-9]")
            elif e == "D":
                if in_class:
                    raise UnsupportedPattern(f"\\D inside a class in {pattern!r}")
                out.append("[^0-9]")
            elif e == "s":
                out.append(_RE2_SPACE if in_class else f"[{_RE2_SPACE}]")
            elif e == "S":
                if in_class:
                    raise UnsupportedPattern(f"\\S inside a class in {pattern!r}")
                out.append(f"[^{_RE2_SPACE}]")
            elif e in "wWbB":
                out.append("\\" + e)  # ASCII under re.ASCII, as in RE2
            elif e.isdigit() or e in "AZzQEpPkgx":
                raise UnsupportedPattern(f"escape \\{e} in {pattern!r}")
            else:
                out.append("\\" + e)
            continue
        if in_class:
            if c == "]":
                in_class = False
            elif c == "[" and pattern[i + 1 : i + 2] == ":":
                raise UnsupportedPattern(f"POSIX class in {pattern!r}")
            out.append(c)
            i += 1
            continue
        if c == "[":
            in_class = True
            out.append(c)
            i += 1
            if pattern[i : i + 1] == "^":
                out.append("^")
                i += 1
            if pattern[i : i + 1] == "]":
                out.append("\\]")
                i += 1
            continue
        if c == "$":
            out.append("\\Z")
            i += 1
            continue
        if c == "(" and pattern[i + 1 : i + 2] == "?" and pattern[i + 2 : i + 3] != ":":
            raise UnsupportedPattern(f"group flags or lookaround in {pattern!r}")
        out.append(c)
        i += 1
    if in_class:
        raise UnsupportedPattern(f"unterminated class in {pattern!r}")
    return "".join(out)


@lru_cache(maxsize=512)
def compile_go(pattern: str) -> re.Pattern[str]:
    """Compiles a schema pattern with Go RE2 matching semantics (search, not full match)."""
    return re.compile(translate(pattern), re.ASCII)
