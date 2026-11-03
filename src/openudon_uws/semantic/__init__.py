"""Port of the upstream ``uws1`` semantic validation layer (``Document.ValidateResult``).

Upstream: github.com/OpenUdon/uws ``uws1/validation*.go``, ``document_index.go``, ``step_walk.go``,
``param_schema.go``, ``pending_step.go`` and ``content_trust.go`` at the pinned commit. The input is the decoded
JSON value, already checked against the exact-version schema and Go's struct decoding.
"""

from __future__ import annotations

from typing import Any

from ..diagnostics import Collector, ValidationResult
from ..versions import UWS1_VERSION_PATTERN, is_published_uws_version, is_valid_uws_version, supports_version_at_least
from .access import (
    CONSTRUCT_ID,
    DOTTED_NAME,
    SOURCE_NAME,
    is_obj,
    items,
    map_keys,
    map_value,
    matches,
    present,
    quote,
    s,
)
from .checks import (
    SOURCE_TYPES,
    document_has_pending_steps,
    validate_content_trust,
    validate_expression_accessible_names,
    validate_operation,
    validate_structural_result,
    validate_trigger,
    validate_versioned_fields,
    validate_workflow,
)
from .graph import detect_dependency_cycles, detect_workflow_recursion
from .index import build_index

__all__ = ["validate_semantics"]


def validate_semantics(doc: Any) -> ValidationResult:
    """Runs the semantic validation layer over a decoded UWS document."""
    r = Collector("semantic")
    if not is_obj(doc):
        r.add("document", "is required")
        return r.result()
    v = s(doc, "uws")
    if v == "":
        r.add("uws", "version is required")
    elif not UWS1_VERSION_PATTERN.match(v):
        r.add("uws", f"version {quote(v)} does not match pattern 1.x.x")
    elif not is_valid_uws_version(v):
        r.add("uws", f"version {quote(v)} is not valid SemVer")
    elif not is_published_uws_version(v):
        r.add("uws", f"version {quote(v)} is not a published UWS version")

    if not present(doc, "info"):
        r.add("info", "is required")
    else:
        info = map_value(doc, "info")
        if not s(info, "title"):
            r.add("info.title", "is required")
        if not s(info, "version"):
            r.add("info.version", "is required")
    if not items(doc, "operations") and not (
        supports_version_at_least(v, 1, 12, 0) and document_has_pending_steps(doc)
    ):
        r.add("operations", "at least one operation is required")
    if not items(doc, "sourceDescriptions"):
        for i, op in enumerate(items(doc, "operations")):
            if op is not None and s(op, "sourceDescription") != "":
                r.add(
                    "sourceDescriptions",
                    "is required when any operation declares sourceDescription; "
                    f"operations[{i}].sourceDescription is {quote(s(op, 'sourceDescription'))}",
                )
                break
    validate_versioned_fields(doc, r)
    validate_expression_accessible_names(doc, r)

    idx = build_index(doc, r)
    validate_content_trust(doc, idx, r)

    for i, sd in enumerate(items(doc, "sourceDescriptions")):
        if sd is None:
            continue
        path = f"sourceDescriptions[{i}]"
        name = s(sd, "name")
        if not name:
            r.add(f"{path}.name", "is required")
        elif not matches(SOURCE_NAME, name):
            r.add(f"{path}.name", f"must match pattern ^[A-Za-z0-9_-]+$; got {name}")
        if not s(sd, "url"):
            r.add(f"{path}.url", "is required")
        typ = s(sd, "type")
        if typ not in SOURCE_TYPES:
            r.add(
                f"{path}.type",
                f"{quote(typ)} is not valid (must be openapi, google-discovery, aws-smithy, asyncapi, graphql, "
                "openrpc, grpc-protobuf, odata, browser-profile, or ansible-module)",
            )
    for i, op in enumerate(items(doc, "operations")):
        if op is not None:
            validate_operation(op, f"operations[{i}]", idx, r)
    for i, wf in enumerate(items(doc, "workflows")):
        if wf is not None:
            validate_workflow(wf, f"workflows[{i}]", idx, r)
    for i, t in enumerate(items(doc, "triggers")):
        if t is None:
            r.add(f"triggers[{i}]", "is nil")
        else:
            validate_trigger(t, f"triggers[{i}]", idx, r)
    seen_results: set[str] = set()
    for i, res in enumerate(items(doc, "results")):
        if res is None:
            r.add(f"results[{i}]", "is nil")
        else:
            validate_structural_result(res, f"results[{i}]", idx, seen_results, r)
    if present(doc, "components"):
        pattern = CONSTRUCT_ID if supports_version_at_least(v, 1, 10, 0) else DOTTED_NAME
        for name in map_keys(map_value(doc, "components"), "variables"):
            if not matches(pattern, name):
                r.add(f"components.variables.{name}", f"component name {quote(name)} is not valid")
    detect_dependency_cycles(idx, r)
    detect_workflow_recursion(idx, r)
    return r.result()
