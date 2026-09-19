#!/usr/bin/env python3
"""Thin public-API driver for a newly generated SFIR JSON file."""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "s_seir"))

from s_seir_research_actions import build_function_ref, extract_semantic_actions, sfir_input_fingerprint
from s_seir_research_abstract_domain import build_contract
from s_seir_research_contracts import canonical_json
from s_seir_research_regions import detect_flattened_regions
from s_seir_research_propagation import propagate_regions
from s_seir_research_control_edges import reconstruct_candidate_control_edges
from s_seir_research_local_refinement import refine_candidates
from s_seir_research_guards import build_module1_result, validate_module1_result


LIMITS = {"max_processed_items": 10000, "max_cache_entries": 10000,
          "max_contributions": 100000, "max_requeues": 100000}


def save(output_dir: Path, name: str, value: object) -> None:
    path = output_dir / f"{name}.json.gz"
    with gzip.GzipFile(filename=str(path), mode="wb", compresslevel=9, mtime=0) as stream:
        stream.write((canonical_json(value) + "\n").encode("utf-8"))
    print(f"saved {path}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sfir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    sfir = json.loads(args.sfir.read_text(encoding="utf-8"))
    fingerprint = sfir_input_fingerprint(sfir)
    print(f"SFIR functions={len(sfir['functions'])} fingerprint={fingerprint}", flush=True)

    actions = extract_semantic_actions(sfir, input_fingerprint=fingerprint)
    save(output_dir, "p1_t1_actions", actions)
    print(f"P1-T1 actions={len(actions['actions'])}", flush=True)

    regions = detect_flattened_regions(sfir, actions, input_fingerprint=fingerprint)
    save(output_dir, "p1_t2_regions", regions)
    print(f"P1-T2 regions={len(regions['regions'])}", flush=True)

    domain = build_contract(k=2, finite_cap=4, input_fingerprint=fingerprint)
    save(output_dir, "p1_t3_domain", domain)

    propagation = propagate_regions(sfir, regions, domain["contract"], resource_limits=LIMITS)
    save(output_dir, "p1_t4_propagation", propagation)
    print(f"P1-T4 propagations={len(propagation['propagations'])}", flush=True)

    candidates = reconstruct_candidate_control_edges(sfir, actions, regions, propagation)
    save(output_dir, "p1_t5_candidates", candidates)
    print(f"P1-T5 candidates={len(candidates['edges'])}", flush=True)

    refinement = refine_candidates(sfir, candidates, propagation)
    save(output_dir, "p1_t6_refinement", refinement)
    print(f"P1-T6 refinements={len(refinement['refinements'])}", flush=True)

    results = []
    for function in sfir["functions"]:
        function_ref = build_function_ref(function, fingerprint)
        result = build_module1_result(refinement, actions, function_ref=function_ref)
        validate_module1_result(result)
        results.append(result)
        payload = result["payload"]
        print(f"P1-T7 {function_ref['canonical_signature']}: "
              f"actions={len(payload['actions'])} feasible={len(payload['feasible_edges'])} "
              f"rejected={len(payload['rejected_edges'])} unresolved={len(payload['unresolved_edges'])} "
              f"status={result['status']['completion']}", flush=True)
    save(output_dir, "p1_t7_module1_results", results)
    print("Module 1 complete", flush=True)


if __name__ == "__main__":
    main()
