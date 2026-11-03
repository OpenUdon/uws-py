# uws-py

`openudon-uws` is a Python validator for the Udon Workflow Specification (UWS) 1.x. It ports the validation
layers of the Go reference implementation ([`OpenUdon/uws`](https://github.com/OpenUdon/uws)) and is developed
in parallel with the TypeScript port ([`OpenUdon/uws-ts`](https://github.com/OpenUdon/uws-ts)). Accept/reject
verdicts are checked against the Go validator by a differential oracle.

See [PLAN.md](PLAN.md) for scope, layout, conformance gates and milestones.

## Development

```bash
uv sync
scripts/gate.sh
```

Gate G6 builds `tools/go-oracle` with Go; set `UWS_REQUIRE_ORACLE=1` to fail instead of skip when Go is missing.
