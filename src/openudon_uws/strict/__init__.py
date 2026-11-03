"""Strict decoding of one JSON or YAML document."""

from __future__ import annotations

from typing import Literal

from ..diagnostics import DecodeError
from .json import JsonValue, decode_utf8, parse_strict_json
from .yaml import parse_profile_yaml, parse_strict_yaml

__all__ = [
    "DEFAULT_MAX_BYTES",
    "InputFormat",
    "JsonValue",
    "decode_document",
    "decode_utf8",
    "format_for_path",
    "parse_profile_yaml",
    "parse_strict_json",
    "parse_strict_yaml",
]

InputFormat = Literal["json", "yaml", "auto"]

DEFAULT_MAX_BYTES = 16 * 1024 * 1024


def format_for_path(path: str) -> InputFormat:
    """Go selects the decoder by extension: ``.json`` is JSON, ``.yaml``/``.yml`` is YAML."""
    lower = path.lower()
    if lower.endswith(".json"):
        return "json"
    if lower.endswith((".yaml", ".yml")):
        return "yaml"
    raise DecodeError(f"unsupported UWS document extension in {path!r}")


def decode_document(
    data: str | bytes,
    *,
    format: InputFormat = "auto",
    reject_duplicate_keys: bool = False,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> JsonValue:
    """Decodes one JSON or YAML document from text or UTF-8 bytes.

    ``format="auto"`` decodes JSON when the input is a JSON document and YAML otherwise, so a YAML flow mapping
    such as ``{uws: 1.13.0}`` is read as YAML. ``reject_duplicate_keys`` applies to JSON; YAML always refuses
    duplicate keys, as Go does.
    """
    size = len(data) if isinstance(data, bytes) else len(data.encode("utf-8", errors="surrogatepass"))
    if size > max_bytes:
        raise DecodeError(f"document exceeds {max_bytes} bytes")
    if format == "yaml":
        return parse_strict_yaml(data)
    text = decode_utf8(data) if isinstance(data, bytes) else data
    if format == "json":
        return parse_strict_json(text, reject_duplicates=reject_duplicate_keys)
    if not text.lstrip(" \t\r\n").startswith(("{", "[")):
        return parse_strict_yaml(data)
    try:
        return parse_strict_json(text, reject_duplicates=reject_duplicate_keys)
    except DecodeError as json_error:
        try:
            return parse_strict_yaml(data)
        except DecodeError:
            raise json_error from None
