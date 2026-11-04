"""G2: schema layer units."""

from __future__ import annotations

from typing import Any

import pytest

from openudon_uws.schema import compiled_schema, schema_document, schema_names, validate_inline
from openudon_uws.schema.goformats import go_date_time, go_uri, go_uri_reference, is_loopback_ip, parse_url
from openudon_uws.schema.goregex import UnsupportedPattern, compile_go, translate


def _patterns(node: Any, out: set[str]) -> set[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "pattern" and isinstance(value, str):
                out.add(value)
            if key == "patternProperties" and isinstance(value, dict):
                out.update(value)
            _patterns(value, out)
    elif isinstance(node, list):
        for item in node:
            _patterns(item, out)
    return out


def test_every_vendored_pattern_translates() -> None:
    patterns: set[str] = set()
    for name in schema_names():
        _patterns(schema_document(name), patterns)
    assert len(patterns) >= 19
    for pattern in patterns:
        compile_go(pattern)


def test_every_vendored_schema_compiles() -> None:
    for name in schema_names():
        compiled_schema(name)


@pytest.mark.parametrize("pattern", ["(?=x)", "\\1", "[[:alpha:]]", "\\p{L}"])
def test_unsupported_patterns_are_refused(pattern: str) -> None:
    with pytest.raises(UnsupportedPattern):
        translate(pattern)


def test_re2_semantics() -> None:
    assert compile_go("^[a-z]+$").search("abc\n") is None
    assert compile_go("^\\d+$").search("\u0661") is None
    assert compile_go("\\S").search("\u00a0") is not None
    assert compile_go("\\S").search("\x0b") is not None


def test_formats() -> None:
    assert go_uri("https://example.com/a b")
    assert not go_uri("/relative")
    assert not go_uri("https://ex%61mple.com")
    assert go_uri_reference("/relative")
    assert not go_uri_reference("a\\b")
    assert go_date_time("2024-01-02T23:59:60Z")
    assert not go_date_time("2024-01-02T22:59:60Z")


def test_url_helpers() -> None:
    u = parse_url("http://[::ffff:7f00:1]:80/p")
    assert u is not None and u.hostname() == "::ffff:7f00:1" and u.port() == "80"
    assert is_loopback_ip(u.hostname())
    assert not is_loopback_ip("fe80::1%eth0")


def test_go_time_stand_in_fails_typed_positions() -> None:
    from openudon_uws.schema import GoTime

    assert validate_inline({"type": "string"}, GoTime("2024-01-02T00:00:00Z"))
    assert not validate_inline({}, GoTime("2024-01-02T00:00:00Z"))


def test_pattern_properties_mean_the_same_under_python_re() -> None:
    """jsonschema's additionalProperties/unevaluatedProperties match patternProperties keys with Python ``re``.
    That is Go-equivalent only for patterns without constructs whose meaning differs from RE2."""
    import re as python_re

    risky = python_re.compile(r"\$|\\[dDsSwWbB]|\[\^?]|\(\?")
    keys: set[str] = set()

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            keys.update((node.get("patternProperties") or {}).keys())
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    for name in schema_names():
        collect(schema_document(name))
    assert keys, "expected patternProperties in the vendored schemas"
    for pattern in keys:
        assert not risky.search(pattern), f"{pattern!r} may match differently under Python re and Go RE2"
        assert translate(pattern) == pattern
