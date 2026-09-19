#!/usr/bin/env python3
"""Rebuild P1-T7 handoff and audit summaries from persisted P1-T6 inputs."""
from __future__ import annotations

import gzip
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "s_seir"))
from s_seir_research_actions import extract_semantic_actions
from s_seir_research_contracts import content_digest
from s_seir_research_guards import build_module1_result, guard_comparison_projection

HERE = Path(__file__).resolve().parent
UPSTREAM = HERE.parent / "P1-T6_evidence"


def write_json(name, value):
    (HERE / name).write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def main():
    refinement = json.loads((UPSTREAM / "refinement_result.json").read_text())
    sfir = json.loads((UPSTREAM / "handoff_input.json").read_text())["sfir"]
    actions = extract_semantic_actions(sfir, input_fingerprint=refinement["input_fingerprint"])
    result = build_module1_result(refinement, actions)
    with gzip.GzipFile(filename=str(HERE / "module1_result.json.gz"), mode="wb",
                       compresslevel=9, mtime=0) as stream:
        stream.write((json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode())
    by_candidate = {r["candidate_id"]: r for r in refinement["refinements"]}
    canonical = []
    for guard in result["payload"]["guards"]:
        original = next(r["guard"] for r in refinement["refinements"]
                        if r["guard"]["id"] == guard["extensions"]["precanonical_guard_ref"])
        canonical.append({"candidate_id": guard["payload"]["scope"]["candidate_id"],
            "precanonical_guard_ref": original["id"], "sealed_guard_ref": guard["id"],
            "canonical_rule": guard["payload"]["normalization_rules"][-1],
            "projection": guard_comparison_projection(guard),
            "scope": guard["payload"]["scope"], "assumptions": guard["payload"]["assumptions"],
            "symbol_bindings": guard["payload"]["symbol_bindings"],
            "source_predicate_refs": guard["payload"]["source_predicate_refs"],
            "evidence_refs": guard["evidence_refs"],
            "payload_digest": content_digest(guard["payload"])})
    write_json("canonicalization.json", canonical)
    partition = {"module1_result_ref": result["id"],
        "action_refs": [a["id"] for a in result["payload"]["actions"]],
        "candidate_ids": result["payload"]["accounting"]["candidate_ids"],
        "partitions": {name: [e["id"] for e in result["payload"][name]]
                       for name in ("feasible_edges", "rejected_edges", "unresolved_edges")},
        "status": result["status"], "upstream_status": refinement["status"],
        "rows": [{"candidate_id": cid,
                  "upstream_feasibility": row["edge"]["payload"]["feasibility"],
                  "solver_evidence_ref": row["feasibility_evidence_ref"],
                  "scope_completeness": row["scope"]["scope_completeness"],
                  "sealed_guard_ref": next(g["id"] for g in result["payload"]["guards"]
                                           if g["payload"]["scope"]["candidate_id"] == cid)}
                 for cid, row in sorted(by_candidate.items())]}
    write_json("partition.json", partition)
    evaluations = json.loads((HERE / "module1_evaluation.json").read_text())
    comparisons = [{"case_id": ev["case_id"], "expected_ref": row["expected_ref"],
                    "recovered_ref": row["recovered_ref"], "comparison": row["comparison"]}
                   for ev in evaluations for row in ev["rows"] if row["comparison"] is not None]
    write_json("comparison.json", comparisons)
    print(result["id"], len(canonical), "Guards", len(comparisons), "comparisons")


if __name__ == "__main__":
    main()
