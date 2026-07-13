#!/usr/bin/env python3
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from assembly_ast_cfg import parse_src
from assembly_event_ir import keccak256


@dataclass
class SelectorInfo:
    selector: str
    signature: str
    name: str
    kind: str
    contract: str | None
    source: str
    src: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_selector_registry(ast: dict[str, Any], source_text: str) -> dict[str, list[dict[str, Any]]]:
    registry: dict[str, list[SelectorInfo]] = {}
    current_contract: list[str | None] = [None]

    def add(info: SelectorInfo) -> None:
        bucket = registry.setdefault(info.selector.lower(), [])
        key = (info.signature, info.kind, info.contract, info.source)
        if not any((item.signature, item.kind, item.contract, item.source) == key for item in bucket):
            bucket.append(info)

    def visit(node: Any) -> None:
        if not isinstance(node, dict):
            if isinstance(node, list):
                for item in node:
                    visit(item)
            return
        node_type = node.get("nodeType")
        if node_type == "ContractDefinition":
            previous = current_contract[0]
            current_contract[0] = node.get("name")
            for child in node.get("nodes") or []:
                visit(child)
            current_contract[0] = previous
            return
        if node_type == "FunctionDefinition":
            name = node.get("name")
            if name:
                signature = abi_signature(name, node.get("parameters") or {})
                if signature:
                    add(SelectorInfo(selector_for(signature), signature, name, "function", current_contract[0], "ast", str(node.get("src", ""))))
        elif node_type == "ErrorDefinition":
            name = node.get("name")
            if name:
                signature = abi_signature(name, node.get("parameters") or {})
                if signature:
                    add(SelectorInfo(selector_for(signature), signature, name, "error", current_contract[0], "ast", str(node.get("src", ""))))
        for child in node.values():
            visit(child)

    visit(ast)
    for hint in selector_comment_hints(source_text):
        add(hint)
    return {selector: [item.to_dict() for item in values] for selector, values in registry.items()}


def selector_for(signature: str) -> str:
    return "0x" + keccak256(signature.encode("utf-8"))[:4].hex()


def abi_signature(name: str, parameter_list: dict[str, Any]) -> str | None:
    params = parameter_list.get("parameters") or []
    types: list[str] = []
    for param in params:
        abi_type = canonical_abi_type(param)
        if not abi_type:
            return None
        types.append(abi_type)
    return f"{name}({','.join(types)})"


def canonical_abi_type(param: dict[str, Any]) -> str | None:
    type_string = str((param.get("typeDescriptions") or {}).get("typeString") or "").strip()
    if not type_string:
        type_string = type_name_text(param.get("typeName"))
    return canonical_abi_type_text(type_string)


def type_name_text(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    if node.get("nodeType") == "ElementaryTypeName":
        return str(node.get("name") or "")
    if node.get("nodeType") == "UserDefinedTypeName":
        return str(node.get("name") or node.get("pathNode", {}).get("name") or "")
    if node.get("nodeType") == "ArrayTypeName":
        base = type_name_text(node.get("baseType"))
        length = node.get("length")
        length_text = ""
        if isinstance(length, dict):
            length_text = str(length.get("value") or "")
        return f"{base}[{length_text}]"
    return str(node.get("name") or "")


def canonical_abi_type_text(text: str) -> str | None:
    if not text:
        return None
    text = re.sub(r"\b(memory|calldata|storage|payable)\b", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    array_suffix = ""
    while True:
        match = re.search(r"(\[[0-9]*\])$", text)
        if not match:
            break
        array_suffix = match.group(1) + array_suffix
        text = text[: match.start()].strip()
    if text.startswith("contract "):
        return "address" + array_suffix
    if text.startswith("enum "):
        return "uint8" + array_suffix
    if text in {"uint", "int"}:
        return f"{text}256{array_suffix}"
    if text.startswith("bytes ") or text == "bytes":
        return "bytes" + array_suffix
    if text.startswith("string ") or text == "string":
        return "string" + array_suffix
    if re.match(r"^(address|bool|uint[0-9]+|int[0-9]+|bytes[0-9]+)$", text):
        return text + array_suffix
    if text.startswith("struct ") or text.startswith("mapping("):
        return None
    # User-defined value types usually appear as the underlying type in modern
    # solc typeDescriptions; if a bare name reaches here, avoid guessing.
    return None


def selector_comment_hints(source_text: str) -> Iterable[SelectorInfo]:
    # Covers common hand-written assembly comments:
    #   mstore(0x00, 0x12345678) // `foo(address)`.
    # The selector is accepted only when the signature re-hashes to the literal.
    signature_re = re.compile(r"`([A-Za-z_$][A-Za-z0-9_$]*\([^`)]*\))`")
    selector_re = re.compile(r"\b0x([0-9a-fA-F]{8})\b")
    for line_no, line in enumerate(source_text.splitlines(), start=1):
        selector_match = selector_re.search(line)
        if not selector_match:
            continue
        selector = "0x" + selector_match.group(1).lower()
        for sig_match in signature_re.finditer(line):
            signature = sig_match.group(1)
            if selector_for(signature).lower() != selector:
                continue
            name = signature.split("(", 1)[0]
            kind = "error" if name and name[0].isupper() else "function"
            yield SelectorInfo(selector, signature, name, kind, None, "comment", f"line:{line_no}")


def normalize_selector_value(value: Any) -> str | None:
    text = str(value or "").strip()
    low = re.match(r"^low_bytes\((0x[0-9a-fA-F]+),\s*4\)$", text)
    if low:
        return selector_from_literal(low.group(1))
    literal = re.fullmatch(r"0x[0-9a-fA-F]+", text)
    if literal:
        return selector_from_literal(text)
    return None


def selector_from_literal(value: str) -> str | None:
    try:
        number = int(str(value), 16)
    except Exception:
        return None
    return f"0x{number & 0xffffffff:08x}"
