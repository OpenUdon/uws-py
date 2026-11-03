"""YAML decoding with Go verdicts.

Go reads a YAML core document with ``yaml.Unmarshal`` into a ``yaml.Node`` (first document only) and converts it
with ``convert.yamlNodeToJSONCompatible``: mapping keys must be plain strings, duplicate keys are refused, ``!!int``
and ``!!float`` scalars that are valid JSON numbers keep their text, every other scalar is decoded with yaml.v3's
resolver, and the result must survive ``json.Marshal`` (no NaN or infinity). PyYAML composes the node graph; the
rest is ported.
"""

from __future__ import annotations

import re
from typing import Any

import yaml as pyyaml
from yaml.composer import Composer, ComposerError
from yaml.events import AliasEvent, ScalarEvent, SequenceStartEvent
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode
from yaml.parser import Parser
from yaml.reader import Reader
from yaml.resolver import BaseResolver
from yaml.scanner import Scanner

from ..diagnostics import DecodeError
from . import goyaml
from .json import JsonValue, parse_strict_json

_JSON_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", re.ASCII)
_QUOTED_STYLES = ("'", '"', "|", ">")
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


class _Alias:
    """An alias occurrence. PyYAML returns the anchored node itself; Go keeps a distinct ``AliasNode``."""

    __slots__ = ("target",)

    def __init__(self, target: Node) -> None:
        self.target = target


class _Resolver(BaseResolver):
    def resolve(self, kind: type, value: Any, implicit: Any) -> str:
        if kind is ScalarNode:
            return ""
        if kind is SequenceNode:
            return "tag:yaml.org,2002:seq"
        return "tag:yaml.org,2002:map"


class _Loader(Reader, Scanner, Parser, Composer, _Resolver):
    """PyYAML's loader stack, except that aliases stay distinct and anchors may be redefined (as in yaml.v3)."""

    def __init__(self, stream: str | bytes) -> None:
        Reader.__init__(self, stream)
        Scanner.__init__(self)
        Parser.__init__(self)
        Composer.__init__(self)
        _Resolver.__init__(self)

    def compose_node(self, parent: Node | None, index: Any) -> Any:
        if self.check_event(AliasEvent):
            event = self.get_event()  # type: ignore[no-untyped-call]
            anchor = event.anchor
            if anchor not in self.anchors:
                raise ComposerError(None, None, f"unknown anchor '{anchor}' referenced", event.start_mark)
            return _Alias(self.anchors[anchor])
        self.descend_resolver(parent, index)
        node: Node
        anchor = self.peek_event().anchor  # type: ignore[no-untyped-call]
        if self.check_event(ScalarEvent):
            node = self.compose_scalar_node(anchor)
        elif self.check_event(SequenceStartEvent):
            node = self.compose_sequence_node(anchor)
        else:
            node = self.compose_mapping_node(anchor)
        self.ascend_resolver()
        return node


def _explicit_tag(node: Node) -> str:
    tag = node.tag or ""
    return "" if tag == "!" else goyaml.short_tag(tag)


def _indicated_string(node: Any) -> bool:
    if not isinstance(node, ScalarNode):
        return False
    tag = _explicit_tag(node)
    return tag == goyaml.STR_TAG or (tag == "" and node.style in _QUOTED_STYLES)


def _short_tag(node: Any, depth: int = 0) -> str:
    """yaml.v3 ``Node.ShortTag``."""
    if isinstance(node, _Alias):
        return "" if depth > 100 else _short_tag(node.target, depth + 1)
    if _indicated_string(node):
        return goyaml.STR_TAG
    tag = _explicit_tag(node)
    if tag:
        return tag
    if isinstance(node, MappingNode):
        return goyaml.MAP_TAG
    if isinstance(node, SequenceNode):
        return goyaml.SEQ_TAG
    return goyaml.resolve("", node.value)[0]


class _Converter:
    def __init__(self) -> None:
        self.active: set[int] = set()
        self.decode_count = 0
        self.alias_count = 0
        self.alias_depth = 0

    def _count(self) -> None:
        # yaml.v3 decoder's alias-expansion guard ("document contains excessive aliasing").
        self.decode_count += 1
        if self.alias_depth > 0:
            self.alias_count += 1
        if self.alias_count > 100 and self.decode_count > 1000:
            ratio = self.alias_count / self.decode_count
            if ratio > _allowed_alias_ratio(self.decode_count):
                raise DecodeError("invalid YAML: document contains excessive aliasing")

    def convert(self, node: Any, path: str) -> JsonValue:
        self._count()
        if isinstance(node, _Alias):
            key = id(node.target)
            if key in self.active:
                raise DecodeError(f"invalid YAML: {path}: anchor value contains itself")
            self.active.add(key)
            self.alias_depth += 1
            try:
                return self.convert(node.target, path)
            finally:
                self.alias_depth -= 1
                self.active.discard(key)
        if isinstance(node, MappingNode):
            return self._mapping(node, path)
        if isinstance(node, SequenceNode):
            return [self.convert(item, f"{path}[{i}]") for i, item in enumerate(node.value)]
        return self._scalar(node, path)

    def _mapping(self, node: MappingNode, path: str) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {}
        merge: Any = None
        for key_node, value_node in node.value:
            if _short_tag(key_node) == goyaml.MERGE_TAG:
                if merge is not None:
                    raise DecodeError(f"invalid YAML: {path}: YAML merge key is defined more than once")
                merge = value_node
                continue
            if not isinstance(key_node, ScalarNode) or _short_tag(key_node) != goyaml.STR_TAG:
                value = key_node.target.value if isinstance(key_node, _Alias) else key_node.value
                raise DecodeError(
                    f"invalid YAML: {path}: YAML mapping key {value!r} has tag {_short_tag(key_node)!r}; "
                    "only string keys are supported"
                )
            key = key_node.value
            if key in result:
                raise DecodeError(f"invalid YAML: {path}: YAML mapping key {key!r} is defined more than once")
            result[key] = self.convert(value_node, f"{path}.{key}")
        if merge is not None:
            for mapping in self._merge(merge, f"{path}.<<"):
                for key, value in mapping.items():
                    if key not in result:
                        result[key] = value
        return result

    def _merge(self, node: Any, path: str) -> list[dict[str, JsonValue]]:
        if isinstance(node, _Alias):
            key = id(node.target)
            if key in self.active:
                raise DecodeError(f"invalid YAML: {path}: anchor value contains itself")
            self.active.add(key)
            try:
                return self._merge(node.target, path)
            finally:
                self.active.discard(key)
        if isinstance(node, SequenceNode):
            out: list[dict[str, JsonValue]] = []
            for i, item in enumerate(node.value):
                out.extend(self._merge(item, f"{path}[{i}]"))
            return out
        if not isinstance(node, MappingNode):
            raise DecodeError(f"invalid YAML: {path}: YAML map merge requires a map or sequence of maps")
        return [self._mapping(node, path)]

    def _scalar(self, node: ScalarNode, path: str) -> JsonValue:
        tag = _short_tag(node)
        text: str = node.value
        stored = _explicit_tag(node) or goyaml.resolve("", text)[0]
        if tag in (goyaml.INT_TAG, goyaml.FLOAT_TAG) and _JSON_NUMBER.fullmatch(text):
            # Go keeps the number text here, but the schema layer re-reads the YAML with yaml.v3, whose
            # resolver must also accept the scalar (``!!int 1.5`` is refused there).
            try:
                goyaml.resolve(stored, text)
            except goyaml.ResolveError as exc:
                raise DecodeError(f"invalid YAML: {path}: {exc}") from exc
            return parse_strict_json(text)
        if _LONE_SURROGATE.search(text):
            raise DecodeError(f"invalid YAML: {path}: found invalid Unicode character escape code")
        if _indicated_string(node):
            return text
        try:
            rtag, out = goyaml.resolve(stored, text)
            if rtag == goyaml.BINARY_TAG:
                out = goyaml.decode_binary(out)
        except goyaml.ResolveError as exc:
            raise DecodeError(f"invalid YAML: {path}: {exc}") from exc
        if isinstance(out, goyaml.GoFloat):
            raise DecodeError(f"invalid YAML: {path}: json: unsupported value: {out.value}")
        return out


def _allowed_alias_ratio(decode_count: int) -> float:
    low, high = 400_000, 4_000_000
    if decode_count <= low:
        return 0.99
    if decode_count >= high:
        return 0.10
    return 0.99 - 0.89 * ((decode_count - low) / (high - low))


def parse_strict_yaml(text: str | bytes) -> JsonValue:
    """Decodes the first YAML document into a JSON-compatible value with Go ``uws/convert`` semantics."""
    try:
        loader = _Loader(text)
    except pyyaml.YAMLError as exc:
        raise DecodeError(f"invalid YAML: {exc}") from exc
    try:
        try:
            root = loader.get_node() if loader.check_node() else None
        except pyyaml.YAMLError as exc:
            raise DecodeError(f"invalid YAML: {exc}") from exc
        except RecursionError as exc:
            raise DecodeError("invalid YAML: nesting is too deep") from exc
        if loader.yaml_version is not None and loader.yaml_version != (1, 1):
            raise DecodeError("invalid YAML: found incompatible YAML document")
        if root is None:
            return None
        try:
            return _Converter().convert(root, "document")
        except RecursionError as exc:
            raise DecodeError("invalid YAML: nesting is too deep") from exc
    finally:
        loader.dispose()
