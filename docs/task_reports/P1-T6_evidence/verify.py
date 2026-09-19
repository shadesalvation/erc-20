#!/usr/bin/env python3
"""Recompute P1-T6 acceptance, targeted results and a solver-free handoff smoke.

Tests may construct upstream fixtures. This file is not a production dependency.
Run after the final regression runner to evaluate all fourteen acceptance groups.
"""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
for path in (ROOT / "scripts/s_seir", ROOT / "scripts/legacy_yul"):
    sys.path.insert(0, str(path))
import s_seir_research_local_refinement as r
import s_seir_research_local_refinement_tests as tests
import s_seir_research_control_edges_tests as t5
import s_seir_research_propagation_tests as t4


def write(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []
    def addSuccess(self, test):
        super().addSuccess(test)
        self.records.append({"test": test.id(), "status": "PASS"})
    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.records.append({"test": test.id(), "status": "FAIL", "reason": self._exc_info_to_string(err, test)})
    def addError(self, test, err):
        super().addError(test, err)
        self.records.append({"test": test.id(), "status": "ERROR", "reason": self._exc_info_to_string(err, test)})


source_hashes = {str(Path(m.__file__).relative_to(ROOT)): hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in (r, tests, t5, t4)}
check_only = "--check-only" in sys.argv
if check_only:
    if source_hashes != json.loads((OUT / "verified_sources.json").read_text()):
        raise RuntimeError("source changed since targeted verification; rerun without --check-only")
    records = json.loads((OUT / "targeted_tests.json").read_text())["tests"]
    result = SimpleNamespace(records=records, testsRun=len(records), wasSuccessful=lambda: all(x["status"] == "PASS" for x in records))
else:
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(m) for m in (tests, t5, t4))
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=RecordingResult).run(suite)
    (OUT / "targeted.log").write_text(stream.getvalue())
    write("targeted_tests.json", {"tests": result.records, "counts": {
        "PASS": sum(x["status"] == "PASS" for x in result.records),
        "FAIL": sum(x["status"] != "PASS" for x in result.records)}})
    write("verified_sources.json", source_hashes)
passed = {x["test"].split(".")[-1] for x in result.records if x["status"] == "PASS"}

if check_only:
    upstream = json.loads((OUT / "handoff_input.json").read_text())["candidate_result"]
else:
    sfir = tests.typed_branch_fixture([tests.binary("p", "==", tests.operand("x"), tests.operand("x"))])
    upstream, propagation, refined = tests.execute(sfir)
    write("handoff_input.json", {"sfir": sfir, "candidate_result": upstream, "propagation_result": propagation})
    write("refinement_result.json", refined)
    write("solver_accounting.json", refined["accounting"])
# Deserialize and consume without a backend, carrier reconstruction or scope build.
loaded = json.loads((OUT / "refinement_result.json").read_text())
smoke = []
for row in loaded["refinements"]:
    projected = r.query_refinements(loaded, candidate_id=row["candidate_id"])
    candidate = next(x for x in upstream["edges"] if x["id"] == row["candidate_id"])
    r.validate_refined_edge(row["edge"], candidate, row["guard"], row["solver_evidence"][0], row["scope"])
    smoke.append({"candidate_id": row["candidate_id"], "guard_ref": row["guard"]["id"],
                  "solver_evidence_refs": [e["id"] for e in row["solver_evidence"]],
                  "direct_query_equal": projected == [row]})
write("handoff_smoke.json", {"solver_rerun": False, "reconstruction_rerun": False, "rows": smoke})

bootstrap = json.loads((OUT / "bootstrap.json").read_text())
frozen = ["s_seir_research_contracts.py", "s_seir_research_control_edges.py",
          "s_seir_research_propagation.py", "s_seir_semantic_fact_ir.py", "s_seir_predicate_lifter.py"]
frozen_checks = []
for filename in frozen:
    path = "scripts/s_seir/" + filename
    original = subprocess.check_output(["git", "show", bootstrap["start_head"] + ":" + path], cwd=ROOT)
    current = (ROOT / path).read_bytes()
    frozen_checks.append({"path": path, "unchanged": original == current,
                          "sha256": hashlib.sha256(current).hexdigest()})
write("frozen_boundary.json", frozen_checks)

groups = [
    ("01_P1_T6_only_direct_inputs", ["test_production_boundary_and_no_oracle"]),
    ("02_lineage_frozen_schema_validator", ["test_unsat_retained_lineage_and_validator_not_relaxed", "test_no_transitive_candidates"]),
    ("03_carrier_context_scope_completeness", ["test_outside_carrier_definition_external_result_and_order_gap", "test_known_unknown_fixed_point_and_flattened_mixed_consumption"]),
    ("04_typed_BV_checked_wrapping", ["test_checked_vs_wrapping_through_sfir_definitions", "test_real_signed_unsigned_comparisons", "test_cast_sign_zero_extension_truncation_address", "test_bitwise_shifts_and_bool"]),
    ("05_six_solver_three_feasibility", ["test_unknown_timeout_unsupported_stub_mapping", "test_symbolic_budget_not_run_truncated", "test_unsat_retained_lineage_and_validator_not_relaxed"]),
    ("06_scope_gates_SAT_UNSAT", ["test_real_sat_complete_and_partial_are_distinct", "test_incomplete_unsat_never_rejects", "test_unused_checked_operation_cannot_be_ignored"]),
    ("07_no_deletion_or_status_whitewash", ["test_resource_limit_frontier_preserved", "test_upstream_partial_unsupported_error_preserved", "test_unexpected_backend_exception_is_not_empty_success"]),
    ("08_precanonical_guard_full_queries", ["test_query_artifact_replay_and_refs", "test_large_literals_lossless"]),
    ("09_opaque_prefix_nonvacuous", ["test_true_false_nonconstant", "test_multiple_predicates_prefix_and_unsat_base", "test_solver_call_budget_marks_partial"]),
    ("10_normal_flattened_mixed_upstream", ["test_known_unknown_fixed_point_and_flattened_mixed_consumption", "test_resource_limit_frontier_preserved", "test_real_propagation_unsupported_error_are_not_empty_success"]),
    ("11_determinism_isolation", ["test_reorder_deterministic_query_guard_accounting", "test_production_boundary_and_no_oracle"]),
    ("12_no_later_task_implementation", ["test_production_boundary_and_no_oracle"]),
]
checks = [{"criterion": name, "tests": names, "passed": all(n in passed for n in names)} for name, names in groups]
regression_path = OUT / "final_regression/results.json"
regression = json.loads(regression_path.read_text()) if regression_path.exists() else {}
checks.append({"criterion": "13_targeted_and_runner", "passed": result.wasSuccessful() and
               len(regression.get("results", [])) == regression.get("entrypoints_planned", -1) and
               all(x["status"] == "PASS" for x in regression.get("results", [])), "runner_counts": regression.get("counts")})
report = ROOT / "docs/task_reports/P1-T6.md"
status = (ROOT / "PROJECT_STATUS.md").read_text()
checks.append({"criterion": "14_report_status_evidence_handoff_consistency", "passed":
    report.exists() and "**P1-T6 = COMPLETE**" in status and "Status: COMPLETE" in report.read_text() and "P1-T7 Handoff" in report.read_text() and
    all(x["direct_query_equal"] for x in smoke) and all(x["unchanged"] for x in frozen_checks) and
    loaded["accounting"] == r.refinement_accounting(loaded["refinements"])})
write("acceptance.json", {"task": "P1-T6", "checks": checks,
    "passed": sum(c["passed"] for c in checks), "total": len(checks),
    "status": "COMPLETE" if all(c["passed"] for c in checks) else "PARTIAL"})
print(json.dumps({"targeted_tests": result.testsRun, "acceptance_passed": sum(c["passed"] for c in checks), "acceptance_total": len(checks)}))
sys.exit(0 if all(c["passed"] for c in checks) else 1)
