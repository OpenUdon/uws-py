"""Deterministic document mutations for the differential gate (G6).

Each mutation is a small edit to a valid seed document; the Go reference validator decides the expected verdict.
The vocabulary covers the uws-ts lessons (prototype names, hostile URL hosts) and Python-specific traps
(``bool`` vs ``int``, integral floats such as ``1.0`` for Go ``int`` fields, keys Go's struct types reject).
"""

from __future__ import annotations

import copy
import random
from typing import Any

from openudon_uws.versions import PUBLISHED_UWS_VERSIONS

Path = list[str | int]

ID_FIELDS = {
    "operationId",
    "workflowId",
    "stepId",
    "triggerId",
    "name",
    "operationRef",
    "workflow",
    "sourceDescription",
}
REF_FIELDS = {"operationRef", "workflow", "sourceDescription", "stepId", "workflowId"}
ODD_VALUES: list[Any] = [
    "",
    " ",
    "a.b",
    "bad id",
    "x-ext",
    "main",
    "$inputs.x",
    "-1",
    0,
    -1,
    1.5,
    1.0,
    True,
    None,
    [],
    {},
]
VERSIONS = [*PUBLISHED_UWS_VERSIONS, "1.13.1", "1.9.2-rc.1", "2.0.0", "1.0", "", " 1.13.0", "1.13.0\n"]
PROTO_NAMES = ["constructor", "toString", "__proto__", "hasOwnProperty", "__class__", "__dict__", "keys", "get"]
HOSTS = [
    "ex%61mple.com", "example.com%2F", "ex%25ample.com", "%C3%A9xample.com", "EXAMPLE.com", "[::1]", "[::ffff:7f00:1]",
    "[::ffff:127.0.0.1]", "[0:0:0:0:0:0:0:1]", "[fe80::1%25eth0]", "[1.2.3.4]", "[v1.x]", "127.000.0.1", "localhost",
    "LOCALHOST", "a[::1]", "example.com:", "example.com:443", "[::1]:", "example.com:x",
]  # fmt: skip
EXTRA_KEYS = ["x-note", "unknownField", "Description", "timeout", "description", "enum", "minLength", "default", "$ref"]
NUMBERS: list[Any] = [0, -1, 1, 0.5, "1", 1.0, 2.0, 1e2, True, 9223372036854775808]


def paths(value: Any, prefix: Path | None = None, out: list[Path] | None = None) -> list[Path]:
    prefix = prefix or []
    out = [] if out is None else out
    if type(value) is list:
        for i, v in enumerate(value):
            out.append([*prefix, i])
            paths(v, [*prefix, i], out)
    elif type(value) is dict:
        for k, v in value.items():
            out.append([*prefix, k])
            paths(v, [*prefix, k], out)
    return out


def get(root: Any, path: Path) -> Any:
    cur = root
    for key in path:
        if type(cur) is dict and isinstance(key, str):
            cur = cur.get(key)
        elif type(cur) is list and isinstance(key, int) and 0 <= key < len(cur):
            cur = cur[key]
        else:
            return None
    return cur


def set_(root: Any, path: Path, value: Any) -> None:
    if not path:
        return
    parent = get(root, path[:-1])
    key = path[-1]
    if type(parent) is dict and isinstance(key, str):
        parent[key] = value
        return
    if type(parent) is list and isinstance(key, int) and 0 <= key < len(parent):
        parent[key] = value


def remove(root: Any, path: Path) -> None:
    if not path:
        return
    parent = get(root, path[:-1])
    key = path[-1]
    if type(parent) is dict and isinstance(key, str):
        parent.pop(key, None)
    elif type(parent) is list and isinstance(key, int) and 0 <= key < len(parent):
        del parent[key]


def _last(p: Path) -> str:
    return str(p[-1]) if p else ""


def hostile_url(rand: random.Random, original: str) -> str:
    host = rand.choice(HOSTS)
    for scheme in ("https://", "http://"):
        if original.startswith(scheme):
            rest = original[len(scheme) :]
            cut = min([i for i in (rest.find("/"), rest.find("?"), rest.find("#")) if i >= 0], default=len(rest))
            return f"{scheme}{host}{rest[cut:]}"
    return f"{rand.choice(['https', 'http'])}://{host}{rand.choice(['', '/'])}"


def mutate(seeds: list[Any], count: int, seed: int = 1) -> list[Any]:
    rand = random.Random(seed)
    out: list[Any] = []
    for _ in range(count):
        doc = copy.deepcopy(rand.choice(seeds))
        for _edit in range(1 + rand.randrange(2)):
            every = paths(doc)
            target: Path = rand.choice(every) if every else []
            kind = rand.randrange(14)
            if kind == 0:
                remove(doc, target)
            elif kind == 1:
                set_(doc, ["uws"], rand.choice(VERSIONS))
            elif kind == 2:
                parent = get(doc, target[:-1])
                if type(parent) is list and target:
                    parent.append(copy.deepcopy(parent[target[-1]]))  # type: ignore[index]
            elif kind == 3:
                set_(doc, target, copy.deepcopy(rand.choice(ODD_VALUES)))
            elif kind == 4:
                ids = [p for p in every if _last(p) in ID_FIELDS]
                refs = [p for p in every if _last(p) in REF_FIELDS]
                if ids and refs:
                    set_(doc, rand.choice(refs), get(doc, rand.choice(ids)) or "")
            elif kind == 5:
                owners = [p for p in every if _last(p) in ("operationId", "workflowId", "stepId")]
                if owners:
                    owner = rand.choice(owners)
                    dep = get(doc, rand.choice(owners))
                    deps_path = [*owner[:-1], "dependsOn"]
                    existing = get(doc, deps_path)
                    set_(doc, deps_path, [*(existing if type(existing) is list else []), dep or "missing"])
            elif kind == 6:
                objs = [p for p in every if type(get(doc, p)) is dict]
                if objs:
                    set_(doc, [*rand.choice(objs), rand.choice(EXTRA_KEYS)], copy.deepcopy(rand.choice(ODD_VALUES)))
            elif kind == 7:
                set_(doc, [*target[:-1], "timeout"], rand.choice(NUMBERS))
            elif kind == 8:
                set_(
                    doc,
                    [*target[:-1], "type"],
                    rand.choice(["sequence", "parallel", "switch", "merge", "loop", "await", "bogus", "openapi",
                                 "browser-profile", "ansible-module", ""]),
                )  # fmt: skip
            elif kind == 9:
                set_(doc, [*target[:-1], rand.choice(["retryLimit", "retryAfter", "ttl"])], rand.choice(NUMBERS))
            elif kind == 10:
                strs = [p for p in every if type(get(doc, p)) is str]
                if strs:
                    set_(doc, rand.choice(strs), rand.choice(PROTO_NAMES))
            elif kind == 11:
                urls = [
                    p for p in every if type(get(doc, p)) is str and get(doc, p).startswith(("http://", "https://"))
                ]
                if urls:
                    p = rand.choice(urls)
                    set_(doc, p, hostile_url(rand, get(doc, p)))
                else:
                    set_(doc, target, hostile_url(rand, ""))
            elif kind == 12:
                # Workflow inputs keywords the schema may allow but Go's ParamSchema refuses.
                wfs = [p for p in every if len(p) == 2 and p[0] == "workflows"]
                if wfs:
                    wf = rand.choice(wfs)
                    inputs = get(doc, [*wf, "inputs"])
                    if type(inputs) is not dict:
                        inputs = {"type": "object", "properties": {}}
                        set_(doc, [*wf, "inputs"], inputs)
                    props = inputs.setdefault("properties", {})
                    if type(props) is dict:
                        props[rand.choice(["a", "b", "constructor", "x y"])] = {
                            rand.choice(["type", "description", "enum", "default", "format", "x-hint"]): "string"
                        }
                    if rand.random() < 0.5:
                        inputs.setdefault("required", []).append(rand.choice(["a", "b", "constructor", ""]))
            else:
                set_(
                    doc,
                    [*target[:-1], rand.choice(["items", "wait", "cases", "default", "pending", "effect", "inputs"])],
                    copy.deepcopy(
                        rand.choice(
                            ["$inputs.list", "", [], "write", {"a": {"type": "string"}},
                             {"purpose": "p", "inputs": {"type": "object"}, "outputs": {"type": "object"},
                              "effect": "read"}]
                        )
                    ),
                )  # fmt: skip
        out.append(doc)
    return out
