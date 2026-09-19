#!/usr/bin/env python3
"""Verify P1-T5 machine evidence and a direct P1-T6 handoff smoke."""
from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
for item in (ROOT / "scripts" / "legacy_yul", ROOT / "scripts" / "s_seir"):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from s_seir_research_actions import extract_semantic_actions
from s_seir_research_control_edges import (
    PAYLOAD_FIELDS,
    candidate_accounting,
    query_candidates,
    reconstruct_candidate_control_edges,
    serialize_candidate_result,
    validate_candidate_collection,
)
from s_seir_research_propagation import propagate_regions
from s_seir_research_propagation_tests import typed_fixture
from s_seir_research_regions_tests import INPUT_FINGERPRINT


EVIDENCE = Path(__file__).resolve().parent
LIMITS = {
    "max_processed_items": 10000,
    "max_cache_entries": 10000,
    "max_contributions": 100000,
    "max_requeues": 100000,
}


def load(name: str):
    return json.loads((EVIDENCE / name).read_text(encoding="utf-8"))


def main() -> int:
    acceptance = load("acceptance.json")
    targeted = load("targeted_tests.json")
    smoke = load("handoff_smoke.json")
    regression = load("final_regression/results.json")
    assert acceptance["counts"] == {"FAIL": 0, "N/A": 0, "PASS": 21}
    assert targeted["summary"] == {"failed": 0, "passed": 77}
    assert regression["counts"] == {"PASS": 31, "FAIL": 0, "SKIP": 0, "TIMEOUT": 0}

    sfir, regions, contract = typed_fixture(k=2)
    actions = extract_semantic_actions(sfir, input_fingerprint=INPUT_FINGERPRINT)
    propagation = propagate_regions(sfir, regions, contract, resource_limits=LIMITS)
    result = reconstruct_candidate_control_edges(sfir, actions, regions, propagation)
    validate_candidate_collection(
        result,
        actions=actions["actions"],
        regions=regions["regions"],
        propagations=propagation["propagations"],
    )
    expected = smoke["candidate_fixture"]
    accounting = candidate_accounting(result)[0]
    assert len(result["edges"]) == expected["candidate_count"]
    assert len(result["evidence_records"]) == expected["evidence_count"]
    assert set(result["edges"][0]["payload"]) == PAYLOAD_FIELDS
    assert {item["payload"]["feasibility"] for item in result["edges"]} == {"UNRESOLVED"}
    assert {item["status"]["solver"] for item in result["edges"]} == {"NOT_RUN"}
    assert len(query_candidates(result, region_ref=regions["regions"][0]["id"])) == expected["query_by_region_count"]
    assert int(accounting["normal_scope_candidate_count"]) == expected["normal_scope_candidate_count"]
    assert int(accounting["flattened_scope_candidate_count"]) == expected["flattened_scope_candidate_count"]
    assert serialize_candidate_result(result) == json.dumps(
        result, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    print(json.dumps({
        "acceptance": "PASS",
        "candidate_count": len(result["edges"]),
        "evidence_closure": "PASS",
        "final_regression": regression["counts"],
        "handoff_smoke": "PASS",
        "targeted": targeted["summary"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
