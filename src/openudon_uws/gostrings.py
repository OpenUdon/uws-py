"""Go ``strings``/``strconv`` behaviors that differ from Python built-ins."""

from __future__ import annotations

import re

# unicode.IsSpace: Latin-1 spaces plus the Unicode White_Space property. Python's str.strip() also strips
# U+001C..U+001F and does not match this set exactly.
_GO_SPACE = "\t\n\v\f\r \u0085                 　"
_ATOI = re.compile(r"[+-]?[0-9]+\Z", re.ASCII)
_INT64_MIN, _INT64_MAX = -(1 << 63), (1 << 63) - 1


def go_trim_space(s: str) -> str:
    """``strings.TrimSpace``."""
    return s.strip(_GO_SPACE)


def go_is_space(c: str) -> bool:
    """``unicode.IsSpace``."""
    return c in _GO_SPACE


def go_atoi(s: str) -> int | None:
    """``strconv.Atoi`` on a 64-bit platform; ``None`` on error."""
    if not _ATOI.match(s):
        return None
    value = int(s)
    return value if _INT64_MIN <= value <= _INT64_MAX else None


def _simple_lower(c: str) -> str:
    if "\udc80" <= c <= "\udcff":  # an invalid UTF-8 byte (surrogateescape): strings.Map writes U+FFFD
        return "�"
    lower = c.lower()
    if len(lower) == 1:
        return lower
    return "i" if c == "İ" else c


def go_to_lower(s: str) -> str:
    """``strings.ToLower``: per-rune simple lowercase mapping (no final-sigma context, ``İ`` -> ``i``)."""
    if s.isascii():
        return s.lower()
    return "".join(_simple_lower(c) for c in s)


def _fold(c: str) -> str:
    folded = c.casefold()
    if len(folded) == 1:
        return folded
    lower = c.lower()
    return lower if len(lower) == 1 else c


def go_equal_fold(a: str, b: str) -> bool:
    """``strings.EqualFold``: simple Unicode case folding rune by rune (``ß`` does not match ``ss``)."""
    if len(a) != len(b):
        return False
    return all(x == y or _fold(x) == _fold(y) for x, y in zip(a, b, strict=True))
