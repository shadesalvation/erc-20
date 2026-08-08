from __future__ import annotations

from conftest import lower_example, lower_source, operations


def test_compound_assignment_computes_storage_location_once() -> None:
    program = lower_example("CompoundAssignment.sol")
    ops = operations(program)
    kinds = [op["kind"] for op in ops]
    assert kinds.count("STORAGE_LOCATION") == 1
    assert "STORAGE_READ" in kinds
    assert "STORAGE_WRITE" in kinds
    assert any(
        op["kind"] == "BINARY_OP" and op["attributes"]["operator"] == "+"
        for op in ops
    )


def test_prefix_and_postfix_increment_return_different_values(tmp_path) -> None:
    program = lower_source(
        tmp_path,
        """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract Increment {
    function postfix(uint256 x) external pure returns (uint256 y) {
        y = x++;
    }

    function prefix(uint256 x) external pure returns (uint256 y) {
        y = ++x;
    }
}
""",
    )
    postfix_y = _write_to_y_value(program, "postfix")
    prefix_y = _write_to_y_value(program, "prefix")
    assert postfix_y != prefix_y


def _write_to_y_value(program: dict, function_name: str) -> str:
    for source_file in program["source_files"]:
        for contract in source_file["contracts"]:
            for function in contract["functions"]:
                if function["name"] != function_name:
                    continue
                for block in function["blocks"]:
                    for op in block["operations"]:
                        if (
                            op["kind"] == "WRITE_LOCAL"
                            and op["attributes"].get("name") == "y"
                        ):
                            return op["inputs"][0]
    raise AssertionError(f"WRITE_LOCAL y not found in {function_name}")
