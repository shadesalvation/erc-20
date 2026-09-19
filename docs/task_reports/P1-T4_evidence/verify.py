#!/usr/bin/env python3
"""Recompute P1-T4 targeted regressions and machine-readable acceptance."""
from __future__ import annotations

import ast
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / "scripts/s_seir"), str(ROOT / "scripts/legacy_yul")]
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


class Results(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.records.append({"test": test.id(), "status": "PASS"})

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.records.append({
            "test": test.id(), "status": "FAIL",
            "reason": self._exc_info_to_string(err, test),
        })

    def addError(self, test, err):
        super().addError(test, err)
        self.records.append({
            "test": test.id(), "status": "FAIL",
            "reason": self._exc_info_to_string(err, test),
        })

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.records.append({"test": test.id(), "status": "N/A", "reason": reason})


records = []
for module_name, log_name in (
    ("s_seir_research_propagation_tests", "targeted.log"),
    ("s_seir_research_abstract_domain_tests", "abstract_domain_regression.log"),
    ("s_seir_research_regions_tests", "regions_regression.log"),
):
    module = importlib.import_module(module_name)
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    with (OUT / log_name).open("w") as stream:
        result = unittest.TextTestRunner(
            stream=stream, verbosity=2, resultclass=Results
        ).run(suite)
    records.extend(result.records)
counts = {
    status: sum(record["status"] == status for record in records)
    for status in ("PASS", "FAIL", "N/A")
}
save("targeted_tests.json", {"results": records, "counts": counts})

import s_seir_research_propagation as propagation
from s_seir_research_propagation_tests import artifact, limits, run, typed_fixture

sfir, upstream, contract = typed_fixture(k=2)
result = run(sfir, upstream, contract)
output = artifact(result)
first = propagation.node_context_states(output)[0]
queried = propagation.query_node_context_state(output, first["node_ref"], first["context"])
save("handoff_smoke.json", {
    "status": "PASS" if queried == first else "FAIL",
    "schema": output["schema"],
    "artifact_id": output["id"],
    "payload_fields": sorted(output["payload"]),
    "termination": output["payload"]["termination"],
    "node_context_count": len(output["payload"]["node_context_states"]),
    "entry_fields": sorted(first),
    "state_phase": output["payload"]["convergence_evidence"]["state_cache_phase"],
    "resource_limits": limits(),
    "consumer_api": ["validate_propagation", "node_context_states", "query_node_context_state"],
    "semantic_claims": output["payload"]["convergence_evidence"]["semantic_claims"],
})


def selected(*prefixes):
    return [
        record for record in records
        if record["test"].startswith("s_seir_research_propagation_tests.")
        and any(record["test"].split(".")[-1].startswith("test_" + prefix) for prefix in prefixes)
    ]


criteria = [
    ("Direct P1-T2/P1-T3 inputs and APIs are reused without mutation", ("direct_real_handoff", "production_isolation")),
    ("Cache identity is node plus canonical context", ("cache_is_node_context", "exact_artifact")),
    ("Same-context predecessors join and different contexts remain separate", ("same_context_branch_join", "cache_is_node_context")),
    ("Re-enqueue occurs only for new semantic information", ("loop_back_edge_reenqueues",)),
    ("FIXED_POINT means drained worklist after stabilization", ("loop_back_edge_reenqueues", "exact_artifact")),
    ("UNKNOWN is distinct from engine UNSUPPORTED", ("missing_typed_semantics", "engine_unsupported")),
    ("RESOURCE_LIMIT is distinct from FIXED_POINT and retains frontier", ("small_deterministic_budget",)),
    ("Only observed dispatcher arms update context and no real-successor claim is made", ("k1_k2_partition",)),
    ("Propagation counters and trace data do not pollute AbstractState", ("identity_uses_region",)),
    ("Repeated and reordered inputs are deterministic, including limited frontier", ("repeat_and_collection_reorder",)),
    ("AbstractPropagation is the only new formal artifact with exact six-field payload", ("exact_artifact", "production_isolation")),
    ("No-region, no-consumable and inherited PARTIAL remain distinct", ("no_region_and_no_consumable", "upstream_partial")),
    ("No P1-T5/P1-T6/P2+ capability or second parser/domain is implemented", ("production_isolation",)),
    ("Consumer query API directly reads canonical node/context state", ("direct_real_handoff",)),
]
checks = []
for index, (criterion, prefixes) in enumerate(criteria, 1):
    evidence = selected(*prefixes)
    checks.append({
        "id": index,
        "criterion": criterion,
        "status": "PASS" if evidence and all(item["status"] == "PASS" for item in evidence) else "FAIL",
        "evidence": [item["test"] for item in evidence],
    })

runner = json.loads((OUT / "final_regression/results.json").read_text())
checks.append({
    "id": 15,
    "criterion": "P1-T4/P1-T3/P1-T2 targeted and required runner pass",
    "status": "PASS" if counts == {"PASS": 65, "FAIL": 0, "N/A": 0}
    and runner["counts"] == {"PASS": 30, "FAIL": 0, "SKIP": 0, "TIMEOUT": 0} else "FAIL",
    "evidence": ["targeted_tests.json", "final_regression/results.json"],
})

report = (ROOT / "docs/task_reports/P1-T4.md").read_text()
project_status = (ROOT / "PROJECT_STATUS.md").read_text()
production_path = ROOT / "scripts/s_seir/s_seir_research_propagation.py"
tree = ast.parse(production_path.read_text())
public_api = [
    node.name for node in tree.body
    if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
]
doc_checks = {
    name: name in report for name in (
        "propagate_regions", "validate_propagation", "node_context_states",
        "query_node_context_state", "normalize_resource_limits",
    )
}
doc_checks.update({
    "report_complete": "**P1-T4 = COMPLETE**" in report,
    "status_complete": "**P1-T4 = COMPLETE**" in project_status,
    "handoff": "## P1-T5 Handoff" in report,
    "six_fields": all(name in report for name in propagation.PAYLOAD_FIELDS),
    "termination_states": all(name in report for name in propagation.TERMINATIONS),
    "next_task_requires_authorization": "等待单独授权 P1-T5" in project_status,
    "smoke": json.loads((OUT / "handoff_smoke.json").read_text())["status"] == "PASS",
})

bootstrap = json.loads((OUT / "bootstrap.json").read_text())
tracked_diff = git("diff", "--name-only").splitlines()
untracked = git("ls-files", "--others", "--exclude-standard").splitlines()
allowed = {
    "PROJECT_STATUS.md",
    "scripts/s_seir/s_seir_research_propagation.py",
    "scripts/s_seir/s_seir_research_propagation_tests.py",
    "docs/task_reports/P1-T4.md",
}
unexpected = [
    path for path in tracked_diff + untracked
    if path not in allowed
    and not path.startswith("docs/task_reports/P1-T4_evidence/")
    and path not in bootstrap["existing_worktree_status"]
]
checks.append({
    "id": 16,
    "criterion": "Report/status/evidence/handoff match code and task-only diff",
    "status": "PASS" if all(doc_checks.values()) and not unexpected else "FAIL",
    "evidence": ["../P1-T4.md", "../../../PROJECT_STATUS.md", "handoff_smoke.json", "change_summary.json"],
    **({"unexpected_changes": unexpected} if unexpected else {}),
})

end_head = git("rev-parse", "HEAD")
hash_paths = (
    production_path,
    ROOT / "scripts/s_seir/s_seir_research_propagation_tests.py",
    ROOT / "docs/task_reports/P1-T4.md",
    ROOT / "PROJECT_STATUS.md",
)
save("change_summary.json", {
    "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
    "start_head": bootstrap["start_head"],
    "end_head": end_head,
    "git_status": git("status", "--short"),
    "tracked_diff": tracked_diff,
    "task_files": sorted(allowed),
    "preexisting_preserved": bootstrap["existing_worktree_status"],
    "unexpected_changes": unexpected,
    "document_checks": doc_checks,
    "public_api": public_api,
    "sha256": {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in hash_paths
    },
    "scope_review": "Only P1-T4 propagation/artifact/tests/report/evidence/status; no upstream source change and no P1-T5/P1-T6/P2+ implementation.",
})
acceptance = {
    "task": "P1-T4",
    "start_head": bootstrap["start_head"],
    "end_head": end_head,
    "criteria": checks,
    "counts": {
        status: sum(item["status"] == status for item in checks)
        for status in ("PASS", "FAIL", "N/A")
    },
}
acceptance["status"] = "PASS" if all(item["status"] == "PASS" for item in checks) else "FAIL"
save("acceptance.json", acceptance)
print(json.dumps({
    "targeted_counts": counts,
    "acceptance": {item["id"]: item["status"] for item in checks},
}, indent=2))
sys.exit(0 if acceptance["status"] == "PASS" else 1)
