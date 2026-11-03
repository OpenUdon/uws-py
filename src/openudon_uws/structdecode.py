"""Go ``encoding/json`` struct decoding of a core document into ``uws1.Document``.

Upstream decodes the document into Go structs before validating it. Every ``uws1`` type has an ``UnmarshalJSON``
that refuses members other than its fields and ``x-*`` extensions, and Go's field types refuse mistyped values
(a string where a number is required, ``1.0`` or ``1e2`` for an ``int``). The schema layer usually reports the
same documents, but not always (for example workflow ``inputs`` keywords the schema allows but ``ParamSchema``
does not know). The type model in ``_gomodel.json`` is generated from the Go types by reflection
(``scripts/gen_gomodel.py``) and checked against the oracle.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any

from .diagnostics import Diagnostic

_INT64_MIN, _INT64_MAX = -(1 << 63), (1 << 63) - 1


@lru_cache(maxsize=1)
def _model() -> dict[str, Any]:
    model: dict[str, Any] = json.loads(files("openudon_uws").joinpath("_gomodel.json").read_text(encoding="utf-8"))
    return model


class _Decoder:
    def __init__(self) -> None:
        self.errors: list[Diagnostic] = []
        self.types: dict[str, Any] = _model()["types"]

    def error(self, path: str, message: str) -> None:
        self.errors.append(Diagnostic(path, message, "decode"))

    def decode(self, value: Any, typ: dict[str, Any], path: str) -> None:
        if value is None:
            return  # null leaves the Go zero value
        kind = typ["kind"]
        if kind in ("any", "raw"):
            return
        if kind == "string":
            if type(value) is not str:
                self.error(path, f"cannot unmarshal {_json_type(value)} into Go value of type string")
        elif kind == "bool":
            if type(value) is not bool:
                self.error(path, f"cannot unmarshal {_json_type(value)} into Go value of type bool")
        elif kind == "int":
            if type(value) is not int or not (_INT64_MIN <= value <= _INT64_MAX):
                self.error(path, f"cannot unmarshal {_json_type(value)} {value!r} into Go value of type int")
        elif kind == "float":
            if type(value) not in (int, float):
                self.error(path, f"cannot unmarshal {_json_type(value)} into Go value of type float64")
        elif kind == "ptr":
            self.decode(value, typ["elem"], path)
        elif kind == "slice":
            if type(value) is not list:
                self.error(path, f"cannot unmarshal {_json_type(value)} into Go value of type slice")
                return
            for i, item in enumerate(value):
                self.decode(item, typ["elem"], f"{path}[{i}]")
        elif kind == "map":
            if type(value) is not dict:
                self.error(path, f"cannot unmarshal {_json_type(value)} into Go value of type map")
                return
            for key, item in value.items():
                self.decode(item, typ["elem"], f"{path}.{key}" if path else key)
        elif kind == "struct":
            self.struct(value, typ["name"], path)
        else:  # pragma: no cover - the model only contains the kinds above
            raise AssertionError(f"unsupported Go kind {kind}")

    def struct(self, value: Any, name: str, path: str) -> None:
        spec = self.types[name]
        if type(value) is not dict:
            self.error(path, f"cannot unmarshal {_json_type(value)} into Go value of type uws1.{name}")
            return
        fields = {f["name"]: f["type"] for f in spec["fields"]}
        for key, item in value.items():
            sub = f"{path}.{key}" if path else key
            if key in fields:
                self.decode(item, fields[key], sub)
            elif spec["unmarshalJSON"] and not key.startswith("x-"):
                self.error(sub, f"field {key!r} is not defined by UWS core; use an x-* extension for non-core metadata")
        if name == "Criterion" and value.get("type") == "":
            self.error(f"{path}.type", "criterion.type must be omitted or one of simple, regex, jsonpath, or xpath")


def _json_type(value: Any) -> str:
    if type(value) is bool:
        return "bool"
    if type(value) in (int, float):
        return "number"
    if type(value) is str:
        return "string"
    if type(value) is list:
        return "array"
    return "object"


def struct_decode_errors(document: Any) -> list[Diagnostic]:
    """Errors Go's ``json.Unmarshal(data, &uws1.Document{})`` reports for this decoded document."""
    decoder = _Decoder()
    decoder.decode(document, _model()["root"], "")
    return decoder.errors
