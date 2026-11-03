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
