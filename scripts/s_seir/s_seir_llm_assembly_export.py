#!/usr/bin/env python3
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from s_seir_model import EffectNode, FunctionSSEIR, SemanticOverlay, SourceStatement


def json_ready(value: Any) -> Any:
    if is_dataclass(value):
        return json_ready(asdict(value))
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(v) for v in value]
    return value


def is_assembly_ref(stmt_id: str, stmt_by_id: dict[str, SourceStatement]) -> bool:
    stmt = stmt_by_id.get(stmt_id)
    return bool(stmt and stmt.lang == "yul")


def has_assembly_ref(stmt_refs: list[str], stmt_by_id: dict[str, SourceStatement]) -> bool:
    return any(is_assembly_ref(ref, stmt_by_id) for ref in stmt_refs)


def function_has_assembly(fn: FunctionSSEIR) -> bool:
    return any(stmt.lang == "yul" for stmt in fn.source_statements)


def compact_block(block: dict[str, Any]) -> dict[str, Any]:
    attrs = block.get("attrs") if isinstance(block.get("attrs"), dict) else {}
    out: dict[str, Any] = {
        "block_id": block.get("block_id"),
        "kind": block.get("kind"),
        "stmts": block.get("stmts", []),
        "terminator": block.get("terminator", {}),
    }
    if block.get("assembly_block") is not None:
        out["assembly_block"] = block.get("assembly_block")
    keep_attrs = {}
    for key in (
        "slither_node_id",
        "slither_node_type",
        "node_id",
        "node_kind",
        "src",
        "text",
        "function_loop_context",
    ):
        if key in attrs:
            keep_attrs[key] = attrs[key]
    if keep_attrs:
        out["attrs"] = keep_attrs
    return out


def compact_control(control: dict[str, Any]) -> dict[str, Any]:
    blocks = control.get("blocks", []) if isinstance(control, dict) else []
    edges = control.get("edges", []) if isinstance(control, dict) else []
    return {
        "notes": control.get("notes", []) if isinstance(control, dict) else [],
        "blocks": [compact_block(block) for block in blocks],
        "edges": edges,
        "loop_contexts": control.get("loop_contexts", {}) if isinstance(control, dict) else {},
    }


def source_layers(fn: FunctionSSEIR) -> dict[str, Any]:
    solidity = [stmt.to_dict() for stmt in fn.source_statements if stmt.lang == "solidity"]
    yul = [stmt.to_dict() for stmt in fn.source_statements if stmt.lang == "yul"]
    assembly_blocks: dict[str, list[dict[str, Any]]] = {}
    for stmt in fn.source_statements:
        if stmt.lang != "yul":
            continue
        block_id = stmt.block_id or "assembly_unknown"
        assembly_blocks.setdefault(block_id, []).append(stmt.to_dict())
    return {
        "solidity_context_statements": solidity,
        "assembly_statements": yul,
        "assembly_blocks": [
            {"block_id": block_id, "statements": statements}
            for block_id, statements in sorted(assembly_blocks.items())
        ],
    }


def assembly_expr_roles(fn: FunctionSSEIR, stmt_by_id: dict[str, SourceStatement]) -> list[dict[str, Any]]:
    return [
        role.to_dict()
        for role in fn.expr_roles
        if is_assembly_ref(role.stmt_ref, stmt_by_id)
    ]


def assembly_effects(fn: FunctionSSEIR, stmt_by_id: dict[str, SourceStatement]) -> tuple[list[dict[str, Any]], set[str]]:
    out: list[dict[str, Any]] = []
    ids: set[str] = set()
    for effect in fn.effects:
        if has_assembly_ref(effect.stmt_refs, stmt_by_id) or effect.kind == "BranchMaterialization":
            out.append(effect.to_dict())
            ids.add(effect.effect_id)
    return out, ids


def assembly_overlays(
    fn: FunctionSSEIR,
    stmt_by_id: dict[str, SourceStatement],
    effect_ids: set[str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for overlay in fn.semantic_overlays:
        if has_assembly_ref(overlay.stmt_refs, stmt_by_id) or any(effect in effect_ids for effect in overlay.effects):
            out.append(overlay.to_dict())
    return out


def function_to_llm_assembly_input(fn: FunctionSSEIR) -> dict[str, Any] | None:
    if not function_has_assembly(fn):
        return None
    stmt_by_id = {stmt.stmt_id: stmt for stmt in fn.source_statements}
    effects, effect_ids = assembly_effects(fn, stmt_by_id)
    return {
        "function_id": fn.function_id,
        "contract": fn.contract,
        "function": fn.function,
        "signature": fn.signature,
        "llm_boundary": {
            "input_layers": [
                "source_statements",
                "control",
                "expression_roles",
                "effects",
                "semantic_overlays",
            ],
            "excluded_layers": [
                "security_facts",
                "analysis_facts",
                "projection_policies",
            ],
            "task": "Decide which assembly semantics can be rendered as Solidity-like code and which assembly semantics must be preserved.",
        },
        "source_statements": source_layers(fn),
        "control": compact_control(fn.control),
        "expression_roles": assembly_expr_roles(fn, stmt_by_id),
        "effects": effects,
        "semantic_overlays": assembly_overlays(fn, stmt_by_id, effect_ids),
    }


def build_llm_assembly_input(functions: list[FunctionSSEIR]) -> dict[str, Any]:
    exported = []
    for fn in functions:
        item = function_to_llm_assembly_input(fn)
        if item is not None:
            exported.append(item)
    return {
        "schema": "s-seir-assembly-llm-input/v1",
        "description": "Assembly-focused S-SEIR export. It keeps only the first five deterministic semantic layers and leaves recoverability/projection decisions to the LLM.",
        "functions": exported,
    }


def write_llm_assembly_json(functions: list[FunctionSSEIR], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = build_llm_assembly_input(functions)
    output.write_text(json.dumps(json_ready(payload), indent=2, ensure_ascii=False), encoding="utf-8")


def render_attrs(attrs: dict[str, Any], limit: int = 1200) -> str:
    text = json.dumps(json_ready(attrs), ensure_ascii=False)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def render_llm_assembly_text(functions: list[FunctionSSEIR]) -> str:
    payload = build_llm_assembly_input(functions)
    lines: list[str] = []
    lines.append("S-SEIR Assembly LLM Input")
    lines.append("Layers: SourceStatements, Control, ExpressionRoles, Effects, SemanticOverlays")
    lines.append("Excluded: SecurityFacts, AnalysisFacts, ProjectionPolicies")
    lines.append("")
    for fn in payload["functions"]:
        lines.append(f"Function {fn['contract']}.{fn['signature']}")
        lines.append("  Boundary:")
        lines.append(f"    task: {fn['llm_boundary']['task']}")
        lines.append("  SourceStatements:")
        solidity = fn["source_statements"]["solidity_context_statements"]
        yul_blocks = fn["source_statements"]["assembly_blocks"]
        lines.append(f"    solidity_context_count: {len(solidity)}")
        for block in yul_blocks:
            lines.append(f"    {block['block_id']}:")
            for stmt in block["statements"]:
                lines.append(f"      {stmt['stmt_id']} [{stmt['origin'].get('nodeType')}] {stmt['text']}")
        lines.append("  Control:")
        lines.append(f"    blocks: {len(fn['control']['blocks'])}")
        lines.append(f"    edges: {len(fn['control']['edges'])}")
        for note in fn["control"].get("notes", []):
            lines.append(f"    note: {note}")
        lines.append("  ExpressionRoles:")
        for role in fn["expression_roles"]:
            normalized = f" -> {role['normalized']}" if role.get("normalized") else ""
            lines.append(f"    {role['expr_id']} {role['role']}: {role['text']}{normalized} @ {role['stmt_ref']}")
        lines.append("  Effects:")
        for effect in fn["effects"]:
            lines.append(
                f"    {effect['effect_id']} {effect['kind']} refs={effect['stmt_refs']} attrs={render_attrs(effect.get('attrs', {}))}"
            )
        lines.append("  SemanticOverlays:")
        for overlay in fn["semantic_overlays"]:
            lines.append(
                f"    {overlay['overlay_id']} {overlay['kind']} effects={overlay['effects']} refs={overlay['stmt_refs']} attrs={render_attrs(overlay.get('attrs', {}))}"
            )
        lines.append("")
    return "\n".join(lines)


def write_llm_assembly_text(functions: list[FunctionSSEIR], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_llm_assembly_text(functions), encoding="utf-8")


def truncate_list(values: list[Any], limit: int = 6) -> list[Any]:
    if len(values) <= limit:
        return values
    return values[:limit] + [{"truncated": len(values) - limit}]


def compact_function_source(fn: FunctionSSEIR) -> dict[str, Any]:
    source = getattr(fn, "_sseir_function_source", None)
    if isinstance(source, dict) and source.get("text"):
        return {"src": source.get("src", ""), "text": source.get("text", "")}
    src_values = []
    for stmt in fn.source_statements:
        try:
            start_text, length_text, _ = str(stmt.src).split(":", 2)
            start = int(start_text)
            length = int(length_text)
            src_values.append((start, start + length))
        except (TypeError, ValueError):
            continue
    if not src_values:
        return {"src": "", "text": ""}
    start = min(x[0] for x in src_values)
    end = max(x[1] for x in src_values)
    return {"src": f"{start}:{end - start}:derived", "text": ""}


def compact_assembly_sources(fn: FunctionSSEIR) -> list[dict[str, Any]]:
    sources = getattr(fn, "_sseir_assembly_sources", None)
    if isinstance(sources, list) and sources:
        return [dict(item) for item in sources if isinstance(item, dict)]
    blocks: dict[str, list[str]] = {}
    srcs: dict[str, list[str]] = {}
    for stmt in fn.source_statements:
        if stmt.lang != "yul":
            continue
        block_id = stmt.block_id or "assembly_unknown"
        blocks.setdefault(block_id, []).append(stmt.text)
        srcs.setdefault(block_id, []).append(stmt.src)
    return [
        {"block_id": block_id, "src": ",".join(srcs.get(block_id, [])), "text": "\n".join(lines)}
        for block_id, lines in sorted(blocks.items())
    ]


def compact_control_summary(fn: FunctionSSEIR) -> dict[str, Any]:
    blocks = fn.control.get("blocks", []) if isinstance(fn.control, dict) else []
    edges = fn.control.get("edges", []) if isinstance(fn.control, dict) else []
    yul_blocks = [b for b in blocks if b.get("kind") == "yul"]
    terms = [b.get("terminator", {}) for b in blocks if isinstance(b.get("terminator"), dict)]
    return {
        "block_count": len(blocks),
        "edge_count": len(edges),
        "assembly_cfg_block_count": len(yul_blocks),
        "has_branch": any(t.get("kind") == "Branch" for t in terms),
        "has_loop": any(str(e.get("kind", "")) in {"loop", "back", "backedge"} for e in edges)
        or bool(fn.control.get("loop_contexts")) if isinstance(fn.control, dict) else False,
        "notes": fn.control.get("notes", []) if isinstance(fn.control, dict) else [],
        "assembly_blocks": sorted({b.get("assembly_block") for b in yul_blocks if b.get("assembly_block") is not None}),
    }


def compact_key_expression_roles(fn: FunctionSSEIR, stmt_by_id: dict[str, SourceStatement]) -> list[dict[str, Any]]:
    keep_roles = {
        "guard_condition",
        "branch_condition",
        "mapping_slot_expr",
        "mapping_key_material",
        "state_access_expr",
        "storage_slot_expr",
        "storage_write_value",
        "event_topic0",
        "event_indexed_argument",
        "abi_argument",
        "call_target",
        "call_gas",
        "call_input_ptr",
        "call_input_size",
        "call_output_ptr",
        "call_output_size",
    }
    out = []
    for role in fn.expr_roles:
        if role.role not in keep_roles or not is_assembly_ref(role.stmt_ref, stmt_by_id):
            continue
        item = {
            "expr_id": role.expr_id,
            "role": role.role,
            "text": role.text,
            "normalized": role.normalized,
            "stmt_ref": role.stmt_ref,
        }
        if role.type_hint:
            item["type_hint"] = role.type_hint
        out.append(item)
    return out


def compact_effect_summary(effects: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    selected = []
    for effect in effects:
        kind = str(effect.get("kind", ""))
        counts[kind] = counts.get(kind, 0) + 1
        attrs = effect.get("attrs", {}) if isinstance(effect.get("attrs"), dict) else {}
        if kind not in {"StorageRead", "StorageWrite", "EventLog", "Call", "StaticCall", "DelegateCall", "Revert", "Return", "BranchMaterialization"}:
            continue
        item = {"effect_id": effect.get("effect_id"), "kind": kind, "stmt_refs": effect.get("stmt_refs", [])}
        for key in (
            "slot",
            "value",
            "target",
            "op",
            "args",
            "payload_ptr",
            "payload_size",
            "condition",
            "topic0",
            "topics",
            "data",
            "path_states",
            "text_ir",
        ):
            if key not in attrs:
                continue
            value = attrs[key]
            if isinstance(value, list):
                item[key] = truncate_list(value, 4)
            else:
                item[key] = value
        selected.append(item)
    return {"counts": counts, "selected_effects": selected}


def compact_overlay_attrs(kind: str, attrs: dict[str, Any]) -> dict[str, Any]:
    keys_by_kind = {
        "RequireOverlay": ("condition", "nearest_condition", "require_like", "revert_payload", "control_path", "merged_conditions", "discarded_before_revert"),
        "MappingSlot": ("target", "target_key", "expression", "state_variable", "key", "base", "slot_kind", "activation", "notes"),
        "MappingRead": ("access", "target", "state_variable", "key", "slot", "slot_key", "solidity_like", "notes"),
        "MappingWrite": ("access", "value", "value_yul", "state_variable", "key", "slot", "slot_key", "solidity_like", "notes"),
        "PathConditionedStorageRead": ("slot", "target", "path_states", "candidates", "note"),
        "PathConditionedStorageWrite": ("slot", "value", "value_yul", "path_states", "candidates", "note"),
        "StateVariableRead": ("access", "target", "state_variable", "slot", "solidity_like"),
        "StateVariableWrite": ("access", "value", "value_yul", "state_variable", "slot", "solidity_like"),
        "EventEmit": ("event", "signature", "topic0", "args", "topics", "solidity_like"),
        "PrecompileCall": ("op", "target", "gas", "input_size", "output_size", "native_name", "solidity_like"),
        "PrecompileOutputRead": ("source_precompile_overlay", "target", "value", "solidity_like"),
        "ExpressionNormalization": ("target", "expression", "solidity_like", "context", "division_guards"),
    }
    keys = keys_by_kind.get(kind, ("solidity_like", "access", "value", "target", "condition", "event", "signature", "note"))
    out: dict[str, Any] = {}
    for key in keys:
        if key not in attrs:
            continue
        value = attrs[key]
        if isinstance(value, list):
            out[key] = truncate_list(value, 5)
        elif isinstance(value, dict):
            out[key] = compact_nested_dict(value)
        else:
            out[key] = value
    return out


def compact_nested_dict(value: dict[str, Any], max_items: int = 8) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for idx, (key, item) in enumerate(value.items()):
        if idx >= max_items:
            out["truncated_keys"] = len(value) - max_items
            break
        if isinstance(item, list):
            out[key] = truncate_list(item, 4)
        elif isinstance(item, dict):
            out[key] = compact_nested_dict(item, 4)
        else:
            out[key] = item
    return out


def compact_overlay(overlay: dict[str, Any]) -> dict[str, Any]:
    kind = str(overlay.get("kind", ""))
    return {
        "overlay_id": overlay.get("overlay_id"),
        "kind": kind,
        "effects": overlay.get("effects", []),
        "stmt_refs": overlay.get("stmt_refs", []),
        "attrs": compact_overlay_attrs(kind, overlay.get("attrs", {}) if isinstance(overlay.get("attrs"), dict) else {}),
    }


def unresolved_low_level_semantics(effects: list[dict[str, Any]], overlays: list[dict[str, Any]]) -> list[dict[str, Any]]:
    covered = {effect for overlay in overlays for effect in overlay.get("effects", [])}
    out = []
    for effect in effects:
        kind = effect.get("kind")
        if kind not in {"Call", "StaticCall", "DelegateCall", "CallCode", "Create", "Create2", "Return", "Revert"}:
            continue
        if effect.get("effect_id") in covered and kind not in {"Call", "DelegateCall", "CallCode", "Create", "Create2"}:
            continue
        attrs = effect.get("attrs", {}) if isinstance(effect.get("attrs"), dict) else {}
        out.append({
            "effect_id": effect.get("effect_id"),
            "kind": kind,
            "stmt_refs": effect.get("stmt_refs", []),
            "summary": {k: attrs.get(k) for k in ("op", "target", "gas", "value", "input_ptr", "input_size", "output_ptr", "output_size", "payload_ptr", "payload_size") if k in attrs},
        })
    return out


def function_to_llm_assembly_compact(fn: FunctionSSEIR) -> dict[str, Any] | None:
    if not function_has_assembly(fn):
        return None
    stmt_by_id = {stmt.stmt_id: stmt for stmt in fn.source_statements}
    effects, effect_ids = assembly_effects(fn, stmt_by_id)
    overlays = assembly_overlays(fn, stmt_by_id, effect_ids)
    compact_overlays = [compact_overlay(overlay) for overlay in overlays]
    return {
        "function_id": fn.function_id,
        "contract": fn.contract,
        "function": fn.function,
        "signature": fn.signature,
        "recovery_task": "Recover only the assembly region using function source and semantic overlays. Decide which semantics can be rendered as Solidity-like code and preserve assembly where exact recovery is unsafe.",
        "function_source": compact_function_source(fn),
        "assembly_blocks": compact_assembly_sources(fn),
        "control_summary": compact_control_summary(fn),
        "key_expression_roles": compact_key_expression_roles(fn, stmt_by_id),
        "effect_summary": compact_effect_summary(effects),
        "semantic_overlays": compact_overlays,
        "unresolved_low_level_semantics": unresolved_low_level_semantics(effects, compact_overlays),
    }


def build_llm_assembly_compact_input(functions: list[FunctionSSEIR]) -> dict[str, Any]:
    exported = []
    for fn in functions:
        item = function_to_llm_assembly_compact(fn)
        if item is not None:
            exported.append(item)
    return {"schema": "s-seir-assembly-llm-compact/v1", "functions": exported}


def write_llm_assembly_compact_json(functions: list[FunctionSSEIR], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = build_llm_assembly_compact_input(functions)
    output.write_text(json.dumps(json_ready(payload), indent=2, ensure_ascii=False), encoding="utf-8")


def render_llm_assembly_compact_text(functions: list[FunctionSSEIR]) -> str:
    payload = build_llm_assembly_compact_input(functions)
    lines = ["S-SEIR Assembly LLM Compact Input", "Schema: s-seir-assembly-llm-compact/v1", ""]
    for fn in payload["functions"]:
        lines.append(f"Function {fn['contract']}.{fn['signature']}")
        lines.append(f"  Task: {fn['recovery_task']}")
        lines.append("  FunctionSource:")
        function_text = fn.get("function_source", {}).get("text", "")
        for line in function_text.strip().splitlines():
            lines.append(f"    {line}")
        lines.append("  AssemblyBlocks:")
        for block in fn.get("assembly_blocks", []):
            lines.append(f"    {block.get('block_id')} src={block.get('src')}")
            for line in str(block.get("text", "")).strip().splitlines():
                lines.append(f"      {line}")
        summary = fn.get("control_summary", {})
        lines.append(
            f"  ControlSummary: blocks={summary.get('block_count')} edges={summary.get('edge_count')} "
            f"asm_blocks={summary.get('assembly_cfg_block_count')} branch={summary.get('has_branch')} loop={summary.get('has_loop')}"
        )
        lines.append(f"  EffectCounts: {json.dumps(fn.get('effect_summary', {}).get('counts', {}), ensure_ascii=False)}")
        lines.append("  SemanticOverlays:")
        for overlay in fn.get("semantic_overlays", []):
            lines.append(
                f"    {overlay.get('overlay_id')} {overlay.get('kind')} refs={overlay.get('stmt_refs')} "
                f"attrs={render_attrs(overlay.get('attrs', {}), 700)}"
            )
        unresolved = fn.get("unresolved_low_level_semantics", [])
        if unresolved:
            lines.append("  UnresolvedLowLevelSemantics:")
            for item in unresolved:
                lines.append(f"    {item.get('effect_id')} {item.get('kind')} refs={item.get('stmt_refs')} summary={json.dumps(item.get('summary', {}), ensure_ascii=False)}")
        lines.append("")
    return "\n".join(lines)


def write_llm_assembly_compact_text(functions: list[FunctionSSEIR], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_llm_assembly_compact_text(functions), encoding="utf-8")
