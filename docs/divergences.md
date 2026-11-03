# Recorded divergences

Differences between UWS prose, the Go reference implementation (`OpenUdon/uws` at the pinned commit) and this
package. Accept/reject parity with Go is the default; every intentional difference is listed here.

| # | Area | Go behavior | uws-py behavior | Reason |
|---|---|---|---|---|
| 1 | Duplicate JSON members in core documents | Accepted; the last value wins (`encoding/json`) | Same by default; `reject_duplicate_keys=True` refuses them | Verdict parity by default, with an opt-in strict mode |
| 2 | YAML anchor whose value contains an alias to itself | `convert.yamlNodeToJSONCompatible` recurses without bound and the process crashes | Refused with a decode error | A crash is not a verdict |
| 3 | YAML alias expansion while converting nodes | No limit in the node conversion (exponential "billion laughs" input does not terminate) | yaml.v3's decoder limit ("document contains excessive aliasing") is applied to the conversion | Go applies this limit when it re-reads the YAML for the schema layer; applying it earlier keeps decoding bounded |
| 4 | `format="auto"` for in-memory input | Not applicable: Go chooses the decoder by file extension (`.json`, `.yaml`/`.yml`) | Input that starts with `{` or `[` is tried as JSON first, then as YAML; anything else is YAML. `validate_document_file` uses Go's extension rule | In-memory input has no extension |
| 5 | YAML nesting depth | yaml.v3 accepts deeply nested YAML (thousands of levels) | YAML nested more than about 500 levels is refused ("nesting is too deep"); PyYAML's composer is recursive | No UWS construct nests that deep; JSON input follows Go's 10000-level limit exactly on every supported Python version |
| 6 | Mock fixture validation entry point | `schemas.ValidateMockFixtures` is schema-only; `mockruntime.DecodeFixtures` adds the semantic MUSTs | `validate_mock_fixtures` matches `DecodeFixtures` (the conforming reader) | The schema-only API accepts spec-invalid documents (the same choice as uws-ts) |
| 7 | Runtime Supplement 1.0 | No semantic validator; the reader is lenient | `validate_runtime_supplement` applies the published schema to the `x-uws-runtime` envelope | The schema is the only published contract (the same choice as uws-ts) |
