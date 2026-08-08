from __future__ import annotations

from conftest import function_ops, lower_example, lower_source


def test_nested_storage_and_conditional_example() -> None:
    program = lower_example("NestedStorageAndConditional.sol")
    ops = function_ops(program, "adjust")
    kinds = [op["kind"] for op in ops]

    assert kinds.count("STORAGE_LOCATION") == 2
    assert "STORAGE_READ" in kinds
    assert "STORAGE_WRITE" in kinds
    assert "BRANCH" in kinds
    assert "PHI" in kinds
    assert any(
        op["kind"] == "BINARY_OP"
        and op["attributes"].get("compound_assignment_operator") == "+="
        for op in ops
    )


def test_control_flow_short_circuit_and_storage_reads_example() -> None:
    program = lower_example("ControlFlowAndShortCircuit.sol")
    ops = function_ops(program, "pick")
    kinds = [op["kind"] for op in ops]

    assert kinds.count("BRANCH") >= 3
    assert "PHI" in kinds
    assert kinds.count("STORAGE_READ") >= 3
    assert not any(
        op["kind"] == "BINARY_OP" and op["attributes"].get("operator") == "&&"
        for op in ops
    )
    assert any(
        op["kind"] == "READ_LOCAL"
        and op["attributes"].get("declaration_kind") == "return_parameter"
        for op in ops
    ) is False


def test_for_loop_body_is_lowered_to_atomic_operations() -> None:
    program = lower_example("UnsupportedLoopDiagnostic.sol")
    diagnostics = program["diagnostics"]
    ops = function_ops(program, "sum")
    kinds = [op["kind"] for op in ops]

    assert not any(
        item["code"] == "UNSUPPORTED_NODE" and item["node_type"] == "ForStatement"
        for item in diagnostics
    )
    assert "UNKNOWN_OPERATION" not in kinds
    assert kinds.count("BRANCH") == 1
    assert any(
        op["kind"] == "BINARY_OP"
        and op["attributes"].get("compound_assignment_operator") == "+="
        for op in ops
    )
    assert any(
        op["kind"] == "BINARY_OP"
        and op["source"]["original_node_type"] == "UnaryOperation"
        for op in ops
    )


def test_while_do_while_break_and_continue_are_lowered(tmp_path) -> None:
    program = lower_source(
        tmp_path,
        """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract Loops {
    function sum(uint256 n) external pure returns (uint256 total) {
        uint256 i = 0;
        while (i < n) {
            i++;
            if (i == 2) continue;
            total += i;
            if (total > n) break;
        }
        do {
            total += 1;
        } while (total < n);
    }
}
""",
    )
    ops = function_ops(program, "sum")
    unsupported_types = {
        item["node_type"]
        for item in program["diagnostics"]
        if item["code"] == "UNSUPPORTED_NODE"
    }

    assert not unsupported_types.intersection(
        {"WhileStatement", "DoWhileStatement", "Break", "Continue"}
    )
    assert all(op["kind"] != "UNKNOWN_OPERATION" for op in ops)
    assert sum(op["kind"] == "BRANCH" for op in ops) >= 4
    assert any(
        op["kind"] == "JUMP" and op["source"]["original_node_type"] == "Break"
        for op in ops
    )
    assert any(
        op["kind"] == "JUMP" and op["source"]["original_node_type"] == "Continue"
        for op in ops
    )
    assert sum(
        op["kind"] == "BINARY_OP"
        and op["attributes"].get("compound_assignment_operator") == "+="
        for op in ops
    ) == 2
