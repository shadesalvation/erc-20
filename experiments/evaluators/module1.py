#!/usr/bin/env python3
"""P1-T7 micro evaluator. Expected claims enter only through this module."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "s_seir"))

from s_seir_research_contracts import canonical_json, canonicalize, stable_identity
from s_seir_research_guards import compare_guards, validate_module1_result
from s_seir_research_local_refinement import Unsupported, term, typed_type, validate_term

MATCHER_VERSION = "p1-t7/semantic-edge-matcher/v1"
SAFE_LITERAL_KINDS = {"literal", "constant"}


def _safe_value(value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, dict) and set(value) == {"kind", "type", "value"}:
        if value["kind"] in SAFE_LITERAL_KINDS and isinstance(value["type"], dict):
            try:
                if value["type"] != typed_type(value["type"].get("name")):
                    return None
                validate_term(term("literal", value["type"], literal=value["value"]))
                return canonicalize(value)
            except (Unsupported, ValueError, TypeError):
                return None
    return None


def action_projection(action):
    """Project only values whose semantic identity is explicit in P1-T1 data."""
    if action["status"]["completion"] != "COMPLETE":
        return None
    p = action["payload"]
    operands = []
    for operand in p["operand_refs"]:
        if not isinstance(operand, dict) or "role" not in operand:
            return None
        value = _safe_value(operand.get("value"))
        if value is None:
            return None
        operands.append({"role": operand["role"], "value": value})
    return {"projection_version": MATCHER_VERSION, "kind": p["kind"],
            "operands": sorted(operands, key=canonical_json)}


def endpoint_projection(endpoint, actions):
    kind = endpoint["kind"]
    if kind in {"ENTRY", "EXIT"}:
        f = endpoint["function_ref"]
        return {"kind": kind, "entry_kind": f["entry_kind"],
                "canonical_signature": f["canonical_signature"]}
    if kind == "ACTION":
        action = actions.get(endpoint["action_ref"])
        return action_projection(action) if action else None
    return None


def semantic_edge_projection(edge, actions):
    p = edge["payload"]
    source = endpoint_projection(p["from"], actions)
    target = endpoint_projection(p["to"], actions)
    if source is None or target is None:
        return None
    return {"from": source, "to": target}


def _row(kind, expected_ref=None, recovered_ref=None, reason=None, comparison=None,
         **contribution):
    metrics = {key: int(contribution.get(key, 0)) for key in
               ("tp", "fp", "fn", "guard_equivalent", "guard_different",
                "fake_removed", "fake_feasible", "fake_unresolved",
                "non_evaluable_expected", "non_evaluable_recovered")}
    return canonicalize({"kind": kind, "expected_ref": expected_ref,
        "recovered_ref": recovered_ref, "reason": reason,
        "comparison": comparison, "metric_contribution": metrics})


def _ratio(numerator, denominator):
    return None if denominator == 0 else numerator / denominator


def evaluate_case(case_id, result, expected_claims, *, comparison_contexts=None,
                  runtime_seconds=0.0, config=None):
    """One-to-one matching; ambiguous or unknown rows remain in coverage."""
    validate_module1_result(result)
    comparison_contexts = comparison_contexts or {}
    expected_claims = deepcopy(expected_claims)
    actions = {a["id"]: a for a in result["payload"]["actions"]}
    guards = {g["id"]: g for g in result["payload"]["guards"]}
    recovered = {state: result["payload"][name] for state, name in (
        ("FEASIBLE", "feasible_edges"), ("INFEASIBLE", "rejected_edges"),
        ("UNRESOLVED", "unresolved_edges"))}
    grouped = defaultdict(list)
    rows = []
    for state, edges in recovered.items():
        for edge in edges:
            projection = semantic_edge_projection(edge, actions)
            if projection is None:
                rows.append(_row("NON_EVALUABLE_RECOVERED", recovered_ref=edge["id"],
                    reason="endpoint lacks certified semantic action projection",
                    non_evaluable_recovered=1))
            else:
                grouped[(state, canonical_json(projection))].append(edge)
    claims = defaultdict(list)
    for claim in expected_claims:
        if claim["classification"] not in {"FEASIBLE", "FAKE", "UNRESOLVED"}:
            raise ValueError("invalid micro expected classification")
        if claim.get("edge_projection") is None:
            rows.append(_row("NON_EVALUABLE_EXPECTED", expected_ref=claim["id"],
                reason="expected endpoint projection unavailable", non_evaluable_expected=1))
        else:
            claims[(claim["classification"], canonical_json(claim["edge_projection"]))].append(claim)
    all_keys = set(claims) | {(s if s != "INFEASIBLE" else "FAKE", key)
                             for s, key in grouped}
    for classification, key in sorted(all_keys):
        exp = claims.get((classification, key), [])
        if classification == "FAKE":
            got_rejected = grouped.get(("INFEASIBLE", key), [])
            got_feasible = grouped.get(("FEASIBLE", key), [])
            got_unresolved = grouped.get(("UNRESOLVED", key), [])
            if len(exp) > 1 or sum(map(len, (got_rejected, got_feasible, got_unresolved))) > 1:
                for claim in exp:
                    rows.append(_row("AMBIGUOUS_EXPECTED", expected_ref=claim["id"],
                        reason="candidate multiplicity has no unique semantic match", non_evaluable_expected=1))
                for edge in [*got_rejected, *got_feasible, *got_unresolved]:
                    rows.append(_row("AMBIGUOUS_RECOVERED", recovered_ref=edge["id"],
                        reason="candidate multiplicity has no unique semantic match", non_evaluable_recovered=1))
                continue
            if not exp:
                continue
            edge = next(iter([*got_rejected, *got_feasible, *got_unresolved]), None)
            if edge is None:
                rows.append(_row("FAKE_MISSING", expected_ref=exp[0]["id"],
                    reason="no matching recovered candidate", non_evaluable_expected=1))
            elif edge in got_rejected:
                rows.append(_row("FAKE_REMOVED", exp[0]["id"], edge["id"], fake_removed=1))
            elif edge in got_feasible:
                rows.append(_row("FAKE_FEASIBLE", exp[0]["id"], edge["id"], fake_feasible=1))
            else:
                rows.append(_row("FAKE_UNRESOLVED", exp[0]["id"], edge["id"],
                                 reason="P1-T6 unresolved", fake_unresolved=1))
            continue
        state = classification
        got = grouped.get((state, key), [])
        if len(exp) > 1 or len(got) > 1:
            for claim in exp:
                rows.append(_row("AMBIGUOUS_EXPECTED", expected_ref=claim["id"],
                    reason="candidate multiplicity has no unique semantic match", non_evaluable_expected=1))
            for edge in got:
                rows.append(_row("AMBIGUOUS_RECOVERED", recovered_ref=edge["id"],
                    reason="candidate multiplicity has no unique semantic match", non_evaluable_recovered=1))
            continue
        if classification == "UNRESOLVED":
            if exp and got:
                rows.append(_row("EXPECTED_UNRESOLVED", exp[0]["id"], got[0]["id"],
                    reason="coverage limitation retained", non_evaluable_expected=1,
                    non_evaluable_recovered=1))
            elif exp:
                rows.append(_row("EXPECTED_UNRESOLVED_MISSING", expected_ref=exp[0]["id"],
                    reason="expected unresolved candidate absent", non_evaluable_expected=1))
            elif got:
                rows.append(_row("RECOVERED_UNRESOLVED", recovered_ref=got[0]["id"],
                    reason="unresolved candidate outside expected claims", non_evaluable_recovered=1))
            continue
        if exp and got:
            claim, edge = exp[0], got[0]
            guard = guards[edge["payload"]["guard_ref"]["artifact_ref"]]
            context = comparison_contexts.get(claim["id"], {})
            comparison = compare_guards(guard, claim["guard"], context)
            outcome = comparison["outcome"]
            if outcome == "EQUIVALENT":
                rows.append(_row("MATCH", claim["id"], edge["id"], comparison=comparison,
                                 tp=1, guard_equivalent=1))
            elif outcome == "DIFFERENT":
                rows.append(_row("GUARD_DIFFERENT", claim["id"], edge["id"], comparison=comparison,
                                 fp=1, fn=1, guard_different=1))
            else:
                rows.append(_row("GUARD_" + outcome, claim["id"], edge["id"],
                    reason="Guard comparison is not decidable", comparison=comparison,
                    non_evaluable_expected=1, non_evaluable_recovered=1))
        elif exp:
            rows.append(_row("MISSING_FEASIBLE", expected_ref=exp[0]["id"], fn=1))
        elif got:
            rows.append(_row("EXTRA_FEASIBLE", recovered_ref=got[0]["id"], fp=1))
    rows.sort(key=lambda x: (x["kind"], x["expected_ref"] or "", x["recovered_ref"] or ""))
    sums = {key: sum(int(row["metric_contribution"][key]) for row in rows)
            for key in rows[0]["metric_contribution"]} if rows else {}
    tp, fp, fn = (sums.get(key, 0) for key in ("tp", "fp", "fn"))
    precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    f1 = (None if precision is None or recall is None else
          0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall))
    comparisons = {state: sum(row["comparison"] is not None and row["comparison"]["outcome"] == state
                              for row in rows) for state in ("EQUIVALENT", "DIFFERENT", "UNKNOWN", "TIMEOUT", "UNSUPPORTED")}
    metrics = {"semantic_edge_precision": precision, "semantic_edge_recall": recall,
               "semantic_edge_f1": f1,
               "guard_equivalence_rate": _ratio(sums.get("guard_equivalent", 0),
                   sums.get("guard_equivalent", 0) + sums.get("guard_different", 0)),
               "fake_edge_removal_rate": _ratio(sums.get("fake_removed", 0),
                   sums.get("fake_removed", 0) + sums.get("fake_feasible", 0))}
    data = {"schema": "erc20-evaluation-result/v1", "module": "Module1",
            "matcher_version": MATCHER_VERSION, "case_id": case_id,
            "recovered_result_ref": result["id"],
            "expected_claim_refs": sorted(x["id"] for x in expected_claims),
            "rows": rows, "contribution_totals": sums, "metrics": metrics,
            "coverage": {"recovered": {state: len(edges) for state, edges in recovered.items()},
                "expected": {state: sum(x["classification"] == state for x in expected_claims)
                             for state in ("FEASIBLE", "FAKE", "UNRESOLVED")},
                "evaluated_expected": len(expected_claims) - sums.get("non_evaluable_expected", 0),
                "evaluated_recovered": sum(map(len, recovered.values())) - sums.get("non_evaluable_recovered", 0),
                "non_evaluable_expected": sums.get("non_evaluable_expected", 0),
                "non_evaluable_recovered": sums.get("non_evaluable_recovered", 0),
                "guard_comparisons": comparisons, "upstream_status": result["status"]},
            "runtime_seconds": runtime_seconds, "config": config or {},
            "status": "PARTIAL" if (sums.get("non_evaluable_expected", 0) or sums.get("non_evaluable_recovered", 0)
                                     or result["status"]["completion"] != "COMPLETE") else "COMPLETE",
            "reason": "non-evaluable or upstream partial coverage" if (sums.get("non_evaluable_expected", 0)
                or sums.get("non_evaluable_recovered", 0) or result["status"]["completion"] != "COMPLETE") else None}
    data["id"] = stable_identity("module1-evaluation", {k: v for k, v in data.items() if k != "runtime_seconds"})
    return canonicalize(data)


def serialize_evaluation(evaluation):
    return canonical_json(evaluation)
