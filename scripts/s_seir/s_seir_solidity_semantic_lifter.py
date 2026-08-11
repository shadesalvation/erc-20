#!/usr/bin/env python3
from __future__ import annotations

import re
from typing import Any

from s_seir_semantic_fact_adapter import (
    SSeirFactAdapter,
    SlitherFactAdapter,
    clean_dict,
)


HIGH_LEVEL_SOLIDITY_OVERLAYS = {
    "StateVariableRead",
    "StateVariableWrite",
    "MappingRead",
    "MappingWrite",
    "PathConditionedStorageRead",
    "PathConditionedStorageWrite",
    "EventEmit",
    "PathConditionedEventEmit",
    "RequireOverlay",
    "CustomErrorRevert",
    "RawRevertBytes",
    "RevertOverlay",
    "PathConditionedCustomErrorRevert",
    "PathConditionedRevert",
    "ExternalCall",
    "LowLevelCall",
    "StaticCallOverlay",
    "DelegateCallOverlay",
    "PathConditionedPrecompileCall",
    "PathConditionedExternalCall",
    "PathConditionedLowLevelCall",
    "PrecompileCall",
    "InternalCall",
    "ReturnValue",
    "MemoryRegionAllocate",
    "MemoryArrayConstruction",
    "StructMemoryMutation",
    "StructInitializationFragment",
    "MemoryRegionWrite",
    "CursorBasedMemoryWrite",
}


# These SlithIR operations are already semantic sinks. They are used only when
# no Solidity overlay covers the same source statement and behavior.
HIGH_LEVEL_SLITHIR_FALLBACK_KINDS = {
    "NewContract",
    "NewArray",
    "NewStructure",
    "NewElementaryType",
    "Delete",
    "ValueTransferCall",
}


class SoliditySemanticLifter:
    """Project Solidity code to high-level SemanticFact rows.

    This lifter does not expose every SlithIR SSA operation as a final fact.
    It consumes the Solidity overlays already built inside S-SEIR and uses
    SlithIR only as evidence or as a conservative fallback for sink operations
    that are not currently represented by overlays.
    """

    def __init__(self) -> None:
        self.overlay_adapter = SSeirFactAdapter()
        self.slither_adapter = SlitherFactAdapter()

    def facts_from_function(self, fn: Any) -> list[dict[str, Any]]:
        fn_dict = fn.to_semantic_dict() if hasattr(fn, "to_semantic_dict") else fn
        if not isinstance(fn_dict, dict):
            return []
        overlay_facts = self.overlay_facts(fn_dict)
        fallback_facts = self.slithir_sink_fallback_facts(fn, overlay_facts)
        return overlay_facts + fallback_facts

    def overlay_facts(self, fn: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        stmt_lang = self.overlay_adapter.statement_languages(fn)
        effect_by_id = {
            item.get("effect_id"): item
            for item in fn.get("effects") or []
            if isinstance(item, dict)
        }
        for overlay in fn.get("semantic_overlays") or []:
            if not isinstance(overlay, dict):
                continue
            kind = str(overlay.get("kind") or "")
            if kind not in HIGH_LEVEL_SOLIDITY_OVERLAYS:
                continue
            if overlay_source_lang(overlay, stmt_lang, effect_by_id) != "solidity":
                continue
            lifted_overlay = self.overlay_adapter.overlay_with_effect_refs(overlay, effect_by_id)
            for fact in self.overlay_adapter.overlay_facts(fn, lifted_overlay, stmt_lang, effect_by_id):
                item = fact.to_dict()
                item["source_lang"] = "solidity"
                item["origin"] = "slither_lifted"
                evidence = dict(item.get("evidence") or {})
                evidence["lifted_from"] = "sseir_solidity_overlay"
                item["evidence"] = clean_dict(evidence)
                out.append(item)
        return out

    def slithir_sink_fallback_facts(self, fn: Any, overlay_facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        covered = covered_stmt_kind_keys(overlay_facts)
        for fact in self.slither_adapter.facts_from_sseir_control(fn):
            item = fact.to_dict()
            if item.get("kind") not in HIGH_LEVEL_SLITHIR_FALLBACK_KINDS:
                continue
            key = stmt_kind_key(item)
            if key in covered:
                continue
            item["origin"] = "slither_lifted"
            normalize_slithir_sink_fact(item)
            evidence = dict(item.get("evidence") or {})
            evidence["lifted_from"] = "slithir_sink_fallback"
            item["evidence"] = clean_dict(evidence)
            out.append(item)
        return out


def overlay_source_lang(
    overlay: dict[str, Any],
    stmt_lang: dict[str, str],
    effect_by_id: dict[str, dict[str, Any]],
) -> str:
    langs = {
        stmt_lang.get(str(ref))
        for ref in overlay.get("stmt_refs") or []
        if stmt_lang.get(str(ref))
    }
    if langs == {"solidity"}:
        return "solidity"
    if langs == {"yul"}:
        return "yul"
    if "solidity" in langs and "yul" in langs:
        return "mixed"

    attrs = overlay.get("attrs") or {}
    if attrs.get("source") == "slithir_ssa" or attrs.get("language") == "solidity":
        return "solidity"
    if attrs.get("language") == "yul":
        return "yul"

    effect_langs = {
        (effect_by_id.get(effect_id) or {}).get("attrs", {}).get("language")
        for effect_id in overlay.get("effects") or []
    }
    effect_langs = {str(item) for item in effect_langs if item}
    if effect_langs == {"solidity"}:
        return "solidity"
    if effect_langs == {"yul"}:
        return "yul"
    if "solidity" in effect_langs and "yul" in effect_langs:
        return "mixed"
    return "unknown"


def covered_stmt_kind_keys(facts: list[dict[str, Any]]) -> set[tuple[tuple[str, ...], str]]:
    return {stmt_kind_key(fact) for fact in facts}


def stmt_kind_key(fact: dict[str, Any]) -> tuple[tuple[str, ...], str]:
    return (tuple(str(item) for item in fact.get("stmt_refs") or []), str(fact.get("kind") or ""))


def normalize_slithir_sink_fact(fact: dict[str, Any]) -> None:
    op = (fact.get("evidence") or {}).get("slither") or {}
    kind = str(fact.get("kind") or "")
    if kind == "NewContract":
        semantic = {
            "operation": "new_contract",
            "contract": contract_name_from_new_contract(op, fact),
            "arguments": high_level_values(op.get("arguments")),
            "value": high_level_value(op.get("call_value")),
            "salt": salt_from_new_contract(op),
            "source_expression": op.get("source_expression"),
        }
        fact["semantic"] = clean_dict(semantic)
        fact["reads"] = clean_value_list(semantic.get("arguments"), semantic.get("value"), semantic.get("salt"))
        return
    if kind == "NewArray":
        semantic = {
            "operation": "new_array",
            "array_type": high_level_value(op.get("array_type") or op.get("type")),
            "arguments": high_level_values(op.get("arguments")),
            "source_expression": op.get("source_expression"),
        }
        fact["semantic"] = clean_dict(semantic)
        fact["reads"] = clean_value_list(semantic.get("arguments"))
        return
    if kind == "NewStructure":
        semantic = {
            "operation": "new_structure",
            "struct_type": high_level_value(op.get("structure_name") or op.get("type")),
            "arguments": high_level_values(op.get("arguments")),
            "source_expression": op.get("source_expression"),
        }
        fact["semantic"] = clean_dict(semantic)
        fact["reads"] = clean_value_list(semantic.get("arguments"))
        return
    if kind == "NewElementaryType":
        semantic = {
            "operation": "new_elementary_type",
            "type": high_level_value(op.get("type")),
            "arguments": high_level_values(op.get("arguments")),
            "source_expression": op.get("source_expression"),
        }
        fact["semantic"] = clean_dict(semantic)
        fact["reads"] = clean_value_list(semantic.get("arguments"))
        return
    if kind == "Delete":
        target = high_level_value(op.get("variable") or op.get("lvalue") or fact.get("lvalue"))
        fact["semantic"] = clean_dict({
            "operation": "delete",
            "target": target,
            "source_expression": op.get("source_expression"),
        })
        fact["lvalue"] = target
        fact["writes"] = clean_value_list(target)
        return
    if kind == "ValueTransferCall":
        fact["semantic"] = clean_dict({
            "operation": "value_transfer",
            "call_kind": op.get("kind"),
            "destination": high_level_value(op.get("destination")),
            "value": high_level_value(op.get("call_value")),
            "source_expression": op.get("source_expression"),
        })
        fact["reads"] = clean_value_list(fact["semantic"].get("destination"), fact["semantic"].get("value"))


def contract_name_from_new_contract(op: dict[str, Any], fact: dict[str, Any]) -> Any:
    explicit = high_level_value(op.get("contract_name") or op.get("type"))
    if explicit:
        return explicit
    lvalue = op.get("lvalue") or fact.get("lvalue")
    if isinstance(lvalue, dict):
        return lvalue.get("type") or lvalue.get("base_name") or lvalue.get("text")
    return None


def salt_from_new_contract(op: dict[str, Any]) -> Any:
    text = str(op.get("text") or "")
    match = re.search(r"\bsalt:([A-Za-z_$][A-Za-z0-9_$]*)", text)
    if not match:
        return None
    token = match.group(1)
    for item in op.get("read") or []:
        if isinstance(item, dict) and item.get("text") == token:
            return high_level_value(item)
    return strip_ssa_suffix(token)


def high_level_values(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple, set)):
        return [high_level_value(item) for item in value if high_level_value(item) is not None]
    item = high_level_value(value)
    return [] if item is None else [item]


def high_level_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        kind = str(value.get("kind") or "")
        text = value.get("text")
        base = value.get("base_name") or value.get("name")
        if kind in {"LocalIRVariable", "StateIRVariable", "LocalVariable", "StateVariable"} and base:
            return str(base)
        if kind in {"SolidityVariable", "SolidityVariableComposed", "Constant"} and text is not None:
            return str(text)
        return text or base or value
    if isinstance(value, str):
        return strip_ssa_suffix(value)
    return value


def strip_ssa_suffix(value: str) -> str:
    return re.sub(r"_(?:\\d+)$", "", value)


def clean_value_list(*values: Any) -> list[Any]:
    out: list[Any] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, list):
            out.extend(item for item in value if item is not None)
        else:
            out.append(value)
    return list(dict.fromkeys(str(item) for item in out))
