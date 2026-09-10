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

    def test_loop_header_projects_complementary_body_and_exit_guards(self) -> None:
        control = {
            "blocks": [
                {
                    **block("loop", "yul"),
                    "terminator": {
                        "kind": "Branch",
                        "node_kind": "loop-condition",
                        "condition": "for condition lt(i, accounts.length)",
                        "text": "for condition lt(i, accounts.length)",
                    },
                },
                block("body", "yul"),
                block("exit", "yul"),
            ],
            "edges": [
                {"from": "loop", "to": "body", "kind": "true: lt(i, accounts.length)"},
                {"from": "loop", "to": "exit", "kind": "loop exit: !(lt(i, accounts.length))"},
            ],
        }
        result = SemanticFactIRBridge().build_function(function(control), [], [])
        loop = next(item for item in result["fact_cfg"]["blocks"] if item["origin"]["source_block_id"] == "loop")
        edges = [item for item in result["fact_cfg"]["edges"] if item["from"] == loop["block_id"]]
        self.assertEqual("lt(i, accounts.length)", loop["terminator"]["condition"])
        self.assertEqual(
            {
                "true: lt(i, accounts.length)": "lt(i, accounts.length)",
                "loop exit: !(lt(i, accounts.length))": "!(lt(i, accounts.length))",
            },
            {item["kind"]: item["guard"] for item in edges},
        )

    def test_fact_cfg_erases_empty_entry_and_relay_as_transport_provenance(self) -> None:
        yul_statement = {"kind": "YulNode", "node_kind": "statement"}
        control = {
            "blocks": [
                {**block("entry", "yul"), "terminator": {"kind": "YulNode", "node_kind": "entry"}},
                {**block("relay", "yul"), "terminator": yul_statement},
                {**block("semantic", "yul"), "terminator": yul_statement},
            ],
            "edges": [
                {"from": "entry", "to": "relay", "kind": "next"},
                {"from": "relay", "to": "semantic", "kind": "next"},
            ],
        }
        result = SemanticFactIRBridge().build_function(function(control), [], [yul("write", "semantic")])
        blocks = result["fact_cfg"]["blocks"]
        self.assertNotIn("relay", [item["origin"]["source_block_id"] for item in blocks])
        self.assertNotIn("entry", [item["origin"]["source_block_id"] for item in blocks])
        semantic = blocks[0]
        self.assertEqual("semantic", semantic["origin"]["source_block_id"])
        self.assertEqual({"entry", "statement"}, {
            item["role"] for item in semantic["collapsed_entry_transport"]
        })
        self.assertEqual([], result["diagnostics"])

    def test_fact_cfg_redirects_guarded_path_across_empty_relay(self) -> None:
        yul_statement = {"kind": "YulNode", "node_kind": "statement"}
        control = {
            "blocks": [
                {**block("branch", "yul"), "terminator": {"kind": "Branch", "condition": "flag", "node_kind": "condition"}},
                {**block("relay", "yul"), "terminator": yul_statement},
                {**block("semantic", "yul"), "terminator": yul_statement},
                {**block("other", "yul"), "terminator": yul_statement},
            ],
            "edges": [
                {"from": "branch", "to": "relay", "kind": "true"},
                {"from": "branch", "to": "other", "kind": "false"},
                {"from": "relay", "to": "semantic", "kind": "next"},
            ],
        }
        result = SemanticFactIRBridge().build_function(function(control), [], [yul("write", "semantic")])
        self.assertNotIn("relay", [item["origin"]["source_block_id"] for item in result["fact_cfg"]["blocks"]])
        semantic = next(item for item in result["fact_cfg"]["blocks"] if item["origin"]["source_block_id"] == "semantic")
        incoming = next(item for item in result["fact_cfg"]["edges"] if item["to"] == semantic["block_id"])
        self.assertEqual("true", incoming["kind"])
        self.assertEqual("flag", incoming["guard"])
        self.assertEqual("relay", incoming["collapsed_transport"][0]["source_block_id"])

    def test_fact_cfg_erases_empty_join_and_rebuilds_phi_at_successor(self) -> None:
        statement = {"kind": "YulNode", "node_kind": "statement"}
        control = {
            "blocks": [
                block("branch", condition="flag"), block("left"), block("right"),
                {**block("join", "yul"), "terminator": {"kind": "YulNode", "node_kind": "merge"}},
                {**block("read", "yul"), "terminator": statement},
            ],
            "edges": [
                {"from": "branch", "to": "left", "kind": "true"},
                {"from": "branch", "to": "right", "kind": "false"},
                {"from": "left", "to": "join", "kind": "next"},
                {"from": "right", "to": "join", "kind": "next"},
                {"from": "join", "to": "read", "kind": "next"},
            ],
        }
        result = SemanticFactIRBridge().build_function(
            function(control),
            [solidity("left_def", "left", writes=["amount_1"]), solidity("right_def", "right", writes=["amount_2"])],
            [yul("read", "read", reads=["amount"])],
        )
        self.assertNotIn("join", [item["origin"]["source_block_id"] for item in result["fact_cfg"]["blocks"]])
        read_block = next(item for item in result["fact_cfg"]["blocks"] if item["origin"]["source_block_id"] == "read")
        self.assertEqual(2, len(read_block["predecessors"]))
        self.assertEqual(read_block["block_id"], result["fact_ssa"]["phis"][0]["block_id"])
        read_node = next(item for item in result["semantic_nodes"] if item["semantic_id"].endswith(":read"))
        self.assertEqual(result["fact_ssa"]["phis"][0]["version"], read_node["fact_ssa"]["reads"][0]["version"])

    def test_fact_cfg_erases_cross_language_entry_exit_with_boundary_provenance(self) -> None:
        control = {
            "blocks": [
                {**block("sol_entry"), "terminator": {"kind": "Fallthrough"}},
                {**block("yul_entry", "yul"), "terminator": {"kind": "YulNode", "node_kind": "entry"}},
                {**block("work", "yul"), "terminator": {"kind": "YulNode", "node_kind": "statement"}},
                {**block("yul_exit", "yul"), "terminator": {"kind": "YulNode", "node_kind": "exit"}},
                {**block("sol_return"), "terminator": {"kind": "Return"}},
            ],
            "edges": [
                {"from": "sol_entry", "to": "yul_entry", "kind": "fallthrough"},
                {"from": "yul_entry", "to": "work", "kind": "next"},
                {"from": "work", "to": "yul_exit", "kind": "next"},
                {"from": "yul_exit", "to": "sol_return", "kind": "fallthrough"},
            ],
        }
        result = SemanticFactIRBridge().build_function(
            function(control), [solidity("return", "sol_return", reads=["result"])], [yul("write", "work", writes=["result"])],
        )
        blocks = {item["origin"]["source_block_id"]: item for item in result["fact_cfg"]["blocks"]}
        self.assertEqual({"work", "sol_return"}, set(blocks))
        self.assertEqual(["solidity-entry", "entry"], [item["role"] for item in blocks["work"]["collapsed_entry_transport"]])
        self.assertEqual("solidity", blocks["work"]["entry_boundary_transitions"][0]["from_lang"])
        edge = result["fact_cfg"]["edges"][0]
        self.assertEqual(blocks["work"]["block_id"], edge["from"])
        self.assertEqual(blocks["sol_return"]["block_id"], edge["to"])
        self.assertEqual("yul", edge["boundary_transitions"][0]["from_lang"])
        self.assertEqual("solidity", edge["boundary_transitions"][0]["to_lang"])

    def test_fact_cfg_fuses_unique_linear_semantic_blocks_in_cfg_order(self) -> None:
        """Fusion must preserve a write/read sequence even when IDs sort backwards."""
        statement = {"kind": "YulNode", "node_kind": "statement"}
        control = {
            "blocks": [
                {**block("entry", "yul"), "terminator": {"kind": "YulNode", "node_kind": "entry"}},
                {**block("write", "yul"), "terminator": statement},
                {**block("read", "yul"), "terminator": statement},
                {**block("exit", "yul"), "terminator": {"kind": "YulNode", "node_kind": "exit"}},
            ],
            "edges": [
                {"from": "entry", "to": "write", "kind": "next"},
                {"from": "write", "to": "read", "kind": "next"},
                {"from": "read", "to": "exit", "kind": "next"},
            ],
        }
        # Both facts intentionally have the same source order and the write's
        # ID sorts after the read's.  Only the CFG edge proves their order.
        result = SemanticFactIRBridge().build_function(
            function(control), [],
            [yul("z_write", "write", writes=["result"]), yul("a_read", "read", reads=["result"])],
        )
        fused = next(item for item in result["fact_cfg"]["blocks"] if item["origin"]["source_block_id"] == "write")
        self.assertEqual([
            f"sfir:{FUNCTION_ID}:z_write", f"sfir:{FUNCTION_ID}:a_read",
        ], fused["semantic_ids"])
        self.assertEqual(["write", "read"], fused["fused_source_blocks"])
        self.assertNotIn("read", [item["origin"]["source_block_id"] for item in result["fact_cfg"]["blocks"]])
        read_node = next(item for item in result["semantic_nodes"] if item["semantic_id"].endswith(":a_read"))
        self.assertEqual(fused["block_id"], read_node["placement"]["anchor_block"])
        self.assertIn("version", read_node["fact_ssa"]["reads"][0])
        self.assertTrue(any(
            edge["from"].endswith(":z_write") and edge["to"].endswith(":a_read")
            for edge in result["semantic_edges"]
        ))

    def test_fact_cfg_does_not_fuse_across_solidity_yul_boundary(self) -> None:
        control = {
            "blocks": [block("sol", "solidity"), block("yul", "yul")],
            "edges": [{"from": "sol", "to": "yul", "kind": "next"}],
        }
        result = SemanticFactIRBridge().build_function(
            function(control), [solidity("sol_write", "sol", writes=["result"])], [yul("yul_read", "yul", reads=["result"])],
        )
        self.assertEqual(2, len(result["fact_cfg"]["blocks"]))
        self.assertFalse((result["fact_cfg"].get("normalization") or {}).get("linear_semantic_block_fusion"))

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
            "operation_id": "location", "kind": "StorageLocationResolve", "rvalue": "_balances[account]", "stmt_refs": ["slot"],
            "semantic_provenance": {"anchor_cfg_node": "y0"}, "evidence": {"overlay": "location"},
        }
        generic = {
            "operation_id": "keccak", "kind": "ValueCompute", "lvalue": "slot", "stmt_refs": ["slot"],
            "semantic_provenance": {"anchor_cfg_node": "y0"}, "evidence": {"overlay": "generic"},
        }
        overlays = {
            "location": {"kind": "MappingSlot", "attrs": {"target": "slot"}},
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

    def test_calldata_word_replaces_its_complete_atomic_derivation(self) -> None:
        common = {"stmt_refs": ["read"], "semantic_provenance": {"anchor_cfg_node": "y1"}}
        high = {**common, "operation_id": "calldata", "kind": "ValueCompute", "lvalue": "account", "evidence": {"overlay": "calldata"}}
        generic = {**common, "operation_id": "generic", "kind": "ValueCompute", "lvalue": "account", "evidence": {"overlay": "generic"}}
        multiply = {**common, "operation_id": "multiply", "kind": "ValueCompute", "lvalue": "tmp_1", "evidence": {"overlay": "multiply"}}
        load = {**common, "operation_id": "load", "kind": "ValueCompute", "lvalue": "tmp_2", "evidence": {"overlay": "load"}}
        unrelated = {**common, "operation_id": "unrelated", "kind": "ValueCompute", "lvalue": "other", "evidence": {"overlay": "unrelated"}}
        overlays = {
            "calldata": {"kind": "CalldataWordRead", "effects": ["value_def"], "attrs": {"target": "account"}},
            "generic": {"kind": "ExpressionNormalization", "effects": ["value_def"], "attrs": {"target": "account"}},
            "multiply": {"kind": "EvaluationStep", "attrs": {"parent_effect": "value_def"}},
            "load": {"kind": "EvaluationStep", "attrs": {"parent_effect": "value_def"}},
            "unrelated": {"kind": "EvaluationStep", "attrs": {"parent_effect": "another_value_def"}},
        }
        result = YulSemanticLifter._select_canonical_high_semantics(
            [high, generic, multiply, load, unrelated], overlays
        )
        self.assertEqual(["calldata", "unrelated"], [item["operation_id"] for item in result])

    def test_calldata_array_read_replaces_its_complete_atomic_derivation(self) -> None:
        common = {"stmt_refs": ["read"], "semantic_provenance": {"anchor_cfg_node": "y1"}}
        high = {**common, "operation_id": "array_read", "kind": "ValueCompute", "lvalue": "account", "evidence": {"overlay": "array_read"}}
        generic = {**common, "operation_id": "generic", "kind": "ValueCompute", "lvalue": "account", "evidence": {"overlay": "generic"}}
        load = {**common, "operation_id": "load", "kind": "ValueCompute", "lvalue": "tmp", "evidence": {"overlay": "load"}}
        overlays = {
            "array_read": {"kind": "CalldataArrayElementRead", "effects": ["value_def"], "attrs": {"target": "account"}},
            "generic": {"kind": "ExpressionNormalization", "effects": ["value_def"], "attrs": {"target": "account"}},
            "load": {"kind": "EvaluationStep", "attrs": {"parent_effect": "value_def"}},
        }
        result = YulSemanticLifter._select_canonical_high_semantics([high, generic, load], overlays)
        self.assertEqual(["array_read"], [item["operation_id"] for item in result])

    def test_completed_call_owns_its_private_payload_buffer(self) -> None:
        control = {
            "blocks": [block("y0", "yul"), block("y1", "yul"), block("y2", "yul")],
            "edges": [
                {"from": "y0", "to": "y1", "kind": "next"},
                {"from": "y1", "to": "y2", "kind": "next"},
            ],
        }
        pointer = {
            "operation_id": "pointer", "kind": "ValueCompute", "lvalue": "ptr", "writes": ["ptr"],
            "stmt_refs": ["ptr_stmt"], "semantic_provenance": {"anchor_cfg_node": "y0"},
            "evidence": {"overlay": "pointer"},
        }
        call = {
            "operation_id": "call", "kind": "ExternalCall", "lvalue": "success", "writes": ["success"],
            "stmt_refs": ["call_stmt"], "semantic_provenance": {"anchor_cfg_node": "y1"},
            "evidence": {"overlay": "call"},
        }
        output = {
            "operation_id": "output", "kind": "ValueCompute", "lvalue": "result", "writes": ["result"],
            "stmt_refs": ["read_stmt"], "semantic_provenance": {"anchor_cfg_node": "y2"},
            "evidence": {"overlay": "output"},
        }
        overlays = {
            "pointer": {"overlay_id": "pointer", "kind": "ExpressionNormalization", "attrs": {"target": "ptr"}},
            "call": {"overlay_id": "call", "kind": "StaticCallOverlay", "attrs": {
                "input_ptr": "ptr", "output_ptr": "ptr", "decoded_input": "MemorySlice(selector, account)",
            }},
            "output": {"overlay_id": "output", "kind": "CallOutputRead", "attrs": {"source_call_overlay": "call"}},
        }
        result = YulSemanticLifter._drop_completed_call_buffer_temporaries(
            [pointer, call, output], overlays, control
        )
        self.assertEqual(["call", "output"], [item["operation_id"] for item in result])

    def test_call_buffer_remains_when_another_fact_reads_it(self) -> None:
        control = {
            "blocks": [block("y0", "yul"), block("y1", "yul"), block("y2", "yul")],
            "edges": [
                {"from": "y0", "to": "y1", "kind": "next"},
                {"from": "y1", "to": "y2", "kind": "next"},
            ],
        }
        pointer = {
            "operation_id": "pointer", "kind": "ValueCompute", "lvalue": "ptr", "writes": ["ptr"],
            "stmt_refs": ["ptr_stmt"], "semantic_provenance": {"anchor_cfg_node": "y0"},
            "evidence": {"overlay": "pointer"},
        }
        call = {
            "operation_id": "call", "kind": "ExternalCall", "stmt_refs": ["call_stmt"],
            "semantic_provenance": {"anchor_cfg_node": "y1"}, "evidence": {"overlay": "call"},
        }
        escaped_use = {
            "operation_id": "escaped", "kind": "ValueCompute", "reads": ["ptr"],
            "stmt_refs": ["other_stmt"], "semantic_provenance": {"anchor_cfg_node": "y2"},
            "evidence": {"overlay": "escaped"},
        }
        overlays = {
            "pointer": {"overlay_id": "pointer", "kind": "ExpressionNormalization", "attrs": {"target": "ptr"}},
            "call": {"overlay_id": "call", "kind": "StaticCallOverlay", "attrs": {
                "input_ptr": "ptr", "decoded_input": "MemorySlice(selector, account)",
            }},
            "escaped": {"overlay_id": "escaped", "kind": "ExpressionNormalization", "attrs": {"target": "other"}},
        }
        result = YulSemanticLifter._drop_completed_call_buffer_temporaries(
            [pointer, call, escaped_use], overlays, control
        )
        self.assertEqual(["pointer", "call", "escaped"], [item["operation_id"] for item in result])

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
        self.assertEqual("(result + _balances[account])", parent["semantic"]["expression_normalized"])
        self.assertNotIn("expression", parent["semantic"])
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

    def test_recovered_storage_location_has_an_exact_fact_ssa_binding(self) -> None:
        control = {"blocks": [block("s0"), block("s1")], "edges": [{"from": "s0", "to": "s1", "kind": "next"}]}
        location = {
            "kind": "mapping", "access": "balances[owner]", "state_variable": "balances",
            "keys": ["owner"],
        }
        read = {
            **solidity("storage_read", "s0", reads=["balances[owner]"], writes=["loaded"]),
            "kind": "StateRead", "semantic": {"location": location},
        }
        write = {
            **solidity("storage_write", "s1", reads=["loaded"], writes=["balances[owner]"]),
            "kind": "StateWrite", "semantic": {"location": location},
        }
        result = SemanticFactIRBridge().build_function(
            function(control, VARIABLES + [{"name": "balances", "kind": "state", "type_string": "mapping(address => uint256)", "declaration_id": 3}]),
            [read, write], [],
        )
        nodes = {node["semantic_id"].rsplit(":", 1)[-1]: node for node in result["semantic_nodes"]}
        read_ref = nodes["storage_read"]["fact_ssa"]["reads"][0]
        write_ref = nodes["storage_write"]["fact_ssa"]["writes"][0]
        self.assertEqual(read_ref["binding_id"], write_ref["binding_id"])
        self.assertIn(":storage_location:balances[owner]", write_ref["binding_id"])
        self.assertIn("version", read_ref)
        self.assertIn("version", write_ref)

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
