# uws-py

`openudon-uws` is a Python validator for the Udon Workflow Specification (UWS) 1.x. It ports the validation
layers of the Go reference implementation ([`OpenUdon/uws`](https://github.com/OpenUdon/uws)) and is developed
in parallel with the TypeScript port ([`OpenUdon/uws-ts`](https://github.com/OpenUdon/uws-ts)). Accept/reject
verdicts are checked against the Go validator by a differential oracle.

It covers:

- Strict JSON/YAML decoding.
- Validation against the schema for the exact declared version (1.0.0–1.13.0).
- Go's struct decoding, plus semantic and executable validation.
- §5.6 runtime expressions.
- Browser, browser-authentication, runtime-supplement and mock-fixture profiles, including the RFC 8785 request
  digest.

It is a validator, not an orchestrator.

```python
from openudon_uws import validate_document_file, parse_expression

result = validate_document_file("workflow.uws.yaml")
if not result.valid:
    for d in result.diagnostics:
        print(d.layer, d.path, d.message)

parse_expression("$steps.fetch.outputs.items", version="1.13.0", field="value")
```

Requires Python ≥ 3.11. Runtime dependencies: `jsonschema`, `referencing` and `PyYAML`. See
[docs/API.md](docs/API.md) for the API and [docs/divergences.md](docs/divergences.md) for the recorded differences
from Go.

## Development

```bash
uv sync
scripts/gate.sh
```

The gates are:

| Gate | Checks |
|---|---|
| G0 | Provenance of the vendored upstream files |
| G1 | `ruff` and `mypy --strict` |
| G2–G5 | Unit tests, the upstream corpora and the profile corpora |
| G6 | The Go differential |

G6 builds `tools/go-oracle` with Go (the toolchain from its `go.mod` is fetched automatically). Set
`UWS_REQUIRE_ORACLE=1`, as CI does, to fail instead of skip when Go is missing. `UWS_MUTATIONS`,
`UWS_PROFILE_MUTATIONS` and `UWS_SEED` size and seed the generated corpora.

See [PLAN.md](PLAN.md) for scope, layout and milestones. Nothing is published to PyPI yet.
