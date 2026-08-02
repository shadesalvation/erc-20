#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path as _SSEIRPath
import sys as _sseir_sys
import re
_SSEIR_ROOT = _SSEIRPath(__file__).resolve().parents[1]
for _sseir_path in (_SSEIR_ROOT / "legacy_yul", _SSEIR_ROOT / "s_seir"):
    _sseir_text = str(_sseir_path)
    if _sseir_text not in _sseir_sys.path:
        _sseir_sys.path.insert(0, _sseir_text)

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from assembly_cfg_memory_adapter import resolve_memory_read_with_loops as legacy_resolve_memory_read
from assembly_memory_ssa import analyze_block
from assembly_semantic_ir import parse_int_literal, strip_ssa
from s_seir_model import FunctionUnit
from s_seir_yul_normalize import call_parts, normalize_expr


@dataclass
class SSeirMemorySSAView:
    """S-SEIR-owned MemorySSA view backed by the legacy Yul MemorySSA engine.

    This class is intentionally thin: S-SEIR owns the analysis object, loop
    context, and query API, while reusing the existing MemorySSA algorithm and
    data structures through `backend`.
    """

    assembly_block_id: int
    backend: Any
    function_loop_context: list[dict[str, Any]]
    scope: str = "s_seir_function_memoryssa_view"
    inherited_states: list[Any] = field(default_factory=list)
    bridge_facts: list[dict[str, Any]] = field(default_factory=list)
    persistent_value_names: set[str] = field(default_factory=set)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.backend, name)

    def states_at(self, node_id: int):
        return self.backend.states_at(node_id)

    def query_states_at(self, node_id: int) -> list[Any]:
        current_states = list(self.backend.states_at(node_id))
        if not self.inherited_states:
            return current_states
        if not current_states:
            return [clone_bridge_state(state, self.assembly_block_id, self.persistent_value_names) for state in self.inherited_states]
        out = []
        for inherited in self.inherited_states:
            for current in current_states:
                out.append(merge_inherited_state(inherited, current, self.assembly_block_id, self.persistent_value_names))
        return out

    def query_memory(self, node_id: int, pointer: str, length: str | None = None, reason: str | None = None) -> dict[str, Any]:
        result = legacy_resolve_memory_read(self.backend, node_id, pointer, length, reason)
        byte_slice = self.resolve_memory_byte_slice(node_id, pointer, length, reason)
        if byte_slice:
            result["byte_slice"] = byte_slice
            symbolic_words = words_from_byte_slice(byte_slice)
            if byte_slice.get("complete") and int(byte_slice.get("size") or 0) == 0:
                result["words"] = []
                result["complete"] = True
                result["has_unknown"] = False
                result["empty_range"] = True
            elif symbolic_words:
                result["words"] = symbolic_words
                result["complete"] = bool(byte_slice.get("complete"))
                result["has_unknown"] = not bool(byte_slice.get("complete"))
        result["owner"] = "S-SEIR"
        result["tracker_scope"] = "s_seir_memoryssa_query"
        result["assembly_block"] = self.assembly_block_id
        result["function_loop_context"] = self.function_loop_context
        result["loop_alignment"] = "s_seir_controls_all_loop_contexts_legacy_yul_memoryssa_backend"
        if self.bridge_facts:
            result["function_memory_bridge"] = self.bridge_facts
        if self.function_loop_context:
            result["inside_function_level_loop"] = True
        return result

    def resolve_memory_byte_slice(self, node_id: int, pointer: str, length: str | None = None, reason: str | None = None) -> dict[str, Any] | None:
        size = self.resolve_static_int(node_id, length or "")
        if size is None or size < 0 or size > 4096:
            return None
        states = self.query_states_at(node_id)
        if not states:
            return None
        path_results = []
        for state in states:
            query_aliases = linear_aliases_for_text(pointer, state)
            slices = self.byte_slices_for_state(state, node_id, query_aliases, size)
            path_results.append({
                "path": path_text(state),
                "complete": byte_slice_complete(slices, size),
                "slices": slices,
            })
        signatures = {slice_signature(item["slices"]) for item in path_results}
        merged = path_results[0]["slices"] if len(signatures) == 1 and path_results else []
        complete = bool(path_results) and all(item["complete"] for item in path_results)
        return {
            "query_kind": "MemoryByteSliceResult",
            "tracker_scope": "s_seir_memory_byte_axis",
            "node_id": node_id,
            "reason": reason,
            "pointer": pointer,
            "length": length,
            "address_aliases": [alias for alias in linear_aliases_for_text(pointer, states[0])],
            "size": size,
            "complete": complete,
            "has_overlap": any(item.get("overlap_count", 0) > 1 for item in merged),
            "slices": merged,
            "path_slices": path_results,
            "packed_semantics": [slice_semantic(item) for item in merged] if merged else [],
        }

    def resolve_static_int(self, node_id: int, value: str | None) -> int | None:
        direct = static_int(value)
        if direct is not None:
            return direct
        text = str(value or "").strip()
        if not is_simple_identifier(text):
            return None
        for state in self.query_states_at(node_id):
            definition = getattr(state, "values", {}).get(text)
            if not definition:
                continue
            resolved = static_int(yul_expr_text(getattr(definition, "expression", None)))
            if resolved is not None:
                return resolved
        return None

    def byte_slices_for_state(self, state: Any, node_id: int, query_aliases: list[dict[str, Any]], size: int) -> list[dict[str, Any]]:
        cells: dict[int, dict[str, Any]] = {}
        writes = []
        seen_versions = set()
        for definition in getattr(state, "memory", {}).values():
            version = getattr(definition, "version", None)
            if version in seen_versions:
                continue
            seen_versions.add(version)
            def_node = getattr(definition, "node_id", None)
            if def_node is not None and int(def_node) > int(node_id):
                continue
            kind = getattr(definition, "kind", None)
            write_width = 1 if kind == "mstore8" else 32
            write_aliases = aliases_for_definition(definition)
            overlap_plan = first_alias_overlap(query_aliases, write_aliases, size, write_width)
            if overlap_plan is None:
                continue
            writes.append((int(def_node or 0), str(version), overlap_plan, definition))
        for _node, _version, overlap_plan, definition in sorted(writes, key=lambda item: (item[0], item[1])):
            kind = getattr(definition, "kind", None)
            if kind not in {"mstore", "mstore8"}:
                continue
            value = getattr(definition, "value", None)
            if kind == "mstore8":
                width = 1
                source_width = 32
                source_base_offset = 31
            else:
                width = 32
                source_width = 32
                source_base_offset = 0
            query_start = int(overlap_plan["query_offset"])
            write_relative_start = int(overlap_plan["write_relative_start"])
            overlap_size = int(overlap_plan["size"])
            for delta in range(overlap_size):
                query_offset = query_start + delta
                source_offset = source_base_offset + write_relative_start + delta
                previous = cells.get(query_offset)
                cells[query_offset] = {
                    "query_offset": query_offset,
                    "source_value": value,
                    "source_offset": source_offset,
                    "source_width": source_width,
                    "source_version": getattr(definition, "version", None),
                    "source_node_id": getattr(definition, "node_id", None),
                    "source_kind": kind,
                    "origin_src": getattr(definition, "origin_src", None),
                    "query_alias": overlap_plan.get("query_alias"),
                    "write_alias": overlap_plan.get("write_alias"),
                    "overlap_count": int((previous or {}).get("overlap_count", 0)) + 1,
                }
        return coalesce_byte_cells(cells, size)


def loop_context_for_block(control: dict[str, Any] | None, block_id: int) -> list[dict[str, Any]]:
    contexts = (control or {}).get("loop_contexts", {})
    return contexts.get(block_id) or contexts.get(str(block_id)) or []


def build_memory_ssa_views(unit: FunctionUnit, control: dict[str, Any] | None = None) -> dict[int, SSeirMemorySSAView]:
    views: dict[int, SSeirMemorySSAView] = {}
    prior_snapshots: list[Any] = []
    prior_block = None
    for block in unit.assembly_blocks:
        backend = analyze_block(block)
        bridge_facts = []
        inherited = []
        if prior_block is not None and prior_snapshots:
            safety = classify_intervening_solidity_memory_safety(prior_block, block)
            bridge_facts.append({
                "kind": "FunctionLevelAssemblyMemoryBridge",
                "from_assembly_block": prior_block.block_id,
                "to_assembly_block": block.block_id,
                "policy": "inherit_exit_memory_snapshot" if safety["safe"] else "break_on_possible_solidity_memory_mutation",
                "safe": safety["safe"],
                "reason": safety["reason"],
            })
            if safety["safe"]:
                inherited = prior_snapshots
        views[block.block_id] = SSeirMemorySSAView(
            assembly_block_id=block.block_id,
            backend=backend,
            function_loop_context=loop_context_for_block(control, block.block_id),
            inherited_states=inherited,
            bridge_facts=bridge_facts,
            persistent_value_names=function_level_value_names(unit),
        )
        prior_snapshots = exit_snapshots_for_backend(backend)
        prior_block = block
    return views


def exit_snapshots_for_backend(backend: Any) -> list[Any]:
    cfg = getattr(backend, "cfg", None)
    observations = getattr(backend, "observations", {}) or {}
    exit_id = getattr(cfg, "exit", None)
    states: list[Any] = []
    if exit_id is not None and exit_id in observations:
        obs = observations[exit_id]
        states = list(getattr(obs, "outgoing", None) or getattr(obs, "incoming", None) or [])
    if not states:
        for obs in observations.values():
            outgoing = list(getattr(obs, "outgoing", None) or [])
            if outgoing:
                states = outgoing
    return states[:8]


def classify_intervening_solidity_memory_safety(previous_block: Any, next_block: Any) -> dict[str, Any]:
    prev_end = previous_block.source_range[1]
    next_start = next_block.source_range[0]
    if next_start <= prev_end:
        return {"safe": False, "reason": "overlapping_or_unordered_assembly_ranges"}
    span = previous_block.source[prev_end:next_start]
    clean = strip_comments_and_strings(span)
    if not clean.strip():
        return {"safe": True, "reason": "empty_intervening_span"}
    unsafe_patterns = [
        (r"\bassembly\b", "intervening_inline_assembly"),
        (r"\bnew\s+(?:bytes|string|[A-Za-z_$][\w$]*(?:\s*\[\s*\])?)", "memory_allocation"),
        (r"\babi\s*\.", "abi_memory_operation"),
        (r"\breturn\b", "intervening_return"),
        (r"\brevert\b", "intervening_revert"),
        (r"\bdelete\b", "delete_may_touch_storage_or_memory"),
        (r"\.(?:call|staticcall|delegatecall|callcode)\s*\(", "external_low_level_call"),
        (r"\b[A-Za-z_$][\w$]*\s*\.\s*(?:push|pop)\s*\(", "array_mutation_call"),
    ]
    for pattern, reason in unsafe_patterns:
        if re.search(pattern, clean):
            return {"safe": False, "reason": reason}
    calls = [
        match.group(1)
        for match in re.finditer(r"(?<!\.)\b([A-Za-z_$][\w$]*)\s*\(", clean)
        if match.group(1) not in {"if", "for", "while", "require", "assert", "unchecked"}
    ]
    if calls:
        return {"safe": False, "reason": "intervening_function_call:" + ",".join(sorted(set(calls))[:4])}
    return {"safe": True, "reason": "value_only_solidity_span"}


def strip_comments_and_strings(text: str) -> str:
    text = re.sub(r"//.*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)
    text = re.sub(r"'(?:\\.|[^'\\])*'", "''", text)
    return text


def function_level_value_names(unit: FunctionUnit) -> set[str]:
    names = set()
    for item in [*unit.parameters, *unit.returns, *unit.locals, *unit.state_variables]:
        name = getattr(item, "name", None)
        if name:
            names.add(str(name))
    return names


def clone_bridge_state(state: Any, target_block_id: int, persistent_value_names: set[str] | None = None) -> Any:
    memory, mapping = clone_bridge_memory(getattr(state, "memory", {}) or {}, target_block_id)
    keep = persistent_value_names or set()
    inherited_values = {
        name: definition
        for name, definition in (getattr(state, "values", {}) or {}).items()
        if str(name) in keep
    }
    return SimpleNamespace(
        predicates=tuple(getattr(state, "predicates", ()) or ()),
        memory=memory,
        values=inherited_values,
        loop_bases={},
        trace=tuple(getattr(state, "trace", ()) or ()),
        _sseir_bridge_versions=mapping,
    )


def merge_inherited_state(inherited: Any, current: Any, target_block_id: int, persistent_value_names: set[str] | None = None) -> Any:
    bridge = clone_bridge_state(inherited, target_block_id, persistent_value_names)
    memory = dict(bridge.memory)
    memory.update(getattr(current, "memory", {}) or {})
    values = dict(getattr(bridge, "values", {}) or {})
    values.update(getattr(current, "values", {}) or {})
    predicates = tuple(getattr(inherited, "predicates", ()) or ()) + tuple(getattr(current, "predicates", ()) or ())
    return SimpleNamespace(
        predicates=tuple(dict.fromkeys(predicates)),
        memory=memory,
        values=values,
        loop_bases=dict(getattr(current, "loop_bases", {}) or {}),
        trace=tuple(getattr(current, "trace", ()) or ()),
        _sseir_bridge_versions=getattr(bridge, "_sseir_bridge_versions", {}),
    )


def clone_bridge_memory(memory: dict[str, Any], target_block_id: int) -> tuple[dict[str, Any], dict[str, str]]:
    by_old_version: dict[str, Any] = {}
    version_map: dict[str, str] = {}
    out: dict[str, Any] = {}
    for key, definition in memory.items():
        old_version = str(getattr(definition, "version", key))
        clone = by_old_version.get(old_version)
        if clone is None:
            new_version = f"bridge_b{target_block_id}_{old_version}"
            version_map[old_version] = new_version
            clone = SimpleNamespace(
                version=new_version,
                node_id=-1,
                address=getattr(definition, "address", None),
                value=getattr(definition, "value", None),
                kind=getattr(definition, "kind", None),
                origin_src=getattr(definition, "origin_src", None),
                memory_before={},
                aliases=clone_aliases(getattr(definition, "aliases", ()) or ()),
                inherited_from_version=old_version,
            )
            by_old_version[old_version] = clone
        out[key] = clone
    return out, version_map


def clone_aliases(aliases: Any) -> tuple[Any, ...]:
    return tuple(
        SimpleNamespace(
            key=getattr(alias, "key", None),
            base=getattr(alias, "base", None),
            offset=getattr(alias, "offset", 0),
            expression=getattr(alias, "expression", None),
            source=f"function_bridge:{getattr(alias, 'source', '')}",
        )
        for alias in aliases
    )


def resolve_memory_read_with_loops(memory_ssa: Any, node_id: int, pointer: str, length: str | None = None, reason: str | None = None) -> dict[str, Any]:
    if hasattr(memory_ssa, "query_memory"):
        return memory_ssa.query_memory(node_id, pointer, length, reason)
    result = legacy_resolve_memory_read(memory_ssa, node_id, pointer, length, reason)
    result["owner"] = "legacy_yul"
    return result


def static_int(value: Any) -> int | None:
    return parse_int_literal(strip_ssa(str(value or "").strip()))


def yul_expr_text(node: Any) -> str:
    if isinstance(node, dict):
        node_type = node.get("nodeType")
        if node_type == "YulIdentifier":
            return str(node.get("name") or "")
        if node_type == "YulLiteral":
            return str(node.get("value") or node.get("hexValue") or "")
        if node_type == "YulFunctionCall":
            fn = node.get("functionName") or {}
            name = yul_expr_text(fn)
            args = ", ".join(yul_expr_text(arg) for arg in node.get("arguments") or [])
            return f"{name}({args})"
    return str(node or "")


def is_simple_identifier(value: Any) -> bool:
    text = str(value or "")
    return bool(text) and all(ch.isalnum() or ch in {"_", "$"} for ch in text) and not text[0].isdigit()


def path_text(state: Any) -> str:
    return " && ".join(getattr(state, "predicates", []) or []) or "entry"


def linear_aliases_for_text(expr: Any, state: Any, seen: set[str] | None = None) -> list[dict[str, Any]]:
    seen = seen or set()
    text = str(expr or "").strip()
    if not text:
        return []
    parsed = static_int(text)
    if parsed is not None:
        return [
            {"key": normalize_alias_key("0x00", parsed), "base": "0x00", "offset": parsed, "expression": alias_expression("0x00", parsed), "source": "literal-zero-base"},
            {"key": normalize_expr(text), "base": text, "offset": 0, "expression": text, "source": "literal"},
        ]
    if is_simple_identifier(text):
        aliases = [{"key": normalize_alias_key(text, 0), "base": text, "offset": 0, "expression": text, "source": "identifier"}]
        if text in seen:
            return aliases
        definition = getattr(state, "values", {}).get(text)
        if not definition:
            return aliases
        source = yul_expr_text(getattr(definition, "expression", None))
        name, _args = call_parts(source)
        if name in {"add", "sub"} or is_simple_identifier(source):
            for alias in linear_aliases_for_text(source, state, seen | {text}):
                if alias["key"] not in {item["key"] for item in aliases}:
                    item = dict(alias)
                    item["source"] = f"value:{getattr(definition, 'version', '')}"
                    aliases.append(item)
        return aliases
    name, args = call_parts(text)
    if name in {"add", "sub"} and len(args) == 2:
        left, right = args
        right_const = static_int(right)
        left_const = static_int(left)
        if right_const is not None:
            delta = right_const if name == "add" else -right_const
            return shift_aliases(linear_aliases_for_text(left, state, seen), delta, text)
        if name == "add" and left_const is not None:
            return shift_aliases(linear_aliases_for_text(right, state, seen), left_const, text)
    return [{"key": normalize_expr(text), "base": text, "offset": 0, "expression": text, "source": "symbolic"}]


def shift_aliases(aliases: list[dict[str, Any]], delta: int, original: str) -> list[dict[str, Any]]:
    out = []
    seen = set()
    for alias in aliases:
        offset = int(alias.get("offset") or 0) + delta
        key = normalize_alias_key(str(alias.get("base")), offset)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "key": key,
            "base": alias.get("base"),
            "offset": offset,
            "expression": alias_expression(str(alias.get("base")), offset),
            "source": alias.get("source"),
        })
    original_key = normalize_expr(original)
    if original_key not in seen:
        out.append({"key": original_key, "base": original, "offset": 0, "expression": original, "source": "original"})
    return out


def normalize_alias_key(base: str, offset: int) -> str:
    base_text = normalize_expr(base)
    return base_text if offset == 0 else f"{base_text} + {offset}"


def alias_expression(base: str, offset: int) -> str:
    base_text = str(base)
    return base_text if offset == 0 else f"{base_text} + {offset}"


def aliases_for_definition(definition: Any) -> list[dict[str, Any]]:
    aliases = []
    for alias in getattr(definition, "aliases", ()) or ():
        aliases.append({
            "key": getattr(alias, "key", None),
            "base": getattr(alias, "base", None),
            "offset": int(getattr(alias, "offset", 0) or 0),
            "expression": getattr(alias, "expression", None),
            "source": getattr(alias, "source", None),
        })
    if not aliases:
        address = str(getattr(definition, "address", "") or "")
        parsed = static_int(address)
        if parsed is not None:
            aliases.extend(linear_aliases_for_text(address, type("State", (), {"values": {}})()))
        elif address:
            aliases.append({"key": normalize_expr(address), "base": address, "offset": 0, "expression": address, "source": "definition-address"})
    return aliases


def first_alias_overlap(query_aliases: list[dict[str, Any]], write_aliases: list[dict[str, Any]], size: int, write_width: int) -> dict[str, Any] | None:
    for query in query_aliases:
        for write in write_aliases:
            if str(query.get("base")) != str(write.get("base")):
                continue
            query_start = int(query.get("offset") or 0)
            query_end = query_start + size
            write_start = int(write.get("offset") or 0)
            overlap_start = max(query_start, write_start)
            overlap_end = min(query_end, write_start + write_width)
            if overlap_start >= overlap_end:
                continue
            return {
                "query_offset": overlap_start - query_start,
                "write_relative_start": overlap_start - write_start,
                "size": overlap_end - overlap_start,
                "query_alias": query,
                "write_alias": write,
            }
    return None


def known_value_info(value: Any) -> dict[str, Any]:
    raw = str(value or "").strip()
    text = normalize_expr(raw)
    if not raw or raw == "unknown" or raw.startswith("unknown("):
        return {"known": False, "reason": "unknown_value"}
    if static_int(raw) is not None:
        return {"known": True, "kind": "constant", "expr": text}
    if text in {"msg.sender", "caller()"}:
        return {"known": True, "kind": "evm_builtin", "expr": "msg.sender"}
    name, _args = call_parts(raw)
    if name in {"caller", "timestamp", "gas", "address", "number", "origin", "callvalue", "calldatasize"}:
        return {"known": True, "kind": "evm_builtin", "expr": text}
    if is_simple_identifier(raw) or raw.endswith(".slot"):
        return {"known": True, "kind": "symbolic_known", "expr": text}
    return {"known": True, "kind": "derived_known", "expr": text}


def words_from_byte_slice(byte_slice: dict[str, Any]) -> list[dict[str, Any]]:
    size = int(byte_slice.get("size") or 0)
    slices = byte_slice.get("slices") or []
    if not byte_slice.get("complete") or not slices:
        return []
    if len(slices) == 1:
        item = slices[0]
        return [word_from_slice(byte_slice, item, size)]
    if all(int(item.get("size") or 0) == 32 for item in slices):
        return [word_from_slice(byte_slice, item, int(item.get("size") or 32)) for item in slices]
    return [{
        "offset": 0,
        "offset_expr": "0",
        "address_key": str((byte_slice.get("address_aliases") or [{}])[0].get("expression") or byte_slice.get("pointer")),
        "value": "abi.encodePacked(" + ", ".join(str(item.get("extraction")) for item in slices) + ")",
        "source": "byte_axis_memory_slice",
        "size": size,
        "byte_slices": slices,
        "known_value": {
            "known": all((item.get("known_value") or {}).get("known") for item in slices),
            "kind": "byte_slice_composite",
        },
    }]


def word_from_slice(byte_slice: dict[str, Any], item: dict[str, Any], size: int) -> dict[str, Any]:
    return {
        "offset": int(item.get("query_offset") or 0),
        "offset_expr": str(item.get("query_offset") or 0),
        "address_key": str((item.get("query_alias") or {}).get("expression") or byte_slice.get("pointer")),
        "value": item.get("extraction"),
        "source": "byte_axis_memory_slice",
        "size": size,
        "memory_ssa": item.get("source_version"),
        "definition": {
            "node_id": item.get("source_node_id"),
            "version": item.get("source_version"),
            "address": (item.get("write_alias") or {}).get("expression"),
            "value": item.get("source_value"),
        },
        "known_value": item.get("known_value"),
    }


def coalesce_byte_cells(cells: dict[int, dict[str, Any]], size: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    offset = 0
    while offset < size:
        cell = cells.get(offset)
        if not cell:
            offset += 1
            continue
        start = offset
        end = offset + 1
        while end < size:
            nxt = cells.get(end)
            if not nxt:
                break
            if (
                nxt.get("source_value") != cell.get("source_value")
                or nxt.get("source_version") != cell.get("source_version")
                or int(nxt.get("source_offset", -1)) != int(cell.get("source_offset", -1)) + (end - start)
                or nxt.get("source_kind") != cell.get("source_kind")
            ):
                break
            end += 1
        size2 = end - start
        item = {
            "query_offset": start,
            "size": size2,
            "source_value": cell.get("source_value"),
            "source_range": [cell.get("source_offset"), int(cell.get("source_offset")) + size2],
            "source_offset": cell.get("source_offset"),
            "source_width": cell.get("source_width"),
            "source_version": cell.get("source_version"),
            "source_node_id": cell.get("source_node_id"),
            "source_kind": cell.get("source_kind"),
            "origin_src": cell.get("origin_src"),
            "extraction": byte_extraction(cell.get("source_value"), int(cell.get("source_offset")), size2, int(cell.get("source_width") or 32)),
            "known_value": known_value_info(cell.get("source_value")),
            "overlap_count": cell.get("overlap_count", 1),
        }
        out.append(item)
        offset = end
    return out


def byte_extraction(value: Any, source_offset: int, size: int, source_width: int) -> str:
    text = normalize_expr(value)
    if source_width == 32 and source_offset == 0 and size == 32:
        return text
    if source_width == 32 and source_offset == 12 and size == 20 and is_address_like(text):
        return f"bytes20({text})"
    if source_width == 32 and source_offset + size == 32:
        return f"low_bytes({text}, {size})"
    if source_width == 32 and source_offset == 0:
        return f"high_bytes({text}, {size})"
    return f"bytes({text}, offset={source_offset}, size={size})"


def is_address_like(text: str) -> bool:
    return text in {"msg.sender", "caller()"} or text.endswith(".sender")


def slice_semantic(item: dict[str, Any]) -> str:
    return str(item.get("extraction") or "")


def slice_signature(slices: list[dict[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            item.get("query_offset"),
            item.get("size"),
            item.get("source_value"),
            tuple(item.get("source_range") or []),
            item.get("source_version"),
            item.get("extraction"),
        )
        for item in slices
    )


def byte_slice_complete(slices: list[dict[str, Any]], size: int) -> bool:
    cursor = 0
    for item in sorted(slices, key=lambda it: int(it.get("query_offset") or 0)):
        offset = int(item.get("query_offset") or 0)
        width = int(item.get("size") or 0)
        if offset > cursor:
            return False
        cursor = max(cursor, offset + width)
        if cursor >= size:
            return True
    return size == 0
