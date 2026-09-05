#!/usr/bin/env python3
"""Focused invariants for the independent Semantic Fact IR bridge."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
for item in (ROOT / "legacy_yul", ROOT / "s_seir"):
    text = str(item)
    if text not in sys.path:
        sys.path.insert(0, text)

from s_seir_semantic_fact_ir import SemanticFactIRBridge
from s_seir_yul_semantic_lifter import YulSemanticLifter


FUNCTION_ID = "Token.transfer(address,uint256)"
VARIABLES = [
    {"name": "amount", "kind": "parameter", "type_string": "uint256", "declaration_id": 1},
    {"name": "result", "kind": "return", "type_string": "uint256", "declaration_id": 2},
]


def function(control: dict, variables: list[dict] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        function_id=FUNCTION_ID,
        contract="Token",
        function="transfer",
        signature="transfer(address,uint256)",
        control=control,
        _sseir_variable_bindings=variables or VARIABLES,
    )


def block(block_id: str, kind: str = "solidity", condition: str | None = None) -> dict:
    return {
        "block_id": block_id,
        "kind": kind,
        "stmts": [],
        "attrs": {},
        "terminator": {"kind": "if", "condition": condition} if condition else {},
    }


def solidity(operation_id: str, anchor: str, *, reads: list[str] | None = None, writes: list[str] | None = None) -> dict:
    return {
        "operation_id": operation_id,
        "origin": "solidity_atomic_operation",
        "kind": "ValueAssign",
        "reads": reads or [], "writes": writes or [],
        "order": {"operation_order": 1},
        "semantic_provenance": {"anchor_cfg_node": anchor, "evidence_cfg_nodes": [anchor]},
    }


def yul(operation_id: str, anchor: str, *, reads: list[str] | None = None, writes: list[str] | None = None, extra: dict | None = None) -> dict:
    return {
        "operation_id": operation_id,
        "origin": "yul_sseir_semantic_overlay",
        "kind": "StateWrite",
        "reads": reads or [], "writes": writes or [],
        "order": {"operation_order": 1},
        "semantic_provenance": {"semantic_source": {"overlay_id": operation_id}, "anchor_cfg_node": anchor, "evidence_cfg_nodes": [anchor]},
        **(extra or {}),
    }


def all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(all_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(all_keys(item) for item in value)) if value else set()
    return set()


class SemanticFactIRBridgeTests(unittest.TestCase):
    def test_solidity_to_yul_and_yul_boundary_to_solidity_are_explicit(self) -> None:
        control = {
            "blocks": [block("s0"), block("y0", "yul"), block("y1", "yul"), block("s1")],
            "edges": [
                {"from": "s0", "to": "y0", "kind": "next"},
                {"from": "y0", "to": "y1", "kind": "next"},
                {"from": "y1", "to": "s1", "kind": "next"},
            ],
            "assembly_boundaries": {"asm_1": {"assembly_block": "asm_1", "exit_block": "y1", "external_writes": [{"name": "result"}]}},
        }
        result = SemanticFactIRBridge().build_function(
            function(control),
            [solidity("sol_def", "s0", writes=["amount_1"]), solidity("sol_return", "s1", reads=["result"])],
            [yul("yul_store", "y0", reads=["amount"], writes=["result"], extra={"evidence": {"effects": ["must_not_escape"]}})],
        )
        edges = result["semantic_edges"]
        self.assertTrue(any(edge["kind"] == "bridge_input" and edge["from"].endswith(":sol_def") and edge["to"].endswith(":yul_store") for edge in edges))
        self.assertEqual(1, len(result["boundary_links"]))
        self.assertEqual("bridge_output", result["boundary_links"][0]["kind"])
        self.assertTrue(result["boundary_links"][0]["to"].endswith(":sol_return"))
        self.assertTrue(any(edge["kind"] == "bridge_output" and edge["from"].endswith(":yul_store") for edge in edges))
        yul_node = next(node for node in result["semantic_nodes"] if node["source_lang"] == "yul")
        self.assertNotIn("effects", all_keys(yul_node))

    def test_join_uses_a_single_phi_for_the_yul_read(self) -> None:
        control = {
            "blocks": [block("branch", condition="flag"), block("left"), block("right"), block("join", "yul")],
            "edges": [
                {"from": "branch", "to": "left", "kind": "true"},
                {"from": "branch", "to": "right", "kind": "false"},
                {"from": "left", "to": "join", "kind": "next"},
                {"from": "right", "to": "join", "kind": "next"},
            ],
        }
        result = SemanticFactIRBridge().build_function(
            function(control),
            [solidity("left_def", "left", writes=["amount_1"]), solidity("right_def", "right", writes=["amount_2"])],
            [yul("join_read", "join", reads=["amount"])],
        )
        self.assertEqual(1, len(result["fact_ssa"]["phis"]))
        phi = result["fact_ssa"]["phis"][0]
        yul_node = next(node for node in result["semantic_nodes"] if node["semantic_id"].endswith(":join_read"))
        self.assertEqual(phi["version"], yul_node["fact_ssa"]["reads"][0]["version"])
        self.assertEqual(2, len(phi["incoming_versions"]))
        self.assertEqual(2, len([edge for edge in result["semantic_edges"] if edge["kind"] == "data_phi"]))

    def test_revert_is_a_fact_cfg_terminal(self) -> None:
        control = {
            "blocks": [block("branch", condition="bad"), {**block("revert", "yul"), "terminator": {"kind": "Revert", "text": "revert(0, 0)"}}, block("ok", "yul")],
            "edges": [
                {"from": "branch", "to": "revert", "kind": "true"},
                {"from": "branch", "to": "ok", "kind": "false"},
                {"from": "revert", "to": "ok", "kind": "next"},
            ],
        }
        result = SemanticFactIRBridge().build_function(
            function(control), [], [yul("require", "revert", extra={"kind": "Require"}), yul("ok", "ok")]
        )
        revert_block = next(item for item in result["fact_cfg"]["blocks"] if item["origin"]["source_block_id"] == "revert")
        self.assertEqual([], revert_block["successors"])
        self.assertFalse(any(edge["from"] == revert_block["block_id"] for edge in result["fact_cfg"]["edges"]))
        self.assertFalse(any(item["kind"] == "terminal_fact_cfg_successor" for item in result["diagnostics"]))

    def test_location_aliases_merge_by_high_semantic_identity(self) -> None:
        base = {
            "function_id": FUNCTION_ID, "kind": "StorageLocationResolve", "stmt_refs": ["stmt"],
            "lvalue": "fromSlot", "rvalue": "_balances[from]",
            "semantic": {"location": {"kind": "mapping", "access": "_balances[from]"}},
            "semantic_provenance": {"anchor_cfg_node": "y0", "semantic_source": {"overlay_id": "ov1", "overlay_kind": "MappingSlot"}},
        }
        duplicate = {
            **base,
            "semantic_provenance": {"anchor_cfg_node": "y0", "semantic_source": {"overlay_id": "ov2", "overlay_kind": "MappingSlot"}},
        }
        result = YulSemanticLifter._dedupe_location_facts([base, duplicate])
        self.assertEqual(1, len(result))
        self.assertEqual(["ov1", "ov2"], [item["overlay_id"] for item in result[0]["semantic_provenance"]["merged_semantic_sources"]])

    def test_high_semantic_operation_replaces_its_generic_derivation(self) -> None:
        common = {"stmt_refs": ["stmt"], "semantic_provenance": {"anchor_cfg_node": "y0"}}
        high = {**common, "operation_id": "high", "evidence": {"overlay": "high"}, "kind": "ValueCompute", "writes": ["result"]}
        generic = {**common, "operation_id": "generic", "evidence": {"overlay": "generic"}, "kind": "ValueCompute", "writes": ["result"]}
        step = {**common, "operation_id": "step", "evidence": {"overlay": "step"}, "kind": "ValueCompute", "writes": ["__tmp"]}
        overlays = {
            "high": {"kind": "AddressHasCode", "attrs": {"target": "result", "source_expression": "iszero(iszero(extcodesize(account)))", "solidity_equivalent": True}},
            "generic": {"kind": "ExpressionNormalization", "attrs": {"target": "result", "expression": "iszero(iszero(extcodesize(account)))"}},
            "step": {"kind": "EvaluationStep", "attrs": {}},
        }
        result = YulSemanticLifter._select_canonical_high_semantics([high, generic, step], overlays)
        self.assertEqual(["high"], [item["operation_id"] for item in result])

    def test_storage_semantic_operation_replaces_its_keccak_derivation(self) -> None:
        high = {
            "operation_id": "location", "kind": "StorageLocationResolve", "lvalue": "slot", "stmt_refs": ["slot", "load"],
            "semantic_provenance": {"anchor_cfg_node": "y0"}, "evidence": {"overlay": "location"},
        }
        generic = {
            "operation_id": "keccak", "kind": "ValueCompute", "lvalue": "slot", "stmt_refs": ["slot"],
            "semantic_provenance": {"anchor_cfg_node": "y0"}, "evidence": {"overlay": "generic"},
        }
        overlays = {
            "location": {"kind": "MappingSlot", "attrs": {}},
            "generic": {"kind": "ExpressionNormalization", "attrs": {"target": "slot", "expression": "keccak256(0, 64)"}},
        }
        result = YulSemanticLifter._select_canonical_high_semantics([high, generic], overlays)
        self.assertEqual(["location"], [item["operation_id"] for item in result])

    def test_call_output_semantic_operation_replaces_only_its_mload_assignment(self) -> None:
        common = {"stmt_refs": ["read"], "semantic_provenance": {"anchor_cfg_node": "y1"}}
        high = {**common, "operation_id": "call_output", "lvalue": "result", "evidence": {"overlay": "call_output"}}
        generic = {**common, "operation_id": "mload_result", "lvalue": "result", "evidence": {"overlay": "generic"}}
        unrelated = {**common, "operation_id": "other", "lvalue": "other", "evidence": {"overlay": "other"}}
        overlays = {
            "call_output": {"kind": "CallOutputRead", "attrs": {"target": "result"}},
            "generic": {"kind": "ExpressionNormalization", "attrs": {"target": "result"}},
            "other": {"kind": "ExpressionNormalization", "attrs": {"target": "other"}},
        }
        result = YulSemanticLifter._select_canonical_high_semantics([high, generic, unrelated], overlays)
        self.assertEqual(["call_output", "other"], [item["operation_id"] for item in result])

    def test_parent_expression_replaces_its_atomic_derivation_and_uses_state_read(self) -> None:
        common = {"stmt_refs": ["stmt"], "semantic_provenance": {"anchor_cfg_node": "y0"}}
        state_read = {**common, "operation_id": "state", "kind": "StateRead", "lvalue": "tmp_load", "rvalue": "_balances[account]", "evidence": {"overlay": "state"}}
        parent = {**common, "operation_id": "parent", "kind": "ValueCompute", "lvalue": "result", "rvalue": "add(result, sload(slot))", "evidence": {"overlay": "parent"}}
        step = {**common, "operation_id": "step", "kind": "ValueCompute", "lvalue": "tmp_load", "evidence": {"overlay": "step"}}
        overlays = {
            "state": {"kind": "MappingRead", "attrs": {}},
            "parent": {"kind": "ExpressionNormalization", "attrs": {"atomized_value": {"final": "tmp_sum", "steps": [
                {"temp": "tmp_load", "call": "sload", "evaluated_args": ["slot"]},
                {"temp": "tmp_sum", "call": "add", "evaluated_args": ["result", "tmp_load"]},
            ]}}},
            "step": {"kind": "EvaluationStep", "attrs": {}},
        }
        composed = YulSemanticLifter._compose_value_expressions([state_read, parent, step], overlays)
        self.assertEqual("(result + _balances[account])", parent["rvalue"])
        final = YulSemanticLifter._drop_covered_derivation_steps(composed, overlays)
        self.assertEqual(["state", "parent"], [item["operation_id"] for item in final])

    def test_require_read_is_a_lightweight_ssa_input(self) -> None:
        control = {"blocks": [block("guard", "yul")], "edges": []}
        require = yul("require", "guard", reads=["amount"], extra={"kind": "Require", "condition": "amount != 0"})
        result = SemanticFactIRBridge().build_function(function(control), [], [require])
        node = result["semantic_nodes"][0]
        self.assertEqual("amount", node["fact_ssa"]["reads"][0]["value"])
        self.assertIn("version", node["fact_ssa"]["reads"][0])

    def test_high_level_state_read_is_a_fact_ssa_definition(self) -> None:
        control = {"blocks": [block("y0", "yul"), block("y1", "yul")], "edges": [{"from": "y0", "to": "y1", "kind": "next"}]}
        read = yul("state_read", "y0", reads=["_balances[from]"], writes=["balance"], extra={"kind": "StateRead"})
        use = yul("state_use", "y1", reads=["balance"])
        result = SemanticFactIRBridge().build_function(function(control), [], [read, use])
        use_node = next(item for item in result["semantic_nodes"] if item["semantic_id"].endswith(":state_use"))
        self.assertIn("version", use_node["fact_ssa"]["reads"][0])
        self.assertFalse(any(item["kind"] == "unresolved_fact_ssa_read" for item in result["diagnostics"]))

    def test_event_path_candidates_form_one_canonical_node(self) -> None:
        def event(operation_id: str, condition: str) -> dict:
            return {
                "operation_id": operation_id, "function_id": FUNCTION_ID, "kind": "EventEmit", "condition": condition,
                "stmt_refs": ["stmt"], "semantic": {"event": "Transfer", "args": ["from", "to", "value"]},
                "evidence": {"overlay": "event", "candidate": {"condition": condition}},
                "semantic_provenance": {"anchor_cfg_node": "y0", "semantic_source": {"overlay_id": "event", "overlay_kind": "PathConditionedEventEmit"}},
            }
        result = YulSemanticLifter._canonicalize_path_conditioned_events([
            event("one", "safe && finite"), event("two", "safe && unlimited"),
        ])
        self.assertEqual(1, len(result))
        self.assertEqual("safe", result[0]["condition"])
        self.assertEqual(["safe && finite", "safe && unlimited"], result[0]["semantic_provenance"]["path_conditions"])
        self.assertNotIn("candidate", result[0]["evidence"])


if __name__ == "__main__":
    unittest.main()
