#!/usr/bin/env python3
"""Attach semantic-only CFG provenance to completed S-SEIR overlays.

This pass belongs to S-SEIR completion, before the Semantic Fact IR boundary.
It may inspect upstream effects to locate the completed semantic operation, but
persists only Fact-IR-safe control block identifiers—never effect ids, kinds or
path-state payloads.
"""
from __future__ import annotations

from typing import Any


Json = dict[str, Any]

_SINK_KINDS = {
    "Predicate": {"Branch"},
    "StateVariableWrite": {"StorageWrite"}, "MappingWrite": {"StorageWrite"},
    "StateVariableRead": {"StorageRead"}, "MappingRead": {"StorageRead"},
    "EventEmit": {"EventLog"}, "PathConditionedEventEmit": {"EventLog"},
    "RequireOverlay": {"Revert"}, "CustomErrorRevert": {"Revert"},
    "RawRevertBytes": {"Revert"}, "RevertOverlay": {"Revert"},
    "ExternalCall": {"Call", "StaticCall", "DelegateCall", "CallCode", "ExternalCall", "InternalCall"},
    "LowLevelCall": {"Call", "StaticCall", "DelegateCall", "CallCode", "ExternalCall"},
    "StaticCallOverlay": {"StaticCall"}, "DelegateCallOverlay": {"DelegateCall"},
    "PrecompileCall": {"Call", "StaticCall"}, "ReturnValue": {"Return"},
}


def attach_semantic_cfg_provenance(overlays: list[Any], effects: list[Any], control: Json) -> None:
    """Record unique semantic anchors on final overlay attrs.

    A non-unique source is intentionally left without an anchor.  The SFIR
    bridge can then diagnose it rather than choosing a source-order guess.
    """
    effect_by_id = {str(getattr(effect, "effect_id", "")): effect for effect in effects if getattr(effect, "effect_id", None)}
    blocks = [item for item in control.get("blocks") or [] if isinstance(item, dict) and item.get("block_id")]
    for overlay in overlays:
        attrs = getattr(overlay, "attrs", None)
        if not isinstance(attrs, dict):
            continue
        # CFG-native loop predicates have no sink effect: their unique
        # terminator block is already semantic-level provenance.  Preserve
        # this completed anchor instead of rediscovering it from statements.
        if attrs.get("semantic_anchor_cfg_node") and attrs.get("semantic_evidence_cfg_nodes"):
            continue
        refs = {str(ref) for ref in getattr(overlay, "stmt_refs", []) if ref}
        candidates = [effect_by_id[str(effect_id)] for effect_id in getattr(overlay, "effects", []) if str(effect_id) in effect_by_id]
        sink_kinds = _SINK_KINDS.get(str(getattr(overlay, "kind", "")), set())
        if sink_kinds:
            candidates = [effect for effect in candidates if str(getattr(effect, "kind", "")) in sink_kinds] or candidates
        node_ids = {
            (getattr(effect, "attrs", {}) or {}).get("cfg_node_id")
            for effect in candidates
            if (getattr(effect, "attrs", {}) or {}).get("cfg_node_id") is not None
        }
        matches: list[str] = []
        for block in blocks:
            block_refs = {str(ref) for ref in block.get("stmts") or [] if ref}
            block_node_id = (block.get("attrs") or {}).get("node_id")
            if refs and not refs.intersection(block_refs):
                continue
            if node_ids and block_node_id not in node_ids:
                continue
            matches.append(str(block["block_id"]))
        matches = list(dict.fromkeys(matches))
        # These are high-level CFG provenance fields.  Do not record the
        # selected effect or any of its low-level attributes.
        attrs["semantic_evidence_cfg_nodes"] = matches
        if len(matches) == 1:
            attrs["semantic_anchor_cfg_node"] = matches[0]
