#!/usr/bin/env python3
"""Focused regressions for the final-SFIR presentation layer."""
from __future__ import annotations

import tempfile
from copy import deepcopy
from pathlib import Path

from s_seir_semantic_fact_render import (
    render_fact_cfg_dot,
    render_fact_cfg_text,
    render_sfir_c_like,
    render_sfir_node_c_like,
    write_fact_cfg_dot_files,
)


FUNCTION = {
    "function_id": "Token.move(address,uint256)",
    "function": "move",
    "signature": "move(address,uint256)",
    "fact_cfg": {
        "reverse_postorder": {"entry": 0, "write": 1, "revert": 2},
        "blocks": [
            {"block_id": "entry", "kind": "yul", "terminator": {"kind": "Branch"}, "semantic_ids": ["require", "predicate"]},
            {"block_id": "write", "kind": "yul", "terminator": {"kind": "Return"}, "semantic_ids": ["state_write", "event", "return"]},
            {"block_id": "revert", "kind": "yul", "terminator": {"kind": "Revert"}, "semantic_ids": []},
        ],
        "edges": [
            {"edge_id": "e1", "from": "entry", "to": "write", "kind": "true", "guard": "(amount <= balance)"},
            {"edge_id": "e2", "from": "entry", "to": "revert", "kind": "false", "guard": "!((amount <= balance))"},
        ],
    },
    "semantic_nodes": [
        {"semantic_id": "require", "kind": "Require", "condition": "(amount <= balance)", "semantic": {"condition": "(amount <= balance)"}},
        {"semantic_id": "predicate", "kind": "BranchCondition", "rvalue": "(amount <= balance)", "semantic": {"expression": "(amount <= balance)"}},
        {"semantic_id": "state_write", "kind": "StateWrite", "lvalue": "_balances[to]", "rvalue": "(balance + amount)", "semantic": {"access": "_balances[to]", "value": "(balance + amount)"}},
        {"semantic_id": "event", "kind": "EventEmit", "semantic": {"event": "Transfer", "args": ["msg.sender", "to", "amount"]}},
        {"semantic_id": "return", "kind": "Return", "rvalue": "true", "semantic": {"operation": "return"}},
    ],
    "fact_ssa": {"phis": []},
}


def payload() -> dict:
    return {"schema": "s-seir-semantic-fact-ir/v1", "source": "fixture.sol", "functions": [FUNCTION]}


def test_c_like_and_text_are_final_sfir_only() -> None:
    c_like = render_sfir_c_like(payload())
    cfg_text = render_fact_cfg_text(payload())
    assert "_balances[to] = (balance + amount);" in c_like
    assert "if ((amount <= balance)) goto B1; else goto B2;" in c_like
    assert "emit Transfer(msg.sender, to, amount);" in cfg_text
    assert "mload(" not in c_like + cfg_text
    assert "sstore(" not in c_like + cfg_text


def test_dot_carries_semantic_statement_and_guard() -> None:
    dot = render_fact_cfg_dot(FUNCTION)
    assert "_balances[to] = (balance + amount);" in dot
    assert "true: (amount <= balance)" in dot
    assert "false: !((amount <= balance))" in dot
    with tempfile.TemporaryDirectory() as directory:
        paths = write_fact_cfg_dot_files(payload(), Path(directory))
        assert len(paths) == 1 and paths[0].exists()


def test_switch_is_rendered_as_c_like_control() -> None:
    function = deepcopy(FUNCTION)
    function["fact_cfg"]["blocks"][0]["semantic_ids"] = ["predicate"]
    function["fact_cfg"]["blocks"][0]["terminator"] = {"kind": "Switch"}
    function["fact_cfg"]["edges"] = [
        {"edge_id": "e1", "from": "entry", "to": "write", "kind": "case: 0", "guard": "(mode == 0)"},
        {"edge_id": "e2", "from": "entry", "to": "revert", "kind": "default", "guard": "!((mode == 0))"},
    ]
    function["semantic_nodes"][1]["rvalue"] = "mode"
    function["semantic_nodes"][1]["semantic"] = {"expression": "mode"}
    output = render_sfir_c_like({"functions": [function]})
    assert "switch (mode) {" in output
    assert "case 0: goto B1;" in output
    assert "default: goto B2;" in output


def test_external_call_keeps_low_level_call_kind_as_high_semantics() -> None:
    line = render_sfir_node_c_like({
        "kind": "ExternalCall",
        "lvalue": "success",
        "semantic": {"target": "token", "call_kind": "staticcall", "selector_signature": "balanceOf(address)", "arguments": ["account"]},
    })
    assert line == "success = externalStaticCall token.balanceOf(account);"


def test_sseir_high_level_rendering_is_shared_by_c_like_cfg_and_dot() -> None:
    function = deepcopy(FUNCTION)
    function["fact_cfg"]["blocks"] = [{
        "block_id": "entry", "kind": "yul", "terminator": {},
        "semantic_ids": ["abi", "call", "hash", "raw_return", "binding", "unknown"],
    }]
    function["fact_cfg"]["edges"] = []
    function["fact_cfg"]["reverse_postorder"] = {"entry": 0}
    function["semantic_nodes"] = [
        {"semantic_id": "abi", "kind": "AbiEncode", "lvalue": "payload", "semantic": {"builtin_signature": "abi.encodeWithSelector(bytes4,...)", "arguments": ["0x12345678", "account"]}},
        {"semantic_id": "call", "kind": "LowLevelCall", "lvalue": "ok", "semantic": {"target": "token", "call_kind": "staticcall", "encoded_payload": "payload"}},
        {"semantic_id": "hash", "kind": "HashCompute", "lvalue": "digest", "semantic": {"builtin_signature": "keccak256(bytes)", "arguments": ["data"]}},
        {"semantic_id": "raw_return", "kind": "Return", "rvalue": "returnRawAbiWord(digest)", "semantic": {"operation": "raw_return_data"}},
        {"semantic_id": "binding", "kind": "ValueCompute", "lvalue": "ref", "rvalue": "opaqueStorageLocation", "semantic": {"operation": "storage_pointer_binding"}},
        {"semantic_id": "unknown", "kind": "UnmodeledSSeirOverlay", "semantic": {"overlay_kind": "Future", "available_fields": ["x"]}},
    ]
    input_payload = {"functions": [function]}
    c_like = render_sfir_c_like(input_payload)
    cfg_text = render_fact_cfg_text(input_payload)
    dot = render_fact_cfg_dot(function)
    for rendered in (c_like, cfg_text, dot):
        assert "abi.encodeWithSelector(0x12345678, account)" in rendered
        assert "externalStaticCall(token, payload)" in rendered
        assert "keccak256(data)" in rendered
        assert "returnRawAbiWord(digest);" in rendered
        assert "ref = opaqueStorageLocation;" in rendered
        assert "unmodeled S-SEIR overlay: Future" in rendered
        assert "mload(" not in rendered and "mstore(" not in rendered


def test_remaining_sseir_high_semantics_render_without_yul_leakage() -> None:
    function = deepcopy(FUNCTION)
    function["fact_cfg"] = {"reverse_postorder": {"entry": 0}, "blocks": [{"block_id": "entry", "kind": "yul", "terminator": {}, "semantic_ids": ["event", "precompile", "memory", "revert", "raw_revert", "return"]}], "edges": []}
    function["semantic_nodes"] = [
        {"semantic_id": "event", "kind": "EventEmit", "semantic": {"event": "Transfer", "args": ["from", "to", "amount"], "topics": ["topic0", "from", "to"]}},
        {"semantic_id": "precompile", "kind": "PrecompileCall", "semantic": {"native_projection": {"solidity_like": "bytes32 digest = sha256(abi.encodePacked(data));"}}},
        {"semantic_id": "memory", "kind": "MemoryObjectWrite", "semantic": {"struct_object": "pair", "field_updates": [{"field": {"name": "right"}, "new_value_normalized": "amount"}]}},
        {"semantic_id": "revert", "kind": "Revert", "semantic": {"source_object": "reason"}},
        {"semantic_id": "raw_revert", "kind": "Revert", "semantic": {"selector": "0x08c379a0"}},
        {"semantic_id": "return", "kind": "Return", "rvalue": ["left", "right"], "semantic": {"values": ["left", "right"]}},
        {"semantic_id": "nested", "kind": "LocalFunctionDefinition", "lvalue": "addOrLeave", "placement": {"status": "nested_definition"}, "semantic": {"name": "addOrLeave", "parameters": ["a", "b"], "returns": ["sum"], "body": [{"kind": "Assignment", "target": "sum", "value": "(a + b)"}, {"kind": "Leave", "values": ["sum"]}]}},
    ]
    payload_value = {"functions": [function]}
    rendered_values = [render_sfir_c_like(payload_value), render_fact_cfg_text(payload_value), render_fact_cfg_dot(function)]
    for rendered in rendered_values:
        assert "emit Transfer(from, to, amount); /* topics: topic0, from, to */" in rendered
        assert "sha256(abi.encodePacked(data))" in rendered
        assert "pair.right = amount;" in rendered
        assert "revertBytes(reason);" in rendered
        assert "revertRawSelector(0x08c379a0);" in rendered
        assert "return (left, right);" in rendered
        assert "function addOrLeave(a, b) returns (sum)" in rendered
        assert "mload(" not in rendered and "mstore(" not in rendered


def test_nested_completed_yul_semantics_render_as_c_like_without_fallback() -> None:
    function = deepcopy(FUNCTION)
    function["fact_cfg"] = {"reverse_postorder": {"entry": 0}, "blocks": [{"block_id": "entry", "kind": "yul", "terminator": {}, "semantic_ids": []}], "edges": []}
    function["semantic_nodes"] = [{
        "semantic_id": "nested", "kind": "LocalFunctionDefinition", "lvalue": "classify",
        "placement": {"status": "nested_definition"},
        "semantic": {
            "name": "classify", "parameters": [{"name": "x", "type": "uint256"}],
            "returns": [{"name": "r", "type": "uint256"}],
            "body": [
                {"kind": "ValueAssign", "targets": ["r", "tmp"], "value": "x"},
                {"kind": "If", "condition": "(x > 0)", "body": [{"kind": "Expression", "expression": "touch(x)"}]},
                {"kind": "Switch", "expression": "x", "cases": [
                    {"value": "0", "body": [{"kind": "ControlTransfer", "operation": "break"}]},
                    {"value": "default", "body": [{"kind": "ReturnFromLocalFunction"}]},
                ]},
                {"kind": "For", "pre": [{"kind": "ValueAssign", "targets": ["i"], "value": "0"}], "condition": "(i < x)", "body": [{"kind": "Expression", "expression": "touch(i)"}], "post": [{"kind": "ControlTransfer", "operation": "continue"}]},
            ],
        },
    }]
    rendered_values = [render_sfir_c_like({"functions": [function]}), render_fact_cfg_text({"functions": [function]}), render_fact_cfg_dot(function)]
    for rendered in rendered_values:
        assert "r = x;" in rendered and "tmp = x;" in rendered
        assert "switch (x) {" in rendered and "case 0:" in rendered and "default:" in rendered
        assert "while ((i < x)) {" in rendered and "return;" in rendered
        assert "local semantic" not in rendered and "mload(" not in rendered and "mstore(" not in rendered


def test_memory_object_rendering_keeps_only_proved_high_level_fields() -> None:
    array_line = render_sfir_node_c_like({
        "kind": "MemoryObjectConstruct", "lvalue": "items", "rvalue": "new uint256[](2)",
        "semantic": {"element_write_pattern": "cursor_based", "elements": ["left", "right"]},
    })
    assert array_line == "items = new uint256[](2); /* recovered cursor_based element values: left, right; indices unresolved */"
    struct_line = render_sfir_node_c_like({
        "kind": "MemoryObjectWrite", "semantic": {"struct_object": "pair", "fields": [{"field": "left", "value": "amount"}]},
    })
    assert struct_line == "pair.left = amount;"


if __name__ == "__main__":
    test_c_like_and_text_are_final_sfir_only()
    test_dot_carries_semantic_statement_and_guard()
    test_switch_is_rendered_as_c_like_control()
    test_external_call_keeps_low_level_call_kind_as_high_semantics()
    test_sseir_high_level_rendering_is_shared_by_c_like_cfg_and_dot()
    test_remaining_sseir_high_semantics_render_without_yul_leakage()
    test_nested_completed_yul_semantics_render_as_c_like_without_fallback()
    test_memory_object_rendering_keeps_only_proved_high_level_fields()
    print("semantic fact render tests passed")
