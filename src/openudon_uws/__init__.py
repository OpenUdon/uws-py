"""Python validator for the Udon Workflow Specification (UWS) 1.x.

Verdicts follow the Go reference implementation (``github.com/OpenUdon/uws``) at the pinned commit; see
``docs/divergences.md`` for the recorded differences.
"""

from __future__ import annotations

from .diagnostics import DecodeError, Diagnostic, ValidationResult
from .document import validate_decoded_document, validate_document, validate_document_file
from .executable import validate_executable_checks, validate_executable_document
from .expressions import (
    ExpressionError,
    JsonNumber,
    ParsedExpression,
    is_valid_expression,
    parse_expression,
    parse_pointer,
)
from .profiles import (
    BROWSER_PROFILES,
    CanonicalizationError,
    canonical_json,
    canonicalize_request,
    request_digest,
    validate_browser_authentication_call,
    validate_browser_authentication_profile,
    validate_browser_profile,
    validate_mock_fixtures,
    validate_runtime_supplement,
)
from .semantic import validate_semantics
from .strict import decode_document
from .versions import LATEST_UWS_VERSION, PUBLISHED_UWS_VERSIONS

__version__ = "0.1.0"

__all__ = [
    "BROWSER_PROFILES",
    "LATEST_UWS_VERSION",
    "PUBLISHED_UWS_VERSIONS",
    "CanonicalizationError",
    "DecodeError",
    "Diagnostic",
    "ExpressionError",
    "JsonNumber",
    "ParsedExpression",
    "ValidationResult",
    "__version__",
    "canonical_json",
    "canonicalize_request",
    "decode_document",
    "is_valid_expression",
    "parse_expression",
    "parse_pointer",
    "request_digest",
    "validate_browser_authentication_call",
    "validate_browser_authentication_profile",
    "validate_browser_profile",
    "validate_decoded_document",
    "validate_document",
    "validate_document_file",
    "validate_executable_checks",
    "validate_executable_document",
    "validate_mock_fixtures",
    "validate_runtime_supplement",
    "validate_semantics",
]
