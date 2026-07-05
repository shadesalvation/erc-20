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
from s_seir_model import FunctionUnit


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
        result["owner"] = "S-SEIR"
        result["tracker_scope"] = "s_seir_memoryssa_query"
        result["assembly_block"] = self.assembly_block_id
        result["function_loop_context"] = self.function_loop_context
        result["loop_alignment"] = "s_seir_controls_all_loop_contexts_legacy_yul_memoryssa_backend"
        if self.function_loop_context:
            result["inside_function_level_loop"] = True
        return result


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
