"""Dependency cycles and workflow recursion (``validation_graph.go``)."""

from __future__ import annotations

from ..diagnostics import Collector
from .index import DocumentIndex


def canonical_cycle_key(cycle: list[str]) -> str:
    if not cycle:
        return ""
    low = min(range(len(cycle)), key=lambda i: cycle[i])
    return "->".join(cycle[low:] + cycle[:low])


def detect_dependency_cycles(idx: DocumentIndex, r: Collector) -> None:
    if not idx.dependencies:
        return
    color: dict[str, int] = {}
    seen: set[str] = set()

    def neighbors(node: str) -> list[str]:
        out: list[str] = []
        for d in idx.dependencies.get(node, []):
            if idx.has_executable_id(d):
                out.append(d)
                continue
            out.extend(m for m in idx.parallel_group_members.get(d, []) if m != node)
        return out

    for start in sorted(idx.dependencies):
        if color.get(start, 0) != 0:
            continue
        stack: list[tuple[str, list[str], list[int]]] = [(start, neighbors(start), [0])]
        path = [start]
        color[start] = 1
        while stack:
            node, nbs, nxt = stack[-1]
            if nxt[0] >= len(nbs):
                color[node] = 2
                stack.pop()
                path.pop()
                continue
            nb = nbs[nxt[0]]
            nxt[0] += 1
            c = color.get(nb, 0)
            if c == 0:
                color[nb] = 1
                stack.append((nb, neighbors(nb), [0]))
                path.append(nb)
            elif c == 1:
                if nb not in path:
                    continue
                cycle = path[path.index(nb) :]
                key = canonical_cycle_key(cycle)
                if key in seen:
                    continue
                seen.add(key)
                r.add("dependsOn", f"cycle detected: {' -> '.join([*cycle, nb])}")


def detect_workflow_recursion(idx: DocumentIndex, r: Collector) -> None:
    state: dict[str, int] = {}
    stack: list[str] = []
    reported: set[str] = set()

    def visit(ident: str) -> None:
        state[ident] = 1
        stack.append(ident)
        for call in idx.workflow_calls.get(ident, []):
            if call.target not in idx.workflows:
                continue
            st = state.get(call.target, 0)
            if st == 0:
                visit(call.target)
            elif st == 1:
                start = stack.index(call.target) if call.target in stack else 0
                cycle = stack[start:]
                key = canonical_cycle_key(cycle)
                if key in reported:
                    continue
                reported.add(key)
                r.add(call.path, f"recursive workflow call detected: {' -> '.join([*cycle, call.target])}")
        stack.pop()
        state[ident] = 2

    for ident in sorted(idx.workflows):
        if state.get(ident, 0) == 0:
            visit(ident)
