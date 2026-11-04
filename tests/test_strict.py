"""G2: strict decoding units."""

from __future__ import annotations

import pytest

from openudon_uws.diagnostics import DecodeError
from openudon_uws.strict import decode_document, format_for_path, parse_strict_json, parse_strict_yaml


def test_json_last_duplicate_wins_by_default() -> None:
    assert parse_strict_json('{"a":1,"a":2}') == {"a": 2}


def test_json_duplicates_can_be_refused() -> None:
    with pytest.raises(DecodeError, match="duplicate"):
        parse_strict_json('{"a":1,"a":2}', reject_duplicates=True)


@pytest.mark.parametrize("text", ["", "  ", "{} {}", "NaN", "Infinity", "1e400", "﻿{}", "[1,]", "01"])
def test_json_refusals(text: str) -> None:
    with pytest.raises(DecodeError):
        parse_strict_json(text)


def test_json_unpaired_surrogates_become_replacement_characters() -> None:
    assert parse_strict_json('["\\ud800", "\\udc00x", "\\ud83d\\ude00"]') == ["�", "�x", "\U0001f600"]


def test_json_bytes_with_bom_are_refused_like_text() -> None:
    with pytest.raises(DecodeError):
        decode_document(b"\xef\xbb\xbf{}", format="json")


def test_json_invalid_utf8_inside_strings_is_replaced() -> None:
    assert decode_document(b'{"a":"\xff"}', format="json") == {"a": "�"}


def test_auto_reads_yaml_flow_mapping() -> None:
    assert decode_document("{uws: 1.13.0, info: {title: t}}") == {"uws": "1.13.0", "info": {"title": "t"}}


def test_auto_prefers_json() -> None:
    assert decode_document('{"a": 1, "a": 2}') == {"a": 2}


def test_max_bytes() -> None:
    with pytest.raises(DecodeError, match="exceeds"):
        decode_document("{}" * 10, max_bytes=5)


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("a: 0777", {"a": 511}),
        ("a: yes", {"a": "yes"}),
        ("a: 0x1F", {"a": 31}),
        ("a: +1.0", {"a": 1}),
        ("a: 1.0", {"a": 1.0}),
        ("a: 2024-01-02", {"a": "2024-01-02T00:00:00Z"}),
        ("a: '2024-01-02'", {"a": "2024-01-02"}),
        ("base: &b {x: 1}\nd:\n  <<: *b\n  y: 2", {"base": {"x": 1}, "d": {"x": 1, "y": 2}}),
        ("a: 1\n---\nb: 2", {"a": 1}),
        ("", None),
    ],
)
def test_yaml_values(text: str, value: object) -> None:
    assert parse_strict_yaml(text) == value


@pytest.mark.parametrize(
    "text",
    [
        "1: a",
        "a: 1\na: 2",
        "a: .nan",
        "a: .inf",
        "a: !!int 1.5",
        "a: &x {b: *x}",
        '"a": "\\ud800"',
        "%YAML 1.2\n---\na: 1",
    ],
)
def test_yaml_refusals(text: str) -> None:
    with pytest.raises(DecodeError):
        parse_strict_yaml(text)


def test_format_for_path() -> None:
    assert format_for_path("x.uws.JSON") == "json"
    assert format_for_path("x.yml") == "yaml"
    with pytest.raises(DecodeError):
        format_for_path("x.hcl")


def test_auto_falls_back_to_yaml_only_for_malformed_json() -> None:
    assert decode_document("{uws: 1.13.0}") == {"uws": "1.13.0"}
    with pytest.raises(DecodeError, match="out of range"):
        decode_document('{"a": 1e400}')
    with pytest.raises(DecodeError, match="duplicate"):
        decode_document('{"a": 1, "a": 2}', reject_duplicate_keys=True)


def test_invalid_utf8_bytes_each_become_a_replacement_character() -> None:
    assert decode_document(b'{"a":"x\xe2\x82y"}', format="json") == {"a": "x\ufffd\ufffdy"}


@pytest.mark.parametrize(
    "text",
    ["base: &b [{x: 1}]\nd:\n  <<: *b", "d:\n  <<: [[{x: 1}]]", '"\\ud800": 1', '"\\ud83d\\ude00": 1'],
)
def test_yaml_forms_go_refuses(text: str) -> None:
    from openudon_uws.strict import parse_profile_yaml

    with pytest.raises(DecodeError):
        parse_strict_yaml(text)
    with pytest.raises(DecodeError):
        parse_profile_yaml(text)


def test_many_keys_decode_in_linear_time() -> None:
    import time

    from openudon_uws.strict import parse_profile_yaml

    text = "\n".join(f"k{i}: {i}" for i in range(20000))
    start = time.perf_counter()
    assert len(parse_profile_yaml(text)) == 20000
    assert time.perf_counter() - start < 10
