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
    assert line == "success = staticcall token.balanceOf(account);"


if __name__ == "__main__":
    test_c_like_and_text_are_final_sfir_only()
    test_dot_carries_semantic_statement_and_guard()
    test_switch_is_rendered_as_c_like_control()
    test_external_call_keeps_low_level_call_kind_as_high_semantics()
    print("semantic fact render tests passed")
