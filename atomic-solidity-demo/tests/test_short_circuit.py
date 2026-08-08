from __future__ import annotations

from conftest import function_ops, lower_example


def test_short_circuit_builds_cfg_not_plain_binary_op() -> None:
    program = lower_example("ShortCircuit.sol")
    ops = function_ops(program, "test")
    kinds = [op["kind"] for op in ops]
    assert "BRANCH" in kinds
    assert "PHI" in kinds
    assert not any(
        op["kind"] == "BINARY_OP" and op["attributes"].get("operator") == "&&"
        for op in ops
    )

    check_b_call = next(
        op
        for op in ops
        if op["kind"] == "INTERNAL_CALL" and op["attributes"].get("callee") == "checkB"
    )
    branch_index = kinds.index("BRANCH")
    assert ops.index(check_b_call) > branch_index
