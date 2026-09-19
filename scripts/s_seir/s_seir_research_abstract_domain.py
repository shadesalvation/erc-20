#!/usr/bin/env python3
"""P1-T3 local domain API. Dict views below are NOT research artifacts.

Only build_contract emits a formal artifact. No CFG traversal lives here.
A caller supplies one reached local fact and, for copies, a candidate already
selected using existing operand/FactSSA evidence. No reaching definition is
inferred here. All evidence universes must be fixed by the input to P1-T4.
"""
from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from s_seir_research_contracts import (
    ContractValidationError, analysis_status, artifact_envelope, canonicalize,
    canonical_json, content_digest, diagnostic, evidence_record, producer_record,
    stable_identity, validate_analysis_status, validate_artifact_envelope,
    validate_source_ref,
    validate_evidence_record,
)
from s_seir_research_regions import validate_flattened_region

SCHEMA = "erc20-research/abstract-domain-contract/v1"
PAYLOAD_FIELDS = frozenset({
    "domain_version", "value_domain", "context_policy", "k", "transfer_rules",
    "join_rules", "context_update", "stabilization_policy",
})
VERSION = "p1-t3-domain/1"


def _set(items):
    return [canonicalize(item) for _, item in sorted({canonical_json(x): x for x in items}.items())]


def _number(value, minimum, maximum, label):
    if isinstance(value, bool) or not re.fullmatch(r"0|[1-9][0-9]*", str(value)):
        raise ContractValidationError(f"invalid {label}")
    result = int(value)
    if not minimum <= result <= maximum:
        raise ContractValidationError(f"{label} outside supported bounds")
    return result


def _rules(k, cap):
    k = _number(k, 0, 32, "k")
    cap = _number(cap, 1, 256, "finite cap")
    return canonicalize({
        "domain_version": VERSION,
        "value_domain": {"tags": ["BOTTOM", "FINITE", "TOP", "UNKNOWN"],
                         "finite_cap": cap, "types": "explicit uintN/intN; N=8..256, multiple of 8",
                         "bottom": "no reached contribution", "top": "known typed universe",
                         "unknown": "uninterpretable evidence; diagnostics retained"},
        "context_policy": {"kind": "last-k-observed-dispatch-arms", "supported_k": [0, 32],
                           "element": "region id + dispatcher SourceRef + observed arm SourceRef"},
        "k": k,
        "transfer_rules": {"version": VERSION, "supported": ["Assignment", "Binary:&|^+-*", "Phi", "TypeConversion:identity"],
                           "overflow": "CHECKED overflow -> UNKNOWN; WRAPPING -> modulo width",
                           "missing_semantics": "UNKNOWN; no source expression parsing",
                           "scope": "one candidate write fact; strict on unreached state"},
        "join_rules": {"bottom": "identity", "finite": "typed union; size > cap -> TOP",
                       "top": "absorbs known values of same type",
                       "unknown": "absorbing with diagnostic union; distinct from TOP",
                       "type_mismatch": "UNKNOWN", "evidence": "canonical set union"},
        "context_update": {"append": "validated observed arm", "truncate": "discard oldest beyond k",
                           "claim": "observed context only; no successor or feasibility claim"},
        "stabilization_policy": {"equality": "canonical state content (including diagnostics/evidence) + ordered context",
                                 "subsumption": "join(left,right) == right; identical contexts",
                                 "finite_height": "fixed candidates/types/evidence/diagnostics/arms; bounded cap and k",
                                 "widening": "none beyond cap promotion"},
    })


def build_contract(*, k, finite_cap, input_fingerprint, function_ref=None):
    """Return {contract, evidence_records}; k and cap are explicit config, no paper defaults."""
    payload = _rules(k, finite_cap)
    producer = producer_record("P1-T3", VERSION, payload)
    status = analysis_status(proof="PROVEN", completion="COMPLETE")
    evidence = evidence_record(
        kind="abstract_domain_rules", claim={"predicate": "domain_rules_declared", "operands": payload},
        premises=[], source_refs=[], artifact_refs=[], rule=VERSION, scope={"rules_digest": content_digest(payload)},
        assumptions=["definition of local rules, not a reachability proof"], result="DEFINED",
        reason_code="DOMAIN_DEFINITION", producer=producer, status=status,
    )
    contract = artifact_envelope(
        schema=SCHEMA, artifact_id=stable_identity("abstract-domain-contract", payload),
        function_ref=function_ref, input_fingerprint=input_fingerprint, producer=producer,
        evidence_refs=[evidence["id"]], status=status, payload=payload,
    )
    validate_contract(contract)
    return {"contract": contract, "evidence_records": [evidence]}


def validate_contract(contract):
    validate_artifact_envelope(contract)
    p = contract["payload"]
    if contract["schema"] != SCHEMA or set(p) != PAYLOAD_FIELDS:
        raise ContractValidationError("AbstractDomainContract schema/payload mismatch")
    expected = _rules(p["k"], p["value_domain"]["finite_cap"])
    if canonical_json(p) != canonical_json(expected):
        raise ContractValidationError("unsupported or modified domain rules")
    if contract["id"] != stable_identity("abstract-domain-contract", expected):
        raise ContractValidationError("domain identity is not rules/config digest")
    if contract["producer"] != producer_record("P1-T3", VERSION, expected):
        raise ContractValidationError("domain producer/config mismatch")


def _cap(contract):
    validate_contract(contract)
    return int(contract["payload"]["value_domain"]["finite_cap"])


def integer_type(name):
    """Decode only explicit SFIR elementary integer type labels, never infer uint width."""
    match = re.fullmatch(r"(u?int)([0-9]+)", str(name))
    if not match:
        return None
    width = int(match[2])
    if width < 8 or width > 256 or width % 8:
        return None
    return canonicalize({"name": name, "bit_width": width, "signed": match[1] == "int"})


def _limits(typ):
    width = int(typ["bit_width"])
    return (-(1 << (width - 1)), (1 << (width - 1)) - 1) if typ["signed"] else (0, (1 << width) - 1)


def _value(tag, typ=None, values=(), *, sources=(), modes=(), diagnostics=()):
    sources = _set(sources)
    for ref in sources:
        validate_source_ref(ref)
    diagnostics = _set(diagnostics)
    return canonicalize({
        "tag": tag, "type": typ, "values": sorted(set(values), key=int),
        "arithmetic_modes": sorted(set(modes)), "source_refs": sources,
        "status": analysis_status(proof="UNKNOWN" if tag == "UNKNOWN" else "CANDIDATE",
                                  completion="PARTIAL" if tag == "UNKNOWN" else "COMPLETE",
                                  diagnostics=diagnostics),
    })


def bottom():
    return _value("BOTTOM")


def unknown(reason, *, sources=(), code="INSUFFICIENT_EVIDENCE", diagnostics=(), modes=()):
    refs = _set(sources)
    item = diagnostic(code, reason, affected_refs=refs, scope="local-control-state-domain")
    return _value("UNKNOWN", sources=refs, diagnostics=[*diagnostics, item], modes=modes)


def finite(contract, type_name, values, *, sources, modes=("VALUE",)):
    """Construct finite typed values. VALUE is non-arithmetic literal/copy semantics."""
    cap = _cap(contract)
    typ = integer_type(type_name)
    if not sources:
        return unknown("missing value provenance")
    if typ is None:
        return unknown("missing or unsupported explicit type/bit width/signedness", sources=sources)
    if not modes or any(x not in {"VALUE", "CHECKED", "WRAPPING", "BITWISE"} for x in modes):
        return unknown("missing or unsupported arithmetic mode", sources=sources)
    numbers = []
    for item in values:
        if isinstance(item, bool) or not re.fullmatch(r"-?(0|[1-9][0-9]*)", str(item)):
            return unknown("unsupported typed integer literal", sources=sources)
        n = int(item)
        lo, hi = _limits(typ)
        if not lo <= n <= hi:
            return unknown("literal outside declared bit width", sources=sources)
        numbers.append(n)
    if not numbers:
        raise ContractValidationError("empty finite set is not BOTTOM or UNKNOWN")
    tag = "TOP" if len(set(numbers)) > cap else "FINITE"
    return _value(tag, typ, numbers if tag == "FINITE" else (), sources=sources, modes=modes)


def validate_value(value, contract):
    cap = _cap(contract)
    if set(value) != {"tag", "type", "values", "arithmetic_modes", "source_refs", "status"}:
        raise ContractValidationError("invalid abstract value fields")
    tag = value["tag"]
    validate_analysis_status(value["status"])
    for ref in value["source_refs"]:
        validate_source_ref(ref)
    if any(x not in {"VALUE", "CHECKED", "WRAPPING", "BITWISE"} for x in value["arithmetic_modes"]):
        raise ContractValidationError("unsupported arithmetic mode")
    if tag not in {"BOTTOM", "FINITE", "TOP", "UNKNOWN"}:
        raise ContractValidationError("invalid value tag")
    if tag == "BOTTOM":
        if canonical_json(value) != canonical_json(bottom()):
            raise ContractValidationError("BOTTOM must have no facts or diagnostics")
    elif tag == "UNKNOWN":
        if value["values"] or value["type"] is not None or not value["status"]["diagnostics"]:
            raise ContractValidationError("UNKNOWN requires reasons, not values")
        if value["status"]["proof"] != "UNKNOWN" or value["status"]["completion"] != "PARTIAL":
            raise ContractValidationError("UNKNOWN cannot have success status")
    else:
        typ = value["type"]
        if not isinstance(typ, Mapping) or typ != integer_type(typ.get("name")):
            raise ContractValidationError("missing exact typed semantics")
        if not value["source_refs"] or not value["arithmetic_modes"]:
            raise ContractValidationError("known value requires provenance and arithmetic mode")
        if value["status"] != analysis_status(proof="CANDIDATE", completion="COMPLETE"):
            raise ContractValidationError("invalid known value status")
        if tag == "TOP" and value["values"]:
            raise ContractValidationError("TOP cannot contain finite values")
        if tag == "FINITE":
            if not 1 <= len(value["values"]) <= cap:
                raise ContractValidationError("finite values outside cap")
            rebuilt = finite(contract, typ["name"], value["values"], sources=value["source_refs"], modes=value["arithmetic_modes"])
            if canonical_json(rebuilt) != canonical_json(value):
                raise ContractValidationError("noncanonical finite value")


def normalize_value(value, contract):
    """Canonicalize set-valued members without changing ordered operation operands."""
    result = canonicalize(value)
    for key in ("source_refs", "arithmetic_modes"):
        result[key] = _set(result[key])
    result["status"]["diagnostics"] = _set(result["status"]["diagnostics"])
    if any(not re.fullmatch(r"-?(0|[1-9][0-9]*)", str(x)) for x in result["values"]):
        raise ContractValidationError("invalid abstract integer")
    result["values"] = sorted(set(result["values"]), key=int)
    validate_value(result, contract)
    return result


def join_values(left, right, contract):
    """Diagnostic/evidence product join; UNKNOWN is never a successful typed TOP."""
    left, right = normalize_value(left, contract), normalize_value(right, contract)
    if left["tag"] == "BOTTOM":
        return canonicalize(right)
    if right["tag"] == "BOTTOM":
        return canonicalize(left)
    refs = _set(left["source_refs"] + right["source_refs"])
    modes = _set(left["arithmetic_modes"] + right["arithmetic_modes"])
    diags = _set(left["status"]["diagnostics"] + right["status"]["diagnostics"])
    if "UNKNOWN" in {left["tag"], right["tag"]}:
        return _value("UNKNOWN", sources=refs, modes=modes, diagnostics=diags)
    if left["type"] != right["type"]:
        return unknown("incompatible typed value domains", sources=refs, modes=modes)
    if "TOP" in {left["tag"], right["tag"]}:
        return _value("TOP", left["type"], sources=refs, modes=modes)
    return finite(contract, left["type"]["name"], left["values"] + right["values"], sources=refs, modes=modes)


def value_leq(left, right, contract):
    return canonical_json(join_values(left, right, contract)) == canonical_json(normalize_value(right, contract))


def pure_operation(operator, operands, contract, *, sources, mode=None):
    """Local typed operation only; mode must come from supplied SFIR evidence.

    Checked overflow is unresolved here (may fail), never wrapping or BOTTOM.
    This API neither generates failure paths nor solves branch conditions.
    """
    for value in operands:
        validate_value(value, contract)
    refs = _set([*sources, *(ref for value in operands for ref in value["source_refs"])])
    operand_diagnostics = [d for value in operands for d in value["status"]["diagnostics"]]
    if len(operands) != 2 or operator not in {"+", "-", "*", "&", "|", "^"}:
        return unknown("unsupported local pure operation", sources=refs, code="UNSUPPORTED", diagnostics=operand_diagnostics)
    if not sources:
        return unknown("missing operation provenance", sources=refs, diagnostics=operand_diagnostics)
    if operator in {"&", "|", "^"}:
        mode = "BITWISE"
    elif mode not in {"CHECKED", "WRAPPING"}:
        return unknown("missing arithmetic mode", sources=refs, diagnostics=operand_diagnostics)
    if any(v["tag"] == "UNKNOWN" for v in operands):
        return unknown("unknown operation operand", sources=refs, diagnostics=operand_diagnostics)
    if any(v["tag"] == "BOTTOM" for v in operands):
        return bottom()
    left, right = operands
    if left["type"] != right["type"]:
        return unknown("incompatible operation operand types", sources=refs)
    typ = left["type"]
    if any(v["tag"] == "TOP" for v in operands):
        if mode == "CHECKED":
            return unknown("checked operation may overflow", sources=refs, code="UNKNOWN")
        return _value("TOP", typ, sources=refs, modes=[mode])
    lo, hi = _limits(typ)
    width = int(typ["bit_width"])
    results = []
    for a in map(int, left["values"]):
        for b in map(int, right["values"]):
            n = {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b,
                 "&": lambda: a & b, "|": lambda: a | b, "^": lambda: a ^ b}[operator]()
            if mode == "CHECKED" and not lo <= n <= hi:
                return unknown("checked operation may overflow", sources=refs, code="UNKNOWN", modes=[mode])
            n %= 1 << width
            if typ["signed"] and n >= 1 << (width - 1):
                n -= 1 << width
            results.append(n)
    return finite(contract, typ["name"], results, sources=refs, modes=[mode])


def candidate_key(region, candidate):
    """Input-scoped handle from canonical exported record, never a display-name key."""
    validate_flattened_region(region)
    normalized = _candidate_record(candidate)
    if canonical_json(normalized) not in {canonical_json(_candidate_record(c)) for c in region["payload"]["control_state_candidates"]}:
        raise ContractValidationError("candidate is not exported by region")
    return stable_identity("control-state-handle", {"region": region["id"], "candidate": normalized})


def _candidate_record(candidate):
    result = canonicalize(candidate)
    result["source_refs"] = _set(result["source_refs"])
    for ref in result["source_refs"]:
        validate_source_ref(ref)
    for key in ("read_evidence", "write_evidence"):
        result["candidate"][key] = _set(result["candidate"][key])
    result["status"]["diagnostics"] = _set(result["status"]["diagnostics"])
    validate_analysis_status(result["status"])
    return result


def _usable(region):
    statuses = [region["status"], *(c["status"] for c in region["payload"]["control_state_candidates"])]
    return (bool(region["evidence_refs"]) and bool(region["payload"]["detection_evidence"])
            and bool(region["payload"]["control_state_candidates"])
            and all(c.get("source_refs") for c in region["payload"]["control_state_candidates"])
            and all(s["proof"] in {"CANDIDATE", "PROVEN"} and s["completion"] == "COMPLETE" and not s["diagnostics"] for s in statuses))


def make_state(region, contract, *, reached=True):
    _cap(contract)
    validate_flattened_region(region)
    if not _usable(region):
        raise ContractValidationError("region has insufficient evidence/status; consume_regions preserves diagnosis")
    if not isinstance(reached, bool):
        raise ContractValidationError("reached must be an explicit boolean")
    return canonicalize({"region_ref": region["id"], "domain_ref": contract["id"], "reached": bool(reached),
                         "values": {candidate_key(region, c): unknown("candidate has no supplied abstract value", sources=c["source_refs"])
                                    if reached else bottom() for c in region["payload"]["control_state_candidates"]},
                         "upstream_status": region["status"]})


def validate_state(state, region, contract):
    if set(state) != {"region_ref", "domain_ref", "reached", "values", "upstream_status"}:
        raise ContractValidationError("invalid abstract state fields")
    if state["region_ref"] != region["id"] or state["domain_ref"] != contract["id"]:
        raise ContractValidationError("state scope/config mismatch")
    if set(state["values"]) != {candidate_key(region, c) for c in region["payload"]["control_state_candidates"]}:
        raise ContractValidationError("state must contain exactly region candidates")
    if not isinstance(state["reached"], bool):
        raise ContractValidationError("invalid reached flag")
    validate_analysis_status(state["upstream_status"])
    for value in state["values"].values():
        validate_value(value, contract)
        if not state["reached"] and value["tag"] != "BOTTOM":
            raise ContractValidationError("unreached state must contain BOTTOM only")


def state_status(state):
    statuses = [state["upstream_status"], *(v["status"] for v in state["values"].values())]
    partial = any(s["completion"] != "COMPLETE" for s in statuses)
    return analysis_status(proof="UNKNOWN" if partial else "CANDIDATE", completion="PARTIAL" if partial else "COMPLETE",
                           diagnostics=_set(d for s in statuses for d in s["diagnostics"]))


def normalize_state(state, region, contract):
    result = canonicalize(state)
    result["values"] = {key: normalize_value(value, contract) for key, value in result["values"].items()}
    result["upstream_status"]["diagnostics"] = _set(result["upstream_status"]["diagnostics"])
    validate_state(result, region, contract)
    return result


def join_states(left, right, region, contract):
    left, right = normalize_state(left, region, contract), normalize_state(right, region, contract)
    if left["upstream_status"] != right["upstream_status"]:
        raise ContractValidationError("upstream statuses must be preserved identically")
    result = canonicalize(left)
    result["reached"] = left["reached"] or right["reached"]
    result["values"] = {key: join_values(left["values"][key], right["values"][key], contract) for key in sorted(left["values"])}
    return result


def empty_context(contract):
    validate_contract(contract)
    return {"k": contract["payload"]["k"], "elements": []}


def _validate_context(context, contract):
    validate_contract(contract)
    if set(context) != {"k", "elements"} or str(context["k"]) != contract["payload"]["k"]:
        raise ContractValidationError("context k/config mismatch")
    if len(context["elements"]) > int(context["k"]):
        raise ContractValidationError("context exceeds k")
    for elem in context["elements"]:
        if set(elem) != {"region_ref", "dispatcher_ref", "arm_ref"}:
            raise ContractValidationError("invalid context element")
        validate_source_ref(elem["dispatcher_ref"])
        validate_source_ref(elem["arm_ref"])
        if (elem["dispatcher_ref"]["source_kind"] != "CFG_BLOCK"
                or elem["arm_ref"]["source_kind"] != "CFG_EDGE"
                or elem["dispatcher_ref"]["input_fingerprint"] != contract["input_fingerprint"]
                or elem["arm_ref"]["input_fingerprint"] != contract["input_fingerprint"]):
            raise ContractValidationError("context references have wrong kind/input scope")


def update_context(context, region, arm_ref, contract):
    _validate_context(context, contract)
    validate_flattened_region(region)
    if not _usable(region):
        raise ContractValidationError("cannot append unsupported region context")
    p = region["payload"]
    if canonical_json(arm_ref) not in {canonical_json(c["edge_ref"]) for c in p["cases"]}:
        raise ContractValidationError("context arm was not observed by P1-T2")
    if canonical_json(p["dispatcher_ref"]) not in {canonical_json(x) for x in p["region_cfg_refs"]}:
        raise ContractValidationError("dispatcher absent from region CFG refs")
    elem = {"region_ref": region["id"], "dispatcher_ref": p["dispatcher_ref"], "arm_ref": arm_ref}
    k = int(context["k"])
    return canonicalize({"k": k, "elements": (context["elements"] + [elem])[-k:] if k else []})


def stabilized(left, left_context, right, right_context, region, contract):
    left, right = normalize_state(left, region, contract), normalize_state(right, region, contract)
    for state, context in ((left, left_context), (right, right_context)):
        validate_state(state, region, contract)
        _validate_context(context, contract)
    return canonical_json([left, left_context]) == canonical_json([right, right_context])


def state_leq(left, left_context, right, right_context, region, contract):
    joined = join_states(left, right, region, contract)
    return stabilized(joined, left_context, right, right_context, region, contract)


def consume_regions(upstream, contract):
    """Bind exported P1-T2 regions to config and initial views; never detect or read actions."""
    validate_contract(contract)
    validate_analysis_status(upstream["status"])
    if upstream["input_fingerprint"] != contract["input_fingerprint"]:
        raise ContractValidationError("region input fingerprint mismatch")
    diagnostics = list(upstream["status"]["diagnostics"]) + list(upstream["candidate_diagnostics"])
    evidence_ids = set()
    for record in upstream["evidence_records"]:
        validate_evidence_record(record)
        evidence_ids.add(record["id"])
    bindings, rejected = [], []
    for region in upstream["regions"]:
        validate_flattened_region(region)
        if region["input_fingerprint"] != upstream["input_fingerprint"]:
            raise ContractValidationError("region fingerprint mismatch")
        required_evidence = set(region["evidence_refs"]) | set(region["payload"]["detection_evidence"])
        missing_evidence = required_evidence - evidence_ids
        if _usable(region) and not missing_evidence:
            state = make_state(region, contract)
            # Preserve result-level incompleteness on each downstream state too.
            state["upstream_status"] = canonicalize(upstream["status"])
            bindings.append({"region_ref": region["id"], "domain_ref": contract["id"],
                             "initial_state": state, "context": empty_context(contract),
                             "evidence_refs": region["evidence_refs"],
                             "structural_input": region["payload"]})
        else:
            rejected.append(region["id"])
            diagnostics.extend(region["status"]["diagnostics"])
            diagnostics.extend(d for c in region["payload"]["control_state_candidates"] for d in c["status"]["diagnostics"])
            diagnostics.append(diagnostic("INSUFFICIENT_EVIDENCE", "region is not consumable", evidence_refs=region["evidence_refs"], affected_refs=[region["id"]], scope=region["function_ref"]))
            if missing_evidence:
                diagnostics.append(diagnostic("INSUFFICIENT_EVIDENCE", "unresolved upstream evidence references", evidence_refs=sorted(missing_evidence), affected_refs=[region["id"]], scope=region["function_ref"]))
    action_input = upstream["action_input"]
    incomplete_action = (bool(action_input.get("unanchored_action_ids")) or bool(action_input.get("unresolved_diagnostics"))
                         or any(x.get("classification") == "UNRESOLVED" for x in action_input.get("coverage", [])))
    if incomplete_action:
        diagnostics.append(diagnostic("INSUFFICIENT_EVIDENCE", "upstream action input is incomplete", affected_refs=action_input.get("unanchored_action_ids", []), scope="P1-T2 action_input"))
    partial = upstream["status"]["completion"] != "COMPLETE" or bool(diagnostics) or bool(rejected)
    status = analysis_status(proof="CANDIDATE" if bindings else "UNKNOWN", completion="PARTIAL" if partial else "COMPLETE", diagnostics=_set(diagnostics))
    for binding in bindings:
        binding["initial_state"]["upstream_status"] = status
    return canonicalize({"domain_ref": contract["id"], "bindings": sorted(bindings, key=lambda x: x["region_ref"]),
                         "outcome": "BOUND" if bindings else "NO_CONSUMABLE_REGION" if partial else "NO_REGION",
                         "rejected_region_refs": sorted(rejected), "status": status,
                         "upstream_status": upstream["status"], "candidate_diagnostics": upstream["candidate_diagnostics"],
                         "action_input": action_input, "upstream_evidence_records": upstream["evidence_records"]})


def _binding_type(candidate, bindings):
    ids = {x.get("ref", {}).get("binding_id") for key in ("read_evidence", "write_evidence") for x in candidate["candidate"][key]}
    types = {b.get("type") for b in bindings if b.get("binding_id") in ids - {None}}
    return next(iter(types)) if len(types) == 1 else None


def _literal(operand, contract, refs):
    if not isinstance(operand, Mapping) or not operand.get("is_constant"):
        return unknown("operand is not an explicit typed literal", sources=refs)
    return finite(contract, operand.get("type"), [operand.get("text")], sources=refs)


def _read_candidate(state, region, candidate, operand, fact, contract, bindings, refs):
    key = candidate_key(region, candidate)
    binding_ids = {x.get("ref", {}).get("binding_id")
                   for direction in ("read_evidence", "write_evidence")
                   for x in candidate["candidate"][direction]} - {None}
    operand_text = operand.get("text") if isinstance(operand, Mapping) else operand
    reads = [r for r in (fact.get("fact_ssa") or {}).get("reads", []) if r.get("value") == operand_text]
    if len(reads) != 1 or reads[0].get("binding_id") not in binding_ids:
        return unknown("copy operand has no unique candidate binding", sources=refs)
    typ = operand.get("type") if isinstance(operand, Mapping) else _binding_type(candidate, bindings)
    value = normalize_value(state["values"][key], contract)
    if integer_type(typ) is None:
        return unknown("missing operand type/bit width/signedness", sources=refs)
    if value["tag"] in {"FINITE", "TOP"} and value["type"] != integer_type(typ):
        return unknown("conflicting candidate operand type", sources=refs)
    if value["tag"] != "BOTTOM":
        value["source_refs"] = _set(value["source_refs"] + refs)
    return value


def local_transfer(state, region, candidate, fact, contract, *, bindings=(), copy_candidate=None, operand_candidates=None):
    """Interpret one existing candidate write, never fetch actions or walk CFG.

    `bindings` is the existing function.fact_ssa.bindings list. Optional
    copy_candidate must be another exported candidate whose existing read
    evidence matches this fact's explicit operand; this is not a def-use search.
    """
    state = normalize_state(state, region, contract)
    key = candidate_key(region, candidate)
    refs = [r for r in candidate["source_refs"] if r["source_kind"] == "SEMANTIC_NODE" and r["locator"].get("semantic_id") == fact.get("semantic_id")]
    if not refs:
        raise ContractValidationError("fact is not part of candidate evidence")
    if any(any(ref["locator"].get(field) != fact.get(field) for field in ("origin_id", "source_lang", "kind")) for ref in refs):
        raise ContractValidationError("fact occurrence does not match exported candidate SourceRef")
    if not state["reached"]:
        return canonicalize(state)
    sem = fact.get("semantic") or {}
    raw = (fact.get("evidence") or {}).get("slither") or {}
    op = raw.get("kind") or sem.get("atomic_operation")
    write_refs = [x.get("ref") for x in candidate["candidate"]["write_evidence"] if x.get("source") == "fact_ssa"]
    raw_writes = [x.get("value") for x in candidate["candidate"]["write_evidence"] if x.get("source") == "semantic_node"]
    matching_write = any(x in write_refs for x in (fact.get("fact_ssa") or {}).get("writes", [])) or any(x in raw_writes for x in fact.get("writes", []))
    target_type = (raw.get("lvalue") or {}).get("type") if isinstance(raw.get("lvalue"), Mapping) else None
    binding_type = _binding_type(candidate, bindings)
    target_type = target_type or binding_type
    if not matching_write:
        value = unknown("fact has no explicit candidate write association", sources=refs)
    elif integer_type(target_type) is None:
        value = unknown("missing target type/bit width/signedness", sources=refs)
    elif binding_type and target_type != binding_type:
        value = unknown("conflicting target type evidence", sources=refs)
    elif op == "Assignment":
        operand = raw.get("rvalue")
        if isinstance(operand, Mapping) and operand.get("is_constant"):
            value = _literal(operand, contract, refs)
        elif copy_candidate is not None:
            value = _read_candidate(state, region, copy_candidate, operand if raw else fact.get("rvalue"), fact, contract, bindings, refs)
        elif not raw and re.fullmatch(r"-?(0|[1-9][0-9]*)", str(fact.get("rvalue"))):
            # Atomic SFIR Assignment already identifies a literal RHS; only
            # decode its integer token, not any expression/source syntax.
            value = finite(contract, target_type, [fact["rvalue"]], sources=refs)
        else:
            value = unknown("assignment operand semantics unavailable", sources=refs)
    elif op == "Binary":
        operand_candidates = operand_candidates or {}
        if set(operand_candidates) - {"variable_left", "variable_right"}:
            raise ContractValidationError("unsupported Binary operand field")
        operands = [
            _read_candidate(state, region, operand_candidates[field], raw.get(field), fact, contract, bindings, refs)
            if field in operand_candidates else _literal(raw.get(field), contract, refs)
            for field in ("variable_left", "variable_right")
        ]
        checked = raw.get("checked", sem.get("checked"))
        mode = "CHECKED" if checked is True else "WRAPPING" if checked is False else None
        value = pure_operation(raw.get("operator") or sem.get("operator"), operands, contract, sources=refs, mode=mode)
    elif op == "Phi":
        alternatives = raw.get("rvalues")
        if not isinstance(alternatives, list) or not alternatives:
            value = unknown("missing explicit Phi alternatives", sources=refs)
        else:
            value = bottom()
            for operand in alternatives:
                value = join_values(value, _literal(operand, contract, refs), contract)
    elif op == "TypeConversion":
        value = _literal(raw.get("variable"), contract, refs)
        if value["type"] != integer_type(target_type):
            value = unknown("unsupported nonidentity cast", sources=refs, code="UNSUPPORTED")
    else:
        value = unknown("unsupported control-state local operation", sources=refs, code="UNSUPPORTED")
    if value["tag"] in {"FINITE", "TOP"} and value["type"] != integer_type(target_type):
        value = unknown("assignment requires unsupported conversion", sources=refs, code="UNSUPPORTED")
    result = canonicalize(state)
    result["values"][key] = value
    return result
