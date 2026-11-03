# API reference

All entry points are importable from `openudon_uws`. Validators return a `ValidationResult`:

```python
@dataclass
class ValidationResult:
    valid: bool                     # the accept/reject verdict compared against Go (gate G6)
    diagnostics: list[Diagnostic]   # empty when valid

@dataclass(frozen=True)
class Diagnostic:
    path: str      # Go validator form, e.g. "operations[0].request"; "" for document-level problems
    message: str   # human readable; not a stable API
    layer: str     # "decode" | "version" | "schema" | "semantic" | "executable" | "expression" | "profile"
```

`ValidationResult` is truthy exactly when `valid` is true. Message text is not a stable API; match on `valid`, `layer`
and `path`.

## Core documents

| Function | Purpose |
|---|---|
| `validate_document(data, *, format="auto", reject_duplicate_keys=False, max_bytes=16 MiB)` | Decode, select the schema for the exact declared version, validate the raw document against it (formats asserted), apply Go's struct decoding, then the semantic layer. `data` is `str`, `bytes` or an already decoded value. |
| `validate_document_file(path)` | The same, choosing JSON or YAML by extension (`.json`, `.yaml`, `.yml`) exactly as Go does. |
| `validate_decoded_document(value)` | Validate an already decoded value. |
| `validate_executable_document(data, *, format="auto")` | Ordinary validation, then the checks a runtime applies before execution (`uws1.(*Document).ValidateExecutable`). |
| `validate_executable_checks(value)` | Only the executable checks, for a value that already passed ordinary validation. |
| `validate_semantics(value)` | Only the semantic layer (`uws1.(*Document).Validate`). |
| `decode_document(data, *, format="auto", reject_duplicate_keys=False, max_bytes=16 MiB)` | Strict decoding only; raises `DecodeError`. |

Decoding follows Go:

- **JSON** uses `encoding/json` rules:
  - The last duplicate member wins.
  - A byte order mark, trailing values, and numbers outside float64 range are refused.
  - Invalid UTF-8 and unpaired surrogate escapes become U+FFFD.
  - Nesting is limited to 10,000 levels.
- **YAML** follows yaml.v3 resolution and `uws/convert`:
  - Only the first document is read.
  - Mapping keys must be strings, and duplicate keys are refused.
  - `0777` is octal, and `yes`/`on` stay strings.
  - Unquoted timestamps become RFC 3339 text and validate as Go `time.Time` in typed schema positions.
- **`format="auto"`** tries JSON for input starting with `{` or `[` and falls back to YAML, so `{uws: 1.13.0}` is accepted.

## Runtime expressions (§5.6)

| Function | Purpose |
|---|---|
| `parse_expression(text, *, version="", field="value", in_loop=False)` | Admit one expression (upstream `expressions.Parse`). `field` is `"value"`, `"predicate"` (`when`, `await` waits), `"wait"` (non-await delay) or `"batchSize"`. Returns `ParsedExpression`; raises `ExpressionError` with `kind` `"syntax"`, `"version"`, `"context"` or `"limit"`. An empty `version` means 1.12.0, as upstream. |
| `is_valid_expression(...)` | Boolean form of `parse_expression`. |
| `parse_pointer(text)` | Validate and decode a `#`-prefixed JSON Pointer fragment (upstream `expressions.Pointer`). |

Numeric literals are returned as `JsonNumber` (a `str` subclass holding the literal text, like Go's `json.Number`).

## Profiles

| Function | Profiles |
|---|---|
| `validate_browser_profile(data)` | `uws.browser.1.5` … `uws.browser.1.10` (contexts, 1.8 and 1.9+ parameter templates) |
| `validate_browser_authentication_profile(data)` | `uws.browser-authentication.1.0` / `1.1` (1 MiB bound, safe origins, slots, challenges, contexts) |
| `validate_browser_authentication_call(data, profile="uws.browser-authentication-call.1.0")` | Call envelope (`x-uws-browser-authentication`) for an explicitly selected supplement version |
| `validate_runtime_supplement(data)` | `x-uws-runtime` envelope against the Runtime Supplement 1.0 schema |
| `validate_mock_fixtures(data)` | Mock fixtures 1.0, as `mockruntime.DecodeFixtures` (strict JSON, schema, unique keys, redaction metadata) |

Profiles decode as upstream `decodeSchemaDocument`: exactly one YAML (or JSON) document, decoded by yaml.v3 into
`any` and re-encoded as JSON.

## Mock fixture request digest

| Function | Purpose |
|---|---|
| `canonicalize_request(request)` | RFC 8785 text of a resolved request (`mockruntime.CanonicalizeRequest`). Numbers are float64, members sorted by UTF-16 code units, and the 1 MiB bound applies to Go's `json.Marshal` length. `None` means `{}`. Raises `CanonicalizationError`. |
| `request_digest(request)` | `sha256:` digest of the canonical request (`mockruntime.RequestDigest`). |
| `canonical_json(value)` | RFC 8785 text of any JSON value. |
