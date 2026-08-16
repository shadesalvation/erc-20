#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
for item in (ROOT / "legacy_yul", ROOT / "s_seir"):
    text = str(item)
    if text not in sys.path:
        sys.path.insert(0, text)

from s_seir_semantic_fact_adapter import SSeirFactAdapter, SlitherFactAdapter, build_function_level_semantic_fact_payload


SLITHER_SOURCE = """pragma solidity ^0.8.26;

library L {
    function inc(uint256 x) internal pure returns (uint256) { return x + 1; }
}

contract Created {
    constructor(uint256) payable {}
}

contract Sink {
    event Ping(address indexed who, uint256 value);
    uint256[] public arr;
    struct Pair { uint256 a; uint256 b; }
    mapping(address => uint256) public balances;

    function target(uint256 value) external payable returns (uint256) {
        emit Ping(msg.sender, value);
        return value;
    }

    function complex(address payable to, uint256 x, bool flag) external returns (uint256) {
        uint256 y = x + 1;
        uint256 z = flag ? y : x;
        uint256[] memory tmp = new uint256[](2);
        tmp[0] = z;
        Pair memory pair = Pair({a: tmp.length, b: tmp[0]});
        delete balances[msg.sender];
        (bool ok, bytes memory data) = address(this).call{value: 0}(abi.encodeWithSignature("target(uint256)", pair.b));
        require(ok, string(data));
        to.transfer(1);
        bool sent = to.send(1);
        Created created = new Created{value: 0}(pair.a);
        return L.inc(address(created).code.length + (sent ? 1 : 0));
    }
}
"""


def function(overlays: list[dict]) -> dict:
    return {
        "contract": "Token",
        "function": "transfer",
        "signature": "transfer(address,uint256)",
        "source_statements": [
            {"stmt_id": "sol_s_1", "lang": "solidity", "text": "function transfer(...)"},
            {"stmt_id": "asm_s_1", "lang": "yul", "text": "sstore(slot, amount)"},
        ],
        "control": {
            "blocks": [{"block_id": "bb_1", "stmts": ["asm_s_1"]}],
            "edges": [],
        },
        "effects": [],
        "semantic_overlays": overlays,
    }


def overlay(kind: str, attrs: dict, overlay_id: str = "ov_1") -> dict:
    return {
        "overlay_id": overlay_id,
        "kind": kind,
        "effects": ["eff_1"],
        "stmt_refs": ["asm_s_1"],
        "attrs": attrs,
    }


class FakeFunction:
    function_id = "Token.transfer(address,uint256)"
    contract = "Token"
    function = "transfer"
    signature = "transfer(address,uint256)"
    control = {
        "blocks": [
            {
                "block_id": "bb_sol_slither_n1",
                "kind": "solidity",
                "stmts": ["sol_s_1"],
                "attrs": {
                    "slither_node_id": 1,
                    "slithir": [
                        {
                            "kind": "Binary",
                            "text": "TMP_0 = amount + 1",
                            "lvalue": "TMP_0",
                            "variable_left": "amount",
                            "variable_right": "1",
                            "operator": "+",
                        }
                    ],
                    "slithir_ssa": [],
                },
            },
            {
                "block_id": "bb_asm1_n0",
                "kind": "yul",
                "stmts": ["asm_s_1"],
                "attrs": {},
            },
        ]
        ,
        "control_dependencies": [
            {
                "controller": "bb_sol_slither_n0",
                "dependent": "bb_sol_slither_n1",
                "predicate": "flag",
            }
        ],
    }
    _sseir_solidity_atomic_operations = {
        "function_id": function_id,
        "contract": contract,
        "function": function,
        "signature": signature,
        "operation_count": 1,
        "operations": [
            {
                "atom_id": "sol_atom_1",
                "sequence": 1,
                "block_order": 0,
                "operation_order": 0,
                "cfg_block_id": "bb_sol_slither_n1",
                "stmt_refs": ["sol_s_1"],
                "kind": "Binary",
                "atomic_kind": "ValueCompute",
                "result_ssa": "TMP_0",
                "read": ["amount", "1"],
                "resolved_reads": ["amount", "1"],
                "operator": "+",
                "text": "TMP_0 = amount + 1",
                "source_expression": "amount + 1",
                "runtime_operation": True,
                "depends_on_atoms": [],
            }
        ],
    }

    def to_semantic_dict(self) -> dict:
        yul_overlay = overlay("MappingWrite", {
                "access": "balances[to]",
                "state_variable": "balances",
                "keys": ["to"],
                "value": "amount",
            })
        solidity_overlay = {
            "overlay_id": "ov_sol_1",
            "kind": "MappingWrite",
            "effects": ["eff_sol_1"],
            "stmt_refs": ["sol_s_1"],
            "attrs": {
                "access": "balances[msg.sender]",
                "state_variable": "balances",
                "keys": ["msg.sender"],
                "value": "amount",
                "solidity_like": "balances[msg.sender] = amount;",
                "source": "slithir_ssa",
            },
        }
        return function([solidity_overlay, yul_overlay])


class DuplicateYulRequireFunction:
    contract = "Token"
    function = "calc"
    signature = "calc()"
    control = {"blocks": [], "edges": [], "control_dependencies": []}

    def to_semantic_dict(self) -> dict:
        return {
            "contract": "Token",
            "function": "calc",
            "signature": "calc()",
            "source_statements": [
                {"stmt_id": "asm_s_1", "lang": "yul", "text": "if iszero(D) { revert(0, 0) }"},
                {"stmt_id": "asm_s_2", "lang": "yul", "text": "if iszero(D) { revert(0, 0) }"},
            ],
            "control": {"blocks": [{"block_id": "bb_asm", "stmts": ["asm_s_1", "asm_s_2"]}], "edges": []},
            "effects": [
                {"effect_id": "eff_1", "kind": "Revert", "stmt_refs": ["asm_s_1"], "attrs": {}},
                {"effect_id": "eff_2", "kind": "Revert", "stmt_refs": ["asm_s_2"], "attrs": {}},
            ],
            "semantic_overlays": [
                {"overlay_id": "ov_1", "kind": "RequireOverlay", "effects": ["eff_1"], "stmt_refs": ["asm_s_1"], "attrs": {"condition": "D != 0"}},
                {"overlay_id": "ov_2", "kind": "RequireOverlay", "effects": ["eff_2"], "stmt_refs": ["asm_s_2"], "attrs": {"condition": "D != 0"}},
            ],
        }


def test_sseir_mapping_write_becomes_state_write_fact() -> None:
    fn = function([
        overlay("MappingWrite", {
            "access": "balances[to]",
            "state_variable": "balances",
            "keys": ["to"],
            "value": "amount",
        }),
    ])
    facts = SSeirFactAdapter().function_facts(fn)
    assert len(facts) == 1
    fact = facts[0].to_dict()
    assert fact["kind"] == "StateWrite"
    assert fact["source_lang"] == "yul"
    assert fact["origin"] == "sseir_overlay"
    assert fact["lvalue"] == "balances[to]"
    assert fact["rvalue"] == "amount"
    assert fact["writes"] == ["balances[to]"]
    assert fact["semantic"]["state_variable"] == "balances"
    assert fact["semantic"]["keys"] == ["to"]
    assert fact["evidence"]["overlay"] == "ov_1"


def test_path_conditioned_storage_write_expands_to_multiple_facts() -> None:
    fn = function([
        overlay("PathConditionedStorageWrite", {
            "value": "amount",
            "candidates": [
                {
                    "condition": "flag",
                    "status": "resolved",
                    "access": "balances[a]",
                    "state_variable": "balances",
                    "value": "amount",
                },
                {
                    "condition": "!(flag)",
                    "status": "resolved",
                    "access": "balances[b]",
                    "state_variable": "balances",
                    "value": "amount",
                },
            ],
        }),
    ])
    facts = [item.to_dict() for item in SSeirFactAdapter().function_facts(fn)]
    assert [item["kind"] for item in facts] == ["StateWrite", "StateWrite"]
    assert [item["condition"] for item in facts] == ["flag", "!(flag)"]
    assert [item["lvalue"] for item in facts] == ["balances[a]", "balances[b]"]
    assert all(item["evidence"]["candidate"]["status"] == "resolved" for item in facts)


def test_plain_yul_fact_inherits_single_effect_path_condition() -> None:
    fn = function([
        overlay("StateVariableWrite", {
            "access": "storage[_OWNER_SLOT]",
            "value": "newOwner",
            "storage_model": "manual_constant_slot",
        }),
    ])
    fn["effects"] = [
        {
            "effect_id": "eff_1",
            "kind": "StorageWrite",
            "stmt_refs": ["asm_s_1"],
            "attrs": {"path_states": ["_guardInitializeOwner()"]},
        }
    ]
    fact = SSeirFactAdapter().function_facts(fn)[0].to_dict()
    assert fact["condition"] == "_guardInitializeOwner()"
    assert fact["source_lang"] == "yul"


def test_sseir_event_and_revert_become_behavior_facts() -> None:
    fn = function([
        overlay("EventEmit", {
            "event": "Transfer",
            "signature": "Transfer(address,address,uint256)",
            "args": ["from", "to", "amount"],
        }, "ov_event"),
        overlay("RequireOverlay", {
            "condition": "fromBalance >= amount",
            "custom_error": "InsufficientBalance()",
        }, "ov_require"),
    ])
    facts = [item.to_dict() for item in SSeirFactAdapter().function_facts(fn)]
    assert [item["kind"] for item in facts] == ["EventEmit", "Require"]
    assert facts[0]["semantic"]["event"] == "Transfer"
    assert facts[1]["condition"] == "fromBalance >= amount"


def test_slither_like_operations_map_to_semantic_facts() -> None:
    facts = [
        item.to_dict()
        for item in SlitherFactAdapter().facts_from_operations(
            {"contract": "Token", "function": "transfer", "signature": "transfer(address,uint256)"},
            [
                {
                    "kind": "Binary",
                    "lvalue": "tmp",
                    "rvalue": "balances[to] + amount",
                    "reads": ["balances[to]", "amount"],
                    "writes": ["tmp"],
                    "node_id": "n1",
                },
                {
                    "kind": "EventCall",
                    "event": "Transfer",
                    "arguments": ["from", "to", "amount"],
                    "node_id": "n2",
                },
            ],
        )
    ]
    assert [item["kind"] for item in facts] == ["BinaryOperation", "EventEmit"]
    assert facts[0]["source_lang"] == "solidity"
    assert facts[0]["origin"] == "slither_ir"
    assert facts[0]["reads"] == ["balances[to]", "amount"]
    assert facts[1]["semantic"]["event"] == "Transfer"


def test_slither_defined_operation_dicts_are_covered() -> None:
    kinds = [
        "Assignment", "Binary", "Unary", "TypeConversion", "Condition",
        "Index", "Member", "Length", "Delete", "InitArray", "NewArray",
        "NewContract", "NewElementaryType", "NewStructure", "Phi",
        "PhiCallback", "Unpack", "InternalCall", "InternalDynamicCall",
        "HighLevelCall", "LowLevelCall", "LibraryCall", "SolidityCall",
        "EventCall", "Send", "Transfer", "Nop",
    ]
    operations = [
        {
            "kind": kind,
            "lvalue": f"{kind}_out",
            "read": ["a", "b"],
            "arguments": ["a", "b"],
            "node_id": f"n_{i}",
            "function_name": "f",
            "name": "E" if kind == "EventCall" else None,
        }
        for i, kind in enumerate(kinds)
    ]
    facts = [item.to_dict() for item in SlitherFactAdapter().facts_from_operations(
        {"contract": "Sink", "function": "complex", "signature": "complex()"},
        operations,
    )]
    assert len(facts) == len(kinds)
    assert all(item["kind"] != "SlithIROperation" for item in facts)
    assert {item["semantic"]["slithir_kind"] for item in facts} == set(kinds)
    assert any(item["kind"] == "IndexAccess" for item in facts)
    assert any(item["kind"] == "NewContract" for item in facts)
    assert any(item["kind"] == "ValueTransferCall" for item in facts)


def test_real_slither_operations_project_to_semantic_facts() -> None:
    try:
        from slither.slither import Slither  # type: ignore
    except Exception as exc:
        print(f"SKIP real slither unavailable: {exc}")
        return
    with tempfile.TemporaryDirectory(prefix="sseir_semantic_fact_slither_") as directory:
        source = Path(directory) / "Sink.sol"
        source.write_text(SLITHER_SOURCE, encoding="utf-8")
        solc = ROOT.parent / ".venv" / "bin" / "solc"
        try:
            slither = Slither(str(source), solc=str(solc if solc.exists() else "solc"))
        except Exception as exc:
            print(f"SKIP real slither compile unavailable: {exc}")
            return

    contract = next(item for item in slither.contracts if item.name == "Sink")
    function_obj = next(item for item in contract.functions if item.name == "complex")
    facts = [item.to_dict() for item in SlitherFactAdapter().facts_from_slither_function(function_obj)]
    fact_kinds = {item["kind"] for item in facts}
    slithir_kinds = {item["semantic"]["slithir_kind"] for item in facts}
    assert {"BinaryOperation", "BranchCondition", "IndexAccess", "LengthRead", "NewArray", "LowLevelCall", "BuiltinCall", "ValueTransferCall", "Return"}.issubset(fact_kinds)
    assert {"Assignment", "Binary", "Condition", "Index", "Length", "NewArray", "LowLevelCall", "SolidityCall", "Transfer", "Send", "Return"}.issubset(slithir_kinds)
    assert all(item["origin"] == "slither_ir" and item["source_lang"] == "solidity" for item in facts)


def test_function_level_payload_splits_solidity_and_yul_facts() -> None:
    payload = build_function_level_semantic_fact_payload([FakeFunction()], source="Token.sol", result_dir="outputs/test")
    assert payload["schema"] == "s-seir-function-semantic-facts/v2"
    assert payload["solidity_fact_count"] == 1
    assert payload["yul_fact_count"] == 1
    assert [item["kind"] for item in payload["solidity_facts"]] == ["ValueCompute"]
    assert payload["solidity_facts"][0]["source_lang"] == "solidity"
    assert payload["solidity_facts"][0]["origin"] == "solidity_atomic_operation"
    assert payload["solidity_facts"][0]["operation_id"] == "sol_atom_1"
    assert payload["solidity_facts"][0]["lvalue"] == "TMP_0"
    assert "high_level_semantics" not in payload["solidity_facts"][0]["semantic"]
    assert [item["kind"] for item in payload["yul_facts"]] == ["StateWrite"]
    assert not any(item["kind"] == "BinaryOperation" for item in payload["facts"])
    assert "solidity_like" not in json.dumps(payload["facts"], ensure_ascii=False)
    assert [item["fact_id"] for item in payload["facts"]] == ["fact_1", "fact_2"]


def test_function_level_payload_preserves_distinct_yul_operations() -> None:
    payload = build_function_level_semantic_fact_payload([DuplicateYulRequireFunction()])
    assert payload["yul_fact_count"] == 2
    assert [fact["stmt_refs"] for fact in payload["yul_facts"]] == [["asm_s_1"], ["asm_s_2"]]
    assert [fact["operation_id"] for fact in payload["yul_facts"]] == [
        "yul_overlay:ov_1",
        "yul_overlay:ov_2",
    ]


if __name__ == "__main__":
    tests = [
        test_sseir_mapping_write_becomes_state_write_fact,
        test_path_conditioned_storage_write_expands_to_multiple_facts,
        test_plain_yul_fact_inherits_single_effect_path_condition,
        test_sseir_event_and_revert_become_behavior_facts,
        test_slither_like_operations_map_to_semantic_facts,
        test_slither_defined_operation_dicts_are_covered,
        test_real_slither_operations_project_to_semantic_facts,
        test_function_level_payload_splits_solidity_and_yul_facts,
        test_function_level_payload_preserves_distinct_yul_operations,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
