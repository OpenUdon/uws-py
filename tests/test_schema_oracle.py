"""G6 (schema layer): URL parsing, formats, patterns and keywords against Go.

``url-parse`` compares the Go ``net/url`` port component by component; ``schema-check`` compiles a one-keyword
schema with santhosh-tekuri/jsonschema (format assertion on), as upstream compiles the published schemas.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openudon_uws.schema import validate_inline
from openudon_uws.schema.goformats import is_loopback_ip, parse_url
from tests.oracle import Oracle

VECTORS = Path(__file__).parent / "vectors"
# Formats the vendored schemas use; others are not asserted by this package (no schema relies on them).
ASSERTED_FORMATS = {"uri", "uri-reference", "date-time"}


def _go_text(s: str) -> str:
    return s.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")


def _py_url(raw: str) -> dict[str, Any] | None:
    u = parse_url(raw)
    if u is None:
        return None
    return {
        "scheme": u.scheme,
        "opaque": u.opaque,
        "host": _go_text(u.host),
        "path": _go_text(u.path),
        "rawQuery": u.raw_query,
        "fragment": _go_text(u.fragment),
        "hostname": _go_text(u.hostname()),
        "port": u.port(),
        "hasUser": u.user is not None,
        "loopback": is_loopback_ip(u.hostname()),
    }


def test_url_parsing_matches_go(oracle: Oracle, tmp_path: Path) -> None:
    urls: list[str] = json.loads((VECTORS / "urls.json").read_text())
    files = []
    for i, raw in enumerate(urls):
        f = tmp_path / f"url-{i:04d}.txt"
        f.write_bytes(raw.encode("utf-8"))
        files.append(f)
    go = oracle.run("url-parse", files)
    mismatches = []
    for raw, f in zip(urls, files, strict=True):
        expected = go[str(f)]
        actual = _py_url(raw)
        if expected.valid != (actual is not None):
            mismatches.append(f"{raw!r}: go={expected.valid} ({expected.error}) py={actual is not None}")
        elif actual is not None and actual != expected.value:
            mismatches.append(f"{raw!r}: go {expected.value} != py {actual}")
    assert not mismatches, f"{len(mismatches)} of {len(urls)} mismatches:\n" + "\n".join(mismatches)


def test_schema_keywords_match_go(oracle: Oracle, tmp_path: Path) -> None:
    cases: list[dict[str, Any]] = json.loads((VECTORS / "schema_check.json").read_text())
    files = []
    for i, case in enumerate(cases):
        f = tmp_path / f"schema-{i:04d}.json"
        f.write_text(json.dumps(case), encoding="utf-8")
        files.append(f)
    go = oracle.run("schema-check", files)
    mismatches = []
    checked = 0
    for case, f in zip(cases, files, strict=True):
        fmt = case["schema"].get("format")
        if fmt is not None and fmt not in ASSERTED_FORMATS:
            continue
        checked += 1
        expected = go[str(f)]
        actual = validate_inline(case["schema"], case["value"])
        if expected.valid != (not actual):
            mismatches.append(f"{json.dumps(case)}: go={expected.valid} py={not actual} {expected.error[:120]}")
    assert checked > 900
    assert not mismatches, f"{len(mismatches)} of {checked} mismatches:\n" + "\n".join(mismatches)
