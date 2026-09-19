#!/usr/bin/env python3
"""Focused regressions for completed Yul predicate recovery."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for item in (ROOT / "legacy_yul", ROOT / "s_seir"):
    text = str(item)
    if text not in sys.path:
        sys.path.insert(0, text)

from s_seir_model import EffectNode, SemanticOverlay
from s_seir_predicate_lifter import PredicateLifter


@dataclass
class Value:
    name: str
    type_string: str


class TypeEnv:
    def __init__(self) -> None:
        self.values = {
            "to": Value("to", "address"),
            "balance": Value("balance", "uint256"),
        }

    def lookup(self, name: str):
        return self.values.get(name)

    def state_var_by_slot(self, slot: str):
        return self.values.get("balance") if slot == "balance.slot" else None


def effect(effect_id: str, kind: str, attrs: dict) -> EffectNode:
    return EffectNode(effect_id, kind, ["asm_s_1"], attrs)


def test_branch_predicate_replaces_generic_condition_trace() -> None:
    branch = effect("eff_branch", "Branch", {
        "language": "yul",
        "condition": "iszero(lt(value, sload(balance.slot)))",
        "cfg_node_id": 7,
    })
    generic = SemanticOverlay("ov_generic", "ExpressionNormalization", ["eff_branch"], ["asm_s_1"], {
        "context": "condition", "condition_normalized": "((value < sload(balance.slot)) == 0)",
    })
    trace = SemanticOverlay("ov_trace", "EvaluationStep", ["eff_step"], ["asm_s_1"], {})
    require = SemanticOverlay("ov_require", "RequireOverlay", ["eff_revert"], ["asm_s_1"], {
        "condition": "(value >= balance)",
        "nearest_condition": "iszero(lt(value, sload(balance.slot)))",
    })
    out = PredicateLifter().lift(TypeEnv(), [branch, effect("eff_step", "EvaluationStep", {"parent_effect": "eff_branch"})], [generic, trace, require])
    predicates = [item for item in out if item.kind == "Predicate"]
    assert len(predicates) == 1
    assert predicates[0].attrs["expression"] == "(value >= balance)"
    assert predicates[0].attrs["status"] == "resolved"
    assert not any(item.kind in {"ExpressionNormalization", "EvaluationStep"} for item in out)
    assert next(item for item in out if item.kind == "RequireOverlay").attrs["condition"] == "(value < balance)"


def test_call_status_and_returndata_predicate_are_high_level() -> None:
    call = effect("eff_call", "StaticCall", {"result": "success", "cfg_node_id": 5})
    branch = effect("eff_branch", "Branch", {
        "language": "yul",
        "condition": "iszero(and(success, eq(returndatasize(), 0x20)))",
        "cfg_node_id": 6,
    })
    out = PredicateLifter().lift(TypeEnv(), [call, branch], [])
    predicate = next(item for item in out if item.kind == "Predicate")
    assert predicate.attrs["expression"] == "!((success && (returnDataSize() == 0x20)))"
    assert predicate.attrs["status"] == "resolved"


def test_nested_state_read_uses_branch_evaluation_def_use() -> None:
    branch = effect("eff_branch", "Branch", {
        "language": "yul",
        "condition": "lt(value, sload(mappingSlot))",
        "cfg_node_id": 9,
    })
    read = effect("eff_read", "StorageRead", {
        "value": "__eval_read",
        "evaluation_step": {
            "parent_effect": "eff_branch",
            "temp": "__eval_read",
            "expression": "sload(mappingSlot)",
        },
        "cfg_node_id": 9,
    })
    mapping_read = SemanticOverlay("ov_read", "MappingRead", ["eff_read"], ["asm_s_1"], {
        "target": "__eval_read", "access": "balances[owner]",
    })
    out = PredicateLifter().lift(TypeEnv(), [branch, read], [mapping_read])
    predicate = next(item for item in out if item.kind == "Predicate")
    assert predicate.attrs["expression"] == "(value < balances[owner])"
    assert predicate.attrs["status"] == "resolved"


def test_loop_predicate_comes_from_cfg_true_edge() -> None:
    control = {
        "blocks": [{
            "block_id": "bb_asm1_n3",
            "stmts": ["asm_s_loop"],
            "terminator": {"kind": "Branch", "node_kind": "loop-condition"},
        }],
        "edges": [{"from": "bb_asm1_n3", "to": "bb_asm1_n4", "kind": "true: lt(i, accounts.length)"}],
    }
    out = PredicateLifter().lift(TypeEnv(), [], [], control)
    predicate = next(item for item in out if item.kind == "Predicate")
    assert predicate.attrs["expression"] == "(i < accounts.length)"
    assert predicate.attrs["semantic_anchor_cfg_node"] == "bb_asm1_n3"


def test_typed_predicate_preserves_ast_not_rendered_strings() -> None:
    from copy import deepcopy
    from s_seir_yul_eval_order import YulEvaluationOrder
    def call(name, *args):
        return {"nodeType": "YulFunctionCall", "functionName": {"nodeType": "YulIdentifier", "name": name}, "arguments": list(args)}
    expr = call("iszero", call("lt", {"nodeType": "YulIdentifier", "name": "balance"},
                              {"nodeType": "YulLiteral", "kind": "number", "value": "0x10"}))
    evaluation = YulEvaluationOrder("stmt").materialize(expr)
    branch = effect("eff_typed", "Branch", {"language": "yul", "condition": "iszero(lt(balance, 0x10))", "condition_evaluation": evaluation})
    tree = PredicateLifter._typed_evaluation(branch, "predicate")
    assert tree["resolution_status"] == "resolved"
    assert tree["expression"]["op"] == "iszero"
    comparison = tree["expression"]["operands"][0]
    assert comparison["operands"][1]["literal"] == "16"
    assert comparison["operands"][0]["identity"]["ast_node"]["name"] == "balance"
    altered = deepcopy(branch)
    for step in altered.attrs["condition_evaluation"]["steps"]:
        step["raw_args"] = ["arbitrary display" for _ in step["raw_args"]]
        step["expression_normalized"] = "arbitrary display"
    assert PredicateLifter._typed_evaluation(altered, "predicate") == tree
    altered.attrs["condition_evaluation"]["steps"][0].pop("argument_nodes")
    assert PredicateLifter._typed_evaluation(altered, "predicate")["resolution_status"] == "unsupported"
    # Exercise normal overlay creation, including serialization which drops
    # low-level transport and default status fields elsewhere in the pipeline.
    recovered = next(p for p in PredicateLifter().lift(TypeEnv(), [branch], []) if p.kind == "Predicate")
    assert recovered.to_dict()["attrs"]["typed_predicate"]["resolution_status"] == "resolved"


if __name__ == "__main__":
    for test in (
        test_typed_predicate_preserves_ast_not_rendered_strings,
        test_branch_predicate_replaces_generic_condition_trace,
        test_call_status_and_returndata_predicate_are_high_level,
        test_nested_state_read_uses_branch_evaluation_def_use,
        test_loop_predicate_comes_from_cfg_true_edge,
    ):
        test()
        print(f"PASS {test.__name__}")
