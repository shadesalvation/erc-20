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


def _alias_dict(alias: Any) -> dict[str, Any]:
    return {
        "key": getattr(alias, "key", None),
        "base": getattr(alias, "base", None),
        "offset": getattr(alias, "offset", None),
        "expression": getattr(alias, "expression", None),
        "source": getattr(alias, "source", None),
    }


def _definition_dict(definition: Any | None) -> dict[str, Any] | None:
    if definition is None:
        return None
    return {
        "version": getattr(definition, "version", None),
        "node_id": getattr(definition, "node_id", None),
        "address": getattr(definition, "address", None),
        "value": getattr(definition, "value", None),
        "kind": getattr(definition, "kind", None),
        "origin_src": getattr(definition, "origin_src", None),
        "aliases": [_alias_dict(alias) for alias in getattr(definition, "aliases", ())],
    }


def _memory_key(address: dict[str, Any], word_offset: int) -> str | None:
    base = str(address.get("base", ""))
    offset = address.get("offset")
    if offset is None:
        return None
    # word_offset is absolute relative to the symbolic base, not relative to
    # the queried pointer. mload(add(base, 8)) must read add(base,8), while
    # keccak256(base, 64) reads base and add(base,32).
    text = base if word_offset == 0 else f"add({base}, {word_offset})"
    return "".join(text.split()).replace("0x20", "32").replace("0X20", "32")


def _loop_fact_dict(kind: str, item: Any) -> dict[str, Any]:
    if hasattr(item, "__dict__"):
        value = dict(item.__dict__)
    else:
        value = {"value": str(item)}
    value["kind"] = kind
    return value


class MemoryQuery:
    """Unified read interface over CFG path-sensitive MemorySSA.

    The query object is intentionally read-only. It does not execute Yul and it
    does not rewrite source; it only answers: at this CFG node, what reaching
    memory definitions can describe this pointer/range? The result keeps path
    candidates, alias metadata, and conservative loop/top facts together so all
    downstream passes consume the same shape.
    """

    def __init__(self, memory_ssa: Any) -> None:
        self.memory_ssa = memory_ssa

    def read(self, node_id: int, pointer: str, length: str | None = None, *, reason: str | None = None) -> dict[str, Any]:
        address = parse_memory_address(strip_ssa(pointer))
        states = self.memory_ssa.states_at(node_id)
        result: dict[str, Any] = {
            "query_kind": "MemoryReadResult",
            "tracker_scope": "assembly_block_cfg_memory_ssa_unified_query",
            "node_id": node_id,
            "reason": reason,
            "pointer": pointer,
            "length": length,
            "normalized_pointer": strip_ssa(pointer),
            "address": address,
            "path_states": [_path_text(state) for state in states],
            "words": [],
            "complete": True,
            "has_unknown": False,
            "has_phi": False,
            "loop_facts": self._loop_facts(),
            "top_facts": self._top_facts(),
        }

        start = address.get("offset")
        size = parse_int_literal(length or "") if length is not None else None
        if start is None:
            result["complete"] = False
            result["has_unknown"] = True
            result["words"] = [{
                "offset": None,
                "offset_expr": address.get("offset_expr"),
                "value": "unknown",
                "branch_candidates": [{"value": "unknown", "path": "dynamic address", "source": "cfg_memory_ssa"}],
            }]
            return result

        if any(any(key.startswith("<unknown") for key in state.memory) for state in states):
            result["complete"] = False
            result["has_unknown"] = True
            result["words"] = [{
                "offset": None,
                "offset_expr": "unknown",
                "value": "unknown",
                "branch_candidates": [{"value": "unknown", "path": "local function side effect", "source": "cfg_memory_ssa"}],
            }]
            return result

        count = 1 if size is None else max(1, (size + MEMORY_WORD_BYTES - 1) // MEMORY_WORD_BYTES)
        for index in range(count):
            offset = start + index * MEMORY_WORD_BYTES
            key = _memory_key(address, offset)
            word = self._resolve_word(states, key, offset)
            if word.get("value") == "unknown":
                result["complete"] = False
                result["has_unknown"] = True
            if word.get("memory_phi"):
                result["has_phi"] = True
            result["words"].append(word)
        return result

    def _resolve_word(self, states: list[Any], key: str | None, offset: int) -> dict[str, Any]:
        candidates = []
        for state in states:
            definition = state.memory.get(key) if key else None
            candidates.append({
                "value": definition.value if definition else "unknown",
                "memory_ssa": definition.version if definition else None,
                "path": _path_text(state),
                "source": definition.kind if definition else "uninitialized",
                "definition": _definition_dict(definition),
                "aliases": [_alias_dict(alias) for alias in getattr(definition, "aliases", ())] if definition else [],
            })
        values = {item["value"] for item in candidates}
        word: dict[str, Any] = {
            "offset": offset,
            "offset_expr": str(offset),
            "address_key": key,
            "cfg_path_states": [item["path"] for item in candidates],
        }
        if len(values) == 1 and candidates:
            word["value"] = candidates[0]["value"]
            versions = [item["memory_ssa"] for item in candidates if item["memory_ssa"]]
            if versions:
                unique_versions = list(dict.fromkeys(versions))
                word["memory_ssa"] = unique_versions[0] if len(unique_versions) == 1 else f"phi({', '.join(unique_versions)})"
                if len(unique_versions) > 1:
                    word["memory_phi"] = word["memory_ssa"]
            definitions = [item["definition"] for item in candidates if item["definition"]]
            if definitions:
                word["definitions"] = definitions
            aliases = []
            seen = set()
            for item in candidates:
                for alias in item["aliases"]:
                    key2 = alias.get("key")
                    if key2 not in seen:
                        seen.add(key2)
                        aliases.append(alias)
            if aliases:
                word["aliases"] = aliases
        else:
            word["value"] = "unknown"
            word["branch_candidates"] = candidates
        return word

    def _loop_facts(self) -> list[dict[str, Any]]:
        facts = []
        for item in getattr(self.memory_ssa, "loop_records", {}).values():
            facts.append(_loop_fact_dict("LoopMemoryRecord", item))
        for item in getattr(self.memory_ssa, "memory_phis", {}).values():
            facts.append(_loop_fact_dict("MemoryPhi", item))
        for item in getattr(self.memory_ssa, "range_summaries", {}).values():
            facts.append(_loop_fact_dict("MemoryRangeSummary", item))
        # Current MemorySSA stores loop-carried phis as MemoryDefinition maps.
        for (header, address), definition in getattr(self.memory_ssa, "loop_memory_phis", {}).items():
            facts.append({"kind": "MemoryPhi", "header_node": header, "address": address, "definition": _definition_dict(definition)})
        for (header, address), definition in getattr(self.memory_ssa, "loop_join_memory_phis", {}).items():
            facts.append({"kind": "MemoryPhi", "header_node": header, "address": address, "definition": _definition_dict(definition), "join": True})
        return facts

    def _top_facts(self) -> list[dict[str, Any]]:
        return [_loop_fact_dict("MemoryTop", item) for item in getattr(self.memory_ssa, "memory_tops", [])]


def resolve_memory_read_with_loops(memory_ssa: Any, node_id: int, pointer: str, length: str | None = None, reason: str | None = None) -> dict[str, Any]:
    return MemoryQuery(memory_ssa).read(node_id, pointer, length, reason=reason)


def resolve_words(memory_ssa: Any, node_id: int, pointer: str, length: str | None) -> list[dict[str, Any]]:
    return resolve_memory_read_with_loops(memory_ssa, node_id, pointer, length).get("words", [])


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
            query = resolve_memory_read_with_loops(memory_ssa, node_id, semantic["ptr"], None, "mload")
            words = query["words"]
            semantic["read"]["resolved_words"] = words
            semantic["read"]["memory_read_result"] = query
            semantic["read"]["tracker_scope"] = query["tracker_scope"]
        elif op["kind"] == "memory_hash":
            query = resolve_memory_read_with_loops(memory_ssa, node_id, semantic["ptr"], semantic["length"], "keccak256")
            words = query["words"]
            semantic["read"]["resolved_words"] = words
            semantic["read"]["memory_read_result"] = query
            semantic["read"]["tracker_scope"] = query["tracker_scope"]
            semantic["resolved_inputs"] = words
            semantic["memory_read_result"] = query
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
                query = resolve_memory_read_with_loops(memory_ssa, node_id, data["ptr"], data["length"], "event_log_data")
                semantic["resolved_data"] = query["words"]
                semantic["memory_read_result"] = query
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
