"""G6 (documents): verdict parity with the Go validator on seeds, mutations and YAML renderings."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

from openudon_uws.document import validate_document_file
from openudon_uws.executable import validate_executable_document
from openudon_uws.structdecode import _model
from tests.mutations import mutate
from tests.oracle import Oracle

VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "testdata"
SEEDS = Path(__file__).resolve().parent / "vectors" / "seeds"
MUTATIONS = int(os.environ.get("UWS_MUTATIONS", "1500"))
SEED = int(os.environ.get("UWS_SEED", "20261103"))


def seed_documents() -> list[Any]:
    seeds: list[Any] = [json.loads((VENDOR / "sample.uws.json").read_text())]
    seeds += [json.loads(p.read_text()) for p in sorted((VENDOR / "examples").iterdir())]
    for name in ("1.10.0.json", "1.11.0.json"):
        corpus = json.loads((VENDOR / "conformance" / name).read_text())
        seeds += [c["input"]["document"] for c in corpus["cases"] if "document" in c["input"]]
    # Feature-rich seeds (contentTrust, idempotency, components, switch/loop/await/merge, generic sources,
    # parallel groups, triggers and results) at the latest version and at older versions whose gates refuse some
    # of those features, so every semantic path and version gate reaches the oracle.
    for path in sorted(SEEDS.glob("*.json")):
        doc = json.loads(path.read_text())
        seeds.append(doc)
        for version in ("1.12.0", "1.11.0", "1.10.0", "1.9.2"):
            older = json.loads(json.dumps(doc))
            older["uws"] = version
            seeds.append(older)
    return seeds


def _write(docs: list[Any], directory: Path, ext: str) -> list[Path]:
    files = []
    for i, doc in enumerate(docs):
        f = directory / f"case-{i:05d}.{ext}"
        if ext == "json":
            f.write_text(json.dumps(doc), encoding="utf-8")
        else:
            f.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
        files.append(f)
    return files


def _compare(oracle: Oracle, kind: str, files: list[Path], check: Any) -> tuple[list[str], int]:
    go = oracle.run(kind, files)
    mismatches = []
    accepted = 0
    for f in files:
        expected = go[str(f)]
        actual = check(f)
        accepted += expected.valid
        if expected.error.startswith("crash:"):
            continue
        if expected.valid != actual.valid:
            mismatches.append(
                f"{f.name}: go={expected.valid} py={actual.valid}\n  go: {expected.error[:300]}\n"
                f"  py: {actual.diagnostics[:3]}\n  doc: {f.read_text()[:400]}"
            )
    return mismatches, accepted


def test_documents_match_go(oracle: Oracle, tmp_path: Path) -> None:
    seeds = seed_documents()
    docs = seeds + mutate(seeds, MUTATIONS, SEED)
    files = _write(docs, tmp_path, "json")
    mismatches, accepted = _compare(oracle, "document", files, validate_document_file)
    print(f"G6: {len(files)} documents, {accepted} accepted by Go, {len(mismatches)} mismatches")
    assert accepted > len(seeds), "the mutation corpus must include accepted documents"
    assert not mismatches, f"{len(mismatches)} of {len(files)} mismatches:\n" + "\n\n".join(mismatches[:12])


def test_executable_and_yaml_match_go(oracle: Oracle, tmp_path: Path) -> None:
    seeds = seed_documents()
    docs = seeds + mutate(seeds, MUTATIONS, 31)
    json_files = _write(docs, tmp_path, "json")
    yaml_files = _write(docs, tmp_path, "yaml")
    exec_mismatches, _ = _compare(
        oracle, "executable", json_files, lambda f: validate_executable_document(f.read_bytes(), format="json")
    )
    yaml_mismatches, _ = _compare(oracle, "document", yaml_files, validate_document_file)
    mismatches = exec_mismatches + yaml_mismatches
    assert not mismatches, f"{len(mismatches)} mismatches:\n" + "\n\n".join(mismatches[:12])


def test_struct_model_matches_go(oracle: Oracle) -> None:
    assert oracle.run_one("struct-model", Path("-")).value == _model()


def test_document_vectors_match_go(oracle: Oracle, tmp_path: Path) -> None:
    """Hand-written document vectors (timestamps, BOMs, ParamSchema keywords, Go int fields, nesting)."""
    cases: list[dict[str, str]] = json.loads((Path(__file__).parent / "vectors" / "documents.json").read_text())
    files = []
    for case in cases:
        f = tmp_path / f"{case['name']}.{case['ext']}"
        f.write_text(case["content"], encoding="utf-8")
        files.append(f)
    go = oracle.run("document", files)
    mismatches = []
    for case, f in zip(cases, files, strict=True):
        expected = go[str(f)]
        actual = validate_document_file(f)
        if expected.valid != actual.valid:
            mismatches.append(
                f"{case['name']}: go={expected.valid} ({expected.error[:200]}) py={actual.diagnostics[:2]}"
            )
    assert not mismatches, "\n".join(mismatches)
