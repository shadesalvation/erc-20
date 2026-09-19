#!/usr/bin/env python3
"""P1-T7 seal and comparison of persisted P1-T6 facts.

No candidate discovery, feasibility call, SFIR traversal, or oracle access.
"""
from __future__ import annotations

from copy import deepcopy

from s_seir_research_actions import validate_semantic_action
from s_seir_research_contracts import (
    ContractValidationError, analysis_status, artifact_envelope, canonical_json,
    canonicalize, content_digest, evidence_record, producer_record, stable_identity,
    validate_artifact_envelope, validate_evidence_record,
)
from s_seir_research_local_refinement import (
    BOOL, Z3Backend, query_refinements, refinement_accounting, term,
    validate_guard, validate_solver_evidence, validate_term,
)

VERSION = "p1-t7-v1"
CANONICAL_RULE = "p1-t7/guard-canonicalization/v1"
COMPARISON_VERSION = "p1-t7/guard-comparison/v1"
PROJECTION_VERSION = "p1-t7/typed-guard-projection/v1"
COMMUTATIVE = frozenset({"==", "!=", "&", "|", "^", "+", "*", "&&", "||"})
PARTITIONS = {"FEASIBLE": "feasible_edges", "INFEASIBLE": "rejected_edges",
              "UNRESOLVED": "unresolved_edges"}


def _sorted_unique(values):
    return sorted({canonical_json(value): canonicalize(value) for value in values}.values(),
                  key=canonical_json)


def _has_sensitive_order(value):
    return (value["arithmetic_mode"] in {"CHECKED", "SHORT_CIRCUIT"}
            or any(_has_sensitive_order(x) for x in value["operands"]))


def canonical_term(value):
    """Only commute proven pure, same-type binary terms; never reassociate."""
    validate_term(value)
    result = deepcopy(value)
    result["operands"] = [canonical_term(x) for x in value["operands"]]
    op, args = result["op"], result["operands"]
    safe_mode = ((op in {"==", "!=", "&", "|", "^"} and result["arithmetic_mode"] is None)
                 or (op in {"+", "*"} and result["arithmetic_mode"] == "WRAPPING")
                 or (op in {"&&", "||"} and result["arithmetic_mode"] == "EAGER"))
    if (op in COMMUTATIVE and len(args) == 2 and safe_mode
            and args[0]["type"] == args[1]["type"]
            and not any(_has_sensitive_order(x) for x in args)):
        result["operands"] = sorted(args, key=canonical_json)
    return canonicalize(result)


def validate_sealed_guard(guard):
    validate_artifact_envelope(guard)
    if guard["schema"] != "erc20-research/guard/v1":
        raise ContractValidationError("wrong Guard schema")
    p = guard["payload"]
    if set(p) != {"source_predicate_refs", "expression", "symbol_bindings", "scope",
                  "assumptions", "normalization_rules"}:
        raise ContractValidationError("wrong sealed Guard fields")
    validate_term(p["expression"])
    if (p["expression"]["type"] != BOOL and p["expression"]["op"] != "unknown") or p["expression"] != canonical_term(p["expression"]):
        raise ContractValidationError("noncanonical/Non-Bool Guard expression")
    if p["normalization_rules"] != ["p1-t6/typed-atomic-lowering/v1", CANONICAL_RULE]:
        raise ContractValidationError("wrong Guard normalization rule")
    pre_view = deepcopy(guard)
    pre_view["payload"]["normalization_rules"] = ["p1-t6/typed-atomic-lowering/v1"]
    validate_guard(pre_view)
    if not guard["extensions"].get("precanonical_guard_ref"):
        raise ContractValidationError("lost pre-canonical Guard lineage")
    if p["source_predicate_refs"] != _sorted_unique(p["source_predicate_refs"]):
        raise ContractValidationError("unordered source predicates")
    if p["assumptions"] != _sorted_unique(p["assumptions"]):
        raise ContractValidationError("unordered assumptions")
    if p["symbol_bindings"] != _sorted_unique(p["symbol_bindings"]):
        raise ContractValidationError("unordered symbol bindings")


def seal_guard(precanonical):
    validate_guard(precanonical)
    payload = deepcopy(precanonical["payload"])
    if payload["expression"]["type"] != BOOL and payload["expression"]["op"] != "unknown":
        raise ContractValidationError("P1-T6 Guard must be Bool")
    payload["expression"] = canonical_term(payload["expression"])
    for field in ("source_predicate_refs", "symbol_bindings", "assumptions"):
        payload[field] = _sorted_unique(payload[field])
    payload["normalization_rules"] = [*payload["normalization_rules"], CANONICAL_RULE]
    result = artifact_envelope(
        schema="erc20-research/guard/v1",
        artifact_id=stable_identity("guard", {"candidate_id": payload["scope"]["candidate_id"],
            "payload": payload, "precanonical_guard_ref": precanonical["id"], "version": CANONICAL_RULE}),
        function_ref=precanonical["function_ref"], input_fingerprint=precanonical["input_fingerprint"],
        producer=producer_record("P1-T7", VERSION, {"canonical_rule": CANONICAL_RULE}),
        evidence_refs=_sorted_unique([*precanonical["evidence_refs"], precanonical["id"]]),
        status=precanonical["status"], payload=payload,
        extensions={"precanonical_guard_ref": precanonical["id"]})
    validate_sealed_guard(result)
    return result


def guard_comparison_projection(guard, symbol_mapping=None):
    """Semantic term only; scope and assumptions are checked separately."""
    validate_sealed_guard(guard)
    mapping = symbol_mapping or {}

    def mapped(value):
        result = deepcopy(value)
        if result["op"] == "symbol":
            result["symbol_ref"] = mapping.get(result["symbol_ref"], result["symbol_ref"])
        result["operands"] = [mapped(x) for x in result["operands"]]
        return result

    return {"projection_version": PROJECTION_VERSION,
            "expression": canonical_term(mapped(guard["payload"]["expression"]))}


def _symbols(value):
    return ({value["symbol_ref"]: value["type"]} if value["op"] == "symbol" else
            {k: v for child in value["operands"] for k, v in _symbols(child).items()})


def _comparison(outcome, reason, left, right, *, context, query=None, backend=None,
                evidence_refs=(), fast_path_projection_digest=None):
    data = {"version": COMPARISON_VERSION, "projection_version": PROJECTION_VERSION,
            "outcome": outcome, "reason": reason, "left_guard_ref": left["id"],
            "right_guard_ref": right["id"], "scope_mapping": context.get("scope_mapping"),
            "assumption_mapping": context.get("assumption_mapping"),
            "symbol_mapping": context.get("symbol_mapping", {}),
            "assumptions": _sorted_unique(left["payload"]["assumptions"]),
            "evidence_refs": _sorted_unique(evidence_refs), "query_artifact": query,
            "query_digest": content_digest(query) if query is not None else None,
            "fast_path_projection_digest": fast_path_projection_digest,
            "backend": getattr(backend, "name", None), "backend_version": getattr(backend, "version", None),
            "config": {"timeout_ms": context.get("timeout_ms", 1000),
                       "backend_options": getattr(backend, "options", {})} if backend else None}
    data["id"] = stable_identity("guard-comparison", data)
    return canonicalize(data)


def compare_guards(left, right, context=None, *, backend=None):
    """Scoped five-state comparison; explicit cross-sample mappings need evidence."""
    validate_sealed_guard(left)
    validate_sealed_guard(right)
    context = dict(context or {})
    left_p, right_p = left["payload"], right["payload"]
    refs = []
    for field, key in (("scope", "scope_mapping"), ("assumptions", "assumption_mapping")):
        lval, rval = left_p[field], right_p[field]
        if canonical_json(lval) != canonical_json(rval):
            mapping = context.get(key)
            if (not isinstance(mapping, dict) or mapping.get("left") != lval
                    or mapping.get("right") != rval or not mapping.get("evidence_refs")):
                return _comparison("UNKNOWN", field + " has no evidenced common mapping", left, right, context=context)
            refs.extend(mapping["evidence_refs"])
    symbols_l = _symbols(left_p["expression"])
    symbols_r = _symbols(right_p["expression"])
    mapping = context.get("symbol_mapping", {})
    if mapping:
        if (not isinstance(mapping, dict) or len(set(mapping.values())) != len(mapping)
                or any(k not in symbols_l or v not in symbols_r for k, v in mapping.items())):
            return _comparison("UNKNOWN", "symbol identities cannot be mapped", left, right, context=context, evidence_refs=refs)
        if left["input_fingerprint"] != right["input_fingerprint"] and (set(mapping) != set(symbols_l) or set(mapping.values()) != set(symbols_r)):
            return _comparison("UNKNOWN", "cross-sample symbol mapping is incomplete", left, right, context=context, evidence_refs=refs)
        if not context.get("symbol_mapping_evidence_refs") or any(symbols_l[k] != symbols_r[v] for k, v in mapping.items()):
            return _comparison("UNKNOWN", "symbol mapping lacks evidence or type agreement", left, right, context=context, evidence_refs=refs)
        refs.extend(context["symbol_mapping_evidence_refs"])
    elif left["input_fingerprint"] != right["input_fingerprint"]:
        return _comparison("UNKNOWN", "cross-sample symbols lack evidenced mapping", left, right, context=context, evidence_refs=refs)
    if any(symbols_l[k] != symbols_r[k] for k in symbols_l.keys() & symbols_r.keys()):
        return _comparison("UNSUPPORTED", "conflicting types for one symbol identity", left, right, context=context, evidence_refs=refs)
    if left_p["expression"]["op"] == "unknown" or right_p["expression"]["op"] == "unknown":
        return _comparison("UNSUPPORTED", "unknown typed predicate", left, right, context=context, evidence_refs=refs)
    lp = guard_comparison_projection(left, mapping)
    rp = guard_comparison_projection(right)
    if lp == rp:
        return _comparison("EQUIVALENT", "identical canonical typed projection in mapped scope", left, right,
                           context=context, evidence_refs=[*refs, *left["evidence_refs"], *right["evidence_refs"]],
                           fast_path_projection_digest=content_digest(lp))
    # P1-T6's solve() conjoins checked side conditions. A simple XOR would
    # omit asymmetric undefinedness; leave such structural cases unresolved.
    if _has_sensitive_order(lp["expression"]) or _has_sensitive_order(rp["expression"]):
        return _comparison("UNSUPPORTED", "comparison needs joint definedness semantics", left, right,
                           context=context, evidence_refs=refs)
    assertions = context.get("typed_assumptions", [])
    if not isinstance(assertions, list):
        raise ContractValidationError("typed_assumptions must be a list")
    for assertion in assertions:
        validate_term(assertion)
        if assertion["type"] != BOOL or _has_sensitive_order(assertion):
            return _comparison("UNSUPPORTED", "unsafe typed comparison assumption", left, right, context=context, evidence_refs=refs)
    if assertions and not context.get("typed_assumption_evidence_refs"):
        return _comparison("UNKNOWN", "typed assumptions lack evidence", left, right, context=context, evidence_refs=refs)
    refs.extend(context.get("typed_assumption_evidence_refs", []))
    xor = term("!=", BOOL, [lp["expression"], rp["expression"]])
    solver = backend or Z3Backend()
    timeout = int(context.get("timeout_ms", 1000))
    if timeout < 1:
        raise ContractValidationError("positive comparison timeout required")
    answer = solver.solve([*assertions, xor], timeout)
    outcome = {"UNSAT": "EQUIVALENT", "SAT": "DIFFERENT", "UNKNOWN": "UNKNOWN",
               "TIMEOUT": "TIMEOUT", "UNSUPPORTED": "UNSUPPORTED"}.get(answer["outcome"])
    if outcome is None:
        raise ContractValidationError("invalid comparison backend outcome")
    query = {"format": COMPARISON_VERSION, "assertions": [*assertions, xor],
             "scope_mapping": context.get("scope_mapping"),
             "assumption_mapping": context.get("assumption_mapping"),
             "assumptions": _sorted_unique(left_p["assumptions"]),
             "symbol_mapping": mapping, "smt2": answer.get("smt2"),
             "side_conditions": answer.get("side_conditions", []),
             "bit_widths": answer.get("bit_widths", []), "timeout_ms": timeout,
             "backend": solver.name, "backend_version": solver.version,
             "backend_options": getattr(solver, "options", {})}
    return _comparison(outcome, answer["reason"], left, right, context=context,
                       query=query, backend=solver, evidence_refs=refs)


def _validate_row(row):
    edge, guard = row["edge"], row["guard"]
    validate_artifact_envelope(edge)
    validate_guard(guard)
    validate_evidence_record(row["evidence_record"])
    if edge["payload"]["candidate_id"] != row["candidate_id"] or edge["id"] != row["candidate_id"]:
        raise ContractValidationError("candidate identity changed")
    if row["scope"]["candidate_id"] != row["candidate_id"] or edge["function_ref"] != guard["function_ref"]:
        raise ContractValidationError("refinement scope/function mismatch")
    if guard["payload"]["scope"] != row["scope"]["scope"] or edge["payload"]["guard_ref"] != {"artifact_ref": guard["id"]}:
        raise ContractValidationError("refinement Guard/scope mismatch")
    calls = {item["id"]: item for item in row["solver_evidence"]}
    if len(calls) != len(row["solver_evidence"]):
        raise ContractValidationError("duplicate SolverEvidence")
    for item in calls.values():
        validate_solver_evidence(item)
    main = calls.get(row["feasibility_evidence_ref"])
    if main is None:
        raise ContractValidationError("missing feasibility evidence")
    if main["payload"]["query_artifact"]["scope"] != row["scope"]["scope"]:
        raise ContractValidationError("feasibility query belongs to a different scope")
    actual = edge["payload"]["feasibility"]
    calculated = ({"SAT": "FEASIBLE", "UNSAT": "INFEASIBLE"}.get(main["payload"]["outcome"], "UNRESOLVED")
                  if row["scope"]["scope_completeness"] == "COMPLETE" else "UNRESOLVED")
    if actual != calculated or edge["status"]["solver"] != main["payload"]["outcome"]:
        raise ContractValidationError("feasibility evidence disagrees with partition")
    if row["evidence_record"]["id"] not in edge["evidence_refs"]:
        raise ContractValidationError("lost refinement evidence")
    return main


def build_module1_result(refinement_result, action_result, *, function_ref=None):
    """Seal a persisted P1-T6 result and pre-extracted P1-T1 actions."""
    if refinement_result["accounting"] != refinement_accounting(refinement_result["refinements"]):
        raise ContractValidationError("P1-T6 accounting mismatch")
    if refinement_result["input_fingerprint"] != action_result["input_fingerprint"]:
        raise ContractValidationError("action/refinement input fingerprints differ")
    rows = query_refinements(refinement_result, function_ref=function_ref)
    functions = {canonical_json(r["edge"]["function_ref"]): r["edge"]["function_ref"] for r in rows}
    if function_ref is None:
        if len(functions) != 1:
            raise ContractValidationError("select one function_ref for Module1Result")
        function_ref = next(iter(functions.values()))
    elif rows and canonical_json(function_ref) not in functions:
        raise ContractValidationError("function_ref does not match rows")
    actions = [deepcopy(a) for a in action_result["actions"] if a["function_ref"] == function_ref]
    for action in actions:
        validate_semantic_action(action)
    action_ids = {a["id"] for a in actions}
    if len(action_ids) != len(actions):
        raise ContractValidationError("duplicate SemanticAction")
    partitions = {value: [] for value in PARTITIONS.values()}
    guards, evidence = [], []
    evidence.extend(deepcopy(refinement_result["upstream_evidence_records"]))
    evidence.extend(deepcopy(refinement_result["propagation_evidence_records"]))
    evidence.extend(deepcopy(action_result["evidence_records"]))
    for row in rows:
        _validate_row(row)
        edge = deepcopy(row["edge"])
        for endpoint in (edge["payload"]["from"], edge["payload"]["to"]):
            if endpoint["kind"] == "ACTION" and endpoint["action_ref"] not in action_ids:
                raise ContractValidationError("edge endpoint lacks P1-T1 action")
        sealed = seal_guard(row["guard"])
        seal_evidence = evidence_record(kind="guard_canonical_seal",
            claim={"predicate": "semantic_preserving_guard_seal", "operands": {
                "candidate_id": row["candidate_id"], "guard_ref": sealed["id"]}},
            premises=[{"precanonical_guard_ref": row["guard"]["id"],
                       "feasibility_evidence_ref": row["feasibility_evidence_ref"]}],
            source_refs=row["guard"]["payload"]["source_predicate_refs"],
            artifact_refs=[row["guard"]["id"], sealed["id"], row["feasibility_evidence_ref"]],
            rule=CANONICAL_RULE, scope=row["guard"]["payload"]["scope"],
            assumptions=row["guard"]["payload"]["assumptions"],
            result={"feasibility_unchanged": edge["payload"]["feasibility"]},
            reason_code="CANONICAL_GUARD_SEAL",
            producer=producer_record("P1-T7", VERSION, {"canonical_rule": CANONICAL_RULE}),
            status=edge["status"])
        edge["payload"]["guard_ref"] = {"artifact_ref": sealed["id"]}
        edge["producer"] = producer_record("P1-T7", VERSION, {"canonical_rule": CANONICAL_RULE})
        edge["evidence_refs"] = _sorted_unique([*edge["evidence_refs"], row["guard"]["id"], seal_evidence["id"]])
        edge["extensions"] = {**edge["extensions"], "precanonical_guard_ref": row["guard"]["id"],
                              "feasibility_evidence_ref": row["feasibility_evidence_ref"]}
        partitions[PARTITIONS[edge["payload"]["feasibility"]]].append(edge)
        guards.append(sealed)
        evidence.extend([row["guard"], *row["solver_evidence"], row["evidence_record"], seal_evidence])
    for value in partitions.values():
        value.sort(key=lambda x: x["id"])
    diagnostics = _sorted_unique([*refinement_result["status"]["diagnostics"],
        *refinement_result["upstream_status"]["diagnostics"],
        *refinement_result["propagation_status"]["diagnostics"],
        *action_result["status"]["diagnostics"],
        *[d for r in rows for d in r["edge"]["status"]["diagnostics"]]])
    partial = (any(r["edge"]["payload"]["feasibility"] == "UNRESOLVED" for r in rows)
               or any(x["completion"] != "COMPLETE" for x in (
                   refinement_result["status"], refinement_result["upstream_status"],
                   refinement_result["propagation_status"], action_result["status"]))
               or bool(refinement_result["unresolved_scopes"])
               or any(d["code"] in {"UNKNOWN", "TIMEOUT", "UNSUPPORTED", "TRUNCATED",
                                     "FALLBACK", "ERROR"} for d in diagnostics))
    status = analysis_status(proof="UNKNOWN" if partial else "PROVEN",
        completion="PARTIAL" if partial else "COMPLETE", diagnostics=diagnostics)
    evidence_by_id = {}
    for item in evidence:
        previous = evidence_by_id.setdefault(item["id"], item)
        if previous != item:
            raise ContractValidationError("conflicting evidence records with one id")
    payload = {"actions": sorted(actions, key=lambda x: x["id"]), **partitions,
               "guards": sorted(guards, key=lambda x: x["id"]),
               "evidence": sorted(evidence_by_id.values(), key=lambda x: x["id"]),
               "unresolved_scopes": _sorted_unique(refinement_result["unresolved_scopes"]),
               "accounting": {"candidate_ids": sorted(r["candidate_id"] for r in rows),
                   "feasibility": {k: len(partitions[v]) for k, v in PARTITIONS.items()},
                   "upstream": refinement_result["accounting"]}}
    result = artifact_envelope(schema="erc20-research/module1-result/v1",
        artifact_id=stable_identity("module1-result", {"function_ref": function_ref,
            "input_fingerprint": refinement_result["input_fingerprint"],
            "config": refinement_result["producer"]["config_fingerprint"], "payload": payload}),
        function_ref=function_ref, input_fingerprint=refinement_result["input_fingerprint"],
        producer=producer_record("P1-T7", VERSION, {"canonical_rule": CANONICAL_RULE,
            "refinement_config_fingerprint": refinement_result["producer"]["config_fingerprint"]}),
        evidence_refs=sorted(e["id"] for e in payload["evidence"]), status=status, payload=payload)
    validate_module1_result(result)
    return result


def validate_module1_result(result):
    validate_artifact_envelope(result)
    if result["schema"] != "erc20-research/module1-result/v1":
        raise ContractValidationError("wrong Module1Result schema")
    payload = result["payload"]
    ids = [e["id"] for name in PARTITIONS.values() for e in payload[name]]
    if len(ids) != len(set(ids)) or sorted(ids) != payload["accounting"]["candidate_ids"]:
        raise ContractValidationError("candidate partition not closed/disjoint")
    guards = {g["id"]: g for g in payload["guards"]}
    for g in guards.values():
        validate_sealed_guard(g)
    for feasibility, name in PARTITIONS.items():
        for edge in payload[name]:
            validate_artifact_envelope(edge)
            if (edge["payload"]["feasibility"] != feasibility or edge["id"] != edge["payload"]["candidate_id"]
                    or edge["payload"]["guard_ref"]["artifact_ref"] not in guards):
                raise ContractValidationError("invalid sealed edge partition/Guard")
    if payload["unresolved_edges"] and result["status"]["completion"] == "COMPLETE":
        raise ContractValidationError("unresolved coverage marked COMPLETE")
    for feasibility, name in PARTITIONS.items():
        if int(payload["accounting"]["feasibility"][feasibility]) != len(payload[name]):
            raise ContractValidationError("partition accounting mismatch")


def query_module1_edges(result, *, feasibility=None, candidate_id=None):
    validate_module1_result(result)
    selected = (PARTITIONS[feasibility],) if feasibility else PARTITIONS.values()
    return canonicalize([edge for name in selected for edge in result["payload"][name]
                         if candidate_id is None or edge["id"] == candidate_id])


def serialize_module1_result(result):
    validate_module1_result(result)
    return canonical_json(result)
