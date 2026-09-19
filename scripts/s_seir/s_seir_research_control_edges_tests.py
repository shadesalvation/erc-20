#!/usr/bin/env python3
"""P1-T5 semantic acceptance tests for candidate control edges."""
from __future__ import annotations

import ast
from copy import deepcopy
import inspect
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
for item in (ROOT / "legacy_yul", ROOT / "s_seir"):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

import s_seir_research_control_edges as c
from s_seir_research_abstract_domain import build_contract
from s_seir_research_actions import extract_semantic_actions
from s_seir_research_contracts import ContractValidationError, canonical_json
from s_seir_research_propagation import propagate_regions
from s_seir_research_propagation_tests import typed_fixture
from s_seir_research_regions import detect_flattened_regions
from s_seir_research_regions_tests import (
    INPUT_FINGERPRINT,
    action_node,
    block,
    edge,
    flattened_fixture,
    node,
    payload,
)


LIMITS = {
    "max_processed_items": 10000,
    "max_cache_entries": 10000,
    "max_contributions": 100000,
    "max_requeues": 100000,
}


def prepare(sfir, *, k=2, budget=None, propagation_sfir=None):
    actions = extract_semantic_actions(sfir, input_fingerprint=INPUT_FINGERPRINT)
    regions = detect_flattened_regions(
        sfir, actions, input_fingerprint=INPUT_FINGERPRINT
    )
    contract = build_contract(
        k=k, finite_cap=4, input_fingerprint=INPUT_FINGERPRINT
    )["contract"]
    propagation = propagate_regions(
        propagation_sfir if propagation_sfir is not None else sfir,
        regions,
        contract,
        resource_limits=budget or LIMITS,
    )
    result = c.reconstruct_candidate_control_edges(
        sfir, actions, regions, propagation
    )
    return actions, regions, propagation, result


def aid(actions, semantic_id):
    return next(
        item["id"] for item in actions["actions"]
        if item["payload"]["semantic_ref"]["locator"]["semantic_id"] == semantic_id
    )


def endpoint_name(endpoint, actions):
    if endpoint["kind"] != "ACTION":
        return endpoint["kind"]
    by_id = {
        item["id"]: item["payload"]["semantic_ref"]["locator"]["semantic_id"]
        for item in actions["actions"]
    }
    return by_id[endpoint["action_ref"]]


def pairs(result, actions):
    return {
        (
            endpoint_name(item["payload"]["from"], actions),
            endpoint_name(item["payload"]["to"], actions),
        )
        for item in result["edges"]
    }


def straight_fixture(*semantic_ids):
    blocks = [block("entry")]
    nodes = [action_node(value, "entry") for value in semantic_ids]
    blocks[0]["terminator"] = {"kind": "Return"}
    return payload(blocks, [], nodes)


def chain_fixture():
    blocks = [
        block("entry"), block("fake"), block("b"), block("c", terminator="Return")
    ]
    edges = [edge(1, "entry", "fake"), edge(2, "fake", "b"), edge(3, "b", "c")]
    nodes = [action_node("a", "entry"), action_node("b", "b"), action_node("c", "c")]
    return payload(blocks, edges, nodes)


def branch_join_fixture():
    blocks = [
        block("entry"), block("branch"), block("left"), block("right"),
        block("join", terminator="Return"),
    ]
    edges = [
        edge(1, "entry", "branch"), edge(2, "branch", "left", "true"),
        edge(3, "branch", "right", "false"), edge(4, "left", "join"),
        edge(5, "right", "join"),
    ]
    nodes = [
        action_node("a", "entry"), action_node("b", "left"),
        action_node("c", "right"), action_node("d", "join"),
    ]
    return payload(blocks, edges, nodes)


class BasicCompressionTests(unittest.TestCase):
    def test_entry_action_exit_and_exact_frozen_payload(self):
        actions, regions, propagation, result = prepare(straight_fixture("a"))
        self.assertEqual({("ENTRY", "a"), ("a", "EXIT")}, pairs(result, actions))
        self.assertEqual([], regions["regions"])
        for candidate in result["edges"]:
            c.validate_candidate_edge(candidate)
            self.assertEqual(c.PAYLOAD_FIELDS, set(candidate["payload"]))
            self.assertEqual(c.SCHEMA, candidate["schema"])
            self.assertEqual("UNRESOLVED", candidate["payload"]["feasibility"])
            self.assertEqual("NOT_RUN", candidate["status"]["solver"])
        c.validate_candidate_collection(
            result,
            actions=actions["actions"], regions=regions["regions"],
            propagations=propagation["propagations"],
        )

    def test_fake_blocks_compress_and_no_transitive_a_to_c(self):
        actions, _, _, result = prepare(chain_fixture())
        observed = pairs(result, actions)
        self.assertTrue({("ENTRY", "a"), ("a", "b"), ("b", "c"), ("c", "EXIT")}.issubset(observed))
        self.assertNotIn(("a", "c"), observed)
        a_to_b = next(
            item for item in result["edges"]
            if endpoint_name(item["payload"]["from"], actions) == "a"
            and endpoint_name(item["payload"]["to"], actions) == "b"
        )
        evidence = next(item for item in result["evidence_records"] if item["id"] in a_to_b["evidence_refs"])
        blocks = evidence["result"]["carrier"]["carrier_block_refs"]
        self.assertIn("fake", {item["locator"]["block_id"] for item in blocks})

    def test_identical_payload_occurrences_are_distinct_endpoints(self):
        sfir = straight_fixture("same_one", "same_two")
        actions, _, _, result = prepare(sfir)
        self.assertEqual(2, len(actions["actions"]))
        self.assertNotEqual(aid(actions, "same_one"), aid(actions, "same_two"))
        self.assertIn(("same_one", "same_two"), pairs(result, actions))


class BranchJoinLoopTests(unittest.TestCase):
    def test_branch_and_join_keep_all_first_next_candidates(self):
        actions, _, _, result = prepare(branch_join_fixture())
        observed = pairs(result, actions)
        self.assertTrue({("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")}.issubset(observed))
        self.assertNotIn(("a", "d"), observed)

    def test_same_endpoints_on_distinct_normal_paths_are_not_collapsed(self):
        blocks = [
            block("entry"), block("branch"), block("left"), block("right"),
            block("join", terminator="Return"),
        ]
        edges = [
            edge(1, "entry", "branch"), edge(2, "branch", "left", "true"),
            edge(3, "branch", "right", "false"), edge(4, "left", "join"),
            edge(5, "right", "join"),
        ]
        actions, _, _, result = prepare(payload(
            blocks, edges, [action_node("a", "entry"), action_node("d", "join")]
        ))
        alternatives = [
            item for item in result["edges"]
            if endpoint_name(item["payload"]["from"], actions) == "a"
            and endpoint_name(item["payload"]["to"], actions) == "d"
        ]
        self.assertEqual(2, len(alternatives))
        self.assertEqual(2, len({item["id"] for item in alternatives}))

    def test_simple_loop_back_edge_and_finite_termination(self):
        blocks = [block("entry"), block("relay")]
        edges = [edge(1, "entry", "relay"), edge(2, "relay", "entry")]
        actions, _, _, result = prepare(payload(blocks, edges, [action_node("a", "entry")]))
        self.assertIn(("a", "a"), pairs(result, actions))
        self.assertLess(len(result["edges"]), 10)

    def test_flattened_dispatcher_round_trip_emits_back_candidates(self):
        sfir, regions, contract = typed_fixture(k=2)
        actions = extract_semantic_actions(sfir, input_fingerprint=INPUT_FINGERPRINT)
        propagation = propagate_regions(sfir, regions, contract, resource_limits=LIMITS)
        result = c.reconstruct_candidate_control_edges(sfir, actions, regions, propagation)
        observed = pairs(result, actions)
        self.assertTrue(any(left == right and left not in {"ENTRY", "EXIT"} for left, right in observed))
        self.assertTrue(any(item["payload"]["region_ref"] for item in result["edges"]))
        self.assertLess(len(result["edges"]), 200)


class ContextAlternativeTests(unittest.TestCase):
    def test_same_endpoints_different_context_or_path_remain_distinct(self):
        sfir, regions, contract = typed_fixture(k=2)
        actions = extract_semantic_actions(sfir, input_fingerprint=INPUT_FINGERPRINT)
        propagation = propagate_regions(sfir, regions, contract, resource_limits=LIMITS)
        result = c.reconstruct_candidate_control_edges(sfir, actions, regions, propagation)
        groups = {}
        for item in result["edges"]:
            key = canonical_json([item["payload"]["from"], item["payload"]["to"]])
            groups.setdefault(key, []).append(item)
        alternatives = next(values for values in groups.values() if len(values) > 1)
        self.assertEqual(len(alternatives), len({item["id"] for item in alternatives}))
        self.assertTrue(all(item["payload"]["context_ref"] is not None for item in alternatives))

    def test_collection_reorder_preserves_ids_and_accounting(self):
        sfir, regions, contract = typed_fixture(k=2)
        actions = extract_semantic_actions(sfir, input_fingerprint=INPUT_FINGERPRINT)
        propagation = propagate_regions(sfir, regions, contract, resource_limits=LIMITS)
        first = c.reconstruct_candidate_control_edges(sfir, actions, regions, propagation)
        shuffled_sfir = deepcopy(sfir)
        shuffled_sfir["functions"].reverse()
        function = shuffled_sfir["functions"][0]
        function["fact_cfg"]["blocks"].reverse()
        function["fact_cfg"]["edges"].reverse()
        function["semantic_nodes"].reverse()
        shuffled_actions, shuffled_regions, shuffled_propagation = deepcopy(actions), deepcopy(regions), deepcopy(propagation)
        shuffled_actions["actions"].reverse()
        shuffled_regions["regions"].reverse()
        for region in shuffled_regions["regions"]:
            for key in ("region_cfg_refs", "control_state_candidates", "cases", "entries", "exits"):
                region["payload"][key].reverse()
        shuffled_propagation["propagations"].reverse()
        for item in shuffled_propagation["propagations"]:
            item["payload"]["node_context_states"].reverse()
        second = c.reconstruct_candidate_control_edges(
            shuffled_sfir, shuffled_actions, shuffled_regions, shuffled_propagation
        )
        self.assertEqual([item["id"] for item in first["edges"]], [item["id"] for item in second["edges"]])
        self.assertEqual(first["accounting"], second["accounting"])

    def test_unknown_state_does_not_delete_structural_candidates(self):
        actions, regions, propagation, result = prepare(flattened_fixture(), k=1)
        artifact = propagation["propagations"][0]
        self.assertGreater(artifact["payload"]["convergence_evidence"]["unknown_node_context_count"], "0")
        self.assertTrue(result["edges"])
        self.assertTrue(any(item["payload"]["region_ref"] for item in result["edges"]))
        self.assertTrue(all(item["payload"]["feasibility"] == "UNRESOLVED" for item in result["edges"]))

    def test_context_history_is_evidence_not_real_successor(self):
        actions, _, _, result = prepare(flattened_fixture(), k=2)
        candidate = next(item for item in result["edges"] if item["payload"]["context_ref"])
        record = next(item for item in result["evidence_records"] if item["id"] in candidate["evidence_refs"])
        self.assertEqual("NOT_RUN", record["result"]["solver_outcome"])
        self.assertIn("over-approximated", " ".join(record["assumptions"]))
        self.assertNotEqual("FEASIBLE", candidate["payload"]["feasibility"])


class TerminationStatusTests(unittest.TestCase):
    def test_fixed_point_known_and_unknown_both_remain_unresolved_feasibility(self):
        sfir, regions, contract = typed_fixture(k=1)
        actions = extract_semantic_actions(sfir, input_fingerprint=INPUT_FINGERPRINT)
        propagation = propagate_regions(sfir, regions, contract, resource_limits=LIMITS)
        known = c.reconstruct_candidate_control_edges(sfir, actions, regions, propagation)
        self.assertEqual("FIXED_POINT", propagation["propagations"][0]["payload"]["termination"])
        self.assertTrue(all(item["payload"]["feasibility"] == "UNRESOLVED" for item in known["edges"]))
        _, _, unknown_propagation, unknown = prepare(flattened_fixture(), k=1)
        self.assertEqual("FIXED_POINT", unknown_propagation["propagations"][0]["payload"]["termination"])
        self.assertTrue(unknown["edges"])
        self.assertTrue(all(item["payload"]["feasibility"] == "UNRESOLVED" for item in unknown["edges"]))

    def test_resource_limit_outputs_covered_candidates_and_frontier(self):
        actions, _, propagation, result = prepare(
            flattened_fixture(), k=1,
            budget={**LIMITS, "max_processed_items": 1},
        )
        self.assertEqual("RESOURCE_LIMIT", propagation["propagations"][0]["payload"]["termination"])
        self.assertTrue(result["edges"])
        account = result["accounting"][0]
        self.assertTrue(account["partial"])
        self.assertTrue(account["truncated"])
        self.assertTrue(account["propagation_resource_frontiers"])
        self.assertEqual("PARTIAL", result["status"]["completion"])

    def test_unsupported_and_error_are_not_empty_complete(self):
        sfir = flattened_fixture()
        unsupported_source = {"schema": "other/v1", "functions": []}
        _, _, unsupported, unsupported_result = prepare(
            sfir, propagation_sfir=unsupported_source
        )
        self.assertEqual("UNSUPPORTED", unsupported["propagations"][0]["payload"]["termination"])
        self.assertEqual("PARTIAL", unsupported_result["status"]["completion"])
        self.assertIn("PROPAGATION_UNSUPPORTED", canonical_json(unsupported_result["unresolved_scopes"]))

        broken = deepcopy(sfir)
        broken["functions"][0]["fact_cfg"]["blocks"].append(
            deepcopy(broken["functions"][0]["fact_cfg"]["blocks"][0])
        )
        _, _, error, error_result = prepare(sfir, propagation_sfir=broken)
        self.assertEqual("ERROR", error["propagations"][0]["payload"]["termination"])
        self.assertEqual("PARTIAL", error_result["status"]["completion"])
        self.assertIn("PROPAGATION_ERROR", canonical_json(error_result["unresolved_scopes"]))

    def test_upstream_partial_is_not_whitewashed(self):
        sfir = chain_fixture()
        sfir["functions"][0]["semantic_nodes"].append(action_node("unanchored", None))
        actions, _, _, result = prepare(sfir)
        self.assertEqual("PARTIAL", actions["status"]["completion"])
        self.assertEqual("PARTIAL", result["status"]["completion"])
        self.assertIn("UNANCHORED_ACTION", canonical_json(result["unresolved_scopes"]))


class NormalAndMixedTests(unittest.TestCase):
    def test_action_free_function_has_structural_entry_exit_not_failure(self):
        sfir = payload([block("entry", terminator="Return")], [], [])
        actions, regions, propagation, result = prepare(sfir)
        self.assertEqual([], actions["actions"])
        self.assertEqual([], regions["regions"])
        self.assertEqual([], propagation["propagations"])
        self.assertEqual({("ENTRY", "EXIT")}, pairs(result, actions))
        self.assertEqual("COMPLETE", result["status"]["completion"])

    def test_no_region_straight_line_and_branch_join_are_reconstructed(self):
        actions, regions, propagation, result = prepare(branch_join_fixture())
        self.assertEqual([], regions["regions"])
        self.assertEqual([], propagation["propagations"])
        self.assertTrue(result["edges"])
        self.assertTrue(all(item["payload"]["region_ref"] is None for item in result["edges"]))
        self.assertTrue(all(item["payload"]["context_ref"] is None for item in result["edges"]))
        self.assertGreater(result["accounting"][0]["normal_scope_candidate_count"], "0")
        self.assertEqual({("a", "b"), ("a", "c")}, {pair for pair in pairs(result, actions) if pair[0] == "a"})

    def test_mixed_boundary_uses_region_then_normal_provenance(self):
        actions, regions, _, result = prepare(flattened_fixture(), k=2)
        self.assertTrue(regions["regions"])
        account = result["accounting"][0]
        self.assertGreater(account["mixed_boundary_candidate_count"], "0")
        self.assertGreater(account["normal_scope_candidate_count"], "0")
        return_id = aid(actions, "return")
        return_edges = [item for item in result["edges"] if item["payload"]["from"].get("action_ref") == return_id]
        self.assertEqual({"EXIT"}, {item["payload"]["to"]["kind"] for item in return_edges})
        self.assertTrue(all(item["payload"]["region_ref"] is None for item in return_edges))


class EndpointAmbiguityAndBoundaryTests(unittest.TestCase):
    def test_same_block_reliable_sfir_order_not_action_id_order(self):
        sfir = straight_fixture("z_first", "a_second")
        actions, _, _, result = prepare(sfir)
        self.assertIn(("ENTRY", "z_first"), pairs(result, actions))
        self.assertIn(("z_first", "a_second"), pairs(result, actions))
        self.assertNotIn(("a_second", "z_first"), pairs(result, actions))

    def test_same_block_missing_order_retains_explicit_alternatives(self):
        sfir = straight_fixture("a", "b")
        sfir["functions"][0]["fact_cfg"]["blocks"][0]["semantic_ids"] = []
        actions, _, _, result = prepare(sfir)
        observed = pairs(result, actions)
        self.assertTrue({("ENTRY", "a"), ("ENTRY", "b"), ("a", "b"), ("b", "a")}.issubset(observed))
        self.assertEqual("PARTIAL", result["status"]["completion"])
        self.assertIn("AMBIGUOUS_LOCAL_ACTION_ORDER", canonical_json(result["unresolved_scopes"]))

    def test_unanchored_action_is_unresolved_not_guessed(self):
        sfir = straight_fixture("a")
        sfir["functions"][0]["semantic_nodes"].append(action_node("lost", None))
        actions, _, _, result = prepare(sfir)
        lost = aid(actions, "lost")
        self.assertFalse(any(
            endpoint.get("action_ref") == lost
            for item in result["edges"]
            for endpoint in (item["payload"]["from"], item["payload"]["to"])
        ))
        self.assertIn(lost, canonical_json(result["unresolved_scopes"]))

    def test_unrefined_condition_is_only_source_provenance(self):
        sfir = branch_join_fixture()
        actions, _, _, result = prepare(sfir)
        guarded = [item for item in result["edges"] if item["payload"]["guard_ref"] is not None]
        self.assertTrue(guarded)
        self.assertTrue(all(item["payload"]["guard_ref"]["kind"].startswith("UNREFINED_") for item in guarded))
        serialized = canonical_json(result)
        self.assertNotIn("erc20-research/guard/", serialized)
        self.assertNotIn('"feasibility":"FEASIBLE"', serialized)
        self.assertNotIn('"feasibility":"INFEASIBLE"', serialized)


class QueryIsolationAndImmutabilityTests(unittest.TestCase):
    def test_query_and_accounting_are_direct_consumer_views(self):
        actions, regions, _, result = prepare(flattened_fixture(), k=2)
        region_id = regions["regions"][0]["id"]
        flattened = c.query_candidates(result, region_ref=region_id)
        normal = c.query_candidates(result, region_ref=None)
        self.assertTrue(flattened and normal)
        source = flattened[0]["payload"]["from"]
        self.assertTrue(c.query_candidates(result, source_endpoint=source))
        context = flattened[0]["payload"]["context_ref"]
        self.assertTrue(c.query_candidates(result, context_ref=context))
        self.assertEqual(1, len(c.candidate_accounting(result, function_ref=result["accounting"][0]["function_ref"])))
        self.assertEqual(len(actions["actions"]), int(result["accounting"][0]["input_action_occurrence_count"]))
        self.assertEqual(canonical_json(result), c.serialize_candidate_result(result))

    def test_inputs_are_not_mutated_and_evidence_closes(self):
        sfir, regions, contract = typed_fixture(k=1)
        actions = extract_semantic_actions(sfir, input_fingerprint=INPUT_FINGERPRINT)
        propagation = propagate_regions(sfir, regions, contract, resource_limits=LIMITS)
        before = deepcopy([sfir, actions, regions, propagation])
        result = c.reconstruct_candidate_control_edges(sfir, actions, regions, propagation)
        self.assertEqual(before, [sfir, actions, regions, propagation])
        c.validate_candidate_collection(
            result, actions=actions["actions"], regions=regions["regions"],
            propagations=propagation["propagations"],
        )
        records = {item["id"]: item for item in result["evidence_records"]}
        self.assertTrue(all(item["evidence_refs"][0] in records for item in result["edges"]))
        self.assertTrue(all(records[item["evidence_refs"][0]]["source_refs"] for item in result["edges"]))

    def test_validator_rejects_feasibility_and_identity_tampering(self):
        actions, regions, propagation, result = prepare(straight_fixture("a"))
        broken = deepcopy(result["edges"][0])
        broken["payload"]["feasibility"] = "FEASIBLE"
        with self.assertRaises(ContractValidationError):
            c.validate_candidate_edge(broken)
        broken_result = deepcopy(result)
        broken_result["evidence_records"][0]["result"]["identity_basis"]["path_alternative"]["origin"] = "tampered"
        with self.assertRaises(ContractValidationError):
            c.validate_candidate_collection(
                broken_result, actions=actions["actions"], regions=regions["regions"],
                propagations=propagation["propagations"],
            )

    def test_production_isolation_and_no_second_formal_graph(self):
        source = inspect.getsource(c)
        tree = ast.parse(source)
        imports = {
            alias.name
            for item in ast.walk(tree) if isinstance(item, (ast.Import, ast.ImportFrom))
            for alias in item.names
        }
        self.assertFalse(any(
            forbidden in name.lower()
            for name in imports
            for forbidden in ("z3", "solver", "oracle", "benchmark", "evaluator")
        ))
        schemas = {
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and node.value.startswith("erc20-research/")
        }
        self.assertEqual({c.SCHEMA}, schemas)
        class_names = {item.name.lower() for item in ast.walk(tree) if isinstance(item, ast.ClassDef)}
        self.assertFalse({"candidategraph", "controlgraph", "guard", "symbolicexecutor"}.intersection(class_names))
        lowered = source.lower()
        for forbidden in ("import z3", "sat/unsat", "defuseindex", "valueflowgraph", "statetransitionir"):
            self.assertNotIn(forbidden, lowered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
