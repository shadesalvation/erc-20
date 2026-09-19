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


def test_resolved_mapping_slot_projects_only_high_storage_location() -> None:
    fn = function([overlay("MappingSlot", {
        "target": "keccak256(0x00, 0x40)",
        "expression": "balances[account]",
        "access": "balances[account]",
        "state_variable": "balances",
        "keys": ["account"],
        "slot_kind": "mapping_slot",
        "resolved_inputs": [{"offset": 0, "value": "account"}],
    })])
    fact = SSeirFactAdapter().function_facts(fn, semantic_only=True)[0].to_dict()
    assert fact["kind"] == "StorageLocationResolve"
    assert "lvalue" not in fact and "writes" not in fact
    assert fact["rvalue"] == "balances[account]"
    assert fact["reads"] == ["balances", "account"]
    assert fact["semantic"]["location"] == {
        "kind": "mapping", "access": "balances[account]",
        "state_variable": "balances", "keys": ["account"],
    }
    assert "keccak256" not in str(fact)
    assert "physical_reference" not in fact["evidence"]


def test_unresolved_mapping_slot_becomes_opaque_storage_location() -> None:
    fn = function([overlay("MappingSlot", {
        "target": "keccak256(0x00, 0x40)",
        "expression": "keccak256(0x00, 0x40)",
        "slot_kind": "mapping_slot",
    })])
    fact = SSeirFactAdapter().function_facts(fn, semantic_only=True)[0].to_dict()
    assert fact["kind"] == "UnresolvedStorageLocation"
    assert fact["rvalue"] == "opaqueStorageLocation"
    assert fact["semantic"]["status"] == "unresolved"
    assert "keccak256" not in str(fact)


def test_resolved_mapping_read_hides_physical_slot_evidence() -> None:
    fn = function([overlay("MappingRead", {
        "target": "balance",
        "access": "balances[account]",
        "state_variable": "balances",
        "keys": ["account"],
        "slot": "keccak256(0x00, 0x40)",
        "slot_key": "keccak256(0x00, 0x40)__inline",
        "slot_versions": ["keccak256(0x00, 0x40)__inline"],
    })])
    fact = SSeirFactAdapter().function_facts(fn, semantic_only=True)[0].to_dict()
    assert fact["kind"] == "StateRead"
    assert fact["rvalue"] == "balances[account]"
    assert fact["semantic"]["location"]["access"] == "balances[account]"
    assert "storage_resolution" not in fact["evidence"]
    assert "keccak256" not in str(fact)


def test_expression_normalization_projects_the_normalized_expression() -> None:
    facts = [item.to_dict() for item in SSeirFactAdapter().function_facts(function([
        overlay("ExpressionNormalization", {
            "target": "i",
            "expression": "add(i, 1)",
            "expression_normalized": "(i + 1)",
            "context": "value",
        }),
    ]), semantic_only=True)]
    assert len(facts) == 1
    assert facts[0]["rvalue"] == "(i + 1)"
    assert facts[0]["semantic"]["expression_normalized"] == "(i + 1)"
    assert "expression" not in facts[0]["semantic"]


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


def test_mapping_write_condition_comes_from_storage_sink() -> None:
    fn = function([{
        "overlay_id": "ov_write",
        "kind": "MappingWrite",
        "effects": ["eff_hash", "eff_write"],
        "stmt_refs": ["asm_s_1", "asm_s_2"],
        "attrs": {
            "access": "balances[msg.sender]",
            "state_variable": "balances",
            "keys": ["msg.sender"],
            "value": "nextBalance",
        },
    }])
    fn["source_statements"].append({"stmt_id": "asm_s_2", "lang": "yul", "text": "sstore(slot, nextBalance)"})
    fn["effects"] = [
        {
            "effect_id": "eff_hash",
            "kind": "MemoryHash",
            "stmt_refs": ["asm_s_1"],
            "attrs": {"path_states": ["addressReady"]},
        },
        {
            "effect_id": "eff_write",
            "kind": "StorageWrite",
            "stmt_refs": ["asm_s_2"],
            "attrs": {"path_states": ["addressReady && balanceReady"]},
        },
    ]
    fact = SSeirFactAdapter().function_facts(fn)[0].to_dict()
    assert fact["condition"] == "addressReady && balanceReady"


def test_mapping_write_condition_keeps_common_join_prefix() -> None:
    fn = function([{
        "overlay_id": "ov_write",
        "kind": "MappingWrite",
        "effects": ["eff_hash", "eff_write"],
        "stmt_refs": ["asm_s_1", "asm_s_2"],
        "attrs": {
            "access": "balances[from]",
            "state_variable": "balances",
            "keys": ["from"],
            "value": "nextBalance",
        },
    }])
    fn["source_statements"].append({"stmt_id": "asm_s_2", "lang": "yul", "text": "sstore(slot, nextBalance)"})
    fn["effects"] = [
        {
            "effect_id": "eff_hash",
            "kind": "MemoryHash",
            "stmt_refs": ["asm_s_1"],
            "attrs": {"path_states": ["recipientReady"]},
        },
        {
            "effect_id": "eff_write",
            "kind": "StorageWrite",
            "stmt_refs": ["asm_s_2"],
            "attrs": {"path_states": [
                "recipientReady && allowanceReady && infiniteAllowance && balanceReady",
                "recipientReady && allowanceReady && !(infiniteAllowance) && balanceReady",
            ]},
        },
    ]
    fact = SSeirFactAdapter().function_facts(fn)[0].to_dict()
    assert fact["condition"] == "recipientReady && allowanceReady && balanceReady"


def test_address_and_decoded_call_overlays_project_to_facts() -> None:
    fn = function([
        overlay("AddressHasCode", {
            "target": "result",
            "address": "account",
            "address_normalized": "account",
            "code_size": "account.code.length",
            "condition": "(account.code.length != 0)",
            "check_kind": "has_code",
        }, "ov_code"),
        overlay("StaticCallOverlay", {
            "op": "staticcall",
            "target": "token",
            "target_solidity": "token",
            "selector": "0x70a08231",
            "selector_signature": "balanceOf(address)",
            "arguments": ["account"],
            "decoded_input": "MemorySlice(selector, account)",
        }, "ov_call"),
    ])
    facts = [item.to_dict() for item in SSeirFactAdapter().function_facts(fn)]
    assert facts[0]["kind"] == "ValueCompute"
    assert facts[0]["semantic"]["operation"] == "address_has_code"
    assert facts[0]["semantic"]["predicate"] == "(account.code.length != 0)"
    assert facts[1]["semantic"]["selector"] == "0x70a08231"
    assert facts[1]["semantic"]["selector_signature"] == "balanceOf(address)"
    assert facts[1]["semantic"]["arguments"] == ["account"]
    assert facts[1]["semantic"]["decoded_input"] == "MemorySlice(selector, account)"


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


def test_completed_sseir_advanced_overlays_have_high_level_facts() -> None:
    fn = function([
        overlay("AddressZeroCheck", {"variable": "account", "variable_type": "address", "check": "is_nonzero", "condition": "account != address(0)"}, "ov_zero"),
        overlay("BytesContentHash", {"target": "digest", "object": "data", "object_type": "bytes", "expression": "keccak256(data)", "result_expression": "keccak256(data)", "hash_algorithm": "keccak256"}, "ov_hash"),
        overlay("AbiCallDataConstruction", {"selector": "0x70a08231", "signature": "balanceOf(address)", "arguments": [{"value": "account", "type": "address"}]}, "ov_abi"),
        overlay("AbiEncodedLowLevelCall", {"target": "token", "target_solidity": "token", "op": "staticcall", "result": "ok", "selector": "0x70a08231", "selector_signature": "balanceOf(address)", "arguments": ["account"], "abi_calldata": {"overlay": "ov_abi", "selector": "0x70a08231", "signature": "balanceOf(address)", "arguments": ["account"]}}, "ov_call"),
        overlay("RawReturnData", {"encoding_hint": "abi_word", "values": ["digest"]}, "ov_return"),
        overlay("PrecompileOutputRead", {"target": "recovered", "value": "ecrecover(hash, v, r, s)", "source_precompile_overlay": "ov_pc"}, "ov_pc_out"),
        overlay("StructFieldRead", {"struct_object": "pair", "struct_type": "Pair", "field": {"name": "left", "type_string": "uint256"}, "value": "left"}, "ov_field_read"),
        overlay("StructFieldWrite", {"struct_object": "pair", "struct_type": "Pair", "field": {"name": "right", "type_string": "uint256"}, "value_normalized": "digest"}, "ov_field_write"),
        overlay("StoragePointerSlotBinding", {"pointer": "ref", "slot_normalized": "ignored", "reason": "assembly_only"}, "ov_binding"),
    ])
    facts = [item.to_dict() for item in SSeirFactAdapter().function_facts(fn, semantic_only=True)]
    by_overlay = {item["evidence"]["overlay"]: item for item in facts}
    assert by_overlay["ov_zero"]["kind"] == "BranchCondition"
    assert by_overlay["ov_hash"]["kind"] == "HashCompute"
    assert by_overlay["ov_abi"]["kind"] == "AbiEncode"
    assert by_overlay["ov_call"]["kind"] == "LowLevelCall"
    assert by_overlay["ov_call"]["semantic"]["encoded_payload"] == "__sfir_abi_payload_ov_abi"
    assert by_overlay["ov_return"]["rvalue"] == "returnRawAbiWord(digest)"
    assert by_overlay["ov_pc_out"]["semantic"]["operation"] == "precompile_output_read"
    assert by_overlay["ov_field_read"]["rvalue"] == "pair.left"
    assert by_overlay["ov_field_write"]["lvalue"] == "pair.right"
    assert by_overlay["ov_binding"]["rvalue"] == "opaqueStorageLocation"
    public = json.dumps(facts, ensure_ascii=False)
    assert "mload(" not in public and "mstore(" not in public and ".slot :=" not in public


def test_path_conditioned_sseir_return_output_and_unknown_are_not_dropped() -> None:
    fn = function([
        overlay("PathConditionedRawReturnData", {"candidates": [{"status": "resolved", "condition": "ok", "encoding_hint": "abi_word", "values": ["value"]}]}, "ov_path_return"),
        overlay("PathConditionedPrecompileOutputRead", {"target": "out", "candidates": [{"status": "resolved", "condition": "ok", "target": "out", "value": "ecrecover(hash, v, r, s)"}]}, "ov_path_output"),
        overlay("FutureCompletedOverlay", {"new_semantic_field": "value"}, "ov_future"),
    ])
    facts = [item.to_dict() for item in SSeirFactAdapter().function_facts(fn, semantic_only=True)]
    assert [item["kind"] for item in facts] == ["Return", "ValueCompute", "UnmodeledSSeirOverlay"]
    assert facts[0]["condition"] == "ok"
    assert facts[1]["semantic"]["operation"] == "precompile_output_read"
    assert facts[2]["semantic"]["overlay_kind"] == "FutureCompletedOverlay"


def test_path_overlay_without_structured_candidates_is_visible_unmodeled() -> None:
    fn = function([
        overlay("PathConditionedStaticCallOverlay", {"target": "token", "candidates": []}, "ov_empty_path"),
        overlay("CalldataArrayElementCandidate", {"target": "element"}, "ov_candidate"),
    ])
    facts = [item.to_dict() for item in SSeirFactAdapter().function_facts(fn, semantic_only=True)]
    assert [item["kind"] for item in facts] == ["UnmodeledSSeirOverlay", "UnmodeledSSeirOverlay"]
    assert facts[0]["semantic"]["unmodeled_reason"] == "no_structured_path_candidates"
    assert facts[1]["semantic"]["unmodeled_reason"] == "completion_candidate_reached_sfir_boundary"


def test_final_overlay_policy_covers_every_declared_completed_overlay() -> None:
    adapter = SSeirFactAdapter()
    assert adapter.FINAL_OVERLAY_KINDS <= set(adapter.FINAL_OVERLAY_POLICY)
    assert {
        "EvaluationStep", "ExpressionNormalization", "StructInitializationFragment",
        "StructMutationFragment", "MemoryRegionWrite", "CursorBasedMemoryWrite",
    } == {
        kind for kind, policy in adapter.FINAL_OVERLAY_POLICY.items()
        if policy == "derivation"
    }


def test_every_declared_final_overlay_has_a_non_silent_boundary_outcome() -> None:
    """The completed-overlay contract may never silently lose a future kind.

    Empty attributes deliberately exercise the boundary rather than a
    recognizer: each kind must still produce a projected fact or the explicit
    UnmodeledSSeirOverlay fallback.  Path kinds receive the same structured
    candidate envelope emitted by their S-SEIR builders.
    """
    adapter = SSeirFactAdapter()
    path_kinds = {
        "PathConditionedStorageRead", "PathConditionedStorageWrite",
        "PathConditionedEventEmit", "PathConditionedCustomErrorRevert",
        "PathConditionedRevert", "PathConditionedPrecompileCall",
        "PathConditionedExternalCall", "PathConditionedLowLevelCall",
        "PathConditionedStaticCallOverlay", "PathConditionedDelegateCallOverlay",
        "PathConditionedRawReturnData", "PathConditionedPrecompileOutputRead",
    }
    for index, kind in enumerate(sorted(adapter.FINAL_OVERLAY_KINDS)):
        attrs = {"candidates": [{}]} if kind in path_kinds else {}
        facts = adapter.overlay_facts(
            function([]), overlay(kind, attrs, f"ov_contract_{index}"), {}, {},
        )
        assert facts, kind
        assert all(item.kind or item.semantic.get("status") == "unmodeled" for item in facts), kind


def test_sseir_event_return_revert_memory_and_precompile_fields_are_projected() -> None:
    fn = function([
        overlay("EventEmit", {
            "event": "Transfer", "signature": "Transfer(address,address,uint256)",
            "args": ["from", "to", "amount"],
            "topics": ["Transfer.topic0", "from", "to"],
        }, "ov_event_topics"),
        overlay("ReturnValue", {"values": ["left", "right"]}, "ov_return_values"),
        overlay("RawRevertBytes", {
            "source_object": "reason", "payload": "reason[0:reason.length]", "guard": "!ok",
        }, "ov_revert_bytes"),
        overlay("CursorBasedMemoryWrite", {
            "struct_object": "pair", "struct_type": "Pair", "cursor_field": "right",
            "field_updates": [{"field": {"name": "right"}, "new_value_normalized": "amount"}],
        }, "ov_cursor"),
        overlay("PrecompileCall", {
            "precompile": "sha256", "input_words": ["data"],
            "native_precompile": {
                "precompile": "sha256", "input_words": ["data"],
                "native_precompile": {
                    "result_name": "sha256_result_0", "output_word_expression": "uint256(sha256_result_0)",
                    "solidity_like": "bytes32 sha256_result_0 = sha256(abi.encodePacked(data));",
                },
            },
        }, "ov_precompile"),
    ])
    facts = [item.to_dict() for item in SSeirFactAdapter().function_facts(fn, semantic_only=True)]
    by_overlay = {item["evidence"]["overlay"]: item for item in facts}
    event = by_overlay["ov_event_topics"]
    assert event["semantic"]["topic0"] == "Transfer.topic0"
    assert event["semantic"]["indexed_topic_values"] == ["from", "to"]
    returned = by_overlay["ov_return_values"]
    assert returned["rvalue"] == ["left", "right"] and returned["reads"] == ["left", "right"]
    reverted = by_overlay["ov_revert_bytes"]
    assert reverted["condition"] == "!ok" and reverted["semantic"]["source_object"] == "reason"
    cursor = by_overlay["ov_cursor"]
    assert cursor["semantic"]["cursor_field"] == "right"
    assert cursor["semantic"]["field_updates"][0]["field"]["name"] == "right"
    precompile = by_overlay["ov_precompile"]
    assert precompile["semantic"]["native_projection"] == {
        "precompile": "sha256", "result_name": "sha256_result_0",
        "output_expression": "uint256(sha256_result_0)",
        "solidity_like": "bytes32 sha256_result_0 = sha256(abi.encodePacked(data));",
    }


def test_memory_array_projection_keeps_recovered_values_without_guessing_indices() -> None:
    fn = function([
        overlay("MemoryArrayConstruction", {
            "result": "items", "array_type": "uint256[]", "element_type": "uint256",
            "length_expr_normalized": "length", "element_write_pattern": "cursor_based",
            "element_writes": [
                {"address": "opaqueAddress", "value_normalized": "left"},
                {"address": "opaqueAddress2", "value": "right"},
            ],
        }, "ov_memory_array"),
        overlay("StructInitializationFragment", {
            "target": "pair", "fields": [{"field": "left", "value": "amount"}],
        }, "ov_struct_fragment"),
    ])
    facts = [item.to_dict() for item in SSeirFactAdapter().function_facts(fn, semantic_only=True)]
    by_overlay = {item["evidence"]["overlay"]: item for item in facts}
    array = by_overlay["ov_memory_array"]
    assert array["semantic"]["elements"] == ["left", "right"]
    assert array["semantic"]["element_write_pattern"] == "cursor_based"
    assert array["semantic"]["recovered_element_write_count"] == 2
    fragment = by_overlay["ov_struct_fragment"]
    assert fragment["semantic"]["fields"] == [{"field": "left", "value": "amount"}]


def test_path_call_and_event_keep_completed_high_semantics() -> None:
    fn = function([
        overlay("PathConditionedStaticCallOverlay", {"candidates": [{
            "status": "resolved", "condition": "enabled", "target": "token", "op": "staticcall",
            "selector": "0x70a08231", "selector_signature": "balanceOf(address)",
            "arguments": ["owner"], "result": "balance",
        }]}, "ov_path_call"),
        overlay("PathConditionedEventEmit", {"event": "Transfer", "signature": "Transfer(address,address,uint256)", "candidates": [{
            "status": "resolved", "condition": "enabled", "args": ["from", "to", "amount"],
            "topics": ["Transfer.topic0", "from", "to"],
        }]}, "ov_path_event"),
    ])
    facts = [item.to_dict() for item in SSeirFactAdapter().function_facts(fn, semantic_only=True)]
    call, event = facts
    assert call["semantic"]["target"] == "token"
    assert call["semantic"]["selector_signature"] == "balanceOf(address)"
    assert call["semantic"]["call_status_result"] == "balance"
    assert event["semantic"]["signature"] == "Transfer(address,address,uint256)"
    assert event["semantic"]["indexed_topic_values"] == ["from", "to"]


def test_path_custom_revert_keeps_a_proved_raw_selector_without_guessing_an_error() -> None:
    fn = function([
        overlay("PathConditionedCustomErrorRevert", {"candidates": [{
            "status": "resolved", "condition": "bad", "selector": "0x08c379a0",
            "normalized": "MemorySlice(low_bytes(0x08c379a0, 4))",
        }]}, "ov_path_revert"),
    ])
    fact = SSeirFactAdapter().function_facts(fn, semantic_only=True)[0].to_dict()
    assert fact["kind"] == "Revert" and fact["condition"] == "bad"
    assert fact["semantic"]["selector"] == "0x08c379a0"
    assert "error" not in fact["semantic"]


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
    assert payload["schema"] == "s-seir-function-semantic-facts/v3"
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
    assert "depends_on" not in json.dumps(payload["facts"], ensure_ascii=False)
    assert [item["fact_id"] for item in payload["facts"]] == ["fact_1", "fact_2"]


def test_function_level_payload_preserves_distinct_yul_operations() -> None:
    payload = build_function_level_semantic_fact_payload([DuplicateYulRequireFunction()])
    assert payload["yul_fact_count"] == 2
    assert [fact["stmt_refs"] for fact in payload["yul_facts"]] == [["asm_s_1"], ["asm_s_2"]]
    assert [fact["operation_id"] for fact in payload["yul_facts"]] == [
        "yul_overlay:ov_1",
        "yul_overlay:ov_2",
    ]


def test_state_read_type_and_predicate_structure_are_additive() -> None:
    from s_seir_predicate_lifter import PredicateLifter
    from s_seir_model import EffectNode
    from s_seir_yul_eval_order import YulEvaluationOrder
    ast_node = {"nodeType": "YulFunctionCall", "functionName": {"nodeType": "YulIdentifier", "name": "iszero"},
                "arguments": [{"nodeType": "YulIdentifier", "name": "amount"}]}
    branch = EffectNode("branch", "Branch", ["asm_s_1"], {"condition_evaluation": YulEvaluationOrder("asm_s_1").materialize(ast_node)})
    typed = PredicateLifter._typed_evaluation(branch, "pred")
    overlays = [overlay("MappingRead", {"state_variable": "balances", "access": "balances[to]", "keys": ["to"], "target": "read", "result_type": "uint256"}),
                overlay("Predicate", {"predicate_id": "pred", "expression": "(amount == 0)", "status": "resolved", "context": "condition", "typed_predicate": typed}, "pred")]
    facts = [x.to_dict() for x in SSeirFactAdapter().function_facts(function(overlays))]
    assert facts[0]["semantic"]["result_type"] == "uint256"
    assert facts[0]["semantic"]["resolution_status"] == "resolved"
    assert facts[1]["semantic"]["typed_predicate"] == typed
    overlays[0]["attrs"].pop("result_type")
    missing = SSeirFactAdapter().function_facts(function(overlays))[0].to_dict()
    assert "result_type" not in missing["semantic"]


if __name__ == "__main__":
    tests = [
        test_state_read_type_and_predicate_structure_are_additive,
        test_sseir_mapping_write_becomes_state_write_fact,
        test_resolved_mapping_slot_projects_only_high_storage_location,
        test_unresolved_mapping_slot_becomes_opaque_storage_location,
        test_resolved_mapping_read_hides_physical_slot_evidence,
        test_expression_normalization_projects_the_normalized_expression,
        test_path_conditioned_storage_write_expands_to_multiple_facts,
        test_plain_yul_fact_inherits_single_effect_path_condition,
        test_mapping_write_condition_comes_from_storage_sink,
        test_mapping_write_condition_keeps_common_join_prefix,
        test_address_and_decoded_call_overlays_project_to_facts,
        test_sseir_event_and_revert_become_behavior_facts,
        test_completed_sseir_advanced_overlays_have_high_level_facts,
        test_path_conditioned_sseir_return_output_and_unknown_are_not_dropped,
        test_path_overlay_without_structured_candidates_is_visible_unmodeled,
        test_final_overlay_policy_covers_every_declared_completed_overlay,
        test_every_declared_final_overlay_has_a_non_silent_boundary_outcome,
        test_sseir_event_return_revert_memory_and_precompile_fields_are_projected,
        test_memory_array_projection_keeps_recovered_values_without_guessing_indices,
        test_path_call_and_event_keep_completed_high_semantics,
        test_path_custom_revert_keeps_a_proved_raw_selector_without_guessing_an_error,
        test_slither_like_operations_map_to_semantic_facts,
        test_slither_defined_operation_dicts_are_covered,
        test_real_slither_operations_project_to_semantic_facts,
        test_function_level_payload_splits_solidity_and_yul_facts,
        test_function_level_payload_preserves_distinct_yul_operations,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
