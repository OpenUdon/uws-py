"""Gate G3 (expression.parse vectors) and G6 parity with upstream ``expressions.Parse``."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pytest

from openudon_uws.expressions import (
    MAX_EXPRESSION_BYTES,
    ExpressionError,
    JsonNumber,
    is_valid_expression,
    parse_expression,
    parse_pointer,
)
from tests.oracle import Oracle

VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "testdata"
FIELDS = ["value", "predicate", "wait", "batchSize"]
GO_ERRORS = {
    "expression does not match the UWS core grammar": "syntax",
    "expression feature is unavailable in the declared version": "version",
    "expression feature is unavailable in this context": "context",
    "expression exceeds the reference parser byte limit": "limit",
}


def field_for(field: str, construct_type: str | None) -> str:
    if field == "batchSize":
        return "batchSize"
    if field == "wait":
        return "predicate" if construct_type == "await" else "wait"
    if field == "when":
        return "predicate"
    return "value"


def _conformance() -> list[dict[str, Any]]:
    corpus = json.loads((VENDOR / "conformance" / "1.11.0.json").read_text(encoding="utf-8"))
    return [c for c in corpus["cases"] if c.get("operation") == "expression.parse"]


@pytest.mark.parametrize("case", _conformance(), ids=lambda c: c["id"])
def test_conformance_expression_parse(case: dict[str, Any]) -> None:
    i = case["input"]
    valid = is_valid_expression(
        i["expression"],
        version=i["declaredVersion"],
        field=field_for(i["field"], i.get("constructType")),
        in_loop=i["loopContext"],
    )
    assert valid is case["expected"]["valid"]


def test_units() -> None:
    assert is_valid_expression("$steps.a.outputs.b.c")
    assert is_valid_expression('$inputs.name == "x y"')
    assert is_valid_expression("$response.statusCode >= 200")
    assert is_valid_expression("$response.body#/a/0/~1b")
    assert not is_valid_expression("$steps.a.outputs")
    assert not is_valid_expression(" $inputs")
    assert not is_valid_expression("$inputs.a =~ 1")
    parsed = parse_expression("5", field="wait", version="1.11.0")
    assert parsed.is_number and parsed.literal == JsonNumber("5")
    assert parse_expression("$inputs.a == 1e400").literal == "1e400"
    with pytest.raises(ExpressionError) as err:
        parse_expression("$batchIndex", version="1.11.0")
    assert err.value.kind == "context"
    with pytest.raises(ExpressionError) as err:
        parse_expression("$" + "a" * MAX_EXPRESSION_BYTES)
    assert err.value.kind == "limit"
    assert parse_pointer("#/a~1b/%7E0") == ["a/b", "~"]


PIECES = [
    "$inputs", "$inputs.a", "$inputs.a-b.c", "$steps.s.outputs.o", "$steps.s.outputs.o.x", "$steps.s.out.o",
    "$response.body", "$response.body.a.b", "$response.body#/a~0b", "$response.body#/a%7E2", "$response.body#a",
    "$response.body#/%E2%82", "$response.body#/a%7e1", "$response.body#", "$response.headers.X-Id",
    "$response.statusCode", "$batchIndex", "$item", "$item.k", "$index", "$trigger.p", "$variables.v", "$outputs.o",
    "$foo", "$inputs.", "$inputs..a", "$inputs.é", "1", "1.5", "-0", "01", "1e3", "1e400", "-1E-400", "+1", "NaN",
    "Infinity", '"s"', '"\\ud800"', '"a b"', '"é"', "true", "null", "[]", "{}", "abc", " ", "1 ",
]  # fmt: skip
OPERATORS = ["==", "!=", "<=", ">=", "<", ">", "=", "=~", ""]
VERSIONS = [
    "1.9.2",
    "1.10.0",
    "1.11.0",
    "1.12.0",
    "1.13.0",
    "",
    "2.0.0",
    "1.01.0",
    "1.99999999999999999999.0",
    "1.1.0 ",
]


def _generate(count: int, seed: int) -> list[dict[str, Any]]:
    rand = random.Random(seed)
    cases = []
    for _ in range(count):
        text = rand.choice(PIECES)
        shape = rand.random()
        if shape < 0.45:
            text = f"{rand.choice(PIECES)} {rand.choice(OPERATORS)} {rand.choice(PIECES)}"
        elif shape < 0.5:
            text = f" {text}"
        elif shape < 0.55:
            text = f"{text}  "
        elif shape < 0.6:
            text = text + rand.choice([" ", "\u0085", "﻿", "\x1c", "\t"])
        cases.append(
            {
                "version": rand.choice(VERSIONS),
                "field": rand.choice([*FIELDS, "bogus"]),
                "inLoop": rand.random() < 0.5,
                "text": text,
            }
        )
    return cases


def test_expressions_match_go(oracle: Oracle, tmp_path: Path) -> None:
    cases = _generate(8000, 424242)
    file = tmp_path / "cases.json"
    file.write_text(json.dumps(cases), encoding="utf-8")
    verdicts = oracle.run("expressions", [file])
    assert len(verdicts) == len(cases)
    mismatches = []
    for i, case in enumerate(cases):
        go = verdicts[f"{file}#{i}"]
        go_kind = "" if go.valid else GO_ERRORS.get(go.error, go.error)
        try:
            parse_expression(case["text"], version=case["version"], field=case["field"], in_loop=case["inLoop"])
            py_kind = ""
        except ExpressionError as exc:
            py_kind = exc.kind
        if go_kind != py_kind:
            mismatches.append(f"{case}: go={go_kind or 'ok'} py={py_kind or 'ok'}")
    assert not mismatches, f"{len(mismatches)} of {len(cases)}:\n" + "\n".join(mismatches[:15])
