from __future__ import annotations

import pytest

from conftest import function_ops, lower_example


LOOP_EXAMPLES = [
    ("ForAtomicBody.sol", "accumulate", {"ForStatement"}),
    ("WhileStorageUpdate.sol", "credit", {"WhileStatement"}),
    ("DoWhileAtomicBody.sol", "halve", {"DoWhileStatement"}),
    (
        "NestedLoopControl.sol",
        "sum",
        {"ForStatement", "WhileStatement", "Break", "Continue"},
    ),
    ("LoopShortCircuit.sol", "scan", {"WhileStatement"}),
]


@pytest.mark.parametrize(("example", "function_name", "node_types"), LOOP_EXAMPLES)
def test_loop_example_is_fully_atomicized(
    example: str, function_name: str, node_types: set[str]
) -> None:
    program = lower_example(example)
    ops = function_ops(program, function_name)
    unsupported = {
        item["node_type"]
        for item in program["diagnostics"]
        if item["code"] == "UNSUPPORTED_NODE"
    }

    assert not unsupported.intersection(node_types)
    assert all(op["kind"] != "UNKNOWN_OPERATION" for op in ops)
    assert any(op["kind"] == "BRANCH" for op in ops)
    assert any(op["kind"] == "BINARY_OP" for op in ops)
    assert any(op["kind"] in {"WRITE_LOCAL", "STORAGE_WRITE"} for op in ops)


def test_while_storage_example_emits_atomic_storage_access() -> None:
    program = lower_example("WhileStorageUpdate.sol")
    kinds = [op["kind"] for op in function_ops(program, "credit")]

    assert "STORAGE_LOCATION" in kinds
    assert "STORAGE_READ" in kinds
    assert "STORAGE_WRITE" in kinds


def test_nested_loop_example_preserves_break_and_continue_edges() -> None:
    program = lower_example("NestedLoopControl.sol")
    ops = function_ops(program, "sum")
    jump_sources = {
        op["source"]["original_node_type"]
        for op in ops
        if op["kind"] == "JUMP"
    }

    assert {"Break", "Continue"}.issubset(jump_sources)


def test_short_circuit_loop_condition_uses_phi() -> None:
    program = lower_example("LoopShortCircuit.sol")
    ops = function_ops(program, "scan")

    assert any(op["kind"] == "PHI" for op in ops)
    assert not any(
        op["kind"] == "BINARY_OP" and op["attributes"].get("operator") == "&&"
        for op in ops
    )
