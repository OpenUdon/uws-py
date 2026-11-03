"""Core-document validation, mirroring upstream ``validation.ValidateDocumentFile`` for ``.json``/``.yaml``."""

from __future__ import annotations

import os
from typing import Any

from .diagnostics import DecodeError, Diagnostic, ValidationResult
from .gostrings import go_trim_space
from .schema import GoTime, validate_against_schema
from .strict import DEFAULT_MAX_BYTES, InputFormat, JsonValue, decode_document, format_for_path
from .strict.goyaml import YamlTimestamp
from .structdecode import struct_decode_errors
from .versions import is_published_uws_version

__all__ = ["validate_decoded_document", "validate_document", "validate_document_file"]


def validate_document(
    data: str | bytes | JsonValue,
    *,
    format: InputFormat = "auto",
    reject_duplicate_keys: bool = False,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> ValidationResult:
    """Validates a UWS core document: strict decoding, exact-version schema selection, JSON Schema validation of
    the raw document (formats asserted), then the semantic layer.

    ``data`` is JSON or YAML text/bytes, or an already decoded value.
    """
    if isinstance(data, (str, bytes)):
        try:
            value = decode_document(
                data, format=format, reject_duplicate_keys=reject_duplicate_keys, max_bytes=max_bytes
            )
        except DecodeError as exc:
            return ValidationResult(False, [Diagnostic("", str(exc), "decode")])
        return _validate(value)
    return _validate(data)


def validate_document_file(path: str | os.PathLike[str], *, max_bytes: int = DEFAULT_MAX_BYTES) -> ValidationResult:
    """Validates a document file, choosing JSON or YAML by extension exactly as Go does."""
    name = os.fspath(path)
    try:
        fmt = format_for_path(name)
    except DecodeError as exc:
        return ValidationResult(False, [Diagnostic("", str(exc), "decode")])
    with open(name, "rb") as handle:
        data = handle.read()
    return validate_document(data, format=fmt, max_bytes=max_bytes)


def validate_decoded_document(value: JsonValue) -> ValidationResult:
    """Validates an already-decoded document value."""
    return _validate(value)


def _split_timestamps(value: Any) -> tuple[Any, Any, bool]:
    """Returns (semantic view with plain strings, schema view with GoTime stand-ins, any timestamps found)."""
    if isinstance(value, YamlTimestamp):
        return str(value), GoTime(str(value)), True
    if type(value) is dict:
        sem: dict[str, Any] = {}
        sch: dict[str, Any] = {}
        found = False
        for k, v in value.items():
            sem[k], sch[k], f = _split_timestamps(v)
            found = found or f
        return (sem, sch, True) if found else (value, value, False)
    if type(value) is list:
        parts = [_split_timestamps(v) for v in value]
        if any(p[2] for p in parts):
            return [p[0] for p in parts], [p[1] for p in parts], True
    return value, value, False


def _has_timestamp(value: Any) -> bool:
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, YamlTimestamp):
            return True
        if type(item) is dict:
            stack.extend(item.values())
        elif type(item) is list:
            stack.extend(item)
    return False


def _validate(raw: Any) -> ValidationResult:
    # Only YAML input carries timestamps; its nesting is already bounded by the YAML decoder.
    value, schema_view, _ = _split_timestamps(raw) if _has_timestamp(raw) else (raw, raw, False)
    if type(value) is not dict:
        return ValidationResult(False, [Diagnostic("", "UWS document must be a JSON object", "decode")])
    struct_errors = struct_decode_errors(value)
    if struct_errors:
        return ValidationResult(False, struct_errors)
    declared = value.get("uws", "")
    declared = go_trim_space(declared) if type(declared) is str else ""
    # Upstream selects the 1.0.0 schema when `uws` is empty; the semantic layer then reports it missing.
    schema_version = declared or "1.0.0"
    if not is_published_uws_version(schema_version):
        return ValidationResult(
            False, [Diagnostic("uws", f"no published UWS schema for version {declared!r}", "version")]
        )
    diagnostics = validate_against_schema(f"{schema_version}.json", schema_view)
    if diagnostics:
        return ValidationResult(False, diagnostics)
    from .semantic import validate_semantics

    return validate_semantics(value)
