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
from s_seir_model import EffectNode, ExpressionRole, FunctionSSEIR, SemanticOverlay
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


def test_free_memory_pointer_semantic_export_omits_unresolved_memory_source() -> None:
    e = effect({
        "read_from": "0x40",
        "semantic_value": "free_memory_pointer",
        "memory_read": {"has_unknown": True, "complete": False},
    }, kind="MemoryRead")
    attrs = semantic_function(e).to_semantic_dict()["effects"][0]["attrs"]
    assert attrs["semantic_value"] == "free_memory_pointer"
    assert "memory_semantics" not in attrs


def semantic_function(e: EffectNode, *, analysis_facts: list[dict] | None = None) -> FunctionSSEIR:
    return FunctionSSEIR(
        "Case.f()",
        "Case",
        "f",
        "f()",
        [],
        {
            "blocks": [{
                "block_id": "bb_1",
                "kind": "yul",
                "stmts": ["asm_s_1"],
                "terminator": {"kind": "Fallthrough"},
                "attrs": {
                    "src": "1:2:0",
                    "text": "keccak256(0, 64)",
                    "slithir_ssa": [{"kind": "Temporary"}],
                    "typed_def_use": {"definitions": []},
                },
            }],
            "edges": [],
            "dominance": {"roots": ["bb_1"]},
            "typed_def_use": {"blocks": {}},
            "control_dependencies": [],
            "assembly_boundaries": {},
            "loop_contexts": {},
        },
        [],
        [e],
        [],
        [],
        analysis_facts or [],
    )


def test_semantic_export_hides_resolved_query_trace() -> None:
    e = effect({
        "data_ptr": "0",
        "data_size": "32",
        "data_memory": {
            "complete": False,
            "has_unknown": True,
            "tracker_scope": "s_seir_memoryssa_query",
        },
        "sink_resolution": {
            "sink_kind": "EventLog",
            "path_resolutions": [{
                "condition": "flag",
                "status": "resolved",
                "arg_resolutions": {
                    "data": {
                        "normalized": "MemorySlice(amount)",
                        "memory_slice": {"slices": [{"extraction": "amount"}]},
                    },
                },
            }],
        },
    })
    exported = semantic_function(e, analysis_facts=[
        {"kind": "MemorySSAQueryLayer", "complete": False},
        {"kind": "SSEIRConstantTable", "constants": {}},
    ]).to_semantic_dict()
    attrs = exported["effects"][0]["attrs"]
    assert "data_memory" not in attrs
    assert "sink_resolution" not in attrs
    assert attrs["semantic_inputs"] == {
        "sink_kind": "EventLog",
        "paths": [{"arguments": {"data": "MemorySlice(amount)"}, "condition": "flag"}],
    }
    assert e.attrs["data_memory"]["has_unknown"] is True
    assert [fact["kind"] for fact in exported["analysis_facts"]] == ["SSEIRConstantTable"]
    assert "dominance" not in exported["control"]
    assert "slithir_ssa" not in exported["control"]["blocks"][0]["attrs"]


def test_semantic_export_keeps_only_real_unresolved_reason() -> None:
    e = effect({
        "input_ptr": "ptr",
        "input_size": "size",
        "input_memory": {"complete": False, "has_unknown": True},
        "sink_resolution": {
            "sink_kind": "Call",
            "path_resolutions": [{
                "condition": None,
                "status": "unresolved",
                "reason": "memory_source_unresolved",
                "arg_resolutions": {"input": {"expr": "ptr:size", "normalized": None}},
            }],
        },
    }, kind="Call")
    attrs = semantic_function(e).to_semantic_dict()["effects"][0]["attrs"]
    assert "input_memory" not in attrs
    assert attrs["semantic_inputs"] == {
        "sink_kind": "Call",
        "paths": [{
            "arguments": {"input": "ptr:size"},
            "unresolved_reason": "memory_source_unresolved",
        }],
    }


def test_semantic_export_compacts_memoryssa_fallback() -> None:
    e = effect({
        "ptr": "0",
        "size": "64",
        "memory_read": {
            "complete": True,
            "has_unknown": False,
            "byte_slice": {
                "path_slices": [{
                    "path": "guard",
                    "complete": True,
                    "slices": [{"extraction": "user"}, {"extraction": "balances.slot"}],
                }],
            },
        },
    }, kind="MemoryHash")
    attrs = semantic_function(e).to_semantic_dict()["effects"][0]["attrs"]
    assert "memory_read" not in attrs
    assert attrs["memory_semantics"] == [{
        "role": "memory_read",
        "paths": [{"values": ["user", "balances.slot"], "condition": "guard"}],
    }]


def test_semantic_export_removes_partial_query_implementation_fields() -> None:
    e = effect({
        "input_ptr": "ptr",
        "input_size": "size",
        "input_memory_partial": {"query_kind": "PartialMemorySliceResult", "complete": True},
        "output_memory_query": {"pointer": "out", "length": "32"},
        "payload_memory_partial": {"query_offset": 0, "slices": []},
        "payload_memory_complete": True,
        "words": [{
            "value": "x",
            "memory_ssa": "mem_1",
            "memory_version": "mem_1",
            "memory_versions": ["mem_1"],
        }],
    }, kind="Call")
    attrs = semantic_function(e).to_semantic_dict()["effects"][0]["attrs"]
    assert "input_memory_partial" not in attrs
    assert "output_memory_query" not in attrs
    assert "payload_memory_partial" not in attrs
    assert "payload_memory_complete" not in attrs
    assert attrs["words"] == [{"value": "x"}]


if __name__ == "__main__":
    tests = [
        test_zero_length_memory_query_clears_legacy_unknown,
        test_resolved_sink_keeps_raw_unknown_as_diagnostic_only,
        test_pattern_overlay_can_finish_unknown_call_input,
        test_existing_high_level_read_overlay_finishes_unknown_mload,
        test_true_unknown_remains_unknown_without_resolution,
        test_complete_memoryssa_query_is_resolved,
        test_free_memory_pointer_role_resolves_symbolic_mload,
        test_free_memory_pointer_semantic_export_omits_unresolved_memory_source,
        test_semantic_export_hides_resolved_query_trace,
        test_semantic_export_keeps_only_real_unresolved_reason,
        test_semantic_export_compacts_memoryssa_fallback,
        test_semantic_export_removes_partial_query_implementation_fields,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
