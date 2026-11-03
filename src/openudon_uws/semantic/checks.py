"""Object validators (``validation.go``, ``validation_operation.go``, ``validation_workflow.go``,
``validation_action.go``, ``validation_trigger.go``, ``param_schema.go``, ``pending_step.go``, ``content_trust.go``,
``validation_version.go``)."""

from __future__ import annotations

from typing import Any

from ..diagnostics import Collector
from ..gostrings import go_atoi, go_trim_space
from ..versions import supports_version_at_least
from .access import (
    CONSTRUCT_ID,
    DOTTED_NAME,
    Obj,
    Walk,
    effective_source_type,
    get,
    is_obj,
    items,
    map_keys,
    map_value,
    matches,
    num,
    present,
    quote,
    s,
    strings,
    walk_workflow_tree,
)
from .index import DocumentIndex

WORKFLOW_TYPES = frozenset({"sequence", "parallel", "switch", "merge", "loop", "await"})
RESULT_KINDS = frozenset({"switch", "merge", "loop"})
SOURCE_TYPES = frozenset(
    {
        "",
        "openapi",
        "google-discovery",
        "aws-smithy",
        "asyncapi",
        "graphql",
        "openrpc",
        "grpc-protobuf",
        "odata",
        "browser-profile",
        "ansible-module",
    }
)
GENERIC_SOURCE_TYPES = SOURCE_TYPES - {"", "openapi"}
STANDARD_REQUEST_KEYS = frozenset({"path", "query", "header", "cookie", "body"})
CRITERION_TYPES = frozenset({"simple", "regex", "jsonpath", "xpath"})
CONTENT_TRUST_LEVELS = frozenset({"unknown", "trusted", "untrusted"})


def validate_operation_effect(effect: str, path: str, version: str, r: Collector) -> None:
    if effect == "":
        return
    if not supports_version_at_least(version, 1, 12, 0):
        r.add(path, "requires UWS 1.12.0 or later")
        return
    if effect not in ("read", "write", "unknown"):
        r.add(path, f"{quote(effect)} is not valid (must be read, write, or unknown)")


def validate_request(request: Any, path: str, r: Collector) -> None:
    if not is_obj(request):
        return
    for key, value in request.items():
        if key.startswith("x-"):
            continue
        if key not in STANDARD_REQUEST_KEYS:
            r.add(
                f"{path}.{key}", "is not a standard request binding key; use path, query, header, cookie, body, or x-*"
            )
            continue
        if key != "body" and not is_obj(value):
            r.add(f"{path}.{key}", "must be an object")


def validate_dependency_list(deps: list[str], path: str, idx: DocumentIndex, r: Collector) -> None:
    for i, dep in enumerate(deps):
        if dep == "":
            r.add(f"{path}[{i}]", "is required")
        elif not idx.has_executable_id(dep) and dep not in idx.parallel_groups:
            r.add(f"{path}[{i}]", f"references unknown dependency {quote(dep)}")


def validate_outputs(owner: Obj, path: str, version: str, r: Collector) -> None:
    pattern = CONSTRUCT_ID if supports_version_at_least(version, 1, 10, 0) else DOTTED_NAME
    for key in map_keys(owner, "outputs"):
        if not matches(pattern, key):
            r.add(f"{path}.{key}", f"output name {quote(key)} is not valid")


def validate_criteria(criteria: list[Obj | None], path: str, r: Collector) -> None:
    for i, c in enumerate(criteria):
        p = f"{path}[{i}]"
        if c is None:
            r.add(p, "is nil")
            continue
        if s(c, "condition") == "":
            r.add(f"{p}.condition", "is required")
        typ = s(c, "type")
        if typ != "" and typ not in CRITERION_TYPES:
            r.add(f"{p}.type", f"{quote(typ)} is not valid (must be simple, regex, jsonpath, or xpath)")
        if typ not in ("", "simple") and s(c, "context") == "":
            r.add(f"{p}.context", "is required when type is regex, jsonpath, or xpath")


def validate_goto_target(a: Obj, path: str, idx: DocumentIndex, r: Collector) -> None:
    typ, workflow_id, step_id = s(a, "type"), s(a, "workflowId"), s(a, "stepId")
    if typ != "goto":
        if workflow_id or step_id:
            r.add(path, "workflowId/stepId are only valid for goto actions")
        return
    if not workflow_id and not step_id:
        r.add(path, "goto requires workflowId or stepId")
        return
    if workflow_id and step_id:
        r.add(path, "goto cannot specify both workflowId and stepId")
    if workflow_id and workflow_id not in idx.workflows:
        r.add(f"{path}.workflowId", f"references unknown workflowId {quote(workflow_id)}")
    if step_id and step_id not in idx.steps:
        r.add(f"{path}.stepId", f"references unknown stepId {quote(step_id)}")


def validate_actions(actions: list[Obj | None], path: str, failure: bool, idx: DocumentIndex, r: Collector) -> None:
    valid_types = ("end", "goto", "retry") if failure else ("end", "goto")
    type_list = "end, goto, or retry" if failure else "end or goto"
    seen: set[str] = set()
    for i, a in enumerate(actions):
        p = f"{path}[{i}]"
        if a is None:
            r.add(p, "is nil")
            continue
        name, typ = s(a, "name"), s(a, "type")
        if not name:
            r.add(f"{p}.name", "is required")
        if not typ:
            r.add(f"{p}.type", "is required")
        elif typ not in valid_types:
            r.add(f"{p}.type", f"{quote(typ)} is not valid (must be {type_list})")
        validate_goto_target(a, p, idx, r)
        if name:
            if name in seen:
                r.add(f"{p}.name", f"duplicate action name {quote(name)}")
            seen.add(name)
        if failure:
            if typ == "retry" and (num(a, "retryLimit") or 0) <= 0:
                r.add(p, "retry requires retryLimit > 0")
            if (num(a, "retryAfter") or 0) < 0:
                r.add(f"{p}.retryAfter", "must be non-negative")
        validate_criteria(items(a, "criteria"), f"{p}.criteria", r)


def validate_param_schema(schema: Any, path: str, r: Collector) -> None:
    if not is_obj(schema):
        return
    props = map_value(schema, "properties")
    if props is not None:
        for name, child in props.items():
            if name == "":
                r.add(f"{path}.properties", "property names must be non-empty")
                continue
            if not is_obj(child):
                r.add(f"{path}.properties.{name}", "is nil")
                continue
            validate_param_schema(child, f"{path}.properties.{name}", r)
    seen: set[str] = set()
    for i, name in enumerate(strings(schema, "required")):
        p = f"{path}.required[{i}]"
        if name == "":
            r.add(p, "is required")
            continue
        if name in seen:
            r.add(p, f"duplicate required entry {quote(name)}")
            continue
        seen.add(name)
        if present(schema, "properties") and not (props is not None and name in props):
            r.add(p, f"references unknown property {quote(name)}")
    if is_obj(get(schema, "items")):
        validate_param_schema(schema["items"], f"{path}.items", r)
    for key in ("allOf", "oneOf", "anyOf"):
        for i, child in enumerate(items(schema, key)):
            if child is None:
                r.add(f"{path}.{key}[{i}]", "is nil")
            else:
                validate_param_schema(child, f"{path}.{key}[{i}]", r)


def validate_structural_type_fields(typ: str, owner: Obj, path: str, r: Collector) -> None:
    item_expr = go_trim_space(s(owner, "items"))
    if typ == "loop":
        if item_expr == "":
            r.add(f"{path}.items", f"is required for {typ}")
    elif item_expr != "":
        r.add(f"{path}.items", f"is not valid on {typ}")
    if typ == "await" and go_trim_space(s(owner, "wait")) == "":
        r.add(f"{path}.wait", f"is required for {typ}")
    if items(owner, "cases") and typ != "switch":
        r.add(f"{path}.cases", f"is not valid on {typ}")
    if items(owner, "default") and typ != "switch":
        r.add(f"{path}.default", f"is not valid on {typ}")


def validate_operation(op: Obj, path: str, idx: DocumentIndex, r: Collector) -> None:
    if not s(op, "operationId"):
        r.add(f"{path}.operationId", "is required")
    validate_operation_effect(s(op, "effect"), f"{path}.effect", idx.uws, r)
    source = s(op, "sourceDescription")
    has_source_id = s(op, "sourceOperationId") != ""
    has_source_ref = s(op, "sourceOperationRef") != ""
    has_openapi_id = s(op, "openapiOperationId") != ""
    has_openapi_ref = s(op, "openapiOperationRef") != ""
    has_generic = has_source_id or has_source_ref
    has_legacy = has_openapi_id or has_openapi_ref
    has_any_binding = source != "" or has_generic or has_legacy
    profile_value = get(op, "x-uws-operation-profile")
    profile = go_trim_space(profile_value) if type(profile_value) is str else ""
    if has_source_id and has_source_ref:
        r.add(path, "cannot specify both sourceOperationId and sourceOperationRef")
    elif has_openapi_id and has_openapi_ref:
        r.add(path, "cannot specify both openapiOperationId and openapiOperationRef")
    elif has_generic and has_legacy:
        r.add(path, "cannot mix sourceOperationId/sourceOperationRef with openapiOperationId/openapiOperationRef")
    elif has_any_binding:
        if source == "":
            r.add(
                f"{path}.sourceDescription",
                "is required for OpenAPI-bound operations"
                if has_legacy and not has_generic
                else "is required for source-bound operations",
            )
        elif source not in idx.source_descriptions:
            r.add(f"{path}.sourceDescription", f"references unknown sourceDescription {quote(source)}")
        else:
            source_type = idx.source_descriptions[source]
            if source_type in ("", "openapi"):
                if not has_generic and not has_legacy:
                    r.add(
                        path,
                        "requires exactly one of sourceOperationId, sourceOperationRef, openapiOperationId, or "
                        "openapiOperationRef for OpenAPI-bound operations",
                    )
            elif source_type in GENERIC_SOURCE_TYPES:
                if has_legacy:
                    r.add(
                        path,
                        f"{source_type} sourceDescriptions require sourceOperationId or sourceOperationRef, not "
                        "openapiOperationId or openapiOperationRef",
                    )
                if not has_generic:
                    r.add(
                        path,
                        f"{source_type} sourceDescriptions require exactly one of sourceOperationId or "
                        "sourceOperationRef",
                    )
        if has_source_ref and not s(op, "sourceOperationRef").startswith("#/"):
            r.add(f"{path}.sourceOperationRef", "must be a JSON Pointer fragment beginning with #/")
        if has_openapi_ref and not s(op, "openapiOperationRef").startswith("#/"):
            r.add(f"{path}.openapiOperationRef", "must be a JSON Pointer fragment beginning with #/")
    elif profile == "":
        r.add(path, "requires a source binding or x-uws-operation-profile for extension-owned operations")
    validate_request(get(op, "request"), f"{path}.request", r)
    validate_dependency_list(strings(op, "dependsOn"), f"{path}.dependsOn", idx, r)
    validate_criteria(items(op, "successCriteria"), f"{path}.successCriteria", r)
    validate_actions(items(op, "onFailure"), f"{path}.onFailure", True, idx, r)
    validate_actions(items(op, "onSuccess"), f"{path}.onSuccess", False, idx, r)
    validate_outputs(op, f"{path}.outputs", idx.uws, r)


def validate_pending(pending: Obj, path: str, version: str, r: Collector) -> None:
    if not supports_version_at_least(version, 1, 12, 0):
        r.add(path, "requires UWS 1.12.0 or later")
    if go_trim_space(s(pending, "purpose")) == "":
        r.add(f"{path}.purpose", "is required")
    for key in ("inputs", "outputs"):
        schema = get(pending, key)
        if not is_obj(schema):
            r.add(f"{path}.{key}", "is required")
            continue
        if s(schema, "type") != "object":
            r.add(f"{path}.{key}.type", 'must be "object" for a pending-step field set')
        validate_param_schema(schema, f"{path}.{key}", r)
    effect = s(pending, "effect")
    if effect == "":
        r.add(f"{path}.effect", "is required")
    else:
        validate_operation_effect(effect, f"{path}.effect", "1.12.0", r)


def validate_step(st: Obj, path: str, idx: DocumentIndex, r: Collector) -> None:
    step_id = s(st, "stepId")
    if not step_id:
        r.add(f"{path}.stepId", "is required")
    elif not matches(CONSTRUCT_ID, step_id):
        r.add(f"{path}.stepId", f"must match pattern ^[A-Za-z0-9_-]+$; got {step_id}")
    operation_ref, workflow, typ = s(st, "operationRef"), s(st, "workflow"), s(st, "type")
    has_op, has_wf, has_type = operation_ref != "", workflow != "", typ != ""
    has_nested = present(st, "steps") or present(st, "cases") or present(st, "default")
    if has_op and has_wf:
        r.add(path, "cannot specify both operationRef and workflow")
    elif has_op and has_type:
        r.add(path, "operationRef cannot be combined with structural type")
    elif has_wf and has_type:
        r.add(path, "workflow cannot be combined with structural type")
    if present(st, "pending"):
        pending = map_value(st, "pending")
        if pending is not None:
            validate_pending(pending, f"{path}.pending", idx.uws, r)
        executable = (
            has_op
            or has_wf
            or has_type
            or present(st, "body")
            or present(st, "inputs")
            or any(s(st, k) != "" for k in ("when", "forEach", "wait", "parallelGroup", "items", "mode", "batchSize"))
            or num(st, "timeout") is not None
            or has_nested
            or present(st, "outputs")
        )
        if executable:
            r.add(path, "pending contract cannot be combined with executable or structural fields")
    if has_op and has_nested and supports_version_at_least(idx.uws, 1, 9, 2):
        r.add(path, "operation-reference steps cannot also declare nested child blocks")
    if has_wf and has_nested and supports_version_at_least(idx.uws, 1, 9, 2):
        r.add(path, "workflow-reference steps cannot also declare nested child blocks")
    if present(st, "inputs") and not supports_version_at_least(idx.uws, 1, 5):
        r.add(f"{path}.inputs", "requires UWS 1.5.0 or later")
    if has_type:
        if typ not in WORKFLOW_TYPES:
            r.add(f"{path}.type", f"{quote(typ)} is not valid")
        else:
            validate_structural_type_fields(typ, st, path, r)
            if typ == "merge" and not strings(st, "dependsOn"):
                r.add(f"{path}.dependsOn", "is required and must name at least one upstream construct for merge")
    if has_op and operation_ref not in idx.operations:
        r.add(f"{path}.operationRef", f"references unknown operationId {quote(operation_ref)}")
    if has_wf and not has_op and not has_type and workflow not in idx.workflows:
        r.add(f"{path}.workflow", f"references unknown workflowId {quote(workflow)}")
    validate_dependency_list(strings(st, "dependsOn"), f"{path}.dependsOn", idx, r)
    validate_outputs(st, f"{path}.outputs", idx.uws, r)


def validate_workflow(w: Obj, path: str, idx: DocumentIndex, r: Collector) -> None:
    ident = s(w, "workflowId")
    if not ident:
        r.add(f"{path}.workflowId", "is required")
    elif not matches(CONSTRUCT_ID, ident):
        r.add(f"{path}.workflowId", f"must match pattern ^[A-Za-z0-9_-]+$; got {ident}")
    typ = s(w, "type")
    if not typ:
        r.add(f"{path}.type", "is required")
    elif typ not in WORKFLOW_TYPES:
        r.add(f"{path}.type", f"{quote(typ)} is not valid")
    else:
        validate_structural_type_fields(typ, w, path, r)
        if typ == "merge" and not strings(w, "dependsOn"):
            r.add(f"{path}.dependsOn", "is required and must name at least one upstream construct for merge")
    validate_dependency_list(strings(w, "dependsOn"), f"{path}.dependsOn", idx, r)
    validate_outputs(w, f"{path}.outputs", idx.uws, r)
    validate_param_schema(get(w, "inputs"), f"{path}.inputs", r)

    def case(p: str, c: Obj) -> None:
        if not s(c, "name"):
            r.add(f"{p}.name", "is required")

    walk_workflow_tree(w, path, Walk(step=lambda p, st: validate_step(st, p, idx, r), case=case))


def _resolve_trigger_output(output: str, output_list: list[str], outputs: set[str]) -> bool:
    if output in outputs:
        return True
    index = go_atoi(output)
    return index is not None and 0 <= index < len(output_list)


def validate_trigger(t: Obj, path: str, idx: DocumentIndex, r: Collector) -> None:
    if not s(t, "triggerId"):
        r.add(f"{path}.triggerId", "is required")
    output_list = strings(t, "outputs")
    outputs: set[str] = set()
    for i, name in enumerate(output_list):
        p = f"{path}.outputs[{i}]"
        if name == "":
            r.add(p, "is required")
        elif not matches(DOTTED_NAME, name):
            r.add(p, f"output name {quote(name)} is not valid")
        elif name in outputs:
            r.add(p, f"duplicate output {quote(name)}")
        else:
            outputs.add(name)
    routes = items(t, "routes")
    if routes and not output_list:
        r.add(f"{path}.outputs", "is required when routes is set")
    for i, route in enumerate(routes):
        p = f"{path}.routes[{i}]"
        if route is None:
            r.add(p, "is nil")
            continue
        output = s(route, "output")
        if output == "":
            r.add(f"{p}.output", "is required")
        elif output_list and not _resolve_trigger_output(output, output_list, outputs):
            r.add(f"{p}.output", f"{quote(output)} is not a declared trigger output")
        to = strings(route, "to")
        if not to:
            r.add(f"{p}.to", "must contain at least one top-level stepId or workflowId")
        for j, target in enumerate(to):
            if target == "":
                r.add(f"{p}.to[{j}]", "is required")
            elif target in idx.workflows:
                continue
            elif idx.has_entry_workflow and target not in idx.entry_workflow_steps:
                r.add(f"{p}.to[{j}]", f"references unknown top-level stepId or workflowId {quote(target)}")


def validate_structural_result(res: Obj, path: str, idx: DocumentIndex, seen: set[str], r: Collector) -> None:
    name = s(res, "name")
    if not name:
        r.add(f"{path}.name", "is required")
    else:
        if not matches(DOTTED_NAME, name):
            r.add(f"{path}.name", f"{quote(name)} is not valid")
        if name in seen:
            r.add(f"{path}.name", f"duplicate result name {quote(name)}")
        seen.add(name)
    kind = s(res, "kind")
    if not kind:
        r.add(f"{path}.kind", "is required")
    elif kind not in RESULT_KINDS:
        r.add(f"{path}.kind", f"{quote(kind)} is not valid")
    source = s(res, "from")
    if not source:
        r.add(f"{path}.from", "is required")
        return
    workflow_id, has_step, step_id = source.partition(".")
    has_step_bool = has_step != ""
    if (
        not workflow_id
        or not matches(CONSTRUCT_ID, workflow_id)
        or (has_step_bool and (not step_id or not matches(CONSTRUCT_ID, step_id)))
        or "." in step_id
    ):
        r.add(f"{path}.from", f"{quote(source)} is not a valid workflowId or workflowId.stepId")
        return
    if workflow_id not in idx.workflows:
        r.add(f"{path}.from", f"references unknown workflowId {quote(workflow_id)}")
        return
    if not has_step_bool:
        resolved = idx.workflow_types.get(workflow_id, "")
    else:
        step_types = idx.workflow_steps.get(workflow_id)
        if step_types is None or step_id not in step_types:
            r.add(f"{path}.from", f"references unknown stepId {quote(step_id)} in workflow {quote(workflow_id)}")
            return
        resolved = step_types[step_id]
        if resolved == "":
            r.add(
                f"{path}.from",
                f"references stepId {quote(step_id)} in workflow {quote(workflow_id)}, but that step is not a "
                "structural construct",
            )
            return
    if kind != "" and resolved != "" and resolved != kind:
        r.add(f"{path}.kind", f"kind {quote(kind)} does not match {quote(source)} type {quote(resolved)}")


def validate_content_trust(doc: Obj, idx: DocumentIndex, r: Collector) -> None:
    if not present(doc, "contentTrust"):
        return
    trust = map_value(doc, "contentTrust") or {}
    if not supports_version_at_least(idx.uws, 1, 9, 1):
        r.add("contentTrust", "requires UWS 1.9.1 or later")
    extensions = [k for k in trust if k.startswith("x-")]
    sds = map_value(trust, "sourceDescriptions") or {}
    ops = map_value(trust, "operations") or {}
    trigs = map_value(trust, "triggers") or {}
    wfs = map_value(trust, "workflows") or {}
    if not any((sds, ops, trigs, wfs)) and not extensions:
        r.add("contentTrust", "must contain at least one declaration or extension")

    def level(value: Any, path: str) -> None:
        if type(value) is not str or value not in CONTENT_TRUST_LEVELS:
            r.add(path, f"{quote(value)} is not valid (must be unknown, trusted, or untrusted)")

    for name in sorted(sds):
        path = f"contentTrust.sourceDescriptions.{name}"
        if name not in idx.source_descriptions:
            r.add(path, f"references unknown sourceDescription {quote(name)}")
        level(sds[name], path)
    for ident in sorted(ops):
        path = f"contentTrust.operations.{ident}"
        op = idx.operations.get(ident)
        if op is None:
            r.add(path, f"references unknown operationId {quote(ident)}")
        decl = ops[ident]
        if not is_obj(decl):
            r.add(path, "is nil")
            continue
        outputs = map_value(decl, "outputs") or {}
        if not s(decl, "default") and not outputs and not any(k.startswith("x-") for k in decl):
            r.add(path, "must contain default, outputs, or an extension")
        if s(decl, "default"):
            level(decl["default"], f"{path}.default")
        for output in sorted(outputs):
            output_path = f"{path}.outputs.{output}"
            if op is not None and output not in map_keys(op, "outputs"):
                r.add(output_path, f"references undeclared operation output {quote(output)}")
            level(outputs[output], output_path)
    for ident in sorted(trigs):
        path = f"contentTrust.triggers.{ident}"
        if ident not in idx.triggers:
            r.add(path, f"references unknown triggerId {quote(ident)}")
        level(trigs[ident], path)
    for ident in sorted(wfs):
        path = f"contentTrust.workflows.{ident}"
        wf = idx.workflows.get(ident)
        if wf is None:
            r.add(path, f"references unknown workflowId {quote(ident)}")
        decl = wfs[ident]
        if not is_obj(decl):
            r.add(path, "is nil")
            continue
        inputs = map_value(decl, "inputs") or {}
        if not s(decl, "default") and not inputs and not any(k.startswith("x-") for k in decl):
            r.add(path, "must contain default, inputs, or an extension")
        if s(decl, "default"):
            level(decl["default"], f"{path}.default")
        for name in sorted(inputs):
            input_path = f"{path}.inputs.{name}"
            if wf is not None:
                props = map_value(map_value(wf, "inputs"), "properties")
                if props is None or name not in props:
                    r.add(input_path, f"references undeclared workflow input {quote(name)}")
            level(inputs[name], input_path)


def validate_versioned_fields(doc: Obj, r: Collector) -> None:
    v = s(doc, "uws")

    def sup(minor: int) -> bool:
        return supports_version_at_least(v, 1, minor)

    minimum = {
        "google-discovery": 2,
        "aws-smithy": 2,
        "asyncapi": 3,
        "graphql": 4,
        "openrpc": 4,
        "grpc-protobuf": 4,
        "odata": 4,
        "browser-profile": 5,
    }
    for i, sd in enumerate(items(doc, "sourceDescriptions")):
        if sd is None:
            continue
        path = f"sourceDescriptions[{i}].type"
        source_type = effective_source_type(sd)
        if source_type in minimum:
            if not sup(minimum[source_type]):
                r.add(path, f"requires UWS 1.{minimum[source_type]}.0 or later")
        elif source_type == "ansible-module":
            if not sup(6):
                r.add(path, "requires UWS 1.6.0 or later")
            elif sup(7):
                r.add(path, "removed in UWS 1.7.0; Ansible automation is not supported by UWS 1.7")

    def timeout(owner: Obj, path: str) -> None:
        t = num(owner, "timeout")
        if t is None:
            return
        if not sup(1):
            r.add(path, "requires UWS 1.1.0 or later")
        elif t <= 0:
            r.add(path, "must be greater than 0")

    for i, op in enumerate(items(doc, "operations")):
        if op is None:
            continue
        timeout(op, f"operations[{i}].timeout")
        if (s(op, "sourceOperationId") or s(op, "sourceOperationRef")) and not sup(2):
            r.add(f"operations[{i}]", "sourceOperationId and sourceOperationRef require UWS 1.2.0 or later")
    for i, wf in enumerate(items(doc, "workflows")):
        if wf is None:
            continue
        path = f"workflows[{i}]"
        timeout(wf, f"{path}.timeout")
        if present(wf, "idempotency"):
            idem = map_value(wf, "idempotency") or {}
            if not sup(1):
                r.add(f"{path}.idempotency", "requires UWS 1.1.0 or later")
            else:
                if go_trim_space(s(idem, "key")) == "":
                    r.add(f"{path}.idempotency.key", "is required")
                on_conflict = s(idem, "onConflict")
                if on_conflict not in ("", "reject", "returnPrevious"):
                    r.add(
                        f"{path}.idempotency.onConflict",
                        f"{quote(on_conflict)} is not valid (must be reject or returnPrevious)",
                    )
                ttl = num(idem, "ttl")
                if ttl is not None and ttl <= 0:
                    r.add(f"{path}.idempotency.ttl", "must be greater than 0")
        walk_workflow_tree(wf, path, Walk(step=lambda p, st: timeout(st, f"{p}.timeout")))


def validate_expression_accessible_names(doc: Obj, r: Collector) -> None:
    if not supports_version_at_least(s(doc, "uws"), 1, 10, 0):
        return
    for name in map_keys(doc, "variables"):
        if not matches(CONSTRUCT_ID, name):
            r.add(f"variables.{name}", f"variable name {quote(name)} is not addressable by the UWS expression grammar")
    for i, wf in enumerate(items(doc, "workflows")):
        if wf is None:
            continue
        for name in map_keys(map_value(wf, "inputs"), "properties"):
            if not matches(CONSTRUCT_ID, name):
                r.add(
                    f"workflows[{i}].inputs.properties.{name}",
                    f"input name {quote(name)} is not addressable by the UWS expression grammar",
                )

        def step_inputs(p: str, st: Obj) -> None:
            for name in map_keys(st, "inputs"):
                if not matches(CONSTRUCT_ID, name):
                    r.add(
                        f"{p}.inputs.{name}",
                        f"input name {quote(name)} is not addressable by the UWS expression grammar",
                    )

        walk_workflow_tree(wf, f"workflows[{i}]", Walk(step=step_inputs))


def document_has_pending_steps(doc: Obj) -> bool:
    found = [False]

    def check(_p: str, st: Obj) -> None:
        if present(st, "pending"):
            found[0] = True

    for wf in items(doc, "workflows"):
        if wf is not None:
            walk_workflow_tree(wf, "", Walk(step=check))
    return found[0]
