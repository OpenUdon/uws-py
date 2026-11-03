"""Diagnostics shared by every validation layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Layer = Literal["decode", "version", "schema", "semantic", "executable", "expression", "profile"]


@dataclass(frozen=True)
class Diagnostic:
    """One validation problem.

    ``path`` uses the Go validator's dotted/indexed form, for example ``operations[0].request``; it is empty for
    document-level problems. ``message`` is human readable and not a stable API.
    """

    path: str
    message: str
    layer: Layer


@dataclass
class ValidationResult:
    """Outcome of a validation call. ``valid`` is the accept/reject verdict compared against Go."""

    valid: bool
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.valid


def ok() -> ValidationResult:
    return ValidationResult(True, [])


def fail(layer: Layer, message: str, path: str = "") -> ValidationResult:
    return ValidationResult(False, [Diagnostic(path, message, layer)])


class Collector:
    """Collects diagnostics for one layer, mirroring ``uws1.ValidationResult.addError``."""

    def __init__(self, layer: Layer) -> None:
        self.layer: Layer = layer
        self.diagnostics: list[Diagnostic] = []

    def add(self, path: str, message: str) -> None:
        self.diagnostics.append(Diagnostic(path, message, self.layer))

    @property
    def valid(self) -> bool:
        return not self.diagnostics

    def result(self) -> ValidationResult:
        return ValidationResult(self.valid, list(self.diagnostics))


class DecodeError(ValueError):
    """Raised when input cannot be decoded into exactly one JSON-compatible value."""
