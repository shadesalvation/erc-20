from __future__ import annotations

from conftest import lower_example, operations


def test_compound_arithmetic_expression_is_atomic() -> None:
    program = lower_example("Simple.sol")
    binary_ops = [op for op in operations(program) if op["kind"] == "BINARY_OP"]
    assert [op["attributes"]["operator"] for op in binary_ops] == ["+", "-", "*"]
    assert sum(1 for op in binary_ops if op["may_revert"]) == 3


def test_source_mapping_is_preserved_on_main_operations() -> None:
    program = lower_example("Simple.sol")
    main_ops = [
        op
        for op in operations(program)
        if op["kind"] in {"READ_LOCAL", "BINARY_OP", "WRITE_LOCAL"}
    ]
    assert main_ops
    for op in main_ops:
        assert op["source"]["ast_id"] is not None
        assert op["source"]["src"]
        assert op["source"]["original_node_type"]
