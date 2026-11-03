"""Document index (``document_index.go``)."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..diagnostics import Collector
from .access import Obj, Walk, effective_source_type, items, quote, s, strings, walk_workflow_tree


@dataclass
class WorkflowCall:
    target: str
    path: str


@dataclass
class DocumentIndex:
    uws: str = ""
    operations: dict[str, Obj] = field(default_factory=dict)
    workflows: dict[str, Obj] = field(default_factory=dict)
    workflow_types: dict[str, str] = field(default_factory=dict)
    workflow_steps: dict[str, dict[str, str]] = field(default_factory=dict)
    has_entry_workflow: bool = False
    entry_workflow_steps: set[str] = field(default_factory=set)
    steps: dict[str, Obj] = field(default_factory=dict)
    triggers: set[str] = field(default_factory=set)
    parallel_groups: set[str] = field(default_factory=set)
    parallel_group_members: dict[str, list[str]] = field(default_factory=dict)
    source_descriptions: dict[str, str] = field(default_factory=dict)
    dependencies: dict[str, list[str]] = field(default_factory=dict)
    workflow_calls: dict[str, list[WorkflowCall]] = field(default_factory=dict)

    def has_executable_id(self, name: str) -> bool:
        return name in self.operations or name in self.workflows or name in self.steps

    def add_deps(self, ident: str, deps: list[str]) -> None:
        if deps:
            self.dependencies.setdefault(ident, []).extend(deps)


def select_semantic_entry_workflow(doc: Obj) -> Obj | None:
    wfs = items(doc, "workflows")
    for wf in wfs:
        if wf is not None and s(wf, "workflowId") == "main":
            return wf
    non_nil = [wf for wf in wfs if wf is not None]
    return non_nil[0] if len(non_nil) == 1 else None


def build_index(doc: Obj, r: Collector) -> DocumentIndex:
    idx = DocumentIndex(uws=s(doc, "uws"))
    entry = select_semantic_entry_workflow(doc)
    if entry is not None:
        idx.has_entry_workflow = True
        for step in items(entry, "steps"):
            if step is not None and s(step, "stepId"):
                idx.entry_workflow_steps.add(s(step, "stepId"))

    for i, sd in enumerate(items(doc, "sourceDescriptions")):
        path = f"sourceDescriptions[{i}]"
        if sd is None:
            r.add(path, "is nil")
            continue
        name = s(sd, "name")
        if name:
            if name in idx.source_descriptions:
                r.add(f"{path}.name", f"duplicate sourceDescription name {quote(name)}")
            idx.source_descriptions[name] = effective_source_type(sd)

    for i, op in enumerate(items(doc, "operations")):
        path = f"operations[{i}]"
        if op is None:
            r.add(path, "is nil")
            continue
        ident = s(op, "operationId")
        if ident:
            if ident in idx.operations:
                r.add(f"{path}.operationId", f"duplicate operationId {quote(ident)}")
            idx.operations[ident] = op
            idx.add_deps(ident, strings(op, "dependsOn"))

    for i, wf in enumerate(items(doc, "workflows")):
        path = f"workflows[{i}]"
        if wf is None:
            r.add(path, "is nil")
            continue
        ident = s(wf, "workflowId")
        if ident:
            if ident in idx.workflows:
                r.add(f"{path}.workflowId", f"duplicate workflowId {quote(ident)}")
            idx.workflows[ident] = wf
            idx.workflow_types[ident] = s(wf, "type")
            step_types: dict[str, str] = {}
            idx.workflow_steps[ident] = step_types

            def record_type(_p: str, step: Obj, step_types: dict[str, str] = step_types) -> None:
                if s(step, "stepId"):
                    step_types[s(step, "stepId")] = s(step, "type")

            walk_workflow_tree(wf, "", Walk(step=record_type))
            idx.add_deps(ident, strings(wf, "dependsOn"))

        def index_step(p: str, step: Obj) -> None:
            step_id = s(step, "stepId")
            if not step_id:
                return
            if step_id in idx.steps:
                r.add(f"{p}.stepId", f"duplicate stepId {quote(step_id)}")
            idx.steps[step_id] = step
            idx.add_deps(step_id, strings(step, "dependsOn"))

        walk_workflow_tree(
            wf,
            path,
            Walk(step=index_step, nil_step=lambda p: r.add(p, "is nil"), nil_case=lambda p: r.add(p, "is nil")),
        )
        if ident:
            calls = idx.workflow_calls.setdefault(ident, [])

            def record_call(p: str, step: Obj, calls: list[WorkflowCall] = calls) -> None:
                if s(step, "workflow"):
                    calls.append(WorkflowCall(s(step, "workflow"), f"{p}.workflow"))

            walk_workflow_tree(wf, path, Walk(step=record_call))

    # Parallel groups are checked after every executable identifier is known.
    def add_group(group: str, member: str, path: str) -> None:
        if not group:
            return
        if idx.has_executable_id(group):
            r.add(path, f"parallelGroup {quote(group)} collides with an executable identifier")
        idx.parallel_groups.add(group)
        if member:
            idx.parallel_group_members.setdefault(group, []).append(member)

    for i, op in enumerate(items(doc, "operations")):
        if op is not None:
            add_group(s(op, "parallelGroup"), s(op, "operationId"), f"operations[{i}].parallelGroup")
    for i, wf in enumerate(items(doc, "workflows")):
        if wf is None:
            continue
        walk_workflow_tree(
            wf,
            f"workflows[{i}]",
            Walk(step=lambda p, step: add_group(s(step, "parallelGroup"), s(step, "stepId"), f"{p}.parallelGroup")),
        )

    for i, t in enumerate(items(doc, "triggers")):
        path = f"triggers[{i}]"
        if t is None:
            r.add(path, "is nil")
            continue
        ident = s(t, "triggerId")
        if ident:
            if ident in idx.triggers:
                r.add(f"{path}.triggerId", f"duplicate triggerId {quote(ident)}")
            idx.triggers.add(ident)
    return idx
