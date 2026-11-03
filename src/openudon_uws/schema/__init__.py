"""JSON Schema 2020-12 validation of vendored schemas with Go (santhosh-tekuri/jsonschema) semantics.

``pattern``/``patternProperties`` use Go RE2 matching (:mod:`.goregex`) and the ``uri``, ``uri-reference`` and
``date-time`` formats are asserted with Go's rules (:mod:`.goformats`), as upstream compiles schemas with
``AssertFormat``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from fractions import Fraction
from functools import cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, validators
from jsonschema.exceptions import ValidationError

from ..diagnostics import Diagnostic
from . import goformats
from .goregex import compile_go

__all__ = ["GoTime", "compiled_schema", "schema_document", "schema_names", "validate_against_schema"]


class GoTime:
    """Stand-in for a Go ``time.Time`` in the raw YAML value upstream validates.

    An unquoted YAML timestamp decodes to ``time.Time`` there, which has no JSON type: every ``type``, ``enum`` and
    ``const`` assertion on it fails, while positions without such assertions accept it.
    """

    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text

    def __eq__(self, other: object) -> bool:
        return other is self

    def __hash__(self) -> int:
        return id(self)

    def __repr__(self) -> str:
        return f"GoTime({self.text!r})"


def schema_names() -> list[str]:
    """Names of the shipped schema files, for example ``1.13.0.json`` or ``browser.1.10.json``."""
    return sorted(p.name for p in files("openudon_uws").joinpath("_schemas").iterdir() if p.name.endswith(".json"))


@cache
def _schema_text(name: str) -> str:
    return files("openudon_uws").joinpath("_schemas", name).read_text(encoding="utf-8")


def schema_document(name: str) -> dict[str, Any]:
    """The parsed schema document for a shipped schema file name."""
    if name not in schema_names():
        raise KeyError(f"schema {name} is not shipped")
    value: dict[str, Any] = json.loads(_schema_text(name))
    return value


def _pattern(validator: Any, pattern: str, instance: Any, schema: dict[str, Any]) -> Iterator[ValidationError]:
    if type(instance) is str and not compile_go(pattern).search(instance):
        yield ValidationError(f"{instance!r} does not match {pattern!r}")


def _pattern_properties(
    validator: Any, pattern_properties: dict[str, Any], instance: Any, schema: dict[str, Any]
) -> Iterator[ValidationError]:
    if type(instance) is not dict:
        return
    for pattern, subschema in pattern_properties.items():
        regex = compile_go(pattern)
        for key, value in instance.items():
            if regex.search(key):
                yield from validator.descend(value, subschema, path=key, schema_path=pattern)


def _exact(number: int | float) -> Fraction:
    # Go validates JSON numbers as exact decimals (json.Number -> big.Rat); the shortest repr of a float is the
    # decimal text it was parsed from.
    return Fraction(number) if type(number) is int else Fraction(repr(number))


def _multiple_of(validator: Any, divisor: Any, instance: Any, schema: dict[str, Any]) -> Iterator[ValidationError]:
    if type(instance) not in (int, float) or type(divisor) not in (int, float):
        return
    if type(instance) is float and (instance != instance or instance in (float("inf"), float("-inf"))):
        return
    if (_exact(instance) / _exact(divisor)).denominator != 1:
        yield ValidationError(f"{instance!r} is not a multiple of {divisor!r}")


_GoValidator = validators.extend(  # type: ignore[no-untyped-call]
    Draft202012Validator,
    {"pattern": _pattern, "patternProperties": _pattern_properties, "multipleOf": _multiple_of},
)


def _string_format(check: Any) -> Any:
    # Format assertions apply to strings only; other values (including GoTime) pass, as in santhosh-tekuri.
    return lambda value: type(value) is not str or check(value)


_FORMATS = FormatChecker(formats=())
_FORMATS.checks("uri")(_string_format(goformats.go_uri))
_FORMATS.checks("uri-reference")(_string_format(goformats.go_uri_reference))
_FORMATS.checks("date-time")(_string_format(goformats.go_date_time))


@cache
def compiled_schema(name: str) -> Any:
    """The (cached) validator for a shipped schema file."""
    return _GoValidator(schema_document(name), format_checker=_FORMATS)


def compile_inline(schema: dict[str, Any]) -> Any:
    """A validator for an ad-hoc schema with the same Go semantics (used by tests and profiles)."""
    return _GoValidator(schema, format_checker=_FORMATS)


def _path(error: ValidationError) -> str:
    out = ""
    for token in error.absolute_path:
        if isinstance(token, int):
            out += f"[{token}]"
        else:
            out = f"{out}.{token}" if out else str(token)
    return out


def validate_against_schema(name: str, value: Any) -> list[Diagnostic]:
    """Validates a decoded value against a shipped schema and converts errors to diagnostics."""
    validator = compiled_schema(name)
    return [Diagnostic(_path(e), e.message, "schema") for e in validator.iter_errors(value)]


def validate_inline(schema: dict[str, Any], value: Any) -> list[Diagnostic]:
    return [Diagnostic(_path(e), e.message, "schema") for e in compile_inline(schema).iter_errors(value)]
