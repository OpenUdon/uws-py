"""Shared profile helpers ported from upstream ``schemas/schema.go`` (origins, contexts, navigation targets)."""

from __future__ import annotations

import json
import re
from typing import Any

from ..gostrings import go_equal_fold, go_to_lower
from ..schema.goformats import is_loopback_ip, parse_url

Obj = dict[str, Any]

_ESCAPE = re.compile(rb"%([0-9A-Fa-f]{2})")


class ProfileError(ValueError):
    """The first profile problem, as upstream returns a single error."""


def q(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def as_obj(v: Any) -> Obj:
    """``v.(map[string]any)`` with the Go zero value (an empty map) on mismatch."""
    return v if type(v) is dict else {}


def string_field(o: Any, key: str) -> str:
    v = o.get(key) if type(o) is dict else None
    return v if type(v) is str else ""


def path_unescape(segment: str) -> str | None:
    """``url.PathUnescape``: decodes ``%XX`` bytes; ``None`` on a malformed escape. Bytes survive as
    ``surrogateescape`` characters."""
    raw = segment.encode("utf-8", errors="surrogateescape")
    i = 0
    while i < len(raw):
        if raw[i] == 0x25:
            hexpart = raw[i + 1 : i + 3]
            if len(hexpart) != 2 or not all(c in b"0123456789abcdefABCDEF" for c in hexpart):
                return None
            i += 3
        else:
            i += 1
    decoded = _ESCAPE.sub(lambda m: bytes([int(m.group(1), 16)]), raw)
    return decoded.decode("utf-8", errors="surrogateescape")


def is_loopback_host(host: str) -> bool:
    return go_equal_fold(host, "localhost") or is_loopback_ip(host)


def validate_authentication_origin(raw: str) -> None:
    u = parse_url(raw)
    if u is None:
        raise ProfileError("invalid origin")
    if u.host == "" or u.user is not None or u.raw_query != "" or u.fragment != "" or u.path not in ("", "/"):
        raise ProfileError("must be an exact origin without credentials, path, query, or fragment")
    host = u.hostname()
    loopback = host == "localhost" or is_loopback_ip(host)
    if u.scheme != "https" and not (u.scheme == "http" and loopback):
        raise ProfileError("must use https (http is allowed only for loopback)")


def canonical_origin(raw: str) -> str:
    """Upstream ``canonicalAuthenticationOrigin``."""
    u = parse_url(raw)
    if u is None or u.host == "":
        return ""
    host = go_to_lower(u.hostname())
    port = u.port()
    if (u.scheme == "https" and port == "443") or (u.scheme == "http" and port == "80"):
        port = ""
    if port != "":
        host = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    elif ":" in host:
        host = f"[{host}]"
    return f"{go_to_lower(u.scheme)}://{host}"


def validate_authentication_target(raw: str, origins: set[str]) -> None:
    u = parse_url(raw)
    if u is None or not u.is_abs or u.host == "":
        raise ProfileError("must be an absolute URL")
    if u.user is not None or (u.scheme != "https" and not (u.scheme == "http" and is_loopback_host(u.hostname()))):
        raise ProfileError("must use a declared safe origin")
    if canonical_origin(raw) not in origins:
        raise ProfileError("target origin is not declared by info")


def is_clean_browser_path(raw: str) -> bool:
    if raw == "" or not raw.startswith("/") or any(c in raw for c in "?#\\"):
        return False
    segments = raw.split("/")
    for i, segment in enumerate(segments[1:]):
        if segment == "" and i != len(segments) - 2 and raw != "/":
            return False
        decoded = path_unescape(segment)
        if decoded is None or decoded in (".", "..") or "/" in decoded:
            return False
    return True


def browser_navigate(value: Any) -> tuple[str, str]:
    if type(value) is str:
        return value, "main"
    obj = as_obj(value)
    context_id = string_field(obj, "context") or "main"
    return string_field(obj, "url"), context_id


def browser_profile_origins(root: Obj) -> set[str]:
    origins: set[str] = set()
    origin = as_obj(root.get("info")).get("origin")
    if type(origin) is str:
        origins.add(canonical_origin(origin))
    elif type(origin) is list:
        for o in origin:
            if type(o) is str:
                origins.add(canonical_origin(o))
    return origins


def validate_context_target(root: Obj, context_id: str, raw: str) -> None:
    if context_id in ("", "main"):
        return
    context = as_obj(root.get("contexts")).get(context_id)
    if type(context) is not dict:
        raise ProfileError(f"unknown context {q(context_id)}")
    u = parse_url(raw)
    if u is None:
        raise ProfileError("invalid target URL")
    if u.is_abs and canonical_origin(raw) != canonical_origin(string_field(context, "origin")):
        raise ProfileError(f"target origin does not match context {q(context_id)}")


def validate_context_origin(root: Obj, context_id: str, origin: str) -> None:
    if context_id in ("", "main"):
        return
    context = as_obj(root.get("contexts")).get(context_id)
    if type(context) is not dict:
        raise ProfileError(f"unknown context {q(context_id)}")
    if canonical_origin(string_field(context, "origin")) != canonical_origin(origin):
        raise ProfileError(f"origin does not match context {q(context_id)}")


def _validate_browser_target(root: Obj, raw: str, context_id: str, origins: set[str]) -> None:
    u = parse_url(raw)
    if u is None or raw == "" or u.user is not None or u.fragment != "":
        raise ProfileError("must be a safe URL")
    if u.is_abs:
        if u.scheme != "https" and not (u.scheme == "http" and is_loopback_host(u.hostname())):
            raise ProfileError("must use a declared safe origin")
        if canonical_origin(raw) not in origins:
            raise ProfileError("target origin is not declared by info")
    else:
        if not raw.startswith("/"):
            raise ProfileError("relative target must be root-relative")
        if len(origins) != 1:
            raise ProfileError("relative target requires exactly one info origin")
    validate_context_target(root, context_id, raw)


def validate_browser_contexts(root: Obj, origins: set[str]) -> None:
    """Upstream ``validateBrowserContexts`` (browser 1.6+ and authentication 1.1)."""
    contexts = as_obj(root.get("contexts"))
    info = root.get("info")
    if type(info) is dict:
        value = info.get("origin")
        if type(value) is str:
            _wrap("info.origin", validate_authentication_origin, value)
        elif type(value) is list:
            for i, raw in enumerate(value):
                _wrap(f"info.origin[{i}]", validate_authentication_origin, raw if type(raw) is str else "")
    for ident, raw in contexts.items():
        context = as_obj(raw)
        origin = string_field(context, "origin")
        _wrap(f"contexts.{ident}.origin", validate_authentication_origin, origin)
        if canonical_origin(origin) not in origins:
            raise ProfileError(f"contexts.{ident}.origin: origin is not declared by profile info")
        parent = string_field(context, "parent")
        if parent == ident:
            raise ProfileError(f"contexts.{ident}.parent: context cannot parent itself")
        if parent != "main" and parent not in contexts:
            raise ProfileError(f"contexts.{ident}.parent: unknown context {q(parent)}")
        path = string_field(context, "path")
        if path != "" and not is_clean_browser_path(path):
            raise ProfileError(f"contexts.{ident}.path: must be an exact clean path")

    depths: dict[str, int] = {}
    visiting: set[str] = set()

    def depth(ident: str) -> int:
        if ident == "main":
            return 0
        if ident in depths:
            return depths[ident]
        if ident in visiting:
            raise ProfileError(f"contexts.{ident}.parent: context graph contains a cycle")
        visiting.add(ident)
        got = depth(string_field(as_obj(contexts.get(ident)), "parent")) + 1
        visiting.discard(ident)
        if got > 4:
            raise ProfileError(f"contexts.{ident}: context depth exceeds 4")
        depths[ident] = got
        return got

    for ident in contexts:
        depth(ident)

    opened: dict[str, int] = {}
    stack: list[tuple[Any, str]] = [(root, "document")]
    while stack:
        value, value_path = stack.pop()
        if type(value) is dict:
            context_id = value.get("context")
            if type(context_id) is str and context_id != "main" and context_id not in contexts:
                raise ProfileError(f"{value_path}.context: unknown context {q(context_id)}")
            opened_id = value.get("opensContext")
            if type(opened_id) is str:
                popup = contexts.get(opened_id)
                if type(popup) is not dict or popup.get("kind") != "popup":
                    raise ProfileError(f"{value_path}.opensContext: {q(opened_id)} is not a declared popup context")
                parent = string_field(value, "context") or "main"
                if popup.get("parent") != parent:
                    raise ProfileError(f"{value_path}.opensContext: popup parent does not match click context")
                opened[opened_id] = opened.get(opened_id, 0) + 1
            for key, child in value.items():
                if value_path == "document" and key == "contexts":
                    continue
                stack.append((child, f"{value_path}.{key}"))
        elif type(value) is list:
            stack.extend((child, f"{value_path}[{i}]") for i, child in enumerate(value))
    for ident, raw in contexts.items():
        if as_obj(raw).get("kind") == "popup" and opened.get(ident, 0) != 1:
            raise ProfileError(f"contexts.{ident}: popup must be opened by exactly one approved click")

    actions = root.get("actions")
    if type(actions) is dict:
        for action_id, raw_action in actions.items():
            sequence = as_obj(raw_action).get("sequence")
            for i, raw_step in enumerate(sequence if type(sequence) is list else []):
                step = as_obj(raw_step)
                if "navigate" in step:
                    target, context_id = browser_navigate(step["navigate"])
                    try:
                        _validate_browser_target(root, target, context_id, origins)
                    except ProfileError as exc:
                        raise ProfileError(f"actions.{action_id}.sequence[{i}].navigate: {exc}") from None


def _wrap(prefix: str, check: Any, value: Any) -> None:
    try:
        check(value)
    except ProfileError as exc:
        raise ProfileError(f"{prefix}: {exc}") from None
