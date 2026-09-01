from __future__ import annotations

import unittest

from .builder import SemanticIRBuilder, build_semantic_ir_program
from .exporter import render_semantic_ir_text
from .model import ExpressionNode, SemanticInstruction


def function(blocks, edges):
    return {
        "function_id": "Token.test(address,uint256)",
        "contract": "Token",
        "function": "test",
        "signature": "test(address,uint256)",
        "control": {"blocks": blocks, "edges": edges},
    }


def block(block_id, kind="yul", terminator=None, stmts=None):
    return {
        "block_id": block_id,
        "kind": kind,
        "stmts": stmts or [],
        "terminator": terminator or {"kind": "Fallthrough"},
    }


def fact(fact_id, kind, block_id, **values):
    return {
        "fact_id": fact_id,
        "function_id": "Token.test(address,uint256)",
        "function": "test",
        "contract": "Token",
        "signature": "test(address,uint256)",
        "kind": kind,
        "source_lang": values.pop("source_lang", "yul"),
        "cfg_nodes": [block_id] if block_id else [],
        **values,
    }


class SemanticIRBuilderTests(unittest.TestCase):
    def test_mapping_support_facts_fuse_into_state_read(self):
        fn = function(
            [block("entry"), block("slot", stmts=["asm_s1"]), block("read", stmts=["asm_s2"])],
            [{"from": "entry", "to": "slot", "kind": "next"}, {"from": "slot", "to": "read", "kind": "next"}],
        )
        facts = [
            fact("f_slot", "ValueCompute", "slot", lvalue="slot", rvalue="keccak256(0, 64)", stmt_refs=["asm_s1"], semantic={"expression": "keccak256(0, 64)"}),
            fact("f_raw", "ValueCompute", "read", lvalue="balance", rvalue="sload(slot)", stmt_refs=["asm_s2"], semantic={"expression": "sload(slot)"}),
            fact("f_read", "StateRead", "read", lvalue="balance", rvalue="balances[user]", stmt_refs=["asm_s1", "asm_s2"], semantic={
                "operation": "state_read", "access": "balances[user]", "state_variable": "balances", "keys": ["user"], "slot": "slot",
            }),
        ]
        program = build_semantic_ir_program([fn], {"facts": facts})
        ir_function = program.functions[0]
        instructions = [item for item in ir_function.blocks["read"].instructions if item.op == "StateRead"]
        self.assertEqual(len(instructions), 1)
        self.assertEqual(set(instructions[0].origin_facts), {"f_slot", "f_raw", "f_read"})
        location = ir_function.locations[instructions[0].location]
        self.assertEqual(location.kind, "MappingLocation")
        self.assertEqual(location.base, "balances")
        self.assertFalse(ir_function.blocks["slot"].instructions)

    def test_path_candidates_remain_distinct(self):
        fn = function(
            [
                block("entry", terminator={"kind": "Branch", "condition": "flag"}),
                block("yes"), block("no"), block("exit"),
            ],
            [
                {"from": "entry", "to": "yes", "kind": "true"},
                {"from": "entry", "to": "no", "kind": "false"},
                {"from": "yes", "to": "exit", "kind": "next"},
                {"from": "no", "to": "exit", "kind": "next"},
            ],
        )
        facts = [
            fact("f_true", "StateWrite", "yes", condition="flag", lvalue="balances[user]", rvalue="1", semantic={"operation": "state_write", "state_variable": "balances", "keys": ["user"], "access": "balances[user]", "value": "1"}),
            fact("f_false", "StateWrite", "no", condition="!(flag)", lvalue="balances[user]", rvalue="2", semantic={"operation": "state_write", "state_variable": "balances", "keys": ["user"], "access": "balances[user]", "value": "2"}),
        ]
        ir_function = build_semantic_ir_program([fn], {"facts": facts}).functions[0]
        self.assertEqual(ir_function.blocks["entry"].terminator.kind, "Branch")
        self.assertEqual(ir_function.blocks["yes"].instructions[0].origin_facts, ["f_true"])
        self.assertEqual(ir_function.blocks["no"].instructions[0].origin_facts, ["f_false"])
        self.assertNotEqual(ir_function.blocks["yes"].instructions[0].condition, ir_function.blocks["no"].instructions[0].condition)

    def test_expression_dag_def_use_and_rewrite_history(self):
        fn = function([block("entry")], [])
        facts = [
            fact("f1", "ValueCompute", "entry", lvalue="tmp", rvalue="amount + 1", reads=["amount"], writes=["tmp"], semantic={"expression": "amount + 1"}),
            fact("f2", "StateWrite", "entry", lvalue="totalSupply", rvalue="tmp", reads=["tmp"], writes=["totalSupply"], semantic={"operation": "state_write", "state_variable": "totalSupply", "access": "totalSupply", "value": "tmp"}),
        ]
        ir_function = build_semantic_ir_program([fn], {"facts": facts}).functions[0]
        self.assertEqual(len(ir_function.blocks["entry"].instructions), 1)
        write = ir_function.blocks["entry"].instructions[0]
        self.assertEqual(write.op, "StateWrite")
        self.assertEqual(ir_function.expressions[write.expression].kind, "Add")
        amount = next(value for value in ir_function.values.values() if value.name == "amount")
        self.assertEqual(amount.uses, [write.instruction_id])
        old_expression = write.expression
        ir_function.replace_expression(old_expression, ExpressionNode(old_expression, "Constant", value=7), pass_name="test_fold", reason="fixture")
        self.assertEqual(ir_function.expressions[old_expression].kind, "Constant")
        self.assertEqual(ir_function.rewrite_history[-1].action, "replace_expression")
        replacement = SemanticInstruction(write.instruction_id, "StateWrite", expression=old_expression, location=write.location)
        self.assertTrue(ir_function.replace_instruction(write.instruction_id, replacement, pass_name="test", reason="fixture"))

    def test_terminal_fact_prunes_analysis_fallthrough(self):
        fn = function(
            [block("entry"), block("revert", terminator={"kind": "Revert"}), block("exit")],
            [
                {"from": "entry", "to": "revert", "kind": "next"},
                {"from": "revert", "to": "exit", "kind": "terminate: revert"},
            ],
        )
        facts = [fact("f_revert", "Revert", "revert", semantic={"operation": "revert"})]
        ir_function = build_semantic_ir_program([fn], {"facts": facts}).functions[0]
        self.assertEqual(ir_function.blocks["revert"].terminator.kind, "Revert")
        self.assertEqual(ir_function.blocks["revert"].successors, [])
        self.assertFalse(any(edge.source == "revert" for edge in ir_function.edges))

    def test_human_readable_ir_renders_cfg_expressions_and_provenance(self):
        fn = function(
            [
                block("entry", terminator={"kind": "Branch", "condition": "amount > 0"}),
                block("write"),
                block("exit", terminator={"kind": "Return"}),
            ],
            [
                {"from": "entry", "to": "write", "kind": "true"},
                {"from": "entry", "to": "exit", "kind": "false"},
                {"from": "write", "to": "exit", "kind": "next"},
            ],
        )
        facts = [
            fact("f_write", "StateWrite", "write", lvalue="balances[user]", rvalue="amount + 1", semantic={
                "operation": "state_write",
                "state_variable": "balances",
                "keys": ["user"],
                "access": "balances[user]",
                "value": "amount + 1",
            }),
        ]
        text = render_semantic_ir_text(build_semantic_ir_program([fn], {"facts": facts}))
        self.assertIn("Function Token.test(address,uint256)", text)
        self.assertIn("Branch(Gt(amount, 0), write, exit)", text)
        self.assertIn("StateWrite(", text)
        self.assertIn("MappingLocation(base = balances, keys = [user])", text)
        self.assertIn("Add(amount, 1)", text)
        self.assertIn("origin_facts=[f_write]", text)
        self.assertIn("CFGEdges:", text)

    def test_execution_and_normalized_expressions_are_distinct(self):
        fn = function([block("entry")], [])
        facts = [
            fact(
                "f_compute",
                "ValueCompute",
                "entry",
                lvalue="tmp",
                rvalue="add(amount, 1)",
                reads=["amount"],
                writes=["tmp"],
                semantic={
                    "expression": "add(amount, 1)",
                    "expression_normalized": "visibleAmount + 1",
                },
            ),
        ]
        ir_function = build_semantic_ir_program([fn], {"facts": facts}).functions[0]
        instruction = ir_function.blocks["entry"].instructions[0]
        self.assertNotEqual(instruction.execution_expr, instruction.normalized_expr)
        self.assertEqual(ir_function.expressions[instruction.execution_expr].kind, "Add")
        self.assertEqual(ir_function.expressions[instruction.normalized_expr].kind, "Add")
        names = {record.name for record in ir_function.values.values()}
        self.assertIn("amount", names)
        self.assertNotIn("visibleAmount", names)

    def test_call_arguments_keep_execution_and_normalized_views(self):
        fn = function([block("call", stmts=["asm_s1"])], [])
        fn["yul_atomic_operations"] = {
            "operations": [{
                "atom_id": "a_call",
                "stmt_refs": ["asm_s1"],
                "atomic_kind": "Call",
                "operation": "call",
                "arguments": ["gasTemp", "target", "0", "ptr", "36", "out", "32"],
                "root_operation": True,
                "sequence": 1,
            }],
        }
        facts = [fact(
            "f_call", "ExternalCall", "call", stmt_refs=["asm_s1"],
            semantic={"target": "target", "arguments": ["gasleft()", "target", "0", "abi.encode(x)"]},
        )]
        ir_function = build_semantic_ir_program([fn], {"facts": facts}).functions[0]
        instruction = ir_function.blocks["call"].instructions[0]
        self.assertNotEqual(instruction.execution_arguments, instruction.normalized_arguments)
        execution_names = {
            name
            for expr in instruction.execution_arguments
            for name in SemanticIRBuilder()._expression_variables(ir_function, expr)
        }
        self.assertIn("ptr", execution_names)
        self.assertNotIn("x", execution_names)

    def test_call_data_is_structured_without_query_state(self):
        fn = function([block("call", stmts=["asm_s1", "asm_s2"])], [])
        fn["effects"] = [
            {
                "effect_id": "eff_write", "kind": "MemoryWrite", "stmt_refs": ["asm_s1"],
                "attrs": {"cfg_node_id": 1, "address": "ptr", "value": "word"},
            },
            {
                "effect_id": "eff_call", "kind": "Call", "stmt_refs": ["asm_s2"],
                "attrs": {
                    "cfg_node_id": 2,
                    "input_memory": {
                        "words": [{"value": "word", "definition": {"node_id": 1}}],
                        "complete": True,
                    },
                },
            },
        ]
        facts = [fact(
            "f_call", "ExternalCall", "call", stmt_refs=["asm_s2"],
            evidence={"effects": ["eff_call"]},
            semantic={
                "target": "target", "arguments": ["word"],
                "call_data": {
                    "kind": "CallData", "pointer": "ptr", "size": "32",
                    "values": ["word"], "encoding": "raw_memory", "complete": True,
                },
            },
        )]
        ir_function = build_semantic_ir_program([fn], {"facts": facts}).functions[0]
        call = ir_function.blocks["call"].instructions[0]
        self.assertEqual(len(call.data_objects), 1)
        payload = ir_function.data_objects[call.data_objects[0]]
        self.assertEqual(payload.kind, "CallData")
        self.assertTrue(payload.complete)
        self.assertEqual(payload.stmt_refs, ["asm_s2", "asm_s1"])
        self.assertFalse(any(item.op == "MemoryWrite" for item in ir_function.blocks["call"].instructions))
        core = ir_function.to_dict()
        debug = ir_function.to_dict(include_analysis_indexes=True)
        self.assertNotIn("analysis_indexes", core)
        self.assertIn("analysis_indexes", debug)

    def test_terminal_keeps_raw_memory_range_and_normalized_value(self):
        fn = function([block("exit", terminator={"kind": "Return"}, stmts=["asm_s1"])], [])
        fn["effects"] = [{"effect_id": "eff_return", "kind": "Return", "attrs": {}}]
        fn["yul_atomic_operations"] = {
            "operations": [{
                "atom_id": "a_return",
                "stmt_refs": ["asm_s1"],
                "atomic_kind": "Return",
                "operation": "return",
                "arguments": ["ptr", "32"],
                "root_operation": True,
                "sequence": 1,
            }],
        }
        facts = [fact(
            "f_return", "Return", "exit", stmt_refs=["asm_s1"], rvalue="value",
            semantic={
                "operation": "return", "value": "value",
                "return_payload": {
                    "kind": "ReturnPayload", "pointer": "ptr", "size": "32",
                    "values": ["value"], "encoding": "abi_word", "complete": True,
                },
            },
            evidence={"effects": ["eff_return"]},
        )]
        ir_function = build_semantic_ir_program([fn], {"facts": facts}).functions[0]
        terminator = ir_function.blocks["exit"].terminator
        self.assertEqual(len(terminator.execution_values), 2)
        self.assertEqual(len(terminator.normalized_values), 1)
        self.assertNotEqual(terminator.execution_values, terminator.normalized_values)
        self.assertEqual(len(terminator.data_objects), 1)
        self.assertEqual(ir_function.data_objects[terminator.data_objects[0]].kind, "ReturnPayload")

    def test_unlifted_memory_effect_is_preserved(self):
        fn = function([block("entry", stmts=["asm_s1"])], [])
        fn["effects"] = [{
            "effect_id": "eff_mem", "kind": "MemoryWrite", "stmt_refs": ["asm_s1"],
            "attrs": {"cfg_node_id": 1, "address": "ptr", "value": "value", "write_kind": "mstore"},
        }]
        ir_function = build_semantic_ir_program([fn], {"facts": []}).functions[0]
        memory = next(item for item in ir_function.blocks["entry"].instructions if item.op == "MemoryWrite")
        self.assertEqual(memory.origin_effects, ["eff_mem"])
        self.assertEqual(memory.attrs["unresolved_reason"], "memory_effect_not_semantically_lifted")

    def test_phi_def_use_is_independent_of_block_serialization_order(self):
        fn = function(
            [block("entry"), block("use"), block("left"), block("right"), block("merge")],
            [
                {"from": "entry", "to": "left", "kind": "true"},
                {"from": "entry", "to": "right", "kind": "false"},
                {"from": "left", "to": "merge", "kind": "next"},
                {"from": "right", "to": "merge", "kind": "next"},
                {"from": "merge", "to": "use", "kind": "next"},
            ],
        )
        facts = [
            fact("f_left", "ValueCompute", "left", lvalue="fee_1", rvalue="0", semantic={"expression": "0"}),
            fact("f_right", "ValueCompute", "right", lvalue="fee_2", rvalue="value / 100", semantic={"expression": "value / 100"}),
            fact("f_phi", "ValuePhi", "merge", lvalue="fee_3", rvalue=["fee_1", "fee_2"], semantic={"inputs": ["fee_1", "fee_2"]}),
            fact("f_use", "InternalCall", "use", semantic={"arguments": ["to", "fee_3"]}),
        ]
        ir_function = build_semantic_ir_program([fn], {"facts": facts}).functions[0]
        call = next(item for item in ir_function.blocks["use"].instructions if item.op == "InternalCall")
        phi = next(item for item in ir_function.blocks["merge"].instructions if item.op == "Phi")
        fee = ir_function.values[phi.result_value]
        self.assertIn(call.instruction_id, fee.uses)
        self.assertNotIn("input:fee_3", ir_function.values)

    def test_state_write_keeps_materialized_state_read_as_ssa_operand(self):
        fn = function([block("entry", stmts=["asm_s1", "asm_s2"])], [])
        fn["effects"] = [
            {
                "effect_id": "eff_read",
                "kind": "StorageRead",
                "stmt_refs": ["asm_s1"],
                "attrs": {"atomic_operation_id": "a_read", "slot": "slot", "value": "old"},
            },
            {
                "effect_id": "eff_write",
                "kind": "StorageWrite",
                "stmt_refs": ["asm_s2"],
                "attrs": {"atomic_operation_id": "a_write", "slot": "slot", "value": "add(old, amount)"},
            },
        ]
        fn["yul_atomic_operations"] = {"operations": [
            {
                "atom_id": "a_read", "stmt_refs": ["asm_s1"], "atomic_kind": "StorageRead",
                "operation": "sload", "result": "old", "arguments": ["slot"],
                "execution_expression": "sload(slot)", "root_operation": True, "sequence": 1,
            },
            {
                "atom_id": "a_add", "stmt_refs": ["asm_s2"], "atomic_kind": "ValueCompute",
                "operation": "add", "result": "tmp_add", "arguments": ["old", "amount"],
                "execution_expression": "add(old, amount)", "sequence": 2,
            },
            {
                "atom_id": "a_write", "stmt_refs": ["asm_s2"], "atomic_kind": "StorageWrite",
                "operation": "sstore", "arguments": ["slot", "tmp_add"],
                "execution_expression": "sstore(slot, tmp_add)", "root_operation": True, "sequence": 3,
            },
        ]}
        facts = [
            fact(
                "f_read", "StateRead", "entry", lvalue="old", rvalue="counter",
                stmt_refs=["asm_s1"], evidence={"effects": ["eff_read"]},
                semantic={"operation": "state_read", "state_variable": "counter", "access": "counter"},
            ),
            fact(
                "f_write", "StateWrite", "entry", lvalue="counter", rvalue="counter + amount",
                stmt_refs=["asm_s2"], evidence={"effects": ["eff_write"]},
                semantic={
                    "operation": "state_write", "state_variable": "counter",
                    "access": "counter", "value": "counter + amount",
                },
            ),
        ]
        ir_function = build_semantic_ir_program([fn], {"facts": facts}).functions[0]
        instructions = [item for item in ir_function.blocks["entry"].instructions]
        state_read = next(item for item in instructions if item.op == "StateRead")
        state_write = next(item for item in instructions if item.op == "StateWrite")
        execution = ir_function.expressions[state_write.execution_expr]
        self.assertEqual(execution.kind, "Add")
        self.assertEqual(ir_function.expressions[execution.operands[0]].name, "old")
        old_value = next(item for item in ir_function.values.values() if item.name == "old")
        self.assertEqual(old_value.definition, state_read.instruction_id)
        self.assertIn(state_write.instruction_id, old_value.uses)


if __name__ == "__main__":
    unittest.main()
