#!/usr/bin/env python3
"""P1-T7 evaluator tests over persisted, evaluator-owned micro cases."""
from __future__ import annotations

from copy import deepcopy
import gzip
import json
from pathlib import Path
import unittest

from module1 import evaluate_case, serialize_evaluation

CASES = Path(__file__).with_name("module1_micro_cases.json.gz")


class Module1MicroTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with gzip.open(CASES, "rt", encoding="utf-8") as stream:
            cls.cases = {c["case_id"]: c for c in json.load(stream)}
        cls.evaluations = {name: evaluate_case(name, case["recovery"], case["expected_claims"],
            runtime_seconds=case["fixture_runtime_seconds"], config=case["config"])
            for name, case in cls.cases.items()}

    def test_required_micro_coverage_and_real_flattened_limitation(self):
        self.assertEqual({"straight_line", "if_else", "join", "loop", "opaque_true_fake",
                          "opaque_false_fake", "flattened_mixed", "syntax_different_equivalent",
                          "genuinely_different", "comparison_unsupported", "comparison_unknown"},
                         set(self.cases))
        flat = self.cases["flattened_mixed"]["recovery"]
        self.assertEqual([], flat["payload"]["feasible_edges"])
        self.assertGreater(len(flat["payload"]["unresolved_edges"]), 0)
        self.assertEqual("PARTIAL", flat["status"]["completion"])
        self.assertEqual("PARTIAL", self.evaluations["flattened_mixed"]["status"])

    def test_metrics_recomputed_from_machine_rows(self):
        for name, ev in self.evaluations.items():
            with self.subTest(name=name):
                contributions = [row["metric_contribution"] for row in ev["rows"]]
                totals = {key: sum(int(row[key]) for row in contributions) for key in contributions[0]}
                self.assertEqual({k: int(v) for k, v in ev["contribution_totals"].items()}, totals)
                tp, fp, fn = (totals[key] for key in ("tp", "fp", "fn"))
                precision = tp / (tp + fp) if tp + fp else None
                recall = tp / (tp + fn) if tp + fn else None
                f1 = (None if precision is None or recall is None else
                      0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall))
                self.assertEqual(precision, ev["metrics"]["semantic_edge_precision"])
                self.assertEqual(recall, ev["metrics"]["semantic_edge_recall"])
                self.assertEqual(f1, ev["metrics"]["semantic_edge_f1"])
                self.assertEqual(
                    totals["guard_equivalent"] / (totals["guard_equivalent"] + totals["guard_different"])
                    if totals["guard_equivalent"] + totals["guard_different"] else None,
                    ev["metrics"]["guard_equivalence_rate"])
                self.assertEqual(
                    totals["fake_removed"] / (totals["fake_removed"] + totals["fake_feasible"])
                    if totals["fake_removed"] + totals["fake_feasible"] else None,
                    ev["metrics"]["fake_edge_removal_rate"])

    def test_positive_negative_unknown_and_fake_claims(self):
        for name, classification in (("opaque_true_fake", "ALWAYS_TRUE"),
                                     ("opaque_false_fake", "ALWAYS_FALSE")):
            observed = {e["payload"]["opaque_predicate_result"]["classification"]
                        for e in self.cases[name]["recovery"]["payload"]["evidence"]
                        if e.get("schema") == "erc20-research/solver-evidence/v1"
                        and e["payload"]["opaque_predicate_result"]}
            self.assertIn(classification, observed)
        self.assertEqual("1", self.evaluations["opaque_true_fake"]["contribution_totals"]["fake_removed"])
        self.assertEqual("1", self.evaluations["opaque_false_fake"]["contribution_totals"]["fake_removed"])
        self.assertEqual("1", self.evaluations["syntax_different_equivalent"]["coverage"]["guard_comparisons"]["EQUIVALENT"])
        self.assertEqual("1", self.evaluations["genuinely_different"]["coverage"]["guard_comparisons"]["DIFFERENT"])
        self.assertEqual("1", self.evaluations["comparison_unknown"]["coverage"]["guard_comparisons"]["UNKNOWN"])
        self.assertEqual("1", self.evaluations["comparison_unsupported"]["coverage"]["guard_comparisons"]["UNSUPPORTED"])
        for name in ("join", "loop"):
            self.assertGreater(int(self.evaluations[name]["coverage"]["non_evaluable_recovered"]), 0)
        branch = self.cases["if_else"]
        recovered_guards = {g["id"]: g for g in branch["recovery"]["payload"]["guards"]}
        for expected in branch["expected_claims"]:
            matched = next(row for row in self.evaluations["if_else"]["rows"]
                           if row["expected_ref"] == expected["id"])
            edge = next(edge for edge in branch["recovery"]["payload"]["feasible_edges"]
                        if edge["id"] == matched["recovered_ref"])
            actual = recovered_guards[edge["payload"]["guard_ref"]["artifact_ref"]]
            self.assertNotEqual(actual["payload"]["expression"], expected["guard"]["payload"]["expression"])

    def test_reorder_determinism_and_no_recovery_mutation(self):
        case = deepcopy(self.cases["if_else"])
        original = json.dumps(case["recovery"], sort_keys=True)
        first = evaluate_case(case["case_id"], case["recovery"], case["expected_claims"],
                              runtime_seconds=case["fixture_runtime_seconds"], config=case["config"])
        self.assertEqual(original, json.dumps(case["recovery"], sort_keys=True))
        case["expected_claims"].reverse()
        case["recovery"]["payload"]["feasible_edges"].reverse()
        second = evaluate_case(case["case_id"], case["recovery"], case["expected_claims"],
                               runtime_seconds=case["fixture_runtime_seconds"], config=case["config"])
        self.assertEqual(serialize_evaluation(first), serialize_evaluation(second))


if __name__ == "__main__":
    unittest.main(verbosity=2)
