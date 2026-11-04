# uws-py plan

## Context

OpenUdon now has the Go reference implementation (`OpenUdon/uws`, package `uws1`) and a TypeScript port
(`OpenUdon/uws-ts`, being hardened by a sibling session). The user created the empty repo
`OpenUdon/uws-py` and wants a third, parallel implementation in Python, pushed to its `main` branch.
Goal: a Python package that gives the **same accept/reject verdict as Go** for every UWS document and
profile, proven by a differential Go oracle, with the same scope as uws-ts so the three stay comparable.
uws-ts' second review found 13 Go-parity bugs the random mutation testing never hit; this plan bakes
those lessons (and their Python analogs) in from day one.

## Source of truth

- Upstream `OpenUdon/uws` pinned at **`fab2179`** (same pin as uws-ts, so all three implementations are
  compared against one Go reference). 
- Schemas (`versions/*.json`) and corpora (`testdata/…`) are vendored **byte for byte**: schemas into the package at
  `src/openudon_uws/_schemas/` (so they ship in the wheel) and corpora into `vendor/testdata/`.
  `vendor/UPSTREAM.json` records commit + SHA-256 per file under its upstream path, including the published conformance digest
  `51ea40b3…`. Cross-checked against `uws-ts/vendor/UPSTREAM.json` (identical hashes expected).
- Behavior reference = Go at the pin. uws-ts is a structural guide only;
  where TS and Go disagree, Go wins. Intentional differences are listed in `docs/divergences.md`.

## Scope (same as uws-ts)

| In scope | Out of scope |
|---|---|
| Strict JSON/YAML decoding (single document, size bounds, duplicate-key policy, BOM, trailing data) | HCL, execution/orchestration, runtimes |
| Exact published-version admission, 1.0.0–1.13.0 | Mock runtime replay, source loading |
| JSON Schema 2020-12 against the exact declared-version schema | Content-trust analysis (§4.7) |
| Semantic validation equivalent to `uws1.(*Document).Validate` | Publishing to PyPI (needs approval) |
| Executable validation (pending steps, entry selection) | |
| §5.6 runtime-expression grammar with version/context gates | |
| Profiles: browser 1.5–1.10, browser-authentication 1.0/1.1 + call 1.0/1.1, runtime supplement 1.0, mock fixtures 1.0 (RFC 8785 request digest) | |

## Package and tooling

- Distribution `openudon-uws`, import package `openudon_uws`. Python **≥ 3.11** (3.10 is EOL), typed (`py.typed`).
- Runtime dependencies (lower-bounded ranges; dev env locked via `uv.lock`):
  - `jsonschema` + `referencing`, using Draft 2020-12 with **overridden `pattern`/`patternProperties` and `format` checks**.
  - `PyYAML` (its parser descends from libyaml, as yaml.v3's does), used only to compose nodes. A **custom resolver
    and converter reproduce `yaml.v3` `resolve.go` and `uws/convert` `yamlNodeToJSONCompatible`**.
- Dev: `uv`, `hatchling`, `pytest`, `mypy --strict`, `ruff`.
- Schemas ship as package data from `src/openudon_uws/_schemas/`; there is one copy on disk and no generated
  module. Testdata stays out of the wheel.

```text
src/openudon_uws/
  __init__.py        public API (below)
  diagnostics.py     Diagnostic(path, message, layer), ValidationResult, DecodeError
  strict/            json.py, yaml.py (yaml.v3 resolver), __init__.py decode_document
  versions.py        published list, exact admission, semver compare/gates
  schema/            __init__.py (validator cache per version), goregex.py, goformats.py (net/url port, date-time)
  semantic/          index.py, graph.py, operation.py, workflow.py, trigger.py, version.py, params.py
  executable.py
  expressions.py     §5.6 parser
  profiles/          common.py, browser.py, authentication.py, runtime.py, mock_fixtures.py, jcs.py
  _schemas/          vendored upstream versions/*.json (never edited)
vendor/              UPSTREAM.json, testdata/  (never edited)
tests/               pytest per module + gates; tests/vectors/ hand-written adversarial cases
tools/go-oracle/     copied from uws-ts (module path → github.com/OpenUdon/uws-py/tools/go-oracle)
scripts/             provenance.py (--write/--check), gate.sh
docs/                API.md, divergences.md
.github/workflows/ci.yml
```

Public API (snake_case mirror of uws-ts): `validate_document(input, *, format="auto", reject_duplicate_keys=False,
max_bytes=16 MiB)`, `validate_decoded_document`, `validate_executable_document`, `parse_expression(text, *,
version, field, in_loop)`, `validate_browser_profile`, `validate_browser_authentication_profile`,
`validate_browser_authentication_call(…, profile=…)`, `validate_runtime_supplement`, `validate_mock_fixtures`,
`canonical_json`, `request_digest`. `input` accepts `bytes | str | parsed value`.

## Go-parity rules to design in (from uws-ts review + Python specifics)

1. **Regex (Python-specific):** schema patterns run on Go RE2 semantics. `goregex.py` translates the vendored
   subset as follows. `\d` → `[0-9]`, `\s`/`\S` → `[\t\n\f\r ]` / `[^\t\n\f\r ]`, and an unescaped `$` outside a
   character class → `\Z`. Patterns are compiled with `re.ASCII`. A test fails if a vendored pattern uses syntax
   outside the supported subset.
2. **Formats:** port Go `net/url.Parse` and Go 1.25 `parseHost`/`unescape` (host `%XX` < 0x80 rejected
   except `%25`, IPv6 zone handling, `::ffff:7f00:1` loopback) and santhosh-tekuri `uri`, `uri-reference`, `date-time`.
   Do not use `urllib.parse`. The same 28 URL edge cases the TS session verified go into `tests/vectors/`.
3. **JSON decoding:** use `object_pairs_hook` for duplicates (last wins by default, opt-in reject). Reject
   `NaN`/`Infinity` and **overflow like `1e400`** (Python yields `inf`, while Go errors). Treat the BOM the same for
   `bytes` and `str` input. Match Go on lone surrogates and on depth and trailing data, with all of these confirmed
   against the oracle.
4. **YAML:** duplicate keys refused, and a resolver matching yaml.v3. Examples: `0777` is octal,
   `yes`/`on` stay strings, plus yaml.v3's timestamp/float/null forms. As in yaml.v3, only the first document is read. YAML→JSON normalization ports
   `uws/convert/convert.go`. This closes TS divergence #4 instead of re-recording it.
5. **Auto format:** follow the fixed uws-ts behavior / Go's choice so a YAML flow mapping `{uws: 1.13.0, …}` is
   accepted.
6. **Type checks:** use `type(x) is dict`/`is str`/`is bool`. `bool` is a subclass of `int` and non-string YAML
   keys exist, so never rely on `isinstance(x, int)` or duck-typed `in`. Python dicts have no prototype keys, but
   `constructor`/`toString`/`__class__` vectors are still added.
7. **String functions:** Go `strings.TrimSpace` uses `unicode.IsSpace`, which differs from `str.strip()` (U+FEFF,
   U+001C–1F), so use an explicit set. Lengths measured in bytes in Go use `len(s.encode())`.
8. **RFC 8785:** ECMAScript number serialization (not `repr`). Apply the 1 MiB canonical-request bound to the same
   bytes Go bounds, checked by an oracle `request-digest` kind.

## Conformance gates (`scripts/gate.sh`, all required in CI)

| Gate | Proves |
|---|---|
| G0 provenance | vendored files match `UPSTREAM.json`, conformance digest `51ea40b3…`, and package-data schemas equal vendor |
| G1 static | `ruff check`, `ruff format --check`, `mypy --strict` |
| G2 unit | pytest per module, including `tests/vectors/` adversarial cases |
| G3 upstream corpus | `conformance/1.11.0.json` `expression.parse` and `document.validate` match `expected`. `document.execute` is reported as out of scope, never silently passed |
| G4 valid/invalid | every `testdata/invalid/*` is rejected, and every example plus `sample.uws.json` is accepted |
| G5 profile corpora | browser, authentication, registration and mock-fixture vectors match upstream expectations |
| G6 differential | Python verdict equals Go oracle verdict for every corpus file, adversarial vector and seeded mutation (documents, executable, YAML, expressions, profiles, request digest) |

Oracle harness, with the lessons from the TS review:
- One **session-scoped pytest fixture** builds the oracle before any test runs.
- The binary is cached under a key made from `main.go`, `go.mod` and `go.sum` hashes, so it never goes stale.
- With `UWS_REQUIRE_ORACLE=1`, a missing oracle **fails** the run instead of skipping it.
- Temporary files use `tmp_path_factory` (cleaned up automatically).
- Mutations use a seeded PRNG (`UWS_SEED`, `UWS_MUTATIONS`).

## Milestones (each ends green on `scripts/gate.sh` and is pushed to `main` of OpenUdon/uws-py)

1. **M1 scaffold, vendoring, strict decoding:** pyproject, uv lock, CI, PLAN.md, provenance, and strict
   JSON/YAML with the yaml.v3 resolver (G0–G2).
2. **M2 Go oracle early:** copy and adapt `tools/go-oracle`, add the pytest fixture and differential tests for
   decoding (G6 for decoding). Building the oracle before semantics lets every later milestone be checked against Go
   as it lands.
3. **M3 versions + schema:** exact admission, jsonschema with Go regex and Go formats, URL vectors (G3/G4 partial).
4. **M4 semantic + executable:** port `uws1` validation, using `validation*.go`, `executable_validation.go` and
   `pending_step.go` (G4 complete, G6 documents).
5. **M5 expressions:** the §5.6 parser and version/context gates (G3 complete, plus a 6,000-case differential).
6. **M6 profiles:** browser, authentication and call, runtime and mock fixtures with JCS (G5, G6 profiles).
7. **M7 release prep:** README, `docs/API.md`, `docs/divergences.md`, diagnostic codes, and wheel/sdist build
   checks. Nothing is published to PyPI without approval.

## Verification

- `uv sync && UWS_REQUIRE_ORACLE=1 scripts/gate.sh` passes locally: all of G0–G6, with zero oracle mismatches
  reported per kind.
- Removing the built oracle while `UWS_REQUIRE_ORACLE=1` is set makes the run **fail**, not skip.
- `uv build`, then a smoke test that imports `openudon_uws` from the wheel in a fresh venv and validates
  `vendor/testdata/sample.uws.json`.
- The CI workflow runs on push to `main` with Python 3.11 and 3.13 plus Go from `tools/go-oracle/go.mod`.
- Afterwards, a cross-check report shows the Python and TS verdicts on the shared adversarial vectors. Any
  disagreement with Go in uws-ts is reported to the user, not fixed in uws-ts from this session.

## Status (2026-11-03)

- M1–M7 are complete on `main`. Every layer is checked against the Go oracle, and all differentials report zero
  mismatches:
  - Decoding (core and profile): exact decoded values.
  - URLs, formats, patterns and keywords.
  - Struct decoding: a reflected model of the Go types.
  - Semantic and executable layers: 3,000 seeded mutations as JSON and YAML.
  - Expressions: 8,000 cases, compared by verdict and error kind.
  - Profiles: about 12,000 mutations.
  - Request digests.
- Second deep review (2026-11-04) remediated: yaml.v3 merge rules and key escapes, linear duplicate-key
  detection for profiles, auto mode falls back to YAML only for malformed JSON, Go's per-byte UTF-8 replacement,
  JSON-shape checks for decoded input, and a feature-rich seed plus browser-version fan-out in the G6 corpora.
- Open: stable diagnostic codes (messages are not a stable API yet, as in uws-ts) and publishing to PyPI, which
  needs approval.
