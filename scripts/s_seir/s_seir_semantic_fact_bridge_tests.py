#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
for item in (ROOT / "legacy_yul", ROOT / "s_seir"):
    text = str(item)
    if text not in sys.path:
        sys.path.insert(0, text)

from s_seir_semantic_fact_bridge import FunctionSemanticInput, SemanticFactBridge


MAX_PATH = "base && !(P)"
ORDINARY_PATH = "base && P"


def block(block_id: str, node_id: int) -> dict:
    return {
        "block_id": block_id,
        "kind": "yul",
        "stmts": [],
        "attrs": {"node_id": node_id},
    }


def effect(effect_id: str, kind: str, node_id: int, paths: list[str]) -> dict:
    return {
        "effect_id": effect_id,
        "kind": kind,
        "stmt_refs": [effect_id],
        "attrs": {"cfg_node_id": node_id, "path_states": paths},
    }


def fact(
    fact_id: str,
    kind: str,
    node: str,
    order: int,
    effect_id: str,
    condition: str,
    *,
    candidate: bool = False,
) -> dict:
    evidence = {"overlay": f"ov_{effect_id}", "effects": [effect_id]}
    if candidate:
        evidence["candidate"] = {"condition": condition}
    return {
        "fact_id": fact_id,
        "kind": kind,
        "source_lang": "yul",
        "function_id": "Token.transferFrom(address,address,uint256)",
        "cfg_nodes": [node],
        "condition": condition,
        "evidence": evidence,
        "operation_id": fact_id,
        "order": {"operation_order": order},
    }


def test_path_instances_remain_siblings_and_successor_is_split() -> None:
    control = {
        "blocks": [block("b0", 0), block("b1", 1), block("b2", 2), block("b3", 3), block("b4", 4), block("b5", 5), block("b6", 6)],
        "edges": [
            {"from": "b0", "to": "b1", "kind": "next"},
            {"from": "b1", "to": "b2", "kind": "true: P"},
            {"from": "b1", "to": "b3", "kind": "false: !(P)"},
            {"from": "b2", "to": "b4", "kind": "next"},
            {"from": "b3", "to": "b4", "kind": "next"},
            {"from": "b4", "to": "b5", "kind": "next"},
            {"from": "b5", "to": "b6", "kind": "next"},
        ],
    }
    effects = [
        effect("eff_write", "StorageWrite", 2, [ORDINARY_PATH]),
        effect("eff_event", "EventLog", 5, [MAX_PATH, ORDINARY_PATH]),
        effect("eff_ok", "ValueDef", 6, [MAX_PATH, ORDINARY_PATH]),
    ]
    facts = [
        fact("write", "StateWrite", "b2", 10, "eff_write", ORDINARY_PATH),
        fact("event_max", "EventEmit", "b5", 20, "eff_event", MAX_PATH, candidate=True),
        fact("event_ordinary", "EventEmit", "b5", 20, "eff_event", ORDINARY_PATH, candidate=True),
        fact("ok", "ValueCompute", "b6", 30, "eff_ok", "base"),
    ]

    result = SemanticFactBridge().merge_function_facts(
        FunctionSemanticInput("Token.transferFrom(address,address,uint256)", control, effects),
        [],
        facts,
    )

    by_id = {item["fact_id"]: item for item in result}
    assert by_id["event_max"]["path_id"] == "eff_event:path_1"
    assert by_id["event_ordinary"]["path_id"] == "eff_event:path_2"
    assert "event_max" not in by_id["event_ordinary"]["control_predecessors"]
    assert "event_ordinary" not in by_id["event_max"]["control_predecessors"]

    ok_facts = [item for item in result if item["fact_id"].startswith("ok::eff_ok")]
    assert len(ok_facts) == 2
    by_path = {item["path_condition"]: item for item in ok_facts}
    assert by_path[MAX_PATH]["condition"] == MAX_PATH
    assert by_path[ORDINARY_PATH]["condition"] == ORDINARY_PATH
    assert by_path[MAX_PATH]["control_predecessors"] == ["event_max"]
    assert by_path[ORDINARY_PATH]["control_predecessors"] == ["event_ordinary"]


def test_require_keeps_lifted_guard_separate_from_revert_path() -> None:
    control = {"blocks": [block("b0", 0)], "edges": []}
    effects = [effect("eff_revert", "Revert", 0, [MAX_PATH, ORDINARY_PATH])]
    require_fact = fact("require", "Require", "b0", 10, "eff_revert", "success_guard")
    result = SemanticFactBridge().merge_function_facts(
        FunctionSemanticInput("Token.transferFrom(address,address,uint256)", control, effects),
        [],
        [require_fact],
    )

    assert len(result) == 2
    assert {item["condition"] for item in result} == {"success_guard"}
    assert {item["path_condition"] for item in result} == {MAX_PATH, ORDINARY_PATH}


if __name__ == "__main__":
    test_path_instances_remain_siblings_and_successor_is_split()
    print("PASS test_path_instances_remain_siblings_and_successor_is_split")
    test_require_keeps_lifted_guard_separate_from_revert_path()
    print("PASS test_require_keeps_lifted_guard_separate_from_revert_path")
