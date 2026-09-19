#!/usr/bin/env python3
"""P1-T4 semantic acceptance tests over real P1-T2/P1-T3 handoffs."""
from __future__ import annotations

from copy import deepcopy
import ast
import inspect
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
for item in (ROOT / "legacy_yul", ROOT / "s_seir"):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

import s_seir_research_propagation as p
from s_seir_research_abstract_domain import build_contract, candidate_key
from s_seir_research_contracts import ContractValidationError, canonical_json
from s_seir_research_regions import detect_flattened_regions
from s_seir_research_regions_tests import (
    INPUT_FINGERPRINT,
    action_node,
    block,
    edge,
    finalize_cfg,
    flattened_fixture,
    node,
    run_detector,
)
from s_seir_semantic_fact_adapter import SlitherFactAdapter


def limits(**overrides):
    result = {
        "max_processed_items": 10000,
        "max_cache_entries": 10000,
        "max_contributions": 100000,
        "max_requeues": 100000,
    }
    result.update(overrides)
    return result


def domain(k=1, cap=4):
    return build_contract(
        k=k, finite_cap=cap, input_fingerprint=INPUT_FINGERPRINT
    )["contract"]


def literal(value, typ="uint8"):
    return {"kind": "Constant", "text": str(value), "type": typ, "is_constant": True}


def variable(typ="uint8"):
    return {
        "kind": "LocalIRVariable", "text": "alpha_1", "name": "alpha_1",
        "base_name": "alpha", "type": typ, "is_constant": False,
    }


def assignment(value):
    return {"kind": "Assignment", "lvalue": variable(), "rvalue": literal(value)}


def increment():
    return {
        "kind": "Binary", "lvalue": variable(), "variable_left": variable(),
        "variable_right": literal(1), "operator": "+", "checked": False,
    }


def _adapt_fact(fact, operation):
    adapted = SlitherFactAdapter().facts_from_operations(
        {"function": "run", "contract": "Token"}, [operation]
    )[0].to_dict()
    for key in ("kind", "semantic", "evidence", "rvalue", "lvalue"):
        fact[key] = adapted.get(key)
    for item in fact["fact_ssa"]["writes"]:
        if item.get("base_name") == "alpha":
            item["binding_id"] = "decl:42:value"
    if operation["kind"] == "Binary":
        fact["fact_ssa"]["reads"] = [{
            "binding_id": "decl:42:value", "base_name": "alpha", "value": "alpha_1",
        }]


def typed_fixture(*, k=1, cap=4, extra_operations=None, source=None):
    sfir = deepcopy(source or flattened_fixture())
    function = sfir["functions"][0]
    for fact in function["semantic_nodes"]:
        for direction in ("reads", "writes"):
            for item in fact["fact_ssa"][direction]:
                if item.get("base_name") == "alpha":
                    item["binding_id"] = "decl:42:value"
    function["fact_ssa"]["bindings"] = [{
        "binding_id": "decl:42:value", "type": "uint8", "declaration_id": 42,
    }]
    operations = {"set_a": assignment(1), "set_b": assignment(2), "set_fake": assignment(3)}
    operations.update(extra_operations or {})
    by_id = {fact["semantic_id"]: fact for fact in function["semantic_nodes"]}
    for semantic_id, operation in operations.items():
        _adapt_fact(by_id[semantic_id], operation)
    _, upstream = run_detector(sfir)
    contract = domain(k=k, cap=cap)
    return sfir, upstream, contract


def run(sfir, upstream, contract, budget=None):
    return p.propagate_regions(
        sfir, upstream, contract, resource_limits=budget or limits()
    )


def artifact(result):
    if len(result["propagations"]) != 1:
        raise AssertionError("fixture expected exactly one propagation")
    return result["propagations"][0]


def ref(region, block_id):
    refs = list(region["payload"]["region_cfg_refs"])
    refs.extend(item["to_ref"] for item in region["payload"]["exits"] if item["to_ref"])
    return next(item for item in refs if item["locator"]["block_id"] == block_id)


def arm(region, target):
    return next(item["edge_ref"] for item in region["payload"]["cases"] if item["target_ref"]["locator"]["block_id"] == target)


def contexts_at(propagation, region, block_id):
    return p.node_context_states(propagation, node_ref=ref(region, block_id))


def branch_join_fixture():
    sfir = flattened_fixture()
    function = sfir["functions"][0]
    blocks = function["fact_cfg"]["blocks"]
    edges = [item for item in function["fact_cfg"]["edges"] if not (item["from"] == "case_a" and item["to"] == "dispatch")]
    blocks.extend([block("left"), block("right"), block("join")])
    edges.extend([
        edge(50, "case_a", "left", "true"), edge(51, "case_a", "right", "false"),
        edge(52, "left", "join"), edge(53, "right", "join"), edge(54, "join", "dispatch"),
    ])
    left = node("set_left", "left", kind="ValueCompute", writes=("alpha",))
    right = node("set_right", "right", kind="ValueCompute", writes=("alpha",))
    function["semantic_nodes"].extend([left, right])
    function["fact_cfg"] = finalize_cfg(blocks, edges)
    by_block = {item["block_id"]: item for item in blocks}
    for fact in function["semantic_nodes"]:
        anchor = (fact.get("placement") or {}).get("anchor_block")
        if anchor in by_block and fact["semantic_id"] not in by_block[anchor]["semantic_ids"]:
            by_block[anchor]["semantic_ids"].append(fact["semantic_id"])
    return typed_fixture(extra_operations={"set_left": assignment(10), "set_right": assignment(20)}, source=sfir)


def growing_loop_fixture():
    sfir = flattened_fixture()
    function = sfir["functions"][0]
    blocks = function["fact_cfg"]["blocks"]
    edges = [item for item in function["fact_cfg"]["edges"] if not (item["from"] == "case_a" and item["to"] == "dispatch")]
    blocks.append(block("grow"))
    edges.extend([edge(60, "case_a", "grow"), edge(61, "grow", "grow"), edge(62, "grow", "dispatch")])
    grow = node("increment", "grow", kind="ValueCompute", reads=("alpha",), writes=("alpha",))
    function["semantic_nodes"].append(grow)
    function["fact_cfg"] = finalize_cfg(blocks, edges)
    by_block = {item["block_id"]: item for item in blocks}
    for fact in function["semantic_nodes"]:
        anchor = (fact.get("placement") or {}).get("anchor_block")
        if anchor in by_block and fact["semantic_id"] not in by_block[anchor]["semantic_ids"]:
            by_block[anchor]["semantic_ids"].append(fact["semantic_id"])
    return typed_fixture(k=1, cap=2, extra_operations={"set_a": assignment(0), "increment": increment()}, source=sfir)


class ArtifactAndDeterminismTests(unittest.TestCase):
    def test_exact_artifact_contract_termination_and_evidence(self):
        sfir, upstream, contract = typed_fixture()
        result = run(sfir, upstream, contract)
        output = artifact(result)
        self.assertEqual(p.SCHEMA, output["schema"])
        self.assertEqual(p.PAYLOAD_FIELDS, set(output["payload"]))
        self.assertEqual(p.TERMINATIONS, {"FIXED_POINT", "RESOURCE_LIMIT", "UNSUPPORTED", "ERROR"})
        self.assertEqual("FIXED_POINT", output["payload"]["termination"])
        self.assertTrue(output["payload"]["convergence_evidence"]["worklist"]["drained"])
        p.validate_propagation(output, region=upstream["regions"][0], contract=contract)
        p.validate_result_evidence(result)
        self.assertEqual(1, len(result["evidence_records"]))
        broken = deepcopy(output)
        broken["payload"]["region_ref"] = "flattened-region:wrong"
        with self.assertRaises(ContractValidationError):
            p.validate_propagation(broken, region=upstream["regions"][0], contract=contract)
        broken = deepcopy(output)
        broken["payload"]["convergence_evidence"]["worklist"]["frontier"] = [{}]
        with self.assertRaises(ContractValidationError):
            p.validate_propagation(broken, region=upstream["regions"][0], contract=contract)

    def test_repeat_and_collection_reorder_have_identical_artifact_and_frontier(self):
        sfir, upstream, contract = typed_fixture(k=2)
        first = run(sfir, upstream, contract)
        self.assertEqual(first["propagations"], run(sfir, upstream, contract)["propagations"])
        shuffled_sfir, shuffled_upstream = deepcopy(sfir), deepcopy(upstream)
        function = shuffled_sfir["functions"][0]
        function["fact_cfg"]["blocks"].reverse()
        function["fact_cfg"]["edges"].reverse()
        function["semantic_nodes"].reverse()
        region = shuffled_upstream["regions"][0]
        for key in ("region_cfg_refs", "control_state_candidates", "cases", "entries", "exits"):
            region["payload"][key].reverse()
        reordered = run(shuffled_sfir, shuffled_upstream, contract)
        self.assertEqual(first["propagations"], reordered["propagations"])
        small = limits(max_processed_items=1)
        self.assertEqual(
            run(sfir, upstream, contract, small)["propagations"],
            run(shuffled_sfir, shuffled_upstream, contract, small)["propagations"],
        )

    def test_identity_uses_region_domain_and_config_not_iterations(self):
        sfir, upstream, contract = typed_fixture()
        first = artifact(run(sfir, upstream, contract))
        limited = artifact(run(sfir, upstream, contract, limits(max_processed_items=1)))
        self.assertNotEqual(first["id"], limited["id"])
        self.assertNotIn("iteration", canonical_json(first["payload"]["node_context_states"]))
        self.assertNotIn("pop_count", canonical_json(first["payload"]["node_context_states"]))


class TransferJoinAndLoopTests(unittest.TestCase):
    def test_ordinary_facts_are_identity_and_supported_transfer_reuses_domain(self):
        sfir, upstream, contract = typed_fixture()
        output = artifact(run(sfir, upstream, contract))
        region = upstream["regions"][0]
        case_entries = contexts_at(output, region, "case_a")
        self.assertTrue(case_entries)
        dispatch_after = [
            item for item in contexts_at(output, region, "dispatch")
            if item["context"]["elements"] and item["context"]["elements"][-1]["arm_ref"] == arm(region, "case_a")
        ]
        key = candidate_key(region, region["payload"]["control_state_candidates"][0])
        self.assertTrue(any(item["state"]["values"][key]["values"] == ["1"] for item in dispatch_after))
        stats = output["payload"]["convergence_evidence"]["statistics"]
        self.assertGreater(int(stats["identity_facts"]), 0)
        self.assertGreater(int(stats["local_transfers"]), 0)

    def test_missing_typed_semantics_is_unknown_but_reaches_fixed_point(self):
        sfir = flattened_fixture()
        _, upstream = run_detector(sfir)
        output = artifact(run(sfir, upstream, domain()))
        self.assertEqual("FIXED_POINT", output["payload"]["termination"])
        self.assertGreater(int(output["payload"]["convergence_evidence"]["unknown_node_context_count"]), 0)
        self.assertEqual("PARTIAL", output["status"]["completion"])
        self.assertNotIn("UNSUPPORTED", {item["code"] for item in output["status"]["diagnostics"]})

    def test_same_context_branch_join_uses_p1_t3_join(self):
        sfir, upstream, contract = branch_join_fixture()
        output = artifact(run(sfir, upstream, contract))
        region = upstream["regions"][0]
        joined = contexts_at(output, region, "join")
        target_context = next(item for item in joined if item["context"]["elements"][-1]["arm_ref"] == arm(region, "case_a"))
        key = candidate_key(region, region["payload"]["control_state_candidates"][0])
        self.assertEqual(["10", "20"], target_context["state"]["values"][key]["values"])
        self.assertGreater(int(output["payload"]["convergence_evidence"]["statistics"]["joins"]), 0)

    def test_loop_back_edge_reenqueues_until_natural_fixed_point(self):
        sfir, upstream, contract = growing_loop_fixture()
        output = artifact(run(sfir, upstream, contract))
        region = upstream["regions"][0]
        grow = contexts_at(output, region, "grow")
        target = next(item for item in grow if item["context"]["elements"][-1]["arm_ref"] == arm(region, "case_a"))
        key = candidate_key(region, region["payload"]["control_state_candidates"][0])
        self.assertEqual("TOP", target["state"]["values"][key]["tag"])
        stats = output["payload"]["convergence_evidence"]["statistics"]
        self.assertGreaterEqual(int(stats["requeues"]), 2)
        self.assertGreater(int(stats["stable_contributions"]), 0)
        self.assertEqual("FIXED_POINT", output["payload"]["termination"])
        self.assertTrue(output["payload"]["convergence_evidence"]["worklist"]["drained"])

    def test_missing_in_block_order_is_conservative_unknown_not_engine_success_value(self):
        sfir = flattened_fixture()
        function = sfir["functions"][0]
        second = node("set_a_second", "case_a", kind="ValueCompute", writes=("alpha",))
        function["semantic_nodes"].append(second)
        # Deliberately do not place the second relevant fact in block.semantic_ids.
        sfir, upstream, contract = typed_fixture(
            source=sfir, extra_operations={"set_a": assignment(1), "set_a_second": assignment(4)}
        )
        output = artifact(run(sfir, upstream, contract))
        self.assertEqual("FIXED_POINT", output["payload"]["termination"])
        self.assertIn("stable in-block semantic order", canonical_json(output["status"]["diagnostics"]))
        self.assertGreater(int(output["payload"]["convergence_evidence"]["unknown_node_context_count"]), 0)


class ContextAndTerminationTests(unittest.TestCase):
    def test_k1_k2_partition_truncation_and_observed_arm_only(self):
        results = {}
        for k in (1, 2):
            sfir, upstream, contract = typed_fixture(k=k)
            output = artifact(run(sfir, upstream, contract))
            region = upstream["regions"][0]
            entries = output["payload"]["node_context_states"]
            self.assertLessEqual(max(len(item["context"]["elements"]) for item in entries), k)
            results[k] = (output, region)
        self.assertNotEqual(
            len(results[1][0]["payload"]["node_context_states"]),
            len(results[2][0]["payload"]["node_context_states"]),
        )
        output, region = results[2]
        case_a_context = next(item["context"] for item in contexts_at(output, region, "case_a") if len(item["context"]["elements"]) == 1)
        returned = p.query_node_context_state(output, ref(region, "dispatch"), case_a_context)
        self.assertIsNotNone(returned, "ordinary return edge must preserve context")
        for item in contexts_at(output, region, "case_a"):
            self.assertEqual(arm(region, "case_a"), item["context"]["elements"][-1]["arm_ref"])
        claims = output["payload"]["convergence_evidence"]["semantic_claims"]
        self.assertEqual({"real_successor": False, "guard": False, "feasibility": False, "solver_outcome": "NOT_RUN"}, claims)

    def test_unreached_is_absent_and_not_unknown(self):
        sfir, upstream, contract = typed_fixture()
        output = artifact(run(sfir, upstream, contract))
        region = upstream["regions"][0]
        bogus = deepcopy(ref(region, "dispatch"))
        bogus["locator"]["block_id"] = "never-reached"
        self.assertIsNone(p.query_node_context_state(output, bogus, {"k": "1", "elements": []}))
        self.assertTrue(all(item["state"]["reached"] for item in output["payload"]["node_context_states"]))

    def test_small_deterministic_budget_is_resource_limit_with_frontier(self):
        sfir, upstream, contract = typed_fixture()
        output = artifact(run(sfir, upstream, contract, limits(max_processed_items=1)))
        self.assertEqual("RESOURCE_LIMIT", output["payload"]["termination"])
        self.assertFalse(output["payload"]["convergence_evidence"]["worklist"]["drained"])
        self.assertTrue(output["payload"]["convergence_evidence"]["worklist"]["frontier"])
        self.assertEqual("max_processed_items", output["payload"]["resource_budget"]["cutoff"]["counter"])
        self.assertTrue(output["payload"]["node_context_states"])

    def test_engine_unsupported_and_invariant_error_are_distinct(self):
        sfir, upstream, contract = typed_fixture()
        unsupported = deepcopy(sfir)
        unsupported["schema"] = "other/v1"
        unsupported_output = artifact(run(unsupported, upstream, contract))
        self.assertEqual("UNSUPPORTED", unsupported_output["payload"]["termination"])
        self.assertIn("UNSUPPORTED", {item["code"] for item in unsupported_output["status"]["diagnostics"]})

        broken = deepcopy(sfir)
        broken["functions"][0]["fact_cfg"]["blocks"].append(
            deepcopy(broken["functions"][0]["fact_cfg"]["blocks"][0])
        )
        error_output = artifact(run(broken, upstream, contract))
        self.assertEqual("ERROR", error_output["payload"]["termination"])
        self.assertIn("ERROR", {item["code"] for item in error_output["status"]["diagnostics"]})

    def test_contract_violation_is_explicit_exception(self):
        sfir, upstream, contract = typed_fixture()
        bad = deepcopy(contract)
        bad["payload"]["k"] = "9"
        with self.assertRaises(ContractValidationError):
            run(sfir, upstream, bad)
        with self.assertRaises(ContractValidationError):
            p.normalize_resource_limits({"max_processed_items": 1})


class IntakeIntegrationAndIsolationTests(unittest.TestCase):
    def test_no_region_and_no_consumable_region_create_no_artifact(self):
        sfir = flattened_fixture()
        function = sfir["functions"][0]
        function["fact_cfg"]["edges"] = [
            item for item in function["fact_cfg"]["edges"]
            if item["from"] in {"entry", "dispatch"}
        ]
        _, no_region = run_detector(sfir)
        result = run(sfir, no_region, domain())
        self.assertEqual("NO_REGION", result["intake_outcome"])
        self.assertEqual([], result["propagations"])

        actions, _ = run_detector(flattened_fixture())
        unsupported = detect_flattened_regions({"schema": "yul-object/v1"}, actions)
        result = run(flattened_fixture(), unsupported, domain())
        self.assertEqual("NO_CONSUMABLE_REGION", result["intake_outcome"])
        self.assertEqual([], result["propagations"])
        self.assertEqual("PARTIAL", result["status"]["completion"])

    def test_upstream_partial_fixed_point_is_not_whitewashed(self):
        sfir = flattened_fixture(merged_action=True)
        sfir["functions"][0]["semantic_nodes"].extend([
            action_node("unanchored", None),
            node("future", "fake", kind="FutureEffect", fact_role="effect"),
        ])
        sfir, upstream, contract = typed_fixture(source=sfir)
        output = artifact(run(sfir, upstream, contract))
        self.assertEqual("FIXED_POINT", output["payload"]["termination"])
        self.assertEqual("PARTIAL", output["status"]["completion"])
        self.assertEqual("PARTIAL", output["payload"]["convergence_evidence"]["inherited_status"]["completion"])

    def test_direct_real_handoff_does_not_mutate_inputs_and_consumer_query_works(self):
        sfir, upstream, contract = typed_fixture()
        before = deepcopy([sfir, upstream, contract])
        result = run(sfir, upstream, contract)
        self.assertEqual(before, [sfir, upstream, contract])
        output = artifact(result)
        all_entries = p.node_context_states(output)
        first = all_entries[0]
        self.assertEqual(first, p.query_node_context_state(output, first["node_ref"], first["context"]))
        self.assertEqual({"node_ref", "context", "state", "status", "evidence_refs"}, set(first))

    def test_cache_is_node_context_and_different_contexts_never_merge(self):
        sfir, upstream, contract = typed_fixture(k=2)
        output = artifact(run(sfir, upstream, contract))
        region = upstream["regions"][0]
        dispatch = contexts_at(output, region, "dispatch")
        keys = {canonical_json([item["node_ref"], item["context"]]) for item in dispatch}
        self.assertEqual(len(dispatch), len(keys))
        histories = {canonical_json(item["context"]) for item in dispatch}
        self.assertGreater(len(histories), 1)

    def test_production_isolation_and_only_one_new_formal_artifact(self):
        source = inspect.getsource(p)
        tree = ast.parse(source)
        imports = {item.module or "" for item in ast.walk(tree) if isinstance(item, ast.ImportFrom)}
        self.assertIn("s_seir_research_abstract_domain", imports)
        self.assertIn("s_seir_research_contracts", imports)
        self.assertIn("s_seir_research_regions", imports)
        self.assertFalse(any(any(token in name.lower() for token in ("oracle", "evaluator", "benchmark", "z3", "solver", "parser")) for name in imports))
        schemas = {
            item.value for item in ast.walk(tree)
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
            and item.value.startswith("erc20-research/")
        }
        self.assertEqual({p.SCHEMA}, schemas)
        calls = {
            item.func.id for item in ast.walk(tree)
            if isinstance(item, ast.Call) and isinstance(item.func, ast.Name)
        }
        self.assertFalse({"detect_flattened_regions", "extract_semantic_actions", "build_contract"}.intersection(calls))
        self.assertEqual(1, sum(
            isinstance(item, ast.Call) and isinstance(item.func, ast.Name)
            and item.func.id == "artifact_envelope" for item in ast.walk(tree)
        ))
        imported = {
            alias.name for item in ast.walk(tree) if isinstance(item, ast.ImportFrom)
            and item.module == "s_seir_research_abstract_domain" for alias in item.names
        }
        self.assertTrue({
            "validate_contract", "consume_regions", "local_transfer", "join_states",
            "update_context", "stabilized", "state_leq", "validate_state",
            "normalize_state", "state_status",
        }.issubset(imported))


if __name__ == "__main__":
    unittest.main(verbosity=2)
