#!/usr/bin/env python3
"""Adapt legacy semantic IR memory facts to CFG path-sensitive MemorySSA."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from assembly_ast_cfg import compile_source_ast, discover_solc, extract_inline_assembly_blocks
from assembly_arithmetic_compare_ir import ParseError, render_yul_expression
from assembly_memory_ssa import analyze_block
from assembly_semantic_ir import (
    MEMORY_WORD_BYTES,
    build_report,
    normalize_space,
    parse_int_literal,
    parse_memory_address,
    strip_ssa,
)


def _path_text(state: Any) -> str:
    return " && ".join(state.predicates) or "entry"


def _memory_key(address: dict[str, Any], word_offset: int) -> str | None:
    base = str(address.get("base", ""))
    offset = address.get("offset")
    if offset is None:
        return None
    relative = word_offset - offset
    text = base if relative == 0 else f"add({base}, {relative})"
    return "".join(text.split()).replace("0x20", "32").replace("0X20", "32")


def resolve_words(memory_ssa: Any, node_id: int, pointer: str, length: str | None) -> list[dict[str, Any]]:
    address = parse_memory_address(strip_ssa(pointer))
    start = address.get("offset")
    size = parse_int_literal(length or "") if length is not None else None
    if start is None:
        return [{
            "offset": None,
            "offset_expr": address.get("offset_expr"),
            "value": "unknown",
            "branch_candidates": [{"value": "unknown", "path": "dynamic address", "source": "cfg_memory_ssa"}],
        }]

    count = 1 if size is None else max(1, (size + MEMORY_WORD_BYTES - 1) // MEMORY_WORD_BYTES)
    states = memory_ssa.states_at(node_id)
    if any(any(key.startswith("<unknown") for key in state.memory) for state in states):
        return [{
            "offset": None,
            "offset_expr": "unknown",
            "value": "unknown",
            "branch_candidates": [{"value": "unknown", "path": "local function side effect", "source": "cfg_memory_ssa"}],
        }]
    words = []
    for index in range(count):
        offset = start + index * MEMORY_WORD_BYTES
        key = _memory_key(address, offset)
        candidates = []
        for state in states:
            definition = state.memory.get(key) if key else None
            candidates.append({
                "value": definition.value if definition else "unknown",
                "memory_ssa": definition.version if definition else None,
                "path": _path_text(state),
                "source": definition.kind if definition else "uninitialized",
            })
        values = {item["value"] for item in candidates}
        word: dict[str, Any] = {
            "offset": offset,
            "offset_expr": str(offset),
            "cfg_path_states": [item["path"] for item in candidates],
        }
        if len(values) == 1 and candidates:
            word["value"] = candidates[0]["value"]
            versions = [item["memory_ssa"] for item in candidates if item["memory_ssa"]]
            if versions:
                word["memory_ssa"] = versions[0] if len(set(versions)) == 1 else f"phi({', '.join(dict.fromkeys(versions))})"
                if len(set(versions)) > 1:
                    word["memory_phi"] = word["memory_ssa"]
        else:
            word["value"] = "unknown"
            word["branch_candidates"] = candidates
        words.append(word)
    return words


def _cfg_nodes_by_text(memory_ssa: Any) -> dict[str, list[int]]:
    matches: dict[str, list[int]] = {}
    candidates = []
    for node_id, ast_node in memory_ssa.node_ast.items():
        if ast_node.get("nodeType") in {"YulBlock", "YulCase", "YulFunctionDefinition", "YulForLoop"}:
            continue
        src = memory_ssa.cfg.nodes[node_id].src
        try:
            start = int(src.split(":", 1)[0])
        except (ValueError, IndexError):
            start = 10**12
        candidates.append((start, node_id))
    for _start, node_id in sorted(candidates):
        text = normalize_space(memory_ssa.cfg.nodes[node_id].text)
        matches.setdefault(text, []).append(node_id)
    return matches


def apply_memory_ssa(block: dict[str, Any], memory_ssa: Any) -> None:
    candidates = _cfg_nodes_by_text(memory_ssa)
    offsets = {text: 0 for text in candidates}

    for op in block["source_yul_semantic_ir"]:
        text = normalize_space(op["text"])
        position = offsets.get(text, 0)
        node_ids = candidates.get(text, [])
        if position >= len(node_ids):
            continue
        node_id = node_ids[position]
        offsets[text] = position + 1
        op["cfg_node_id"] = node_id
        op["cfg_path_predicates"] = [_path_text(state) for state in memory_ssa.states_at(node_id)]

        semantic = op["semantic"]
        if op["kind"] == "memory_read":
            words = resolve_words(memory_ssa, node_id, semantic["ptr"], None)
            semantic["read"]["resolved_words"] = words
            semantic["read"]["tracker_scope"] = "assembly_block_cfg_memory_ssa"
        elif op["kind"] == "memory_hash":
            words = resolve_words(memory_ssa, node_id, semantic["ptr"], semantic["length"])
            semantic["read"]["resolved_words"] = words
            semantic["read"]["tracker_scope"] = "assembly_block_cfg_memory_ssa"
            semantic["resolved_inputs"] = words
            rendered_values = []
            for word in words:
                value = word["value"]
                try:
                    rendered_values.append(render_yul_expression(value).text)
                except ParseError:
                    rendered_values.append(value)
            values = ", ".join(rendered_values)
            target = semantic.get("target")
            semantic["solidity_like"] = f"{target} = keccak256({values});" if target else f"keccak256({values})"
        elif op["kind"] == "event_log":
            data = semantic.get("data")
            if data:
                semantic["resolved_data"] = resolve_words(memory_ssa, node_id, data["ptr"], data["length"])
        elif op["kind"] in {"memory_write", "memory_copy"}:
            semantic.setdefault("write", semantic.get("copy", {}))
            semantic["memory_tracker_scope"] = "assembly_block_cfg_memory_ssa"

    block["memory_tracker_scope"] = "assembly_block_isolated + cfg_path_memory_ssa"
    block["_memory_ssa_result"] = memory_ssa


def build_cfg_memory_report(source_path: Path, slithir_path: Path | None, solc_bin: str | None = None) -> dict[str, Any]:
    report = build_report(source_path, slithir_path)
    compiler = discover_solc(solc_bin)
    if compiler is None:
        raise RuntimeError("solc executable not found for CFG MemorySSA.")
    ast = compile_source_ast(source_path, compiler)
    ast_blocks = extract_inline_assembly_blocks(ast, source_path)
    by_start = {block.source_range[0]: block for block in ast_blocks}

    for block in report["assembly_blocks"]:
        start = block["context"]["assembly_position"]["start_offset"]
        ast_block = by_start.get(start)
        if ast_block is None:
            raise RuntimeError(f"InlineAssembly AST block not found at source offset {start}.")
        apply_memory_ssa(block, analyze_block(ast_block))

    report["ir_note"] = (
        "Semantic replacement view only. Memory-derived facts are supplied by "
        "per-assembly-block CFG path-sensitive MemorySSA."
    )
    return report
