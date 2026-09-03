from __future__ import annotations

import unittest

from .builder import build_semantic_ir_program
from .exporter import render_semantic_ir_text
from .model import SemanticOperation


def function(blocks, edges):
    return {"function_id": "Token.test(uint256)", "contract": "Token", "function": "test", "signature": "test(uint256)", "control": {"blocks": blocks, "edges": edges}}


def block(block_id, *, terminator=None):
    return {"block_id": block_id, "kind": "yul", "terminator": terminator or {}}


def fact(fact_id, kind, block_id, **extra):
    return {"fact_id": fact_id, "function_id": "Token.test(uint256)", "kind": kind, "cfg_nodes": [block_id], **extra}


class SemanticIRBuilderTests(unittest.TestCase):
    def test_every_fact_becomes_exactly_one_operation_without_fusion(self):
        fn = function([block("slot"), block("read")], [{"from": "slot", "to": "read"}])
        location = {"kind": "mapping", "state_variable": "balances", "access": "balances[user]", "keys": ["user"]}
        facts = [
            fact("slot", "StorageLocationResolve", "slot", fact_role="support", rvalue="balances[user]", semantic={"location": location}),
            fact("raw", "ValueCompute", "read", fact_role="support", lvalue="balance", rvalue="sload(slot)", semantic={"expression": "sload(slot)"}),
            fact("read", "StateRead", "read", fact_role="effect", lvalue="balance", rvalue="balances[user]", semantic={"location": location}),
        ]
        ir = build_semantic_ir_program([fn], {"facts": facts}).functions[0]
        operations = [item for block_value in ir.blocks.values() for item in block_value.operations]
        self.assertEqual([item.fact_id for item in operations], ["slot", "raw", "read"])
        self.assertEqual(len({item.operation_id for item in operations}), 3)
        self.assertEqual(ir.get_operation("slot").source_fact, facts[0])
        self.assertEqual(ir.get_operation("read").semantic, facts[2]["semantic"])
        self.assertEqual(ir.operations_by_location["loc_1"], ["op_slot", "op_read"])

    def test_paths_remain_distinct_and_audit_view_keeps_conditions(self):
        fn = function(
            [block("entry", terminator={"kind": "Branch", "condition": "flag"}), block("yes"), block("no")],
            [{"from": "entry", "to": "yes", "kind": "true: flag"}, {"from": "entry", "to": "no", "kind": "false: !(flag)"}],
        )
        facts = [
            fact("yes", "EventEmit", "yes", condition="flag", semantic={"event": "Transfer", "args": ["a", "b", "1"]}),
            fact("no", "EventEmit", "no", condition="!(flag)", semantic={"event": "Transfer", "args": ["a", "b", "2"]}),
        ]
        program = build_semantic_ir_program([fn], {"facts": facts})
        ir = program.functions[0]
        self.assertEqual(ir.blocks["yes"].operations[0].fact_id, "yes")
        self.assertEqual(ir.blocks["no"].operations[0].fact_id, "no")
        self.assertEqual(ir.blocks["yes"].operations[0].condition, "flag")
        text = render_semantic_ir_text(program)
        self.assertIn("condition=flag", text)
        self.assertIn('"event": "Transfer"', text)

    def test_ambiguous_placement_is_retained_not_guessed(self):
        fn = function(
            [block("entry"), block("left"), block("right")],
            [{"from": "entry", "to": "left", "kind": "true"}, {"from": "entry", "to": "right", "kind": "false"}],
        )
        ambiguous = {"fact_id": "unknown", "function_id": "Token.test(uint256)", "kind": "ValueCompute", "cfg_nodes": ["left", "right"], "rvalue": "x"}
        ir = build_semantic_ir_program([fn], {"facts": [ambiguous]}).functions[0]
        self.assertEqual([item.fact_id for item in ir.unplaced_operations], ["unknown"])
        self.assertEqual(ir.diagnostics[0]["reason"], "no unique CFG placement evidence")

    def test_cfg_terminal_is_copied_without_turning_fact_into_terminator(self):
        fn = function([block("revert", terminator={"kind": "Revert", "text": "revert(0, 0)"})], [])
        facts = [fact("revert_fact", "Require", "revert", condition="flag", semantic={"on_fail": "revert"})]
        ir = build_semantic_ir_program([fn], {"facts": facts}).functions[0]
        self.assertEqual(ir.blocks["revert"].terminator.kind, "Revert")
        self.assertEqual(ir.blocks["revert"].operations[0].kind, "Require")
        self.assertEqual(ir.blocks["revert"].operations[0].fact_id, "revert_fact")

    def test_rewrite_keeps_fact_identity_and_source_fact(self):
        fn = function([block("entry")], [])
        original = fact("compute", "ValueCompute", "entry", lvalue="x", rvalue="1", semantic={"expression": "1"})
        ir = build_semantic_ir_program([fn], {"facts": [original]}).functions[0]
        replacement = SemanticOperation.from_fact(original)
        replacement.rvalue = "2"
        self.assertTrue(ir.replace_operation("compute", replacement, pass_name="constant-fold", reason="test"))
        operation = ir.get_operation("compute")
        self.assertEqual(operation.rvalue, "2")
        self.assertEqual(operation.source_fact["rvalue"], "1")
        self.assertEqual(ir.rewrite_history[-1].action, "replace_operation")


if __name__ == "__main__":
    unittest.main()
