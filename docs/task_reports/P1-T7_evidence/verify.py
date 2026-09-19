#!/usr/bin/env python3
"""Machine-readable P1-T7 acceptance audit over persisted artifacts."""
from __future__ import annotations

import gzip
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts" / "s_seir"))
from s_seir_research_contracts import content_digest
from s_seir_research_guards import validate_module1_result


def load(name):
    return json.loads((HERE / name).read_text())


def test_log(name, count):
    log = (HERE / name).read_text()
    return bool(re.search(rf"Ran {count} tests? in .*\n\nOK\s*$", log))


def main():
    with gzip.open(HERE / "module1_result.json.gz", "rt", encoding="utf-8") as stream:
        result = json.load(stream)
    validate_module1_result(result)
    upstream = json.loads((HERE.parent / "P1-T6_evidence" / "refinement_result.json").read_text())
    canonical, partition, comparisons, evaluations = (
        load("canonicalization.json"), load("partition.json"), load("comparison.json"), load("module1_evaluation.json"))
    by_id = {r["candidate_id"]: r for r in upstream["refinements"]}
    edges = [e for name in ("feasible_edges", "rejected_edges", "unresolved_edges")
             for e in result["payload"][name]]
    edge_ids = [e["id"] for e in edges]
    guards = {g["id"]: g for g in result["payload"]["guards"]}
    A = (set(edge_ids) == set(by_id) and len(edge_ids) == len(set(edge_ids))
         and all(all(e["payload"][field] == by_id[e["id"]]["edge"]["payload"][field]
                     for field in ("candidate_id", "from", "to", "region_ref", "context_ref"))
                 for e in edges))
    B = (len(canonical) == len(by_id) and all(
        guards[c["sealed_guard_ref"]]["extensions"]["precanonical_guard_ref"] == c["precanonical_guard_ref"]
        and c["canonical_rule"] == "p1-t7/guard-canonicalization/v1"
        and c["payload_digest"] == content_digest(guards[c["sealed_guard_ref"]]["payload"])
        for c in canonical))
    outcomes = {c["comparison"]["outcome"] for c in comparisons}
    C = ({"EQUIVALENT", "DIFFERENT", "UNKNOWN", "UNSUPPORTED"} <= outcomes and all(
        c["comparison"]["query_digest"] == content_digest(c["comparison"]["query_artifact"])
        for c in comparisons if c["comparison"]["query_artifact"] is not None))
    D = (result["status"]["completion"] == "PARTIAL" and
         partition["partitions"] == {name: [e["id"] for e in result["payload"][name]]
            for name in ("feasible_edges", "rejected_edges", "unresolved_edges")} and
         all(d in result["status"]["diagnostics"] for d in upstream["status"]["diagnostics"]))
    required = {"straight_line", "if_else", "join", "loop", "opaque_true_fake",
                "opaque_false_fake", "flattened_mixed", "syntax_different_equivalent",
                "genuinely_different", "comparison_unknown", "comparison_unsupported"}
    E = (len(evaluations) == len(required) and {e["case_id"] for e in evaluations} == required
         and all({k: sum(int(r["metric_contribution"][k]) for r in e["rows"])
                  for k in e["contribution_totals"]} == {k: int(v) for k, v in e["contribution_totals"].items()}
                 for e in evaluations)
         and next(e for e in evaluations if e["case_id"] == "flattened_mixed")["coverage"]["recovered"]["UNRESOLVED"] != "0")
    runner = load("final_regression/results.json")
    diff = subprocess.run(["git", "diff", "--check"], cwd=ROOT, capture_output=True, text=True)
    F = (test_log("guards_tests.log", 12) and test_log("micro_tests.log", 4)
         and test_log("p1_t6_regression.log", 33) and test_log("p1_t1_regression.log", 19)
         and runner["counts"] == {"PASS": 33, "FAIL": 0, "SKIP": 0, "TIMEOUT": 0}
         and diff.returncode == 0)
    checks = {"A_direct_handoff_lineage": A, "B_canonical_guard": B,
              "C_semantic_comparison": C, "D_module1_partition_status": D,
              "E_evaluator_micro_isolation": E, "F_boundary_regression": F}
    record = {"task": "P1-T7", "checks": {k: {"outcome": "PASS" if v else "FAIL"}
                                           for k, v in checks.items()},
              "counts": {"PASS": sum(checks.values()), "FAIL": sum(not v for v in checks.values()), "N/A": 0},
              "evidence": ["module1_result.json.gz", "canonicalization.json", "comparison.json",
                           "partition.json", "module1_evaluation.json", "guards_tests.log",
                           "micro_tests.log", "p1_t6_regression.log", "p1_t1_regression.log",
                           "final_regression/results.json"],
              "diff_check_output": diff.stdout + diff.stderr}
    (HERE / "acceptance.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(record["counts"])
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
