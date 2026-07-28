#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path as _SSEIRPath
import sys as _sseir_sys
_SSEIR_ROOT = _SSEIRPath(__file__).resolve().parents[1]
for _sseir_path in (_SSEIR_ROOT / "legacy_yul", _SSEIR_ROOT / "s_seir"):
    _sseir_text = str(_sseir_path)
    if _sseir_text not in _sseir_sys.path:
        _sseir_sys.path.insert(0, _sseir_text)

from dataclasses import dataclass
from typing import Any

from assembly_cfg_memory_adapter import resolve_memory_read_with_loops as legacy_resolve_memory_read
from assembly_memory_ssa import analyze_block
from assembly_semantic_ir import parse_int_literal, strip_ssa
from s_seir_model import FunctionUnit
from s_seir_yul_normalize import normalize_expr


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

    def __getattr__(self, name: str) -> Any:
        return getattr(self.backend, name)

    def states_at(self, node_id: int):
        return self.backend.states_at(node_id)

    def query_memory(self, node_id: int, pointer: str, length: str | None = None, reason: str | None = None) -> dict[str, Any]:
        result = legacy_resolve_memory_read(self.backend, node_id, pointer, length, reason)
        byte_slice = self.resolve_memory_byte_slice(node_id, pointer, length, reason)
        if byte_slice:
            result["byte_slice"] = byte_slice
        result["owner"] = "S-SEIR"
        result["tracker_scope"] = "s_seir_memoryssa_query"
        result["assembly_block"] = self.assembly_block_id
        result["function_loop_context"] = self.function_loop_context
        result["loop_alignment"] = "s_seir_controls_all_loop_contexts_legacy_yul_memoryssa_backend"
        if self.function_loop_context:
            result["inside_function_level_loop"] = True
        return result

    def resolve_memory_byte_slice(self, node_id: int, pointer: str, length: str | None = None, reason: str | None = None) -> dict[str, Any] | None:
        start = static_int(pointer)
        size = static_int(length or "")
        if start is None or size is None or size < 0 or size > 4096:
            return None
        states = list(self.backend.states_at(node_id))
        if not states:
            return None
        path_results = []
        for state in states:
            slices = self.byte_slices_for_state(state, node_id, start, size)
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
            "start": start,
            "size": size,
            "complete": complete,
            "has_overlap": any(item.get("overlap_count", 0) > 1 for item in merged),
            "slices": merged,
            "path_slices": path_results,
            "packed_semantics": [slice_semantic(item) for item in merged] if merged else [],
        }

    def byte_slices_for_state(self, state: Any, node_id: int, start: int, size: int) -> list[dict[str, Any]]:
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
            write_start = static_int(getattr(definition, "address", None))
            if write_start is None:
                write_start = first_static_alias_address(definition)
            if write_start is None:
                continue
            writes.append((int(def_node or 0), str(version), write_start, definition))
        for _node, _version, write_start, definition in sorted(writes, key=lambda item: (item[0], item[1])):
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
            write_end = write_start + width
            overlap_start = max(start, write_start)
            overlap_end = min(start + size, write_end)
            if overlap_start >= overlap_end:
                continue
            for address in range(overlap_start, overlap_end):
                query_offset = address - start
                source_offset = source_base_offset + (address - write_start)
                previous = cells.get(query_offset)
                cells[query_offset] = {
                    "absolute_address": address,
                    "query_offset": query_offset,
                    "source_value": value,
                    "source_offset": source_offset,
                    "source_width": source_width,
                    "source_version": getattr(definition, "version", None),
                    "source_node_id": getattr(definition, "node_id", None),
                    "source_kind": kind,
                    "origin_src": getattr(definition, "origin_src", None),
                    "overlap_count": int((previous or {}).get("overlap_count", 0)) + 1,
                }
        return coalesce_byte_cells(cells, size)


def loop_context_for_block(control: dict[str, Any] | None, block_id: int) -> list[dict[str, Any]]:
    contexts = (control or {}).get("loop_contexts", {})
    return contexts.get(block_id) or contexts.get(str(block_id)) or []


def build_memory_ssa_views(unit: FunctionUnit, control: dict[str, Any] | None = None) -> dict[int, SSeirMemorySSAView]:
    views: dict[int, SSeirMemorySSAView] = {}
    for block in unit.assembly_blocks:
        views[block.block_id] = SSeirMemorySSAView(
            assembly_block_id=block.block_id,
            backend=analyze_block(block),
            function_loop_context=loop_context_for_block(control, block.block_id),
        )
    return views


def resolve_memory_read_with_loops(memory_ssa: Any, node_id: int, pointer: str, length: str | None = None, reason: str | None = None) -> dict[str, Any]:
    if hasattr(memory_ssa, "query_memory"):
        return memory_ssa.query_memory(node_id, pointer, length, reason)
    result = legacy_resolve_memory_read(memory_ssa, node_id, pointer, length, reason)
    result["owner"] = "legacy_yul"
    return result


def static_int(value: Any) -> int | None:
    return parse_int_literal(strip_ssa(str(value or "").strip()))


def path_text(state: Any) -> str:
    return " && ".join(getattr(state, "predicates", []) or []) or "entry"


def first_static_alias_address(definition: Any) -> int | None:
    for alias in getattr(definition, "aliases", ()) or ():
        for value in (getattr(alias, "expression", None), getattr(alias, "key", None), getattr(alias, "base", None)):
            parsed = static_int(value)
            if parsed is not None:
                offset = getattr(alias, "offset", 0) or 0
                base = static_int(getattr(alias, "base", None))
                if base is not None:
                    return base + int(offset)
                return parsed
    return None


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
