# Recorded divergences

Differences between UWS prose, the Go reference implementation (`OpenUdon/uws` at the pinned commit) and this
package. Accept/reject parity with Go is the default; every intentional difference is listed here.

| # | Area | Go behavior | uws-py behavior | Reason |
|---|---|---|---|---|
| 1 | Duplicate JSON members in core documents | Accepted; the last value wins (`encoding/json`) | Same by default; `reject_duplicate_keys=True` refuses them | Verdict parity by default, with an opt-in strict mode |
| 2 | YAML anchor whose value contains an alias to itself | `convert.yamlNodeToJSONCompatible` recurses without bound and the process crashes | Refused with a decode error | A crash is not a verdict |
| 3 | YAML alias expansion while converting nodes | No limit in the node conversion (exponential "billion laughs" input does not terminate) | yaml.v3's decoder limit ("document contains excessive aliasing") is applied to the conversion | Go applies this limit when it re-reads the YAML for the schema layer; applying it earlier keeps decoding bounded |
| 4 | `format="auto"` for in-memory input | Not applicable: Go chooses the decoder by file extension (`.json`, `.yaml`/`.yml`) | Input that starts with `{` or `[` is tried as JSON first, then as YAML; anything else is YAML. `validate_document_file` uses Go's extension rule | In-memory input has no extension |
