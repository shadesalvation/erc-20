#!/usr/bin/env python3
"""Read-only audit of this source-to-Module1Result run."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO / "scripts" / "s_seir"))

from s_seir_research_actions import sfir_input_fingerprint
from s_seir_research_abstract_domain import validate_contract
from s_seir_research_contracts import canonical_json
from s_seir_research_control_edges import validate_candidate_collection
from s_seir_research_local_refinement import refinement_accounting
from s_seir_research_guards import validate_module1_result


def load(name):
    with gzip.open(HERE / f"{name}.json.gz", "rt", encoding="utf-8") as stream:
        return json.load(stream)


def main():
    manifest = json.loads((HERE / "run_manifest.json").read_text())
    source = REPO / manifest["source"]
    sfir = json.loads((HERE / "sfir.json").read_text())
    actions, regions, domain, propagation, candidates, refinement, results = (
        load("p1_t1_actions"), load("p1_t2_regions"), load("p1_t3_domain"),
        load("p1_t4_propagation"), load("p1_t5_candidates"),
        load("p1_t6_refinement"), load("p1_t7_module1_results"))
    summary = json.loads((HERE / "summary.json").read_text())
    validate_contract(domain["contract"])
    validate_candidate_collection(candidates)
    for result in results:
        validate_module1_result(result)
    fingerprint = sfir_input_fingerprint(sfir)
    refinement_by_id = {r["candidate_id"]: r for r in refinement["refinements"]}
    final_edges = [e for result in results for name in
                   ("feasible_edges", "rejected_edges", "unresolved_edges")
                   for e in result["payload"][name]]
    final_ids = [e["id"] for e in final_edges]
    candidate_ids = [e["id"] for e in candidates["edges"]]
    summary_edges = [e for f in summary["functions"] for e in f["edges"]]
    dot_edges = sum(" -> " in line for line in (HERE / "module1_control.dot").read_text().splitlines())
    text_edges = sum("candidate_id:" in line for line in (HERE / "module1_control.txt").read_text().splitlines())
    production = ["scripts/s_seir/s_seir_pipeline.py",
                  *[f"scripts/s_seir/s_seir_research_{name}.py" for name in
                    ("actions", "regions", "abstract_domain", "propagation",
                     "control_edges", "local_refinement", "guards")]]
    producer_hashes = {}
    for relative in production:
        current = (REPO / relative).read_bytes()
        committed = subprocess.check_output(["git", "show", "HEAD:" + relative], cwd=REPO)
        producer_hashes[relative] = {"current_sha256": hashlib.sha256(current).hexdigest(),
                                     "head_sha256": hashlib.sha256(committed).hexdigest(),
                                     "unchanged": current == committed}
    runner = json.loads((HERE / "regression" / "results.json").read_text())
    diff = subprocess.run(["git", "diff", "--check"], cwd=REPO, text=True,
                          capture_output=True)
    checks = {
        "head_matches": manifest["head_sha"] == subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "source_unchanged": hashlib.sha256(source.read_bytes()).hexdigest() == manifest["source_sha256"],
        "sfir_fresh_source": hashlib.sha256((HERE / "analysis_source.sol").read_bytes()).hexdigest() == manifest["source_sha256"],
        "sfir_schema": sfir["schema"] == "s-seir-semantic-fact-ir/v1",
        "fingerprint_chain": all(x == fingerprint for x in
            (actions["input_fingerprint"], regions["input_fingerprint"],
             candidates["input_fingerprint"], refinement["input_fingerprint"])),
        "refinement_accounting": refinement["accounting"] == refinement_accounting(refinement["refinements"]),
        "candidate_partition_complete": len(final_ids) == len(set(final_ids)) == len(candidate_ids)
             and set(final_ids) == set(candidate_ids) == set(refinement_by_id),
        "feasibility_unchanged": all(e["payload"]["feasibility"] == refinement_by_id[e["id"]]["edge"]["payload"]["feasibility"]
             for e in final_edges),
        "solver_outcomes_unchanged": all(e["status"]["solver"] == refinement_by_id[e["id"]]["edge"]["status"]["solver"]
             for e in final_edges),
        "function_coverage": len(results) == len(sfir["functions"]) == len(summary["functions"]),
        "summary_edge_coverage": {e["candidate_id"] for e in summary_edges} == set(final_ids)
             and len(summary_edges) == len(final_ids),
        "unresolved_reasons_present": all(e["unresolved_reason"] and
            e["unresolved_reason"]["scope_incomplete_reasons"]
            for e in summary_edges if e["feasibility"] == "UNRESOLVED"),
        "view_edge_coverage": dot_edges == text_edges == len(final_ids),
        "production_unchanged": all(x["unchanged"] for x in producer_hashes.values()),
        "regression_pass": runner["counts"] == {"PASS": 33, "FAIL": 0, "SKIP": 0, "TIMEOUT": 0},
        "diff_check_pass": diff.returncode == 0,
    }
    record = {"head_sha": manifest["head_sha"], "checks": checks,
              "overall": summary["overall"], "production_hashes": producer_hashes,
              "regression_counts": runner["counts"], "dot_edges": dot_edges,
              "text_edges": text_edges, "diff_check_output": diff.stdout + diff.stderr,
              "outcome": "PASS" if all(checks.values()) else "FAIL"}
    (HERE / "verification.json").write_text(json.dumps(record, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    print(record["outcome"], sum(checks.values()), "/", len(checks))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
