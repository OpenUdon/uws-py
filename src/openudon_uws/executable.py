"""Port of upstream ``uws1/executable_validation.go`` (``Document.ValidateExecutable``): checks a runtime applies
before invoking any operation, on top of ordinary validation."""

from __future__ import annotations

from typing import Any

from .diagnostics import DecodeError, Diagnostic, ValidationResult
from .document import validate_document
from .semantic.access import Obj, items, quote, s
from .strict import DEFAULT_MAX_BYTES, InputFormat, JsonValue, decode_document

__all__ = ["validate_executable_checks", "validate_executable_document"]


class _ExecutableError(Exception):
    pass


def _names(doc: Obj) -> None:
    seen: dict[str, str] = {}

    def add(name: str, kind: str) -> None:
        if name == "":
            return
        prev = seen.get(name)
        if prev is not None:
            if prev == "parallelGroup" and kind == "parallelGroup":
                return
            raise _ExecutableError(f"executable identifier {quote(name)} is ambiguous between {prev} and {kind}")
        seen[name] = kind

    def steps(entries: list[Obj | None]) -> None:
        for step in entries:
            if step is None:
                continue
            ident = s(step, "stepId")
            if ident != "":
                if seen.get(ident) == "operation" and s(step, "operationRef") == ident:
                    seen[ident] = "step"
                else:
                    add(ident, "step")
            add(s(step, "parallelGroup"), "parallelGroup")
            steps(items(step, "steps"))
            steps(items(step, "default"))
            for c in items(step, "cases"):
                if c is not None:
                    steps(items(c, "steps"))

    for op in items(doc, "operations"):
        if op is None:
            continue
        add(s(op, "operationId"), "operation")
        add(s(op, "parallelGroup"), "parallelGroup")
    for wf in items(doc, "workflows"):
        if wf is None:
            continue
        add(s(wf, "workflowId"), "workflow")
        steps(items(wf, "steps"))
        steps(items(wf, "default"))
        for c in items(wf, "cases"):
            if c is not None:
                steps(items(c, "steps"))


def _criteria(entries: list[Obj | None], where: str) -> None:
    for c in entries:
        if c is None:
            continue
        typ = s(c, "type")
        if typ not in ("", "simple", "regex", "jsonpath", "xpath"):
            raise _ExecutableError(f"{where} include unsupported criterion type {quote(typ)}")


def _entry_workflow(doc: Obj) -> None:
    wfs = doc.get("workflows") if type(doc.get("workflows")) is list else []
    assert isinstance(wfs, list)
    if len(wfs) <= 1:
        return
    mains = sum(1 for wf in wfs if type(wf) is dict and s(wf, "workflowId") == "main")
    if mains > 1:
        raise _ExecutableError('multiple workflows use the reserved entry id "main"')
    if mains == 0:
        raise _ExecutableError('multiple workflows require an explicit "main" entry workflow')


def _executable_steps(entries: list[Obj | None]) -> None:
    for step in entries:
        if step is None:
            continue
        if step.get("pending") is not None:
            raise _ExecutableError(f"step {quote(s(step, 'stepId'))} is a pending contract and cannot be executed")
        is_workflow_reference = s(step, "workflow") != "" and s(step, "operationRef") == "" and s(step, "type") == ""
        if is_workflow_reference and (items(step, "steps") or items(step, "cases") or items(step, "default")):
            raise _ExecutableError(
                f"step {quote(s(step, 'stepId'))} workflow references cannot also declare structural content"
            )
        _executable_steps(items(step, "steps"))
        for c in items(step, "cases"):
            if c is not None:
                _executable_steps(items(c, "steps"))
        _executable_steps(items(step, "default"))


def validate_executable_checks(doc: Any) -> ValidationResult:
    """Runs only the executable checks over a decoded document (assumes ordinary validation passed)."""
    try:
        if type(doc) is not dict:
            raise _ExecutableError("document is required")
        _names(doc)
        for op in items(doc, "operations"):
            if op is None:
                continue
            ident = quote(s(op, "operationId"))
            _criteria(items(op, "successCriteria"), f"operation {ident} successCriteria")
            for a in items(op, "onFailure"):
                if a is not None:
                    _criteria(items(a, "criteria"), f"operation {ident} onFailure criteria")
            for a in items(op, "onSuccess"):
                if a is not None:
                    _criteria(items(a, "criteria"), f"operation {ident} onSuccess criteria")
        _entry_workflow(doc)
        for wf in items(doc, "workflows"):
            if wf is None:
                continue
            _executable_steps(items(wf, "steps"))
            for c in items(wf, "cases"):
                if c is not None:
                    _executable_steps(items(c, "steps"))
            _executable_steps(items(wf, "default"))
    except _ExecutableError as exc:
        return ValidationResult(False, [Diagnostic("", str(exc), "executable")])
    return ValidationResult(True, [])


def validate_executable_document(
    data: str | bytes | JsonValue,
    *,
    format: InputFormat = "auto",
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> ValidationResult:
    """Ordinary validation followed by executable validation (what a runtime must check before running)."""
    ordinary = validate_document(data, format=format, max_bytes=max_bytes)
    if not ordinary.valid:
        return ordinary
    if isinstance(data, (str, bytes)):
        try:
            data = decode_document(data, format=format, max_bytes=max_bytes)
        except DecodeError as exc:  # pragma: no cover - already decoded once above
            return ValidationResult(False, [Diagnostic("", str(exc), "decode")])
    return validate_executable_checks(data)
