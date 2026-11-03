"""Profile validators ported from upstream ``schemas/schema.go`` and ``mockruntime/fixtures.go``.

Profiles decode as upstream ``decodeSchemaDocument``: one YAML (or JSON) document decoded by yaml.v3 into ``any``
and re-encoded as JSON, then the exact-version schema, then the profile's semantic rules. Upstream returns the
first problem, so each result carries one diagnostic.
"""

from __future__ import annotations

import posixpath
import re
from typing import Any

from ..diagnostics import DecodeError, Diagnostic, ValidationResult
from ..gostrings import go_trim_space
from ..schema import validate_against_schema
from ..strict import parse_profile_yaml
from ..strict.json import parse_strict_json
from .browser import BROWSER_PROFILES, check_browser_profile
from .common import (
    Obj,
    ProfileError,
    as_obj,
    browser_navigate,
    canonical_origin,
    is_clean_browser_path,
    q,
    string_field,
    validate_authentication_origin,
    validate_authentication_target,
    validate_browser_contexts,
    validate_context_origin,
    validate_context_target,
)
from .jcs import (
    CanonicalizationError,
    canonical_json,
    canonicalize_request,
    go_marshal_length,
    request_digest,
)

__all__ = [
    "BROWSER_PROFILES",
    "CanonicalizationError",
    "canonical_json",
    "canonicalize_request",
    "go_marshal_length",
    "request_digest",
    "validate_browser_authentication_call",
    "validate_browser_authentication_profile",
    "validate_browser_profile",
    "validate_mock_fixtures",
    "validate_runtime_supplement",
]

MIB = 1 << 20
_AUTH_PROFILES = ("uws.browser-authentication.1.0", "uws.browser-authentication.1.1")


def _failure(message: str, path: str = "") -> ValidationResult:
    return ValidationResult(False, [Diagnostic(path, message, "profile")])


def _size(data: str | bytes) -> int:
    return len(data) if isinstance(data, bytes) else len(data.encode("utf-8", errors="surrogatepass"))


def _decode(data: str | bytes, label: str) -> Any:
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
    if go_trim_space(text) == "":
        raise ProfileError(f"{label} document is empty")
    try:
        return parse_profile_yaml(data)
    except DecodeError as exc:
        raise ProfileError(f"decode {label}: {exc}") from None


def _discriminator(value: Any, label: str) -> str:
    if type(value) is not dict:
        raise ProfileError(f"{label} must be an object")
    profile = value.get("profile")
    if type(profile) is not str or go_trim_space(profile) == "":
        raise ProfileError(f"{label} profile discriminator is required")
    return profile


def _schema(name: str, value: Any, label: str) -> ValidationResult | None:
    diagnostics = [
        Diagnostic(d.path, f"validate {label}: {d.message}", "profile") for d in validate_against_schema(name, value)
    ]
    return ValidationResult(False, diagnostics) if diagnostics else None


def _run(check: Any) -> ValidationResult:
    try:
        result: ValidationResult = check()
    except ProfileError as exc:
        return _failure(str(exc))
    return result


def validate_browser_profile(data: str | bytes) -> ValidationResult:
    """Validates a browser capability profile (``uws.browser.1.5`` … ``uws.browser.1.10``)."""

    def check() -> ValidationResult:
        value = _decode(data, "browser source profile")
        profile = _discriminator(value, "browser source profile")
        if profile not in BROWSER_PROFILES:
            raise ProfileError(f"unsupported browser source profile discriminator {q(profile)}")
        failed = _schema(f"{profile.removeprefix('uws.')}.json", value, "browser source profile")
        if failed is not None:
            return failed
        check_browser_profile(profile, value)
        return ValidationResult(True, [])

    return _run(check)


def validate_browser_authentication_profile(data: str | bytes) -> ValidationResult:
    """Validates a browser authentication profile (``uws.browser-authentication.1.0`` / ``1.1``)."""

    def check() -> ValidationResult:
        if _size(data) > MIB:
            raise ProfileError(f"browser authentication profile exceeds {MIB} bytes")
        value = _decode(data, "browser authentication profile")
        profile = _discriminator(value, "browser authentication profile")
        if profile not in _AUTH_PROFILES:
            raise ProfileError(f"unsupported browser authentication profile discriminator {q(profile)}")
        failed = _schema(f"{profile.removeprefix('uws.')}.json", value, "browser authentication profile")
        if failed is not None:
            return failed
        _check_authentication(profile, value)
        return ValidationResult(True, [])

    return _run(check)


def _check_authentication(profile: str, root: Obj) -> None:
    info = as_obj(root.get("info"))
    declared: set[str] = set()
    count = 0
    for field in ("applicationOrigins", "authenticationOrigins"):
        origins = info.get(field)
        origins = origins if type(origins) is list else []
        count += len(origins)
        for i, raw in enumerate(origins):
            origin = raw if type(raw) is str else ""
            try:
                validate_authentication_origin(origin)
            except ProfileError as exc:
                raise ProfileError(f"info.{field}[{i}]: {exc}") from None
            declared.add(canonical_origin(origin))
    if count > 32:
        raise ProfileError("info origins: combined applicationOrigins and authenticationOrigins exceed 32")
    if profile == "uws.browser-authentication.1.1":
        validate_browser_contexts(root, declared)
    slots = as_obj(root.get("credentialSlots"))
    for name, raw_flow in as_obj(root.get("flows")).items():
        flow = as_obj(raw_flow)
        effects = flow.get("effects")
        has_mfa = type(effects) is list and any(type(e) is str and e == "sends_mfa_challenge" for e in effects)
        has_challenge = False
        sequence = flow.get("sequence")
        for i, raw_step in enumerate(sequence if type(sequence) is list else []):
            step = as_obj(raw_step)
            where = f"flows.{name}.sequence[{i}]"
            if "navigate" in step:
                target, context_id = browser_navigate(step["navigate"])
                try:
                    validate_authentication_target(target, declared)
                    validate_context_target(root, context_id, target)
                except ProfileError as exc:
                    raise ProfileError(f"{where}.navigate: {exc}") from None
            if "type_credential" in step:
                slot = string_field(step["type_credential"], "slot")
                if slot not in slots:
                    raise ProfileError(f"{where}.type_credential.slot: undeclared credential slot {q(slot)}")
                if string_field(slots[slot], "kind") == "totp_seed":
                    raise ProfileError(
                        f"{where}.type_credential.slot: totp_seed slots may be used only by a totp challenge"
                    )
            if "challenge" in step:
                has_challenge = True
                slot = string_field(step["challenge"], "slot")
                if slot != "":
                    if slot not in slots:
                        raise ProfileError(f"{where}.challenge.slot: undeclared credential slot {q(slot)}")
                    if string_field(slots[slot], "kind") != "totp_seed":
                        raise ProfileError(f"{where}.challenge.slot: TOTP requires a totp_seed slot")
        if has_challenge != has_mfa:
            raise ProfileError(
                f"flows.{name}.effects: sends_mfa_challenge must be present exactly when the flow has a challenge step"
            )
        success = as_obj(flow.get("success"))
        origin = string_field(success, "origin")
        try:
            validate_authentication_origin(origin)
        except ProfileError as exc:
            raise ProfileError(f"flows.{name}.success.origin: {exc}") from None
        if canonical_origin(origin) not in declared:
            raise ProfileError(f"flows.{name}.success.origin: origin is not declared by info")
        path = string_field(success, "path")
        if path != "" and not is_clean_browser_path(path):
            raise ProfileError(f"flows.{name}.success.path: must be an exact clean path")
        try:
            validate_context_origin(root, string_field(success, "context"), origin)
        except ProfileError as exc:
            raise ProfileError(f"flows.{name}.success.context: {exc}") from None


def _family_schema_name(profile: str, name: str, default_version: str) -> str:
    """Upstream ``familySchemaName``."""
    p = go_trim_space(profile) or default_version
    p = p.removesuffix(".json").removeprefix("uws.")
    if not p.startswith(name + "."):
        p = f"{name}.{p}"
    return p + ".json"


def go_path_clean(p: str) -> str:
    """Go ``path.Clean``."""
    if p == "":
        return "."
    cleaned = posixpath.normpath(p)
    if cleaned.startswith("//"):  # posixpath keeps a leading "//"; Go collapses it
        cleaned = "/" + cleaned.lstrip("/")
    return cleaned


def validate_browser_authentication_call(
    data: str | bytes, profile: str = "uws.browser-authentication-call.1.0"
) -> ValidationResult:
    """Validates a browser authentication call envelope (the operation fragment holding
    ``x-uws-browser-authentication``) for an explicitly selected supplement version."""

    def check() -> ValidationResult:
        value = _decode(data, "browser authentication call")
        name = _family_schema_name(profile, "browser-authentication-call", "1.1")
        if name not in ("browser-authentication-call.1.0.json", "browser-authentication-call.1.1.json"):
            raise ProfileError(f"unsupported browser authentication call profile {q(profile)}")
        failed = _schema(name, value, "browser authentication call")
        if failed is not None:
            return failed
        call = as_obj(as_obj(value).get("x-uws-browser-authentication"))
        clean = go_path_clean(string_field(call, "profile"))
        if clean in (".", "..") or clean.startswith("../") or clean.startswith("/"):
            raise ProfileError("x-uws-browser-authentication.profile must be a safe relative path")
        return ValidationResult(True, [])

    return _run(check)


def validate_runtime_supplement(data: str | bytes) -> ValidationResult:
    """Validates an ``x-uws-runtime`` envelope against the Runtime Supplement 1.0 schema.

    Upstream has no semantic validator for this supplement; the published schema is its whole contract
    (docs/divergences.md).
    """

    def check() -> ValidationResult:
        value = _decode(data, "runtime supplement")
        failed = _schema("runtime.1.0.json", value, "runtime supplement")
        return failed if failed is not None else ValidationResult(True, [])

    return _run(check)


# --- mock fixtures ------------------------------------------------------------------------------------------------

_OPERATION_ID = re.compile(r"[A-Za-z0-9_-]+\Z", re.ASCII)
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_POINTER = re.compile(r"(?:/(?:[^~/]|~[01])*)*\Z", re.ASCII)
_STRICT_DEPTH = 100


def _check_surrogate_escapes(raw: bytes) -> None:
    """strictjson ``validateSurrogateEscapes``: unpaired surrogate escapes are refused."""
    i, n = 0, len(raw)
    while i < n:
        if raw[i] != 0x22:
            i += 1
            continue
        i += 1
        while i < n:
            c = raw[i]
            if c == 0x22:
                break
            if c == 0x5C:
                i += 1
                if i >= n or raw[i] != 0x75:
                    i += 1
                    continue
                if i + 4 >= n:
                    raise ProfileError("truncated JSON Unicode escape")
                value = _hex4(raw[i + 1 : i + 5])
                i += 4
                if 0xDC00 <= value <= 0xDFFF:
                    raise ProfileError("JSON string contains an unpaired low surrogate")
                if 0xD800 <= value <= 0xDBFF:
                    if i + 6 >= n or raw[i + 1] != 0x5C or raw[i + 2] != 0x75:
                        raise ProfileError("JSON string contains an unpaired high surrogate")
                    low = _hex4(raw[i + 3 : i + 7])
                    if not 0xDC00 <= low <= 0xDFFF:
                        raise ProfileError("JSON string contains an unpaired high surrogate")
                    i += 6
            i += 1
        i += 1


def _hex4(chunk: bytes) -> int:
    if len(chunk) != 4 or not all(c in b"0123456789abcdefABCDEF" for c in chunk):
        raise ProfileError("invalid JSON Unicode escape")
    return int(chunk, 16)


def _depth(value: Any) -> int:
    deepest = 0
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        item, level = stack.pop()
        if type(item) in (list, dict):
            level += 1
            deepest = max(deepest, level)
            stack.extend((v, level) for v in (item.values() if type(item) is dict else item))
    return deepest


def _strict_single_value(raw: bytes) -> Any:
    """strictjson ``ValidateSingleValue`` (then decoded as Go does, numbers outside float64 refused)."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ProfileError("JSON document is not valid UTF-8") from None
    if go_trim_space(text) == "":
        raise ProfileError("JSON document is empty")
    _check_surrogate_escapes(raw)
    try:
        value = parse_strict_json(text, reject_duplicates=True)
    except DecodeError as exc:
        raise ProfileError(str(exc)) from None
    if _depth(value) > _STRICT_DEPTH:
        raise ProfileError("JSON nesting exceeds depth 100")
    return value


def validate_mock_fixtures(data: str | bytes) -> ValidationResult:
    """Validates a mock fixture document as upstream ``mockruntime.DecodeFixtures`` does."""

    def check() -> ValidationResult:
        raw = data.encode("utf-8", errors="surrogatepass") if isinstance(data, str) else data
        if len(raw) > 16 * MIB:
            raise ProfileError(f"mock fixture document exceeds {16 * MIB} bytes")
        try:
            value = _strict_single_value(raw)
        except ProfileError as exc:
            raise ProfileError(f"decode mock fixture JSON: {exc}") from None
        trimmed = go_trim_space(raw.decode("utf-8"))
        if not trimmed.startswith("{") or type(value) is not dict:
            raise ProfileError("mock fixture document must be a JSON object")
        fmt = value.get("format")
        if fmt != "uws.mock-fixtures.1.0":
            raise ProfileError(f"unsupported mock fixture format {q(fmt if type(fmt) is str else '')}")
        failed = _schema("mock-fixtures.1.0.json", value, "mock fixture document")
        if failed is not None:
            return failed
        _check_fixtures(value)
        return ValidationResult(True, [])

    return _run(check)


def _check_fixtures(value: Obj) -> None:
    fixtures = value.get("fixtures")
    fixtures = fixtures if type(fixtures) is list else []
    if not 1 <= len(fixtures) <= 10000:
        raise ProfileError("mock fixture set must contain between 1 and 10000 fixtures")
    seen: set[tuple[str, str]] = set()
    for i, raw_fixture in enumerate(fixtures):
        fixture = as_obj(raw_fixture)
        operation_id = string_field(fixture, "operationId")
        digest = string_field(fixture, "requestDigest")
        if not _OPERATION_ID.match(operation_id):
            raise ProfileError(f"fixtures[{i}]: operationId must use UWS identifier characters")
        if not _DIGEST.match(digest):
            raise ProfileError(
                f"fixtures[{i}]: requestDigest must be sha256: followed by 64 lowercase hexadecimal characters"
            )
        if (operation_id, digest) in seen:
            raise ProfileError(f"fixtures[{i}]: duplicate operationId/requestDigest pair")
        seen.add((operation_id, digest))
        if "response" not in fixture:
            raise ProfileError(f"fixtures[{i}].response must contain one JSON value")
        provenance = as_obj(fixture.get("provenance"))
        kind = string_field(provenance, "kind")
        redacted = provenance.get("redacted") is True
        pointers = provenance.get("redactedPointers")
        if kind in ("synthetic", "example"):
            if redacted or pointers is not None:
                raise ProfileError(f"fixtures[{i}]: non-recorded provenance must omit redaction fields")
        elif kind == "recorded":
            if not redacted or pointers is None:
                raise ProfileError(f"fixtures[{i}]: recorded provenance requires explicit redaction metadata")
        else:
            raise ProfileError(f"fixtures[{i}].provenance.kind is unsupported")
        unique: set[str] = set()
        for j, pointer in enumerate(pointers if type(pointers) is list else []):
            text = pointer if type(pointer) is str else ""
            if len(text) > 512 or not _POINTER.match(text):
                raise ProfileError(f"fixtures[{i}].provenance.redactedPointers[{j}] is invalid")
            if text in unique:
                raise ProfileError(f"fixtures[{i}].provenance.redactedPointers has duplicate pointer {q(text)}")
            unique.add(text)
