#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path as _SSEIRPath
import sys as _sseir_sys
_SSEIR_ROOT = _SSEIRPath(__file__).resolve().parents[1]
for _sseir_path in (_SSEIR_ROOT / "legacy_yul", _SSEIR_ROOT / "s_seir"):
    _sseir_text = str(_sseir_path)
    if _sseir_text not in _sseir_sys.path:
        _sseir_sys.path.insert(0, _sseir_text)

from typing import Any

from assembly_branch_materialization import find_expansions, format_expansion
from s_seir_id import IdAllocator
from s_seir_model import EffectNode, FunctionUnit


def build_branch_materialization_nodes(unit: FunctionUnit, memory_results: dict[int, Any]) -> tuple[list[EffectNode], list[dict[str, Any]]]:
    ids = IdAllocator()
    effects: list[EffectNode] = []
    facts: list[dict[str, Any]] = []
    refs_by_text: dict[tuple[int, str], list[str]] = {}
    for stmt in unit.source_statements:
        if stmt.lang == 'yul' and stmt.origin.get('assembly_block') is not None:
            refs_by_text.setdefault((int(stmt.origin['assembly_block']), stmt.text), []).append(stmt.stmt_id)

    for block in unit.assembly_blocks:
        view = memory_results.get(block.block_id)
        if not view:
            continue
        backend = getattr(view, 'backend', view)
        try:
            expansions = find_expansions(backend)
        except Exception as exc:
            facts.append({'kind': 'BranchMaterializationUnavailable', 'assembly_block': block.block_id, 'reason': type(exc).__name__, 'message': str(exc)})
            continue
        for expansion in expansions:
            attrs = expansion_to_attrs(block.block_id, expansion)
            refs = refs_by_text.get((block.block_id, expansion.sink_text), [f'asm_block_{block.block_id}'])
            effects.append(EffectNode(ids.new('eff_branch'), 'BranchMaterialization', refs, attrs))
            facts.append({'kind': 'BranchMaterialization', **attrs, 'text_ir': format_expansion(backend, expansion)})
    return effects, facts


def expansion_to_attrs(block_id: int, expansion: Any) -> dict[str, Any]:
    return {
        'assembly_block': block_id,
        'sink_node': expansion.sink_node,
        'sink_target': expansion.sink_target,
        'sink_text': expansion.sink_text,
        'mode': 'linearized' if expansion.linearized else 'branched',
        'baseline': path_slice(expansion.baseline),
        'branches': [
            {'predicates': list(predicates), 'slice': path_slice(path)}
            for predicates, path in expansion.branches
        ],
        'shared_node_ids': sorted(expansion.shared_node_ids),
        'forced_node_ids': sorted(expansion.forced_node_ids),
        'discarded_unknown_paths': [path_slice(path) for path in expansion.discarded_unknown_paths],
        'discarded_unknown_count': len(expansion.discarded_unknown_paths),
        'policy': 'discard_unknown_memory_paths_and_materialize_known_semantic_sink_paths',
    }


def path_slice(path: Any) -> dict[str, Any]:
    resolved = path.resolved
    return {
        'predicates': list(path.predicates),
        'signature': resolved.signature,
        'node_ids': sorted(resolved.node_ids),
        'memory_versions': sorted(resolved.memory_versions),
        'value_versions': sorted(resolved.value_versions),
        'unknown': resolved.unknown,
    }
