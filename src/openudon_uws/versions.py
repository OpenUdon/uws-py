"""Published UWS versions, exact admission and SemVer gates (upstream ``publishedUWSVersions``)."""

from __future__ import annotations

import re
from dataclasses import dataclass

PUBLISHED_UWS_VERSIONS: tuple[str, ...] = (
    "1.0.0",
    "1.1.0",
    "1.1.1",
    "1.2.0",
    "1.3.0",
    "1.4.0",
    "1.5.0",
    "1.6.0",
    "1.7.0",
    "1.8.0",
    "1.9.0",
    "1.9.1",
    "1.9.2",
    "1.10.0",
    "1.11.0",
    "1.12.0",
    "1.13.0",
)

LATEST_UWS_VERSION = "1.13.0"

# Lexical form required by §1.1. ``\Z`` and ``[0-9]`` keep Go RE2 semantics (no trailing-newline match).
UWS1_VERSION_PATTERN = re.compile(r"^1\.[0-9]+\.[0-9]+(-.+)?\Z", re.ASCII)

_SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-((?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z",
    re.ASCII,
)


@dataclass(frozen=True)
class SemVer:
    major: int
    minor: int
    patch: int
    pre: tuple[str, ...]


def is_published_uws_version(version: str) -> bool:
    return version in PUBLISHED_UWS_VERSIONS


def parse_semver(version: str) -> SemVer | None:
    """Parses SemVer 2.0.0 (the ``golang.org/x/mod/semver`` subset used upstream)."""
    m = _SEMVER.match(version)
    if m is None:
        return None
    pre = tuple(m.group(4).split(".")) if m.group(4) else ()
    return SemVer(int(m.group(1)), int(m.group(2)), int(m.group(3)), pre)


def compare_semver(a: SemVer, b: SemVer) -> int:
    """SemVer precedence comparison; build metadata is ignored."""
    for left, right in ((a.major, b.major), (a.minor, b.minor), (a.patch, b.patch)):
        if left != right:
            return -1 if left < right else 1
    if not a.pre or not b.pre:
        if len(a.pre) == len(b.pre):
            return 0
        return 1 if not a.pre else -1
    for i in range(max(len(a.pre), len(b.pre))):
        if i >= len(a.pre):
            return -1
        if i >= len(b.pre):
            return 1
        x, y = a.pre[i], b.pre[i]
        if x == y:
            continue
        xn, yn = x.isascii() and x.isdigit(), y.isascii() and y.isdigit()
        if xn and yn:
            return -1 if int(x) < int(y) else 1
        if xn != yn:
            return -1 if xn else 1
        return -1 if x < y else 1
    return 0


def is_valid_uws_version(version: str) -> bool:
    """True when ``version`` is lexically valid UWS 1.x SemVer (upstream ``validUWSVersion``)."""
    return UWS1_VERSION_PATTERN.match(version) is not None and parse_semver(version) is not None


def supports_version_at_least(version: str, major: int, minor: int, patch: int = 0) -> bool:
    """Upstream ``supportsUWSVersionAtLeast``: SemVer precedence against a minimum release."""
    if not is_valid_uws_version(version):
        return False
    parsed = parse_semver(version)
    assert parsed is not None
    return compare_semver(parsed, SemVer(major, minor, patch, ())) >= 0
