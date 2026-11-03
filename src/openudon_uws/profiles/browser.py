"""Browser capability profile checks (upstream ``schemas.ValidateBrowserSourceProfile`` with
``browser_templates.go`` for 1.8 and ``browser19_templates.go`` for 1.9 and 1.10)."""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..schema.goformats import escaped_path, parse_url, user_string
from .common import (
    Obj,
    ProfileError,
    as_obj,
    browser_profile_origins,
    path_unescape,
    q,
    validate_browser_contexts,
)

BROWSER_PROFILES = (
    "uws.browser.1.5",
    "uws.browser.1.6",
    "uws.browser.1.7",
    "uws.browser.1.8",
    "uws.browser.1.9",
    "uws.browser.1.10",
)
_TEMPLATE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\Z", re.ASCII)
_MAX_SAFE = 9007199254740991


def check_browser_profile(profile: str, root: Obj) -> None:
    """Semantic checks after schema validation; raises :class:`ProfileError` on the first problem."""
    if profile != "uws.browser.1.5":
        validate_browser_contexts(root, browser_profile_origins(root))
    if profile == "uws.browser.1.8":
        _validate_templates(root, _parse18_names, _validate18_navigate, None)
    if profile in ("uws.browser.1.9", "uws.browser.1.10"):
        _validate19_integer_defaults(root)
        _validate_templates(root, _parse19_names, _validate19_navigate, _validate19_text)


def _parameter_properties(raw: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, prop in as_obj(as_obj(raw).get("properties")).items():
        p = as_obj(prop)
        typ = p.get("type") if type(p.get("type")) is str else ""
        if "oneOf" in p or "anyOf" in p:
            typ = ""
        out[name] = typ if typ in ("string", "boolean", "integer", "number") else ""
    return out


def _parameter_defaults(raw: Any) -> dict[str, Any]:
    return {
        name: as_obj(prop)["default"]
        for name, prop in as_obj(as_obj(raw).get("properties")).items()
        if "default" in as_obj(prop)
    }


def _validate_templates(
    root: Obj,
    parse_names: Callable[[str], list[str]],
    validate_navigate: Callable[[str], None],
    validate_text: Callable[[str, str], None] | None,
) -> None:
    for action_name, raw_action in as_obj(root.get("actions")).items():
        action = as_obj(raw_action)
        properties = _parameter_properties(action.get("parameters"))
        defaults = _parameter_defaults(action.get("parameters"))
        allowed: set[str] = set()
        sequence = action.get("sequence")
        for index, raw_step in enumerate(sequence if type(sequence) is list else []):
            step = as_obj(raw_step)
            if "navigate" in step:
                path = f"actions.{action_name}.sequence[{index}].navigate"
                nav = step["navigate"]
                target = ""
                if type(nav) is str:
                    target = nav
                elif type(nav) is dict:
                    url = nav.get("url")
                    target = url if type(url) is str else ""
                    path += ".url"
                allowed.add(path)
                try:
                    validate_navigate(target)
                except ProfileError as exc:
                    raise ProfileError(f"{path}: {exc}") from None
            for key in ("type_text", "select_option"):
                if type(step.get(key)) is dict:
                    allowed.add(f"actions.{action_name}.sequence[{index}].{key}.value")
        if type(action.get("confirmationPolicy")) is dict:
            allowed.add(f"actions.{action_name}.confirmationPolicy.prompt")

        stack: list[tuple[Any, str]] = [(raw_action, f"actions.{action_name}")]
        while stack:
            value, path = stack.pop()
            if type(value) is dict:
                stack.extend((child, f"{path}.{key}") for key, child in value.items())
                continue
            if type(value) is list:
                stack.extend((child, f"{path}[{i}]") for i, child in enumerate(value))
                continue
            if type(value) is not str:
                continue
            if validate_text is not None:
                validate_text(path, value)
            if "{{" not in value and "}}" not in value:
                continue
            if path not in allowed:
                raise ProfileError(f"{path}: parameter templates are not permitted in this field")
            try:
                names = parse_names(value)
            except ProfileError as exc:
                raise ProfileError(f"{path}: {exc}") from None
            for name in names:
                if name not in properties:
                    raise ProfileError(f"{path}: template parameter {q(name)} is not declared in parameters.properties")
                if properties[name] == "":
                    raise ProfileError(
                        f"{path}: parameter {q(name)} must have exactly one scalar type "
                        "(string, boolean, integer, or number)"
                    )
                default = defaults.get(name)
                if validate_text is not None and type(default) is str:
                    try:
                        validate_text(path, default)
                    except ProfileError as exc:
                        raise ProfileError(f"{path}: default for parameter {q(name)}: {exc}") from None


def _check_name(name: str) -> None:
    if "{" in name or "}" in name or _TEMPLATE_NAME.match(name) is None:
        raise ProfileError(f"invalid template placeholder {q(name)}")


def _parse18_names(value: str) -> list[str]:
    names: list[str] = []
    index = 0
    while index < len(value):
        open_ = value.find("{{", index)
        close = value.find("}}", index)
        if close >= 0 and (open_ < 0 or close < open_):
            raise ProfileError("unmatched template closing braces")
        if open_ < 0:
            break
        end = value.find("}}", open_ + 2)
        if end < 0:
            raise ProfileError("unmatched template opening braces")
        name = value[open_ + 2 : end]
        _check_name(name)
        names.append(name)
        index = end + 2
    return names


@dataclass(frozen=True)
class _Token:
    start: int
    end: int
    name: str
    parameter: bool


def _parse19_tokens(value: str) -> list[_Token]:
    tokens: list[_Token] = []
    index = 0
    while index < len(value):
        if value.startswith("{{{{", index) or value.startswith("}}}}", index):
            tokens.append(_Token(index, index + 4, "", False))
            index += 4
        elif value.startswith("{{", index):
            end = value.find("}}", index + 2)
            if end < 0:
                raise ProfileError("unmatched template opening braces")
            name = value[index + 2 : end]
            _check_name(name)
            tokens.append(_Token(index, end + 2, name, True))
            index = end + 2
        elif value.startswith("}}", index):
            raise ProfileError("unmatched template closing braces")
        else:
            index += 1
    return tokens


def _parse19_names(value: str) -> list[str]:
    return [t.name for t in _parse19_tokens(value) if t.parameter]


def _check_navigate_markers(marked: str, markers: list[str], what: str) -> None:
    u = parse_url(marked)
    if u is None:
        raise ProfileError("navigate URL template is malformed")
    userinfo = user_string(u)
    for marker in markers:
        if marker in u.scheme or marker in u.host or marker in userinfo or marker in u.fragment:
            raise ProfileError(f"navigate {what} forbidden in scheme, authority, or fragment")
    for pair in u.raw_query.split("&"):
        key = pair.partition("=")[0]
        for marker in markers:
            if marker in key:
                raise ProfileError(f"navigate {what} forbidden in query parameter names")
    for segment in escaped_path(u).split("/"):
        decoded = path_unescape(segment)
        if decoded is None:
            raise ProfileError("navigate path contains malformed percent encoding")
        if decoded in (".", ".."):
            raise ProfileError("navigate path must not contain dot segments")


def _validate18_navigate(target: str) -> None:
    _parse18_names(target)
    markers: list[str] = []
    marked: list[str] = []
    index = 0
    while index < len(target):
        open_ = target.find("{{", index)
        if open_ < 0:
            marked.append(target[index:])
            break
        marked.append(target[index:open_])
        end = target.find("}}", open_ + 2)
        marker = f"UWSPLACEHOLDER{len(markers)}TOKEN"
        markers.append(marker)
        marked.append(marker)
        index = end + 2
    _check_navigate_markers("".join(marked), markers, "placeholders are")


def _validate19_navigate(target: str) -> None:
    markers: list[str] = []
    marked: list[str] = []
    last = 0
    for token in _parse19_tokens(target):
        marked.append(target[last : token.start])
        marker = f"UWSBROWSER19TOKEN{len(markers)}END"
        markers.append(marker)
        marked.append(marker)
        last = token.end
    marked.append(target[last:])
    _check_navigate_markers("".join(marked), markers, "placeholders and brace escapes are")


def _validate19_text(path: str, value: str) -> None:
    if not path.endswith(".type_text.value") and not path.endswith(".confirmationPolicy.prompt"):
        return
    for ch in value:
        c = ord(ch)
        control = c < 0x20 or 0x7F <= c <= 0x9F  # unicode.IsControl (Latin-1 controls only)
        bidi = c in (0x061C, 0x200E, 0x200F) or 0x202A <= c <= 0x202E or 0x2066 <= c <= 0x2069
        if control or c in (0x2028, 0x2029) or bidi:
            raise ProfileError("contains a prohibited control, line-separator, or bidi-control character")


def _is_safe_integer(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        f = float(value)  # Go holds JSON numbers as float64 here
    except OverflowError:
        return False
    return not math.isinf(f) and not math.isnan(f) and math.trunc(f) == f and -_MAX_SAFE <= f <= _MAX_SAFE


def _validate19_integer_defaults(root: Obj) -> None:
    for action_name, raw_action in as_obj(root.get("actions")).items():
        properties = as_obj(as_obj(as_obj(raw_action).get("parameters")).get("properties"))
        for name, raw_prop in properties.items():
            prop = as_obj(raw_prop)
            if prop.get("type") != "integer" or "default" not in prop:
                continue
            if not _is_safe_integer(prop["default"]):
                raise ProfileError(
                    f"actions.{action_name}.parameters.properties.{name}.default: "
                    "integer default must be a safe integer"
                )
