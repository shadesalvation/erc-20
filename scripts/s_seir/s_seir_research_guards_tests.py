#!/usr/bin/env python3
"""P1-T7 direct handoff, seal, comparison, partition and isolation tests."""
from __future__ import annotations

import ast
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "s_seir"))
sys.path.insert(0, str(ROOT))

from s_seir_research_actions import extract_semantic_actions
from s_seir_research_contracts import ContractValidationError, canonical_json, content_digest
from s_seir_research_local_refinement import BOOL, term, typed_type
import s_seir_research_guards as g
from experiments.evaluators.module1 import evaluate_case, endpoint_projection

EVIDENCE = ROOT / "docs" / "task_reports" / "P1-T6_evidence"


def persisted():
    refinement = json.loads((EVIDENCE / "refinement_result.json").read_text())
    handoff = json.loads((EVIDENCE / "handoff_input.json").read_text())
    actions = extract_semantic_actions(handoff["sfir"], input_fingerprint=refinement["input_fingerprint"])
    return refinement, actions


def symbol(ref, typ="uint8"):
    return term("symbol", typed_type(typ), symbol_ref=ref)


def lit(value, typ="uint8"):
    return term("literal", typed_type(typ), literal=str(value) if typ != "bool" else value)


def guard_with_expression(pre, expression):
    value = deepcopy(pre)
    value["payload"]["expression"] = expression
    refs = set()

    def visit(t):
        if t["op"] == "symbol":
            refs.add(t["symbol_ref"])
        for child in t["operands"]:
            visit(child)
    visit(expression)
    value["payload"]["symbol_bindings"] = [b for b in pre["payload"]["symbol_bindings"] if b["symbol_ref"] in refs]
    return g.seal_guard(value)


class Stub:
    name = "stub"
    version = "1"
    options = {"test": True}

    def __init__(self, outcome):
        self.outcome = outcome

    def solve(self, assertions, timeout_ms):
        return {"outcome": self.outcome, "reason": self.outcome.lower(),
                "smt2": "(check-sat)", "side_conditions": [], "bit_widths": []}


class DirectHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.refinement, cls.actions = persisted()

    def test_persisted_no_refinement_rerun_and_lineage(self):
        with patch("s_seir_research_local_refinement.refine_candidates", side_effect=AssertionError("rerun")):
            result = g.build_module1_result(self.refinement, self.actions)
        self.assertEqual(self.actions["actions"], result["payload"]["actions"])
        original = {x["candidate_id"]: x for x in self.refinement["refinements"]}
        for edge in g.query_module1_edges(result):
            row = original[edge["id"]]
            for key in ("candidate_id", "from", "to", "region_ref", "context_ref", "feasibility"):
                self.assertEqual(row["edge"]["payload"][key], edge["payload"][key])
            guard = next(x for x in result["payload"]["guards"] if x["id"] == edge["payload"]["guard_ref"]["artifact_ref"])
            self.assertEqual(row["guard"]["id"], guard["extensions"]["precanonical_guard_ref"])
            self.assertIn(row["feasibility_evidence_ref"], edge["extensions"].values())
        self.assertEqual({"FEASIBLE": 1, "INFEASIBLE": 1, "UNRESOLVED": 2},
                         {key: len(result["payload"][name]) for key, name in g.PARTITIONS.items()})
        self.assertEqual("PARTIAL", result["status"]["completion"])

    def test_deterministic_serialization_and_accounting(self):
        first = g.build_module1_result(self.refinement, self.actions)
        r2, a2 = deepcopy(self.refinement), deepcopy(self.actions)
        r2["refinements"].reverse()
        a2["actions"].reverse()
        self.assertEqual(g.serialize_module1_result(first), g.serialize_module1_result(g.build_module1_result(r2, a2)))
        bad = deepcopy(self.refinement)
        bad["refinements"].pop()
        with self.assertRaises(ContractValidationError):
            g.build_module1_result(bad, self.actions)

    def test_upstream_diagnostics_are_conserved(self):
        result = g.build_module1_result(self.refinement, self.actions)
        for source in (self.refinement["status"], self.refinement["upstream_status"],
                       self.refinement["propagation_status"], self.actions["status"]):
            for diagnostic in source["diagnostics"]:
                self.assertIn(diagnostic, result["status"]["diagnostics"])


class CanonicalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pre = next(x["guard"] for x in persisted()[0]["refinements"]
                       if x["guard"]["payload"]["symbol_bindings"])
        cls.ref = cls.pre["payload"]["symbol_bindings"][0]["symbol_ref"]

    def test_safe_commutation_and_unsafe_order(self):
        x, one = symbol(self.ref), lit(1)
        equal_a = term("==", BOOL, [x, one])
        equal_b = term("==", BOOL, [one, x])
        self.assertEqual(g.canonical_term(equal_a), g.canonical_term(equal_b))
        self.assertNotEqual(g.canonical_term(term("<", BOOL, [x, one])),
                            g.canonical_term(term("<", BOOL, [one, x])))
        checked_a = term("+", typed_type("uint8"), [x, one], arithmetic_mode="CHECKED")
        checked_b = term("+", typed_type("uint8"), [one, x], arithmetic_mode="CHECKED")
        self.assertNotEqual(g.canonical_term(checked_a), g.canonical_term(checked_b))
        p = term("==", BOOL, [checked_a, one])
        q = term("==", BOOL, [one, checked_a])
        self.assertNotEqual(g.canonical_term(p), g.canonical_term(q))
        b = lit(True, "bool")
        self.assertNotEqual(g.canonical_term(term("&&", BOOL, [p, b], arithmetic_mode="SHORT_CIRCUIT")),
                            g.canonical_term(term("&&", BOOL, [b, p], arithmetic_mode="SHORT_CIRCUIT")))

    def test_type_scope_assumptions_lineage_are_retained(self):
        a = g.seal_guard(self.pre)
        b = g.seal_guard(self.pre)
        self.assertEqual(a, b)
        self.assertEqual(self.pre["payload"]["scope"], a["payload"]["scope"])
        self.assertEqual(self.pre["payload"]["assumptions"], a["payload"]["assumptions"])
        self.assertEqual(self.pre["payload"]["symbol_bindings"], a["payload"]["symbol_bindings"])
        self.assertIn(self.pre["id"], a["evidence_refs"])
        self.assertEqual(g.CANONICAL_RULE, a["payload"]["normalization_rules"][-1])


class ComparisonTests(CanonicalTests):
    def test_fast_smt_and_five_states(self):
        x, one = symbol(self.ref), lit(1)
        a = guard_with_expression(self.pre, term("<", BOOL, [x, one]))
        same = deepcopy(a)
        same["id"] = "guard:another-artifact-id"
        fast = g.compare_guards(a, same)
        self.assertEqual("EQUIVALENT", fast["outcome"])
        self.assertIsNone(fast["query_artifact"])
        equivalent = guard_with_expression(self.pre, term("!", BOOL, [term(">=", BOOL, [x, one])]))
        diff = guard_with_expression(self.pre, term("<=", BOOL, [x, one]))
        self.assertEqual("EQUIVALENT", g.compare_guards(a, equivalent)["outcome"])
        different = g.compare_guards(a, diff)
        self.assertEqual("DIFFERENT", different["outcome"])
        self.assertEqual(different["query_digest"], content_digest(different["query_artifact"]))
        self.assertEqual("UNKNOWN", g.compare_guards(a, diff, backend=Stub("UNKNOWN"))["outcome"])
        self.assertEqual("TIMEOUT", g.compare_guards(a, diff, backend=Stub("TIMEOUT"))["outcome"])
        self.assertEqual("UNSUPPORTED", g.compare_guards(a, diff, backend=Stub("UNSUPPORTED"))["outcome"])

    def test_scope_symbol_and_checked_boundaries(self):
        x, one = symbol(self.ref), lit(1)
        a = guard_with_expression(self.pre, term("<", BOOL, [x, one]))
        other = deepcopy(a)
        other["payload"]["scope"]["candidate_id"] = "other"
        self.assertEqual("UNKNOWN", g.compare_guards(a, other)["outcome"])
        checked = term("+", typed_type("uint8"), [x, one], arithmetic_mode="CHECKED")
        b = guard_with_expression(self.pre, term("==", BOOL, [checked, x]))
        self.assertEqual("UNSUPPORTED", g.compare_guards(a, b)["outcome"])

    def test_explicit_scoped_symbol_mapping(self):
        x = symbol(self.ref)
        a = guard_with_expression(self.pre, term("==", BOOL, [x, lit(1)]))
        other_pre = deepcopy(self.pre)
        other_ref = "symbol:another-declaration-identity"
        other_pre["payload"]["symbol_bindings"][0]["symbol_ref"] = other_ref
        other_pre["payload"]["scope"]["candidate_id"] = "other-candidate"
        b = guard_with_expression(other_pre, term("==", BOOL, [symbol(other_ref), lit(1)]))
        self.assertEqual("UNKNOWN", g.compare_guards(a, b)["outcome"])
        context = {"scope_mapping": {"left": a["payload"]["scope"],
                    "right": b["payload"]["scope"], "evidence_refs": ["evidence:scope-map"]},
                   "symbol_mapping": {self.ref: other_ref},
                   "symbol_mapping_evidence_refs": ["evidence:abi-position-map"]}
        outcome = g.compare_guards(a, b, context)
        self.assertEqual("EQUIVALENT", outcome["outcome"])
        self.assertIn("evidence:scope-map", outcome["evidence_refs"])


class IsolationTests(unittest.TestCase):
    def test_production_has_no_evaluator_or_oracle_dependency(self):
        source = (ROOT / "scripts/s_seir/s_seir_research_guards.py").read_text()
        imports = [n for n in ast.walk(ast.parse(source)) if isinstance(n, (ast.Import, ast.ImportFrom))]
        self.assertFalse(any("evaluator" in ast.unparse(n) for n in imports))
        self.assertNotIn("expected_claims", source)
        self.assertNotIn("refine_candidates(", source)

    def test_evaluator_does_not_mutate_result_and_reports_unknown(self):
        refinement, actions = persisted()
        result = g.build_module1_result(refinement, actions)
        before = canonical_json(result)
        fake = next(e for e in result["payload"]["rejected_edges"])
        by_id = {a["id"]: a for a in result["payload"]["actions"]}
        # Storage location lacks proven layout; no semantic endpoint equality.
        self.assertIsNone(endpoint_projection(fake["payload"]["to"], by_id))
        ev = evaluate_case("persisted-limitation", result, [{"id": "expected:fake",
            "classification": "FAKE", "edge_projection": None}], runtime_seconds=0.1)
        self.assertEqual(before, canonical_json(result))
        self.assertGreater(int(ev["coverage"]["non_evaluable_expected"]), 0)
        self.assertEqual("PARTIAL", ev["status"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
