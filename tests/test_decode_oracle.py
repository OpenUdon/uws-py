"""G6 (decoding): Python decoders against Go ``convert.YAMLToJSON`` and ``encoding/json``."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import pytest

from openudon_uws.diagnostics import DecodeError
from openudon_uws.strict import parse_profile_yaml, parse_strict_json, parse_strict_yaml
from tests.oracle import Oracle

VECTORS = Path(__file__).parent / "vectors"


def typed(value: Any) -> Any:
    """A comparable form that keeps int/float/bool distinct."""
    if type(value) is dict:
        return {k: typed(v) for k, v in value.items()}
    if type(value) is list:
        return [typed(v) for v in value]
    if type(value) is float:
        return ("float", value)
    if type(value) is int:
        return ("int", value)
    return value


def loose(value: Any) -> Any:
    """Numbers as float64, as Go's ``json.Unmarshal`` into ``any`` produces them."""
    if type(value) is dict:
        return {k: loose(v) for k, v in value.items()}
    if type(value) is list:
        return [loose(v) for v in value]
    if type(value) in (int, float):
        return float(value)
    return value


def go_json(value: Any) -> Any:
    return json.loads(json.dumps(value)) if not isinstance(value, str) else json.loads(value)


def test_yaml_decoding_matches_go(oracle: Oracle, tmp_path: Path) -> None:
    cases: list[str] = json.loads((VECTORS / "yaml_decode.json").read_text())
    files = []
    for i, text in enumerate(cases):
        f = tmp_path / f"case-{i:04d}.yaml"
        f.write_bytes(text.encode("utf-8", errors="surrogatepass"))
        files.append(f)
    go = oracle.run("yaml-to-json", files)
    go_any = oracle.run("yaml-any", files)
    mismatches = []
    for text, f in zip(cases, files, strict=True):
        expected = go[str(f)]
        if expected.valid and not go_any[str(f)].valid:
            # The schema layer's second read (yaml.v3 into ``any``) refuses it, so Go rejects the document.
            expected = go_any[str(f)]
        try:
            actual: Any = parse_strict_yaml(f.read_bytes())
            ok, err = True, ""
        except DecodeError as exc:
            actual, ok, err = None, False, str(exc)
        if ok != expected.valid:
            mismatches.append(f"{text!r}: go={expected.valid} ({expected.error}) py={ok} ({err}) value={actual!r}")
        elif ok:
            want = json.loads(json.dumps(expected.value))
            if typed(json.loads(json.dumps(actual))) != typed(want):
                mismatches.append(f"{text!r}: go value {want!r} != py value {actual!r}")
    assert not mismatches, f"{len(mismatches)} of {len(cases)} mismatches:\n" + "\n".join(mismatches)


def test_json_decoding_matches_go(oracle: Oracle, tmp_path: Path) -> None:
    cases: list[str] = json.loads((VECTORS / "json_decode.json").read_text())
    files = []
    for i, text in enumerate(cases):
        f = tmp_path / f"case-{i:04d}.json"
        f.write_bytes(text.encode("utf-8", errors="surrogatepass"))
        files.append(f)
    go = oracle.run("json-unmarshal", files)
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 50_000))
    mismatches = []
    for text, f in zip(cases, files, strict=True):
        expected = go[str(f)]
        try:
            actual: Any = parse_strict_json(f.read_bytes().decode("utf-8", errors="replace"))
            ok, err = True, ""
        except DecodeError as exc:
            actual, ok, err = None, False, str(exc)
        if ok != expected.valid:
            mismatches.append(f"{text[:60]!r}: go={expected.valid} ({expected.error}) py={ok} ({err})")
        elif ok and loose(actual) != loose(expected.value):
            mismatches.append(f"{text[:60]!r}: go value {expected.value!r} != py value {actual!r}")
    assert not mismatches, f"{len(mismatches)} of {len(cases)} mismatches:\n" + "\n".join(mismatches)


@pytest.mark.parametrize("text", ["1e400", "NaN", "\ufeff{}", "{} {}"])
def test_json_rejections(text: str) -> None:
    with pytest.raises(DecodeError):
        parse_strict_json(text)


def test_json_float_is_finite() -> None:
    assert not math.isinf(parse_strict_json("1.7976931348623157e308"))


def test_profile_yaml_decoding_matches_go(oracle: Oracle, tmp_path: Path) -> None:
    """Profile documents: yaml.v3 into ``any``, single document, then ``json.Marshal`` (exact values)."""
    cases: list[str] = json.loads((VECTORS / "yaml_decode.json").read_text())
    cases += ["a: 1\n---", "a: 1\n...\n", "---\na: 1\n...\n---\nb: 2", "{}", "[]", "~", " \n", "'<<': {x: 1}", "a: 1.0"]
    files = []
    for i, text in enumerate(cases):
        f = tmp_path / f"profile-{i:04d}.yaml"
        f.write_bytes(text.encode("utf-8", errors="surrogatepass"))
        files.append(f)
    go = oracle.run("profile-decode", files)
    mismatches = []
    for text, f in zip(cases, files, strict=True):
        expected = go[str(f)]
        try:
            actual: Any = parse_profile_yaml(f.read_bytes())
            ok, err = True, ""
        except DecodeError as exc:
            actual, ok, err = None, False, str(exc)
        if ok != expected.valid:
            mismatches.append(f"{text!r}: go={expected.valid} ({expected.error}) py={ok} ({err}) value={actual!r}")
        elif ok and typed(json.loads(json.dumps(actual))) != typed(json.loads(json.dumps(expected.value))):
            mismatches.append(f"{text!r}: go value {expected.value!r} != py value {actual!r}")
    assert not mismatches, f"{len(mismatches)} of {len(cases)} mismatches:\n" + "\n".join(mismatches)
