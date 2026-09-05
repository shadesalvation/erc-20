#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path as _SSEIRPath
import sys as _sseir_sys
_SSEIR_ROOT = _SSEIRPath(__file__).resolve().parents[1]
for _sseir_path in (_SSEIR_ROOT / "legacy_yul", _SSEIR_ROOT / "s_seir"):
    _sseir_text = str(_sseir_path)
    if _sseir_text not in _sseir_sys.path:
        _sseir_sys.path.insert(0, _sseir_text)
from dataclasses import asdict, dataclass, field
from typing import Any


_MEMORY_QUERY_FIELDS = (
    "memory_read",
    "payload_memory",
    "input_memory",
    "output_memory",
    "data_memory",
    "topic_memory_reads",
)
_QUERY_DETAIL_FIELDS = {
    "sink_resolution",
    "byte_slice",
    "memory_slice",
    "input_memory_partial",
    "output_memory_query",
    "payload_memory_partial",
    "payload_memory_complete",
    "data_memory_partial",
    "query_kind",
    "query_offset",
    "memory_ssa",
    "memory_version",
    "memory_versions",
    "atomic_operation_id",
    "atomic_kind",
    "atomic_sequence",
}
_QUERY_ANALYSIS_FACTS = {"MemorySSAQueryLayer", "SemanticSinkMemoryQuery"}
_QUERY_NOTES = {
    "byte_axis_memory_slice",
    "hash_input",
    "mapping_slot_from_byte_axis",
    "path_sensitive_sink",
    "path_sensitive_sink_collapsed_same_access",
    "path_sensitive_sink_resolution",
}


def _semantic_slice_values(memory_slice: Any) -> list[str]:
    if not isinstance(memory_slice, dict):
        return []
    values: list[str] = []
    for item in memory_slice.get("slices") or []:
        if not isinstance(item, dict):
            continue
        value = item.get("extraction") or item.get("source_value")
        if value is not None and str(value) not in values:
            values.append(str(value))
    return values


def _semantic_memory_paths(query: Any) -> list[dict[str, Any]]:
    if not isinstance(query, dict):
        return []
    byte_slice = query.get("byte_slice") or {}
    raw_paths = byte_slice.get("path_slices") or []
    if not raw_paths and byte_slice.get("slices") is not None:
        raw_paths = [{
            "path": None,
            "complete": byte_slice.get("complete"),
            "slices": byte_slice.get("slices") or [],
        }]
    out: list[dict[str, Any]] = []
    for raw in raw_paths:
        values = _semantic_slice_values({"slices": raw.get("slices") or []})
        item: dict[str, Any] = {"values": values}
        condition = raw.get("path")
        if condition and condition != "entry":
            item["condition"] = condition
        if not raw.get("complete", False):
            item["unresolved_reason"] = "memory_source_unresolved"
        out.append(item)
    if out:
        return out

    words = query.get("words") or []
    if words:
        values = [str(word.get("value")) for word in words if isinstance(word, dict) and word.get("value") is not None]
        item = {"values": values}
        if any(value == "unknown" for value in values) or query.get("has_unknown"):
            item["unresolved_reason"] = "memory_source_unresolved"
        return [item]
    if query.get("empty_range"):
        return [{"values": []}]
    if query.get("has_unknown"):
        return [{"values": ["unknown"], "unresolved_reason": "memory_source_unresolved"}]
    return []


def _semantic_sink_inputs(resolution: Any) -> dict[str, Any] | None:
    if not isinstance(resolution, dict):
        return None
    paths: list[dict[str, Any]] = []
    for raw_path in resolution.get("path_resolutions") or []:
        arguments: dict[str, Any] = {}
        for role, raw_arg in (raw_path.get("arg_resolutions") or {}).items():
            if not isinstance(raw_arg, dict):
                continue
            value = raw_arg.get("normalized")
            if value is None:
                values = _semantic_slice_values(raw_arg.get("memory_slice"))
                if values:
                    value = values[0] if len(values) == 1 else values
            if value is None:
                value = raw_arg.get("expr") or "unknown"
            arguments[str(role)] = value
        item: dict[str, Any] = {"arguments": arguments}
        condition = raw_path.get("condition")
        if condition and condition != "entry":
            item["condition"] = condition
        if raw_path.get("status") not in {None, "resolved"}:
            item["unresolved_reason"] = raw_path.get("reason") or "sink_input_unresolved"
        paths.append(item)
    if not paths:
        return None
    return {
        "sink_kind": resolution.get("sink_kind"),
        "paths": paths,
    }


def _semantic_notes(value: Any) -> list[Any]:
    return [item for item in (value or []) if item not in _QUERY_NOTES]


def _compact_resolved_inputs(value: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in value or []:
        if not isinstance(item, dict):
            continue
        compact = {
            key: item.get(key)
            for key in ("offset", "offset_expr", "size", "value")
            if item.get(key) is not None
        }
        if compact:
            out.append(compact)
    return out


def _semantic_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_semantic_value(item) for item in value]
    if not isinstance(value, dict):
        return value
    out: dict[str, Any] = {}
    for key, item in value.items():
        if key in _MEMORY_QUERY_FIELDS or key in _QUERY_DETAIL_FIELDS:
            continue
        if key in {"tracker_scope", "owner", "loop_alignment"}:
            continue
        if key == "resolved_inputs":
            compact = _compact_resolved_inputs(item)
            if compact:
                out[key] = compact
            continue
        if key == "notes":
            notes = _semantic_notes(item)
            if notes:
                out[key] = notes
            continue
        if key == "status" and item == "resolved":
            continue
        out[key] = _semantic_value(item)
    return out


def _semantic_attrs(attrs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    sink_inputs = _semantic_sink_inputs(attrs.get("sink_resolution"))
    memory_accesses: list[dict[str, Any]] = []
    for field_name in _MEMORY_QUERY_FIELDS:
        if field_name == "memory_read" and attrs.get("semantic_value") == "free_memory_pointer":
            continue
        query = attrs.get(field_name)
        if not isinstance(query, dict):
            continue
        paths = _semantic_memory_paths(query)
        if paths:
            memory_accesses.append({"role": field_name, "paths": paths})
    for key, value in attrs.items():
        if key in _MEMORY_QUERY_FIELDS or key in _QUERY_DETAIL_FIELDS or key == "slithir":
            continue
        if key == "resolved_inputs":
            compact = _compact_resolved_inputs(value)
            if compact:
                out[key] = compact
            continue
        if key == "notes":
            notes = _semantic_notes(value)
            if notes:
                out[key] = notes
            continue
        out[key] = _semantic_value(value)
    if sink_inputs:
        out["semantic_inputs"] = sink_inputs
    elif memory_accesses:
        out["memory_semantics"] = memory_accesses
    return out


def _semantic_control(control: dict[str, Any]) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    keep_attrs = {"src", "text", "node_id", "node_kind", "slither_node_type", "function_loop_context"}
    for block in control.get("blocks") or []:
        item = {
            "block_id": block.get("block_id"),
            "kind": block.get("kind"),
            "stmts": list(block.get("stmts") or []),
            "terminator": _semantic_value(block.get("terminator") or {}),
        }
        attrs = block.get("attrs") or {}
        compact_attrs = {key: _semantic_value(attrs[key]) for key in keep_attrs if key in attrs}
        if compact_attrs:
            item["attrs"] = compact_attrs
        blocks.append(item)
    return {
        "blocks": blocks,
        "edges": _semantic_value(control.get("edges") or []),
        "loop_contexts": _semantic_value(control.get("loop_contexts") or {}),
        "control_dependencies": _semantic_value(control.get("control_dependencies") or []),
        "assembly_boundaries": _semantic_value(control.get("assembly_boundaries") or {}),
    }

@dataclass
class SourceStatement:
    stmt_id: str; lang: str; text: str; src: str; function_id: str; block_id: str|None=None; origin: dict[str,Any]=field(default_factory=dict)
    def to_dict(self): return asdict(self)
@dataclass
class ExpressionRole:
    expr_id: str; text: str; normalized: str|None; role: str; type_hint: str|None; stmt_ref: str; attrs: dict[str,Any]=field(default_factory=dict)
    def to_dict(self): return asdict(self)
@dataclass
class EffectNode:
    effect_id: str; kind: str; stmt_refs: list[str]; attrs: dict[str,Any]=field(default_factory=dict)
    def to_dict(self): return asdict(self)
@dataclass
class SemanticOverlay:
    overlay_id: str; kind: str; effects: list[str]; stmt_refs: list[str]; attrs: dict[str,Any]=field(default_factory=dict)
    def to_dict(self): return asdict(self)
@dataclass
class SecurityFact:
    fact_id: str; kind: str; attrs: dict[str,Any]; source_overlays: list[str]; source_effects: list[str]; stmt_refs: list[str]
    def to_dict(self): return asdict(self)
@dataclass
class VariableInfo:
    name: str; kind: str; type_string: str; data_location: str|None=None; src: str=""; storage_slot: int|None=None; declaration_id: int|None=None
    def to_dict(self): return asdict(self)
@dataclass
class FunctionUnit:
    function_id: str; contract: str; function: str; signature: str; ast_node: dict[str,Any]
    parameters: list[VariableInfo]=field(default_factory=list); returns: list[VariableInfo]=field(default_factory=list); locals: list[VariableInfo]=field(default_factory=list); state_variables: list[VariableInfo]=field(default_factory=list)
    assembly_blocks: list[Any]=field(default_factory=list); source_statements: list[SourceStatement]=field(default_factory=list)
@dataclass
class FunctionSSEIR:
    function_id: str; contract: str; function: str; signature: str; source_statements: list[SourceStatement]; control: dict[str,Any]
    expr_roles: list[ExpressionRole]; effects: list[EffectNode]; semantic_overlays: list[SemanticOverlay]
    security_facts: list[SecurityFact]=field(default_factory=list); analysis_facts: list[dict[str,Any]]=field(default_factory=list)
    def to_dict(self):
        return {"function_id":self.function_id,"contract":self.contract,"function":self.function,"signature":self.signature,
        "source_statements":[x.to_dict() for x in self.source_statements],"control":self.control,
        "expr_roles":[x.to_dict() for x in self.expr_roles],"effects":[x.to_dict() for x in self.effects],
        "semantic_overlays":[x.to_dict() for x in self.semantic_overlays],
        "security_facts":[x.to_dict() for x in self.security_facts],"analysis_facts":self.analysis_facts}

    def to_semantic_dict(self):
        """Export canonical semantics without MemorySSA/SinkResolver query traces."""
        return {
            "function_id": self.function_id,
            "contract": self.contract,
            "function": self.function,
            "signature": self.signature,
            "source_statements": [x.to_dict() for x in self.source_statements],
            "control": _semantic_control(self.control),
            "expr_roles": [
                {
                    **x.to_dict(),
                    "attrs": _semantic_value(x.attrs),
                }
                for x in self.expr_roles
            ],
            "effects": [
                {
                    "effect_id": x.effect_id,
                    "kind": x.kind,
                    "stmt_refs": list(x.stmt_refs),
                    "attrs": _semantic_attrs(x.attrs),
                }
                for x in self.effects
            ],
            "semantic_overlays": [
                {
                    "overlay_id": x.overlay_id,
                    "kind": x.kind,
                    "effects": list(x.effects),
                    "stmt_refs": list(x.stmt_refs),
                    "attrs": _semantic_attrs(x.attrs),
                }
                for x in self.semantic_overlays
            ],
            "security_facts": [x.to_dict() for x in self.security_facts],
            "analysis_facts": [
                _semantic_value(fact)
                for fact in self.analysis_facts
                if fact.get("kind") not in _QUERY_ANALYSIS_FACTS
            ],
        }
