#!/usr/bin/env python3
"""P1-T2 focused structural-region tests over the frozen SFIR boundary."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import ast
import inspect
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
for item in (ROOT / "legacy_yul", ROOT / "s_seir"):
    text = str(item)
    if text not in sys.path:
        sys.path.insert(0, text)

import s_seir_research_regions as regions_module
from s_seir_research_actions import extract_semantic_actions
from s_seir_research_regions import (
    REGION_PAYLOAD_FIELDS,
    detect_flattened_regions,
    validate_flattened_region,
)


INPUT_FINGERPRINT = "sha256:" + "2" * 64
FUNCTION_ID = "Token.run(uint256)"


def block(block_id: str, *, kind: str = "solidity", terminator: str = "Next", switch=False) -> dict:
    return {
        "block_id": block_id,
        "kind": kind,
        "origin": {"engine": "fixture", "source_span": f"fixture.sol:{block_id}"},
        "stmt_refs": [f"stmt:{block_id}"],
        "terminator": {
            "kind": "Switch" if switch else terminator,
            **({"condition": "alpha", "node_kind": "switch"} if switch else {}),
        },
        "semantic_ids": [],
        "predecessors": [],
        "successors": [],
    }


def edge(index: int, source: str, target: str, kind: str = "next") -> dict:
    return {
        "edge_id": f"edge:{index}", "from": source, "to": target,
        "kind": kind,
        "guard": None if kind == "next" else f"guard:{kind}",
    }


def node(
    semantic_id: str,
    block_id: str | None,
    *,
    kind: str,
    reads=(),
    writes=(),
    source_lang="solidity",
    fact_role="value",
    semantic=None,
    merged=False,
) -> dict:
    placement = (
        {"status": "anchored", "anchor_block": block_id, "operation_order": 1}
        if block_id else {"status": "unanchored", "reason": "fixture"}
    )
    provenance = {
        "anchor_cfg_node": block_id,
        "evidence_cfg_nodes": [block_id] if block_id else [],
    }
    if merged:
        provenance.update({
            "semantic_source": {"overlay_id": f"overlay:{semantic_id}"},
            "merged_semantic_sources": [
                {"overlay_id": f"overlay:{semantic_id}"},
                {"operation_id": f"legacy:{semantic_id}"},
            ],
        })
    result = {
        "semantic_id": semantic_id,
        "origin_id": f"origin:{semantic_id}",
        "operation_id": f"operation:{semantic_id}",
        "origin": "fixture_semantic",
        "source_lang": source_lang,
        "kind": kind,
        "fact_role": fact_role,
        "reads": list(reads),
        "writes": list(writes),
        "stmt_refs": [f"stmt:{semantic_id}"],
        "semantic": dict(semantic or {}),
        "semantic_provenance": provenance,
        "placement": placement,
        "evidence": {"atomic_operation": {"source_span": f"fixture.sol:{semantic_id}"}},
        "fact_ssa": {
            "reads": [{"base_name": value, "value": value} for value in reads],
            "writes": [{"base_name": value, "value": value} for value in writes],
        },
    }
    if kind in {"BranchCondition", "ValueCompute"}:
        result["condition"] = next(iter(reads), "")
        result["rvalue"] = next(iter(reads), "")
    if writes:
        result["lvalue"] = next(iter(writes))
    return result


def action_node(semantic_id: str, block_id: str | None, *, merged=False) -> dict:
    return node(
        semantic_id, block_id, kind="StateWrite", reads=("amount",), writes=("balances",),
        fact_role="effect", merged=merged,
        semantic={
            "operation": "state_write",
            "location": {"kind": "mapping", "access": "balances[user]", "keys": ["user"]},
            "keys": ["user"], "value": "amount",
        },
    )


def finalize_cfg(blocks: list[dict], edges: list[dict], entries=("entry",)) -> dict:
    by_id = {item["block_id"]: item for item in blocks}
    for item in blocks:
        item["predecessors"] = []
        item["successors"] = []
    for item in edges:
        by_id[item["from"]]["successors"].append(item["to"])
        by_id[item["to"]]["predecessors"].append(item["from"])
    return {
        "blocks": blocks,
        "edges": edges,
        "entry_blocks": list(entries),
        "reverse_postorder": {item["block_id"]: index for index, item in enumerate(blocks)},
        "dominance": {},
        "control_dependencies": [],
    }


def payload(blocks: list[dict], edges: list[dict], nodes: list[dict], *, entries=("entry",)) -> dict:
    by_id = {item["block_id"]: item for item in blocks}
    for item in nodes:
        anchor = (item.get("placement") or {}).get("anchor_block")
        if anchor in by_id:
            by_id[anchor]["semantic_ids"].append(item["semantic_id"])
    function = {
        "function_id": FUNCTION_ID,
        "contract": "Token",
        "function": "run",
        "signature": "run(uint256)",
        "declaration": {"name": "run", "full_name": "run(uint256)", "id": "decl:run"},
        "contract_declaration": {"name": "Token", "id": "decl:token"},
        "fact_cfg": finalize_cfg(blocks, edges, entries),
        "fact_ssa": {"definitions": [], "phis": [], "bindings": []},
        "semantic_nodes": nodes,
        "semantic_edges": [],
        "boundary_links": [],
        "path_witnesses": [],
        "diagnostics": [],
    }
    return {
        "schema": "s-seir-semantic-fact-ir/v1",
        "source": "fixture.sol",
        "function_count": 1,
        "semantic_node_count": len(nodes),
        "functions": [function],
    }


def flattened_fixture(
    *,
    switch=True,
    control_name="alpha",
    mixed=False,
    inner_loop=False,
    no_exit=False,
    irreducible=False,
    ambiguous=False,
    merged_action=False,
) -> dict:
    blocks = [
        block("entry"), block("dispatch", switch=switch), block("case_a"),
        block("case_b"), block("fake"), block("exit", terminator="Return"),
    ]
    edges = [
        edge(1, "entry", "dispatch"),
        edge(2, "dispatch", "case_a", "case:1" if switch else "arm:a"),
        edge(3, "dispatch", "case_b", "case:2" if switch else "arm:b"),
        edge(4, "dispatch", "fake", "case:3" if switch else "arm:c"),
        edge(5, "case_a", "dispatch"),
        edge(6, "case_b", "dispatch"),
        edge(7, "fake", "dispatch"),
    ]
    if not no_exit:
        edges.append(edge(8, "dispatch", "exit", "default" if switch else "arm:exit"))
    nodes = [
        node(
            "condition", "dispatch", kind="BranchCondition", reads=(control_name,),
            source_lang="yul" if mixed else "solidity", fact_role="predicate",
            semantic={"context": "switch" if switch else "condition", "expression": control_name},
        ),
        node("set_a", "case_a", kind="ValueCompute", writes=(control_name,), source_lang="yul" if mixed else "solidity"),
        node("set_b", "case_b", kind="ValueCompute", writes=(control_name,), source_lang="solidity"),
        node("set_fake", "fake", kind="ValueCompute", writes=(control_name,), source_lang="yul" if mixed else "solidity"),
        action_node("write_a", "case_a", merged=merged_action),
        action_node("write_b", "case_b"),
        node("return", "exit", kind="Return", fact_role="effect", semantic={"operation": "return", "values": []}),
    ]
    if inner_loop:
        blocks.append(block("loop_body"))
        edges = [item for item in edges if not (item["from"] == "case_b" and item["to"] == "dispatch")]
        edges.extend([edge(20, "case_b", "loop_body"), edge(21, "loop_body", "case_b"), edge(22, "loop_body", "dispatch")])
        nodes.append(node("loop_step", "loop_body", kind="ValueCompute", reads=("counter",), writes=("counter",)))
    if irreducible:
        edges.append(edge(30, "entry", "case_a", "alternate-entry"))
    if ambiguous:
        blocks.append(block("dispatch_two", switch=False))
        nodes.append(node(
            "condition_two", "dispatch_two", kind="BranchCondition", reads=(control_name,),
            fact_role="predicate", semantic={"context": "condition", "expression": control_name},
        ))
        # Place both dispatchers in one SCC; both have three outgoing arms and
        # at least two arms returning through the other dispatcher.
        edges = [item for item in edges if not (item["from"] == "dispatch" and item["to"] == "fake")]
        edges.extend([
            edge(40, "dispatch", "dispatch_two", "arm:c"),
            edge(41, "dispatch_two", "case_a", "arm:a"),
            edge(42, "dispatch_two", "case_b", "arm:b"),
            edge(43, "dispatch_two", "dispatch", "arm:c"),
            edge(44, "dispatch_two", "exit", "arm:exit"),
        ])
    return payload(blocks, edges, nodes)


def run_detector(sfir: dict) -> tuple[dict, dict]:
    actions = extract_semantic_actions(sfir, input_fingerprint=INPUT_FINGERPRINT)
    return actions, detect_flattened_regions(
        sfir, actions, input_fingerprint=INPUT_FINGERPRINT
    )


class PositiveDetectionTests(unittest.TestCase):
    def test_flattened_straight_line_detects_dispatch_state_cases_and_boundaries(self) -> None:
        actions, result = run_detector(flattened_fixture())
        self.assertEqual(1, len(result["regions"]))
        region = result["regions"][0]
        validate_flattened_region(region)
        self.assertEqual(REGION_PAYLOAD_FIELDS, set(region["payload"]))
        self.assertEqual("dispatch", region["payload"]["dispatcher_ref"]["locator"]["block_id"])
        self.assertEqual(4, len(region["payload"]["cases"]))
        self.assertTrue(region["payload"]["control_state_candidates"])
        self.assertEqual({"CFG_EDGE"}, {item["kind"] for item in region["payload"]["entries"]})
        self.assertEqual({"CFG_EDGE"}, {item["kind"] for item in region["payload"]["exits"]})
        self.assertEqual("NOT_RUN", region["status"]["solver"])
        self.assertEqual(
            sorted(item["id"] for item in actions["actions"]),
            result["action_input"]["action_ids"],
        )

    def test_equivalent_multi_branch_flattened_if_else(self) -> None:
        _, result = run_detector(flattened_fixture(switch=False))
        self.assertEqual(1, len(result["regions"]))
        self.assertEqual(4, len(result["regions"][0]["payload"]["cases"]))

    def test_flattened_loop_and_fake_unrelated_block_are_inside_region(self) -> None:
        _, result = run_detector(flattened_fixture(inner_loop=True))
        refs = result["regions"][0]["payload"]["region_cfg_refs"]
        block_ids = {item["locator"]["block_id"] for item in refs}
        self.assertTrue({"dispatch", "case_a", "case_b", "loop_body", "fake"}.issubset(block_ids))

    def test_supported_yul_mixed_switch_uses_unified_sfir(self) -> None:
        sfir = flattened_fixture(mixed=True)
        self.assertEqual({"solidity", "yul"}, {item["source_lang"] for item in sfir["functions"][0]["semantic_nodes"]})
        _, result = run_detector(sfir)
        self.assertEqual(1, len(result["regions"]))

    def test_function_entry_is_recorded_without_incoming_edge(self) -> None:
        sfir = flattened_fixture()
        function = sfir["functions"][0]
        function["fact_cfg"]["edges"] = [
            item for item in function["fact_cfg"]["edges"] if item["from"] != "entry"
        ]
        function["fact_cfg"]["blocks"] = [
            item for item in function["fact_cfg"]["blocks"] if item["block_id"] != "entry"
        ]
        function["fact_cfg"] = finalize_cfg(
            function["fact_cfg"]["blocks"], function["fact_cfg"]["edges"], entries=("dispatch",)
        )
        _, result = run_detector(sfir)
        self.assertEqual("FUNCTION_ENTRY", result["regions"][0]["payload"]["entries"][0]["kind"])


class NegativeAndBoundaryTests(unittest.TestCase):
    def test_ordinary_business_switch_is_not_flattening(self) -> None:
        blocks = [block("entry"), block("switch", switch=True), block("a", terminator="Return"), block("b", terminator="Return"), block("d", terminator="Return")]
        edges = [edge(1, "entry", "switch"), edge(2, "switch", "a", "case:1"), edge(3, "switch", "b", "case:2"), edge(4, "switch", "d", "default")]
        nodes = [
            node("condition", "switch", kind="BranchCondition", reads=("choice",), fact_role="predicate", semantic={"context": "switch"}),
            action_node("write", "a"),
        ]
        actions, result = run_detector(payload(blocks, edges, nodes))
        self.assertFalse(result["regions"])
        self.assertTrue(actions["actions"], "non-flattened actions must remain present")
        self.assertEqual("COMPLETE", result["status"]["completion"])

    def test_ordinary_loop_and_branch_join_are_not_dispatchers(self) -> None:
        loop_blocks = [block("entry"), block("header"), block("body"), block("exit", terminator="Return")]
        loop_edges = [edge(1, "entry", "header"), edge(2, "header", "body", "true"), edge(3, "header", "exit", "false"), edge(4, "body", "header")]
        loop_nodes = [node("condition", "header", kind="BranchCondition", reads=("go",), fact_role="predicate", semantic={"context": "condition"}), action_node("write", "body")]
        _, loop_result = run_detector(payload(loop_blocks, loop_edges, loop_nodes))
        self.assertFalse(loop_result["regions"])

        branch_blocks = [block("entry"), block("branch"), block("left"), block("right"), block("join", terminator="Return")]
        branch_edges = [edge(1, "entry", "branch"), edge(2, "branch", "left", "true"), edge(3, "branch", "right", "false"), edge(4, "left", "join"), edge(5, "right", "join")]
        _, branch_result = run_detector(payload(branch_blocks, branch_edges, [action_node("write", "left")]))
        self.assertFalse(branch_result["regions"])

    def test_ambiguous_or_insufficient_switch_is_explicit_not_negative_success(self) -> None:
        _, ambiguous = run_detector(flattened_fixture(ambiguous=True))
        self.assertFalse(ambiguous["regions"])
        self.assertEqual("PARTIAL", ambiguous["status"]["completion"])
        self.assertTrue(any(item["code"] == "INSUFFICIENT_EVIDENCE" for item in ambiguous["candidate_diagnostics"]))

        sfir = flattened_fixture()
        function = sfir["functions"][0]
        function["semantic_nodes"] = [
            item for item in function["semantic_nodes"] if item["semantic_id"] != "condition"
        ]
        _, insufficient = run_detector(sfir)
        self.assertFalse(insufficient["regions"])
        self.assertTrue(insufficient["candidate_diagnostics"])

    def test_no_exit_and_irreducible_candidates_are_explicitly_unsupported(self) -> None:
        _, no_exit = run_detector(flattened_fixture(no_exit=True))
        self.assertFalse(no_exit["regions"])
        self.assertTrue(any(item["code"] == "UNSUPPORTED" and item["scope"].get("control_shape") == "NO_EXIT" for item in no_exit["candidate_diagnostics"]))

        _, irreducible = run_detector(flattened_fixture(irreducible=True))
        self.assertFalse(irreducible["regions"])
        self.assertTrue(any(item["code"] == "UNSUPPORTED" and item["scope"].get("control_shape") == "MULTI_ENTRY_SCC" for item in irreducible["candidate_diagnostics"]))


class ContractAndStabilityTests(unittest.TestCase):
    def test_action_ids_replacement_and_unresolved_unanchored_are_preserved(self) -> None:
        sfir = flattened_fixture(merged_action=True)
        sfir["functions"][0]["semantic_nodes"].extend([
            action_node("unanchored", None),
            node("future", "fake", kind="FutureEffect", fact_role="effect", semantic={"operation": "future_effect"}),
        ])
        actions = extract_semantic_actions(sfir, input_fingerprint=INPUT_FINGERPRINT)
        before = deepcopy(actions)
        result = detect_flattened_regions(sfir, actions, input_fingerprint=INPUT_FINGERPRINT)
        self.assertEqual(before, actions, "P1-T2 must not mutate or recreate SemanticActions")
        self.assertEqual(sorted(item["id"] for item in actions["actions"]), result["action_input"]["action_ids"])
        self.assertTrue(result["action_input"]["unanchored_action_ids"])
        self.assertTrue(any(item["classification"] == "UNRESOLVED" for item in result["action_input"]["coverage"]))
        merged = next(item for item in actions["actions"] if item["payload"]["replacement_refs"])
        self.assertEqual(1, sum(1 for item in result["action_input"]["action_ids"] if item == merged["id"]))
        observation = next(item for item in result["action_input"]["observations"] if item["id"] == merged["id"])
        self.assertEqual(merged["payload"]["semantic_ref"], observation["semantic_ref"])
        self.assertEqual(merged["payload"]["replacement_refs"], observation["replacement_refs"])
        self.assertEqual(merged["evidence_refs"], observation["evidence_refs"])
        self.assertEqual(merged["status"], observation["status"])
        self.assertNotIn("action_refs", result["regions"][0]["payload"])

    def test_collection_reorder_does_not_change_region_identity(self) -> None:
        first_sfir = flattened_fixture()
        second_sfir = deepcopy(first_sfir)
        function = second_sfir["functions"][0]
        function["fact_cfg"]["blocks"].reverse()
        function["fact_cfg"]["edges"].reverse()
        function["semantic_nodes"].reverse()
        _, first = run_detector(first_sfir)
        _, second = run_detector(second_sfir)
        self.assertEqual(first["regions"][0]["id"], second["regions"][0]["id"])

    def test_detector_does_not_depend_on_control_variable_name(self) -> None:
        _, ordinary = run_detector(flattened_fixture(control_name="alpha"))
        _, renamed = run_detector(flattened_fixture(control_name="totally_unrelated_42"))
        self.assertEqual(1, len(ordinary["regions"]))
        self.assertEqual(1, len(renamed["regions"]))

    def test_frozen_payload_single_artifact_shared_contract_and_no_future_algorithms(self) -> None:
        _, result = run_detector(flattened_fixture())
        region = result["regions"][0]
        self.assertEqual(REGION_PAYLOAD_FIELDS, set(region["payload"]))
        self.assertEqual("erc20-research/flattened-region/v1", region["schema"])
        self.assertFalse(any(item.get("schema", "").startswith("erc20-research/dispatcher") for item in result.values() if isinstance(item, dict)))
        source = inspect.getsource(regions_module)
        tree = ast.parse(source)
        imported_modules = {
            alias.name
            for item in ast.walk(tree) if isinstance(item, (ast.Import, ast.ImportFrom))
            for alias in item.names
        }
        self.assertFalse(any("oracle" in item or "parser" in item or item == "z3" for item in imported_modules))
        declared_classes = {item.name for item in ast.walk(tree) if isinstance(item, ast.ClassDef)}
        self.assertFalse({"ArtifactEnvelope", "EvidenceRecord", "SourceRef", "Dispatcher", "Case"}.intersection(declared_classes))
        function_names = {item.name.lower() for item in ast.walk(tree) if isinstance(item, ast.FunctionDef)}
        self.assertFalse({"fixed_point", "transfer", "join", "solve", "def_use", "reaching_definitions"}.intersection(function_names))
        self.assertTrue(all(item["status"]["solver"] == "NOT_RUN" for item in result["regions"] + result["evidence_records"]))

    def test_invalid_schema_is_structured_unsupported(self) -> None:
        actions, _ = run_detector(flattened_fixture())
        result = detect_flattened_regions({"schema": "yul-object/v1"}, actions)
        self.assertFalse(result["regions"])
        self.assertEqual("UNSUPPORTED", result["candidate_diagnostics"][0]["code"])
        self.assertEqual("NOT_RUN", result["status"]["solver"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
