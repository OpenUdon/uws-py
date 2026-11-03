"""Typed accessors over decoded JSON with Go zero values, and the step-tree walk (``step_walk.go``)."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

Obj = dict[str, Any]

# Go regexp `^...$` is anchored at the end of text; `\Z` keeps that meaning in Python.
CONSTRUCT_ID = re.compile(r"[a-zA-Z0-9_-]+\Z", re.ASCII)
DOTTED_NAME = re.compile(r"[a-zA-Z0-9._-]+\Z", re.ASCII)
SOURCE_NAME = re.compile(r"[A-Za-z0-9_-]+\Z", re.ASCII)


def matches(pattern: re.Pattern[str], value: str) -> bool:
    return pattern.match(value) is not None


def is_obj(v: Any) -> bool:
    return type(v) is dict


def get(o: Obj | None, key: str) -> Any:
    return o.get(key) if type(o) is dict else None


def s(o: Obj | None, key: str) -> str:
    v = get(o, key)
    return v if type(v) is str else ""


def num(o: Obj | None, key: str) -> int | float | None:
    v = get(o, key)
    return v if type(v) in (int, float) else None


def items(o: Obj | None, key: str) -> list[Obj | None]:
    """Elements of an array field; ``None`` entries model Go nil pointers."""
    v = get(o, key)
    if type(v) is not list:
        return []
    return [x if type(x) is dict else None for x in v]


def strings(o: Obj | None, key: str) -> list[str]:
    v = get(o, key)
    return [x if type(x) is str else "" for x in v] if type(v) is list else []


def present(o: Obj | None, key: str) -> bool:
    """Go distinguishes a nil slice/map/pointer (absent or null) from an empty one."""
    return get(o, key) is not None


def map_keys(o: Obj | None, key: str) -> list[str]:
    v = get(o, key)
    return list(v.keys()) if type(v) is dict else []


def map_value(o: Obj | None, key: str) -> Obj | None:
    v = get(o, key)
    return v if type(v) is dict else None


def effective_source_type(sd: Obj) -> str:
    return s(sd, "type") or "openapi"


def quote(value: Any) -> str:
    """Go ``%q``-like quoting for messages (message text is not a stable API)."""
    import json

    return json.dumps(value, ensure_ascii=False)


@dataclass
class Walk:
    step: Callable[[str, Obj], None] | None = None
    nil_step: Callable[[str], None] | None = None
    case: Callable[[str, Obj], None] | None = None
    nil_case: Callable[[str], None] | None = None


def walk_steps(path: str, steps: list[Obj | None], h: Walk) -> None:
    for i, step in enumerate(steps):
        p = f"{path}[{i}]"
        if step is None:
            if h.nil_step:
                h.nil_step(p)
            continue
        if h.step:
            h.step(p, step)
        walk_steps(f"{p}.steps", items(step, "steps"), h)
        walk_cases(f"{p}.cases", items(step, "cases"), h)
        walk_steps(f"{p}.default", items(step, "default"), h)


def walk_cases(path: str, cases: list[Obj | None], h: Walk) -> None:
    for i, c in enumerate(cases):
        p = f"{path}[{i}]"
        if c is None:
            if h.nil_case:
                h.nil_case(p)
            continue
        if h.case:
            h.case(p, c)
        walk_steps(f"{p}.steps", items(c, "steps"), h)


def walk_workflow_tree(wf: Obj, base: str, h: Walk) -> None:
    walk_steps(f"{base}.steps", items(wf, "steps"), h)
    walk_cases(f"{base}.cases", items(wf, "cases"), h)
    walk_steps(f"{base}.default", items(wf, "default"), h)
