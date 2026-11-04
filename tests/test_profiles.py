"""Gate G5 (profile corpora) and G6 parity for the profile validators."""

from __future__ import annotations

import copy
import json
import os
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from openudon_uws.diagnostics import ValidationResult
from openudon_uws.profiles import (
    BROWSER_PROFILES,
    canonicalize_request,
    request_digest,
    validate_browser_authentication_call,
    validate_browser_authentication_profile,
    validate_browser_profile,
    validate_mock_fixtures,
    validate_runtime_supplement,
)
from openudon_uws.strict import parse_profile_yaml
from tests.mutations import get, mutate, paths, set_
from tests.oracle import Oracle

VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "testdata"
COUNT = int(os.environ.get("UWS_PROFILE_MUTATIONS", "800"))
DIGESTS: list[dict[str, str]] = json.loads((VENDOR / "mock-fixtures/1.0/request-digest-vectors.json").read_text())


def _profiles(directory: str) -> list[Path]:
    return sorted(p for p in (VENDOR / directory).iterdir() if not p.name.endswith(".uws.yaml"))


@pytest.mark.parametrize("path", _profiles("browser-profile"), ids=lambda p: p.name)
def test_browser_profile_corpus(path: Path) -> None:
    result = validate_browser_profile(path.read_bytes())
    assert result.valid is ("invalid" not in path.name), result.diagnostics


@pytest.mark.parametrize("path", _profiles("browser-authentication"), ids=lambda p: p.name)
def test_browser_authentication_corpus(path: Path) -> None:
    result = validate_browser_authentication_profile(path.read_bytes())
    assert result.valid, result.diagnostics


@pytest.mark.parametrize("vector", DIGESTS, ids=lambda v: v["name"])
def test_request_digest_vectors(vector: dict[str, str]) -> None:
    request = json.loads(vector["requestJson"])
    assert canonicalize_request(request) == vector["canonical"]
    assert request_digest(request) == vector["digest"]


def test_minimal_fixture_document() -> None:
    fixture = {"operationId": "op", "requestDigest": DIGESTS[0]["digest"], "provenance": {"kind": "example"},
               "response": {"statusCode": 200}}  # fmt: skip
    doc = {"format": "uws.mock-fixtures.1.0", "fixtures": [fixture]}
    assert validate_mock_fixtures(json.dumps(doc)).valid
    doc["fixtures"] = [fixture, fixture]
    assert not validate_mock_fixtures(json.dumps(doc)).valid


def test_runtime_supplement() -> None:
    assert validate_runtime_supplement(json.dumps({"x-uws-runtime": {"type": "fnct", "function": "identity"}})).valid
    assert not validate_runtime_supplement(json.dumps({"x-uws-runtime": {"type": "http"}})).valid


# --- G6 ---------------------------------------------------------------------------------------------------------

PROFILE_STRINGS = [
    "{{name}}", "{{ name }}", "{{1x}}", "{{{{", "}}}}", "{{{{x}}}}", "a}}b", "{{a}}{{b}}", "x{{", "/p/{{name}}",
    "https://{{host}}/", "https://a.example/{{name}}?q={{name}}", "https://a.example/?{{name}}=1", "/a/%2e%2e/b",
    "/a/../b", "/a%2Fb", "https://a.example#{{name}}", "line sep", "bidi‮ok", "tab\tok", "ctrl\x7f",
    "main", "popup", "frame", "totp_seed", "sends_mfa_challenge", "establishes_session", "constructor", "__proto__",
    "https://login.example", "http://localhost:8080", "http://127.0.0.1", "http://[::1]", "https://A.EXAMPLE:443",
    "./b.yaml", "../b.yaml", "/abs.yaml", "a/../../b", "",
]  # fmt: skip


def profile_mutate(seeds: list[Any], count: int, seed: int) -> list[Any]:
    """Generic mutations plus string injections from the profile vocabulary (templates, origins, contexts)."""
    rand = random.Random(seed)
    docs = mutate(seeds, count, seed)
    for doc in docs:
        if rand.random() < 0.6:
            strs = [p for p in paths(doc) if type(get(doc, p)) is str]
            if strs:
                set_(doc, rand.choice(strs), rand.choice(PROFILE_STRINGS))
    return docs


def _differential(
    oracle: Oracle,
    tmp_path: Path,
    kind: str,
    validator: Callable[[bytes], ValidationResult],
    seeds: list[Any],
    count: int,
    seed: int,
    as_yaml: bool,
) -> None:
    docs = seeds + profile_mutate(seeds, count, seed)
    files = []
    for i, doc in enumerate(docs):
        f = tmp_path / f"{kind}-{i:05d}.{'yaml' if as_yaml else 'json'}"
        if as_yaml and i % 2 == 0:
            f.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
        else:
            f.write_text(json.dumps(doc), encoding="utf-8")
        files.append(f)
    go = oracle.run(kind, files)
    mismatches = []
    accepted = 0
    for f in files:
        expected = go[str(f)]
        accepted += expected.valid
        actual = validator(f.read_bytes())
        if expected.valid != actual.valid:
            mismatches.append(
                f"{f.name}: go={expected.valid} py={actual.valid}\n  go: {expected.error[:300]}\n"
                f"  py: {actual.diagnostics[:2]}\n  doc: {f.read_text()[:300]}"
            )
    print(f"G6 {kind}: {len(files)} cases, {accepted} accepted by Go, {len(mismatches)} mismatches")
    assert accepted > 0
    assert not mismatches, f"{len(mismatches)} of {len(files)}:\n" + "\n\n".join(mismatches[:8])


def _seeds(directory: str) -> list[Any]:
    return [parse_profile_yaml(p.read_bytes()) for p in _profiles(directory)]


def test_browser_profiles_match_go(oracle: Oracle, tmp_path: Path) -> None:
    # Every seed under every browser version, so each version's rules (1.8 templates included) reach the oracle.
    seeds = []
    for seed in _seeds("browser-profile"):
        for profile in BROWSER_PROFILES:
            fanned = copy.deepcopy(seed)
            fanned["profile"] = profile
            seeds.append(fanned)
    _differential(oracle, tmp_path, "browser-profile", validate_browser_profile, seeds, COUNT * 2, 11, True)


def test_browser_authentication_profiles_match_go(oracle: Oracle, tmp_path: Path) -> None:
    _differential(oracle, tmp_path, "browser-authentication", validate_browser_authentication_profile,
                  _seeds("browser-authentication"), COUNT, 12, True)  # fmt: skip


@pytest.mark.parametrize("version", ["1.0", "1.1"])
def test_browser_authentication_calls_match_go(oracle: Oracle, tmp_path: Path, version: str) -> None:
    call = {"profile": "./browser-authentication/member.yaml", "flow": "member_login_push",
            "session": "member_portal", "credentialBindings": {"username": "member_username"}}  # fmt: skip
    profiles = ["./a.yaml", "../a.yaml", "/abs/a.yaml", "a/../../b.yaml", "a/./b.yaml", "a//b.yaml", "a/..", ".",
                "x/../y.yaml", "//a.yaml", "a/b/../../..", ""]  # fmt: skip
    seeds: list[Any] = [{"x-uws-browser-authentication": {**call, "profile": p}} for p in profiles]
    _differential(
        oracle, tmp_path, f"browser-authentication-call-{version}",
        lambda d: validate_browser_authentication_call(d, f"uws.browser-authentication-call.{version}"),
        seeds, COUNT // 2, 13, False,
    )  # fmt: skip


def test_mock_fixtures_match_go(oracle: Oracle, tmp_path: Path) -> None:
    seed: Any = {
        "format": "uws.mock-fixtures.1.0",
        "fixtures": [
            {"operationId": "list", "requestDigest": DIGESTS[0]["digest"], "provenance": {"kind": "example"},
             "response": {"statusCode": 200, "body": []}},
            {"operationId": "get", "requestDigest": DIGESTS[1]["digest"],
             "provenance": {"kind": "recorded", "redacted": True, "redactedPointers": ["/body/token"]},
             "response": None},
        ],
    }  # fmt: skip
    seeds = [seed, copy.deepcopy(seed)]
    seeds[1]["fixtures"][0]["provenance"] = {"kind": "synthetic"}
    _differential(oracle, tmp_path, "mock-fixtures", validate_mock_fixtures, seeds, COUNT, 14, False)


def test_request_digests_match_go(oracle: Oracle, tmp_path: Path) -> None:
    requests: list[str] = [v["requestJson"] for v in DIGESTS]
    requests += [
        "null", "{}", "[]", '"s"', '{"a":1e21,"b":1e-7,"c":-0,"d":0.1,"e":123456789012345678901234567890}',
        '{"<":"&>","\\u2028":"\\u2029","\\u0000":"\\u007f\\b\\f"}', '{"b":1,"a":2,"\\ud83d\\ude00":3,"\\uffff":4}',
        '{"a":' + "[" * 99 + "]" * 99 + "}", '{"a":' + "[" * 100 + "]" * 100 + "}",
        '{"big":"' + "x" * (1 << 20) + '"}', '{"big":"' + "<" * ((1 << 20) // 6) + '"}',
    ]  # fmt: skip
    files = []
    for i, text in enumerate(requests):
        f = tmp_path / f"request-{i:03d}.json"
        f.write_text(text, encoding="utf-8")
        files.append(f)
    go = oracle.run("request-digest", files)
    mismatches = []
    for text, f in zip(requests, files, strict=True):
        expected = go[str(f)]
        try:
            actual: str | None = request_digest(json.loads(text))
        except ValueError:
            actual = None
        want = json.loads(json.dumps(expected.value)) if expected.valid else None
        if actual != want:
            mismatches.append(f"{text[:80]!r}: go={want} ({expected.error[:100]}) py={actual}")
    assert not mismatches, "\n".join(mismatches)


@pytest.mark.parametrize(
    ("validator", "document"),
    [
        (validate_browser_profile, {"profile": "uws.browser.1.10", "unexpected": True}),
        (validate_browser_authentication_profile, {"profile": "uws.browser-authentication.1.1", "unexpected": True}),
        (validate_browser_authentication_call, {"x-uws-browser-authentication": {"unexpected": True}}),
        (validate_runtime_supplement, {"x-uws-runtime": {"type": "http"}}),
        (validate_mock_fixtures, {"format": "uws.mock-fixtures.1.0", "fixtures": "nope"}),
    ],
)
def test_schema_failures_are_reported(validator: Callable[[str], ValidationResult], document: Any) -> None:
    """A schema failure must never be mistaken for success (ValidationResult is falsy when invalid)."""
    result = validator(json.dumps(document))
    assert result.valid is False
    assert result.diagnostics
