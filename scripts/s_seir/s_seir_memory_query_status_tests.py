#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for item in (ROOT / "legacy_yul", ROOT / "s_seir"):
    text = str(item)
    if text not in sys.path:
        sys.path.insert(0, text)

from s_seir_memory_ssa import SSeirMemorySSAView
from s_seir_model import EffectNode, ExpressionRole, SemanticOverlay
from s_seir_semantic_normalizer import SemanticNormalizer


class State:
    predicates = ()
    memory = {}
    values = {}


class Backend:
    def states_at(self, _node_id: int):
        return [State()]


def effect(attrs: dict, effect_id: str = "eff_1", kind: str = "EventLog") -> EffectNode:
    return EffectNode(effect_id, kind, ["asm_s_1"], attrs)


def overlay(kind: str, effect_id: str = "eff_1") -> SemanticOverlay:
    return SemanticOverlay("ov_1", kind, [effect_id], ["asm_s_1"], {})


def fact_for(
    e: EffectNode,
    overlays: list[SemanticOverlay] | None = None,
    roles: list[ExpressionRole] | None = None,
) -> dict:
    facts = SemanticNormalizer.branch_materialization_facts([e], overlays or [], roles or [])
    assert len(facts) == 1, facts
    return facts[0]


def test_zero_length_memory_query_clears_legacy_unknown() -> None:
    view = SSeirMemorySSAView(1, Backend(), [])
    legacy = {"words": [{"value": "unknown"}], "complete": False, "has_unknown": True}
    with patch("s_seir_memory_ssa.legacy_resolve_memory_read", return_value=legacy):
        result = view.query_memory(1, "arbitraryPtr", "0x00", "event_log_data")
    assert result["words"] == []
    assert result["complete"] is True
    assert result["has_unknown"] is False
    assert result["empty_range"] is True


def test_resolved_sink_keeps_raw_unknown_as_diagnostic_only() -> None:
    e = effect({
        "data_memory": {"has_unknown": True, "complete": False},
        "sink_resolution": {"path_resolutions": [{"status": "resolved"}]},
    })
    fact = fact_for(e)
    assert fact["memory_query_has_unknown"] is True
    assert fact["has_unknown"] is False
    assert fact["final_status"] == "resolved"
    assert fact["resolved_by"] == "sink_resolver"


def test_pattern_overlay_can_finish_unknown_call_input() -> None:
    e = effect({"input_memory": {"has_unknown": True, "complete": False}}, kind="Call")
    fact = fact_for(e, [overlay("AbiCallDataConstruction")])
    assert fact["memory_query_has_unknown"] is True
    assert fact["has_unknown"] is False
    assert fact["final_status"] == "resolved"
    assert fact["resolved_by"] == "semantic_overlay_pattern"
    assert fact["resolution_overlays"] == ["ov_1"]


def test_existing_high_level_read_overlay_finishes_unknown_mload() -> None:
    e = effect({"memory_read": {"has_unknown": True, "complete": False}}, kind="MemoryRead")
    fact = fact_for(e, [overlay("StructFieldRead")])
    assert fact["memory_query_has_unknown"] is True
    assert fact["has_unknown"] is False
    assert fact["final_status"] == "resolved"
    assert fact["resolved_by"] == "semantic_overlay_pattern"


def test_true_unknown_remains_unknown_without_resolution() -> None:
    e = effect({"input_memory": {"has_unknown": True, "complete": False}}, kind="Call")
    fact = fact_for(e)
    assert fact["memory_query_has_unknown"] is True
    assert fact["has_unknown"] is True
    assert fact["final_status"] == "unresolved"
    assert fact["resolved_by"] is None


def test_complete_memoryssa_query_is_resolved() -> None:
    e = effect({"memory_read": {"has_phi": True, "has_unknown": False, "complete": True}}, kind="MemoryHash")
    fact = fact_for(e)
    assert fact["memory_query_has_unknown"] is False
    assert fact["has_unknown"] is False
    assert fact["final_status"] == "resolved"
    assert fact["resolved_by"] == "memory_ssa"


def test_free_memory_pointer_role_resolves_symbolic_mload() -> None:
    e = effect({
        "read_from": "0x40",
        "memory_read": {"has_unknown": True, "complete": False},
    }, kind="MemoryRead")
    role = ExpressionRole(
        "expr_1",
        "mload(0x40)",
        "free_memory_pointer",
        "free_memory_pointer",
        "memory_ptr",
        "asm_s_1",
        {"effect": "eff_1", "memory_slot": "0x40"},
    )
    fact = fact_for(e, roles=[role])
    assert fact["memory_query_has_unknown"] is True
    assert fact["memory_query_complete"] is False
    assert fact["has_unknown"] is False
    assert fact["final_status"] == "resolved"
    assert fact["resolved_by"] == "expression_role"
    assert fact["resolution_roles"] == ["expr_1"]
    assert fact["semantic_value"] == "free_memory_pointer"


if __name__ == "__main__":
    tests = [
        test_zero_length_memory_query_clears_legacy_unknown,
        test_resolved_sink_keeps_raw_unknown_as_diagnostic_only,
        test_pattern_overlay_can_finish_unknown_call_input,
        test_existing_high_level_read_overlay_finishes_unknown_mload,
        test_true_unknown_remains_unknown_without_resolution,
        test_complete_memoryssa_query_is_resolved,
        test_free_memory_pointer_role_resolves_symbolic_mload,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
