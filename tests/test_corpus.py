"""Gates G3 (conformance document.validate) and G4 (invalid/valid corpora) over vendored upstream data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from openudon_uws.document import validate_document, validate_document_file

VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "testdata"
VALID = [VENDOR / "sample.uws.json", *sorted((VENDOR / "examples").iterdir())]
INVALID = sorted((VENDOR / "invalid").iterdir())


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.name)
def test_invalid_corpus_is_rejected(path: Path) -> None:
    result = validate_document_file(path)
    assert not result.valid


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name)
def test_valid_corpus_is_accepted(path: Path) -> None:
    result = validate_document_file(path)
    assert result.valid, result.diagnostics


def _cases(operation: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for name in ("1.10.0.json", "1.11.0.json"):
        corpus = json.loads((VENDOR / "conformance" / name).read_text(encoding="utf-8"))
        out += [(f"{name}:{c['id']}", c) for c in corpus["cases"] if c.get("operation") == operation]
    return out


@pytest.mark.parametrize(
    ("case_id", "case"), _cases("document.validate"), ids=lambda x: x if isinstance(x, str) else ""
)
def test_conformance_document_validate(case_id: str, case: dict[str, Any]) -> None:
    result = validate_document(case["input"]["document"])
    assert result.valid is case["expected"]["valid"], result.diagnostics


def test_document_execute_cases_are_out_of_scope() -> None:
    # Execution is out of scope (PLAN.md); the cases are counted, never silently passed.
    assert len(_cases("document.execute")) == 5


def test_conformance_1_10_validation_area() -> None:
    """1.10.0 ``validation`` cases: names addressable by the expression grammar (variables, inputs, outputs)."""
    from openudon_uws.semantic.access import CONSTRUCT_ID, matches

    corpus = json.loads((VENDOR / "conformance" / "1.10.0.json").read_text(encoding="utf-8"))
    cases = [c for c in corpus["cases"] if c.get("area") == "validation"]
    assert [c["id"] for c in cases] == ["expression-addressable-identifiers"]
    for case in cases:
        assert [matches(CONSTRUCT_ID, name) for name in case["input"]] == case["expected"]
    # The other 1.10.0 areas (expressions truthiness, structural, execution, criteria, actions, triggers) are
    # runtime behavior, out of scope; they are counted here, never silently passed.
    assert sum(1 for c in corpus["cases"] if c.get("area") != "validation") == 10
