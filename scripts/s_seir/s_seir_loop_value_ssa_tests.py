#!/usr/bin/env python3
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

from s_seir_effect_lifter import EffectLifter
from s_seir_model import EffectNode
from s_seir_pipeline import build_sseir
from semantic_fact import build_function_level_semantic_fact_payload


SOURCE = """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract LoopSSARegression {
    mapping(address => uint256) private balances;

    function accumulator(address[] calldata accounts) external view returns (uint256 result) {
        assembly {
            for { let i := 0 } lt(i, accounts.length) { i := add(i, 1) } {
                let account := calldataload(add(accounts.offset, mul(i, 0x20)))
                mstore(0x00, account)
                mstore(0x20, balances.slot)
                result := add(result, sload(keccak256(0x00, 0x40)))
            }
        }
    }

    function memoryRecurrence(uint256 count) external pure returns (uint256 result) {
        assembly {
            mstore(0x00, 0)
            for { let i := 0 } lt(i, count) { i := add(i, 1) } {
                let old := mload(0x00)
                mstore(0x00, add(old, 1))
            }
            result := mload(0x00)
        }
    }

    function redefineThenUse(uint256 count) external pure returns (uint256 result) {
        assembly {
            let x := 0
            for { let i := 0 } lt(i, count) { i := add(i, 1) } {
                x := add(x, 1)
                result := x
            }
        }
    }

    function callOutput(address token, address account) external view returns (uint256 result) {
        assembly {
            let ptr := mload(0x40)
            mstore(ptr, shl(224, 0x70a08231))
            mstore(add(ptr, 0x04), account)
            let success := staticcall(gas(), token, ptr, 0x24, ptr, 0x20)
            if iszero(and(success, eq(returndatasize(), 0x20))) { revert(0, 0) }
            result := mload(ptr)
        }
    }
}
"""


def analyze():
    root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="sseir_loop_value_") as directory:
        source = Path(directory) / "LoopSSARegression.sol"
        source.write_text(SOURCE, encoding="utf-8")
        functions = build_sseir(
            source,
            str(root / ".venv/bin/solc"),
            str(root / ".venv/bin/slither"),
            root,
            branch_preprocess=False,
        )
        facts = build_function_level_semantic_fact_payload(functions)["facts"]
        return functions, facts


def function(functions, name):
    return next(item for item in functions if item.contract == "LoopSSARegression" and item.function == name)


def function_facts(facts, name):
    return [item for item in facts if item.get("contract") == "LoopSSARegression" and item.get("function") == name]


def test_loop_value_phis_and_static_definitions(functions, facts) -> None:
    fn = function(functions, "accumulator")
    phis = [effect for effect in fn.effects if effect.kind == "ValuePhi"]
    assert {effect.attrs.get("target") for effect in phis} == {"i", "result"}, [effect.attrs for effect in phis]
    result_phi = next(effect for effect in phis if effect.attrs.get("target") == "result")
    assert [item.get("value") for item in result_phi.attrs["inputs"]][0] == "0"

    rows = function_facts(facts, "accumulator")
    computations = [
        item for item in rows
        if item.get("kind") == "ValueCompute"
        and (item.get("semantic") or {}).get("source_target") in {"account", "result"}
    ]
    assert len([item for item in computations if item["semantic"]["source_target"] == "account"]) == 1
    assert len([item for item in computations if item["semantic"]["source_target"] == "result"]) == 1
    account_assignment = next(item for item in computations if item["semantic"]["source_target"] == "account")
    result_assignment = next(item for item in computations if item["semantic"]["source_target"] == "result")
    assert str(account_assignment.get("rvalue")).startswith("__sseir_eval_")
    assert str(result_assignment.get("rvalue")).startswith("__sseir_eval_")
    assert "calldataload(" not in str(account_assignment.get("rvalue"))
    assert "sload(" not in str(result_assignment.get("rvalue"))


def test_memory_read_modify_write_keeps_memory_phi(functions, _facts) -> None:
    fn = function(functions, "memoryRecurrence")
    memory_phis = [effect for effect in fn.effects if effect.kind == "MemoryPhi"]
    assert memory_phis, "loop-carried mload/mstore location must remain a MemoryPhi"
    assert any(effect.attrs.get("inputs") for effect in memory_phis)


def test_in_loop_definition_supersedes_header_phi(functions, facts) -> None:
    fn = function(functions, "redefineThenUse")
    x_phi = next(
        effect.attrs.get("version")
        for effect in fn.effects
        if effect.kind == "ValuePhi" and effect.attrs.get("target") == "x"
    )
    rows = function_facts(facts, "redefineThenUse")
    x_update = next(
        item for item in rows
        if item.get("kind") == "ValueCompute"
        and (item.get("semantic") or {}).get("source_target") == "x"
        and item.get("condition")
    )
    result_update = next(
        item for item in rows
        if item.get("kind") == "ValueCompute"
        and (item.get("semantic") or {}).get("source_target") == "result"
    )
    assert result_update.get("rvalue") == x_update.get("lvalue"), (x_update, result_update)
    assert result_update.get("rvalue") != x_phi


def test_renumbered_return_binding_targets_existing_phi(_functions, facts) -> None:
    rows = function_facts(facts, "accumulator")
    returned = next(item for item in rows if item.get("kind") == "Return")
    producer_id = (returned.get("semantic") or {}).get("yul_boundary_binding", {}).get("producer_fact")
    producer = next(item for item in rows if item.get("fact_id") == producer_id)
    assert producer.get("kind") == "ValuePhi"
    assert producer.get("lvalue") == returned.get("rvalue")


def test_call_output_and_condition_ssa(functions, facts) -> None:
    rows = function_facts(facts, "callOutput")
    output = next(item for item in rows if (item.get("semantic") or {}).get("operation") == "call_output_read")
    assert str(output.get("rvalue")).startswith("call_output_word("), output
    assignments = {
        (item.get("semantic") or {}).get("source_target"): item
        for item in rows
        if item.get("kind") == "ValueCompute"
        and (item.get("semantic") or {}).get("source_target")
    }
    assert str(assignments["success"].get("rvalue")).startswith("__sseir_eval_")
    assert str(assignments["result"].get("rvalue")).startswith("__sseir_eval_")
    assert not any(
        item.get("kind") == "ValueCompute"
        and item.get("rvalue") == "iszero(and(success, eq(returndatasize(), 0x20)))"
        for item in rows
    )
    condition_steps = [
        item for item in rows
        if item.get("kind") == "ValueCompute" and "success" in str(item.get("rvalue"))
    ]
    assert any("success__ssa" in str(item.get("rvalue")) for item in condition_steps), condition_steps


def test_call_output_requires_cfg_dominance(_functions, _facts) -> None:
    call = EffectNode("eff_call", "StaticCall", ["asm_s_1"], {
        "cfg_node_id": 2,
        "path_states": ["flag"],
        "output_ptr": "ptr",
        "output_size": "0x20",
        "op": "staticcall",
    })
    read = EffectNode("eff_read", "MemoryRead", ["asm_s_2"], {
        "cfg_node_id": 4,
        "path_states": ["entry"],
        "read_from": "ptr",
        "memory_read": {"complete": False, "has_unknown": True},
    })
    cfg = SimpleNamespace(
        nodes=[SimpleNamespace(node_id=node_id) for node_id in range(5)],
        edges=[
            SimpleNamespace(source=0, target=1, label="fallthrough"),
            SimpleNamespace(source=1, target=2, label="true"),
            SimpleNamespace(source=1, target=3, label="false"),
            SimpleNamespace(source=2, target=4, label="fallthrough"),
            SimpleNamespace(source=3, target=4, label="fallthrough"),
        ],
        entry=0,
        exit=4,
    )
    EffectLifter.attach_cross_statement_call_outputs([call, read], cfg=cfg)
    assert "value_from_call_output" not in read.attrs


if __name__ == "__main__":
    analyzed_functions, semantic_facts = analyze()
    tests = [
        test_loop_value_phis_and_static_definitions,
        test_memory_read_modify_write_keeps_memory_phi,
        test_in_loop_definition_supersedes_header_phi,
        test_renumbered_return_binding_targets_existing_phi,
        test_call_output_and_condition_ssa,
        test_call_output_requires_cfg_dominance,
    ]
    for test in tests:
        test(analyzed_functions, semantic_facts)
        print(f"PASS {test.__name__}")
