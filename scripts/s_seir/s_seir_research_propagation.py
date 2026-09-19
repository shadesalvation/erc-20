#!/usr/bin/env python3
"""P1-T4 context-sensitive fixed-point propagation over the existing Fact CFG.

The engine owns scheduling, cache convergence, resource cutoffs and the
AbstractPropagation artifact.  It deliberately delegates every abstract
value/state/context operation to P1-T3 and treats the P1-T2 region as a
read-only structural scope.  Traversed CFG edges are carriers only; this file
does not reconstruct semantic successors, Guards, or solver conclusions.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping
import re
from typing import Any

from s_seir_research_abstract_domain import (
    candidate_key,
    consume_regions,
    join_states,
    local_transfer,
    normalize_state,
    stabilized,
    state_leq,
    state_status,
    unknown,
    update_context,
    validate_contract,
    validate_state,
)
from s_seir_research_contracts import (
    ContractValidationError,
    analysis_status,
    artifact_envelope,
    canonical_json,
    canonicalize,
    content_digest,
    diagnostic,
    evidence_record,
    producer_record,
    stable_identity,
    validate_analysis_status,
    validate_artifact_envelope,
    validate_evidence_record,
    validate_source_ref,
)
from s_seir_research_regions import validate_flattened_region


SCHEMA = "erc20-research/abstract-propagation/v1"
IMPLEMENTATION_VERSION = "p1-t4-v1"
PAYLOAD_FIELDS = frozenset({
    "region_ref", "domain_ref", "node_context_states", "convergence_evidence",
    "resource_budget", "termination",
})
TERMINATIONS = frozenset({"FIXED_POINT", "RESOURCE_LIMIT", "UNSUPPORTED", "ERROR"})
LIMIT_FIELDS = frozenset({
    "max_processed_items", "max_cache_entries", "max_contributions", "max_requeues",
})
COUNTER_FIELDS = (
    "processed_items", "cache_entries", "contributions", "joins", "state_updates",
    "enqueues", "requeues", "stable_contributions", "local_transfers",
    "identity_facts", "ordinary_edges", "observed_arm_edges",
)
SCHEDULING_POLICY = "canonical-seeds-and-outgoing-edges/fifo/coalesced-keys/v1"
CACHE_KEY_POLICY = "region_ref + CFG_BLOCK SourceRef + canonical ordered KSwitchContext"


class _EngineUnsupported(Exception):
    pass


class _EngineInvariant(Exception):
    pass


def _set(items):
    return [canonicalize(item) for _, item in sorted({canonical_json(x): x for x in items}.items())]


def _number(value, minimum, label):
    if isinstance(value, bool) or not re.fullmatch(r"0|[1-9][0-9]*", str(value)):
        raise ContractValidationError(f"invalid propagation budget {label}")
    result = int(value)
    if result < minimum:
        raise ContractValidationError(f"propagation budget {label} is below {minimum}")
    return result


def normalize_resource_limits(resource_limits):
    """Validate the explicit deterministic counter budget; there are no defaults."""
    if not isinstance(resource_limits, Mapping) or set(resource_limits) != LIMIT_FIELDS:
        raise ContractValidationError(
            f"resource limits require exactly {sorted(LIMIT_FIELDS)}"
        )
    return canonicalize({
        "max_processed_items": _number(resource_limits["max_processed_items"], 0, "max_processed_items"),
        "max_cache_entries": _number(resource_limits["max_cache_entries"], 1, "max_cache_entries"),
        "max_contributions": _number(resource_limits["max_contributions"], 1, "max_contributions"),
        "max_requeues": _number(resource_limits["max_requeues"], 0, "max_requeues"),
    })


def _config(domain_ref, limits):
    return canonicalize({
        "domain_ref": domain_ref,
        "resource_limits": limits,
        "scheduling_policy": SCHEDULING_POLICY,
        "state_cache_phase": "INCOMING_BEFORE_NODE_TRANSFER",
    })


def _producer(domain_ref, limits):
    return producer_record("P1-T4", IMPLEMENTATION_VERSION, _config(domain_ref, limits))


def _context_key(context):
    return canonical_json(context)


def _cache_key(node_ref, context):
    return canonical_json([node_ref, context])


def _block_id(ref):
    if not isinstance(ref, Mapping) or ref.get("source_kind") != "CFG_BLOCK":
        raise ContractValidationError("propagation node must be a CFG_BLOCK SourceRef")
    validate_source_ref(ref)
    block_id = (ref.get("locator") or {}).get("block_id")
    if block_id in {None, ""}:
        raise ContractValidationError("CFG_BLOCK SourceRef has no block_id")
    return str(block_id)


def _edge_locator_key(value):
    locator = (value.get("locator") or {}) if isinstance(value, Mapping) else value
    if not isinstance(locator, Mapping):
        return None
    return canonical_json({
        "edge_id": locator.get("edge_id"),
        "from": locator.get("from"),
        "to": locator.get("to"),
        "kind": locator.get("kind"),
    })


def _edge_key(edge):
    return canonical_json({
        "edge_id": edge.get("edge_id"), "from": edge.get("from"),
        "to": edge.get("to"), "kind": edge.get("kind"),
    })


def _candidate_binding_ids(candidate):
    return {
        item.get("ref", {}).get("binding_id")
        for direction in ("read_evidence", "write_evidence")
        for item in candidate["candidate"][direction]
        if isinstance(item, Mapping)
    } - {None, ""}


def _fact_occurrence_matches(candidate, fact):
    for ref in candidate["source_refs"]:
        locator = ref.get("locator") or {}
        if (
            ref.get("source_kind") == "SEMANTIC_NODE"
            and locator.get("semantic_id") == fact.get("semantic_id")
            and all(locator.get(field) == fact.get(field) for field in ("origin_id", "source_lang", "kind"))
        ):
            return True
    return False


def _fact_writes_candidate(candidate, fact):
    if not _fact_occurrence_matches(candidate, fact):
        return False
    write_refs = [
        item.get("ref") for item in candidate["candidate"]["write_evidence"]
        if item.get("source") == "fact_ssa"
    ]
    raw_writes = {
        item.get("value") for item in candidate["candidate"]["write_evidence"]
        if item.get("source") == "semantic_node"
    } - {None, ""}
    fact_writes = list((fact.get("fact_ssa") or {}).get("writes") or [])
    return any(item in write_refs for item in fact_writes) or bool(
        raw_writes.intersection(set(fact.get("writes") or []))
    )


def _operand_candidate(field, raw, fact, candidates):
    operand = raw.get(field)
    if not isinstance(operand, Mapping) or operand.get("is_constant"):
        return None
    text = operand.get("text")
    reads = [
        item for item in ((fact.get("fact_ssa") or {}).get("reads") or [])
        if item.get("value") == text
    ]
    if len(reads) != 1 or not reads[0].get("binding_id"):
        return None
    matches = [
        candidate for candidate in candidates
        if reads[0]["binding_id"] in _candidate_binding_ids(candidate)
    ]
    return matches[0] if len(matches) == 1 else None


def _transfer_arguments(fact, candidates):
    raw = (fact.get("evidence") or {}).get("slither") or {}
    operation = raw.get("kind") or (fact.get("semantic") or {}).get("atomic_operation")
    if operation == "Assignment":
        candidate = _operand_candidate("rvalue", raw, fact, candidates)
        return {"copy_candidate": candidate} if candidate is not None else {}
    if operation == "Binary":
        operands = {}
        for field in ("variable_left", "variable_right"):
            candidate = _operand_candidate(field, raw, fact, candidates)
            if candidate is not None:
                operands[field] = candidate
        return {"operand_candidates": operands} if operands else {}
    return {}


def _force_unknown_for_unordered(state, region, contract, relevant):
    affected = sorted({candidate_key(region, candidate) for _, candidate in relevant})
    source_refs = _set(
        ref for _, candidate in relevant for ref in candidate["source_refs"]
    )
    item = diagnostic(
        "INSUFFICIENT_EVIDENCE",
        "multiple control-state write facts lack a complete stable in-block semantic order",
        affected_refs=affected,
        scope={"region_ref": region["id"], "rule": "P1-T4_NODE_FACT_ORDER"},
    )
    result = canonicalize(state)
    for key in affected:
        result["values"][key] = unknown(
            "control-state update order is unavailable", sources=source_refs,
            diagnostics=[item],
        )
    return normalize_state(result, region, contract), item


def _transfer_node(state, node_id, prepared, region, contract):
    validate_state(state, region, contract)
    block = prepared["blocks"][node_id]
    nodes = prepared["nodes_by_block"].get(node_id, [])
    candidates = prepared["candidates"]
    relevant = [
        (fact, candidate)
        for fact in nodes
        for candidate in candidates
        if _fact_writes_candidate(candidate, fact)
    ]
    relevant_fact_ids = {str(fact.get("semantic_id")) for fact, _ in relevant}
    metrics = {
        "local_transfers": 0,
        "identity_facts": max(0, len(nodes) - len(relevant_fact_ids)),
    }
    if not relevant:
        return canonicalize(state), [], metrics

    declared_order = block.get("semantic_ids")
    if not isinstance(declared_order, list):
        declared_order = []
    if len(declared_order) != len(set(map(str, declared_order))):
        raise _EngineInvariant(f"duplicate semantic_ids in Fact CFG block {node_id}")
    positions = {str(value): index for index, value in enumerate(declared_order)}
    missing = [pair for pair in relevant if str(pair[0].get("semantic_id")) not in positions]
    if len(relevant_fact_ids) > 1 and missing:
        result, item = _force_unknown_for_unordered(state, region, contract, relevant)
        return result, [item], metrics

    relevant.sort(key=lambda pair: (
        positions.get(str(pair[0].get("semantic_id")), 0),
        str(pair[0].get("semantic_id") or ""),
        candidate_key(region, pair[1]),
    ))
    result = canonicalize(state)
    for fact, candidate in relevant:
        result = local_transfer(
            result, region, candidate, fact, contract,
            bindings=prepared["bindings"],
            **_transfer_arguments(fact, candidates),
        )
        metrics["local_transfers"] += 1
    return normalize_state(result, region, contract), [], metrics


def _prepare_sfir(sfir_payload, region):
    if not isinstance(sfir_payload, Mapping) or sfir_payload.get("schema") != "s-seir-semantic-fact-ir/v1":
        raise _EngineUnsupported("existing SFIR semantic-fact schema is unavailable")
    function_id = str((region.get("function_ref") or {}).get("sfir_function_id") or "")
    functions = [
        item for item in (sfir_payload.get("functions") or [])
        if isinstance(item, Mapping) and str(item.get("function_id") or "") == function_id
    ]
    if len(functions) != 1:
        raise _EngineUnsupported("region function cannot be resolved uniquely in existing SFIR")
    function = functions[0]
    cfg = function.get("fact_cfg")
    if not isinstance(cfg, Mapping) or not isinstance(cfg.get("blocks"), list) or not isinstance(cfg.get("edges"), list):
        raise _EngineUnsupported("function lacks a usable existing Fact CFG")
    blocks = {}
    for block in cfg["blocks"]:
        if not isinstance(block, Mapping) or block.get("block_id") in {None, ""}:
            raise _EngineUnsupported("Fact CFG contains a block without stable block_id")
        block_id = str(block["block_id"])
        if block_id in blocks:
            raise _EngineInvariant(f"duplicate Fact CFG block_id {block_id}")
        blocks[block_id] = block
    nodes = {}
    nodes_by_block = {}
    for fact in function.get("semantic_nodes") or []:
        if not isinstance(fact, Mapping) or fact.get("semantic_id") in {None, ""}:
            raise _EngineUnsupported("semantic fact lacks stable semantic_id")
        semantic_id = str(fact["semantic_id"])
        if semantic_id in nodes:
            raise _EngineInvariant(f"duplicate semantic_id {semantic_id}")
        nodes[semantic_id] = fact
        placement = fact.get("placement") or {}
        if placement.get("status") == "anchored" and placement.get("anchor_block") not in {None, ""}:
            nodes_by_block.setdefault(str(placement["anchor_block"]), []).append(fact)
    for block_id, values in nodes_by_block.items():
        declared = blocks.get(block_id, {}).get("semantic_ids") or []
        position = {str(value): index for index, value in enumerate(declared)}
        values.sort(key=lambda fact: (position.get(str(fact["semantic_id"]), 1 << 60), str(fact["semantic_id"])))

    payload = region["payload"]
    node_refs = {}
    for ref in payload["region_cfg_refs"]:
        node_refs[_block_id(ref)] = ref
    core_ids = set(node_refs)
    exit_edges = {}
    for item in payload["exits"]:
        edge_ref = item.get("edge_ref")
        target_ref = item.get("to_ref")
        if edge_ref is not None:
            exit_edges[_edge_locator_key(edge_ref)] = edge_ref
        if target_ref is not None:
            node_refs.setdefault(_block_id(target_ref), target_ref)
    for item in payload["entries"]:
        target_ref = item.get("to_ref")
        if target_ref is not None:
            node_refs.setdefault(_block_id(target_ref), target_ref)
    missing_blocks = sorted(set(node_refs).difference(blocks))
    if missing_blocks:
        raise _EngineUnsupported(f"region/boundary CFG blocks are absent: {missing_blocks}")

    cases = {_edge_locator_key(item["edge_ref"]): item["edge_ref"] for item in payload["cases"]}
    outgoing = {block_id: [] for block_id in node_refs}
    for edge in cfg["edges"]:
        if not isinstance(edge, Mapping) or edge.get("from") in {None, ""} or edge.get("to") in {None, ""}:
            raise _EngineUnsupported("Fact CFG edge lacks endpoints")
        source, target = str(edge["from"]), str(edge["to"])
        key = _edge_key(edge)
        allowed = source in core_ids and (
            target in core_ids or key in exit_edges
        )
        if allowed:
            if target not in node_refs:
                raise _EngineUnsupported("explicit region exit target has no boundary SourceRef")
            outgoing[source].append({
                "edge": edge,
                "target_ref": node_refs[target],
                "observed_arm_ref": cases.get(key),
            })
    for values in outgoing.values():
        values.sort(key=lambda item: canonical_json({
            "edge": item["edge"], "target_ref": item["target_ref"],
            "observed_arm_ref": item["observed_arm_ref"],
        }))

    seeds = []
    for entry in payload["entries"]:
        target_ref = entry.get("to_ref")
        if target_ref is None or _block_id(target_ref) not in core_ids:
            raise _EngineUnsupported("region entry does not resolve to its core CFG scope")
        seeds.append({"entry": entry, "target_ref": target_ref})
    if not seeds:
        raise _EngineUnsupported("consumable region has no propagation entry")
    seeds.sort(key=canonical_json)
    return {
        "function": function,
        "blocks": blocks,
        "nodes_by_block": nodes_by_block,
        "bindings": list((function.get("fact_ssa") or {}).get("bindings") or []),
        "node_refs": node_refs,
        "core_ids": core_ids,
        "outgoing": outgoing,
        "seeds": seeds,
        "candidates": sorted(payload["control_state_candidates"], key=lambda item: candidate_key(region, item)),
    }


def _frontier(queue, cache, pending=None):
    records = []
    if pending is not None:
        records.append(canonicalize({"kind": "PENDING_CONTRIBUTION", **pending}))
    for key in queue:
        item = cache[key]
        records.append(canonicalize({
            "kind": "QUEUED_CACHE_KEY", "node_ref": item["node_ref"],
            "context": item["context"], "state": item["state"],
        }))
    return sorted(records, key=canonical_json)


def _node_context_states(cache, region, contract, evidence_refs):
    out = []
    for item in cache.values():
        state = normalize_state(item["state"], region, contract)
        out.append(canonicalize({
            "node_ref": item["node_ref"],
            "context": item["context"],
            "state": state,
            "status": state_status(state),
            "evidence_refs": list(evidence_refs),
        }))
    return sorted(out, key=lambda item: canonical_json([item["node_ref"], item["context"]]))


def _status_for(termination, node_states, inherited_status, diagnostics):
    all_diagnostics = list(inherited_status["diagnostics"]) + list(diagnostics)
    for item in node_states:
        all_diagnostics.extend(item["status"]["diagnostics"])
    incomplete = (
        termination != "FIXED_POINT"
        or inherited_status["completion"] != "COMPLETE"
        or any(item["status"]["completion"] != "COMPLETE" for item in node_states)
        or bool(all_diagnostics)
    )
    return analysis_status(
        proof="UNKNOWN" if incomplete else "CANDIDATE",
        completion="PARTIAL" if incomplete else "COMPLETE",
        diagnostics=_set(all_diagnostics),
    )


def _artifact(region, contract, limits, cache, counters, seeds, queue, termination,
              termination_reason, inherited_status, diagnostics, cutoff=None, pending=None):
    producer = _producer(contract["id"], limits)
    evidence_refs = list(region["evidence_refs"])
    node_states = _node_context_states(cache, region, contract, evidence_refs)
    status = _status_for(termination, node_states, inherited_status, diagnostics)
    resource_budget = canonicalize({
        "policy": "deterministic-counters-only",
        "limits": limits,
        "used": {key: counters[key] for key in COUNTER_FIELDS},
        "cutoff": cutoff,
    })
    convergence = canonicalize({
        "engine_version": IMPLEMENTATION_VERSION,
        "region_ref": region["id"],
        "domain_ref": contract["id"],
        "config_ref": content_digest(_config(contract["id"], limits)),
        "cache_key_policy": CACHE_KEY_POLICY,
        "state_cache_phase": "INCOMING_BEFORE_NODE_TRANSFER",
        "seed_scope": [item["entry"] for item in seeds],
        "statistics": {key: counters[key] for key in COUNTER_FIELDS},
        "final_node_context_count": len(node_states),
        "unknown_node_context_count": sum(
            any(value["tag"] == "UNKNOWN" for value in item["state"]["values"].values())
            for item in node_states
        ),
        "worklist": {
            "policy": SCHEDULING_POLICY,
            "drained": termination == "FIXED_POINT" and not queue and pending is None,
            "frontier": _frontier(queue, cache, pending),
        },
        "termination_reason": termination_reason,
        "inherited_status": inherited_status,
        "diagnostics": _set(diagnostics),
        "semantic_claims": {
            "real_successor": False, "guard": False, "feasibility": False,
            "solver_outcome": "NOT_RUN",
        },
    })
    identity_basis = {
        "region_ref": region["id"], "domain_ref": contract["id"],
        "config": _config(contract["id"], limits),
    }
    artifact_id = stable_identity("abstract-propagation", identity_basis)
    claim = {
        "predicate": "context_sensitive_propagation_termination",
        "operands": {
            "artifact_ref": artifact_id, "region_ref": region["id"],
            "domain_ref": contract["id"], "termination": termination,
        },
    }
    evidence = evidence_record(
        kind="abstract_propagation_convergence",
        claim=claim,
        premises=[
            {"flattened_region_ref": region["id"]},
            {"abstract_domain_contract_ref": contract["id"]},
            {"scheduling_policy": SCHEDULING_POLICY},
        ],
        source_refs=[item["node_ref"] for item in node_states],
        artifact_refs=[region["id"], contract["id"]],
        rule=IMPLEMENTATION_VERSION,
        scope={"region_ref": region["id"], "config_ref": convergence["config_ref"]},
        assumptions=["structural CFG traversal is not a real-successor or feasibility claim"],
        result={
            "termination": termination,
            "reason": termination_reason,
            "statistics": convergence["statistics"],
            "worklist_drained": convergence["worklist"]["drained"],
        },
        reason_code=termination,
        producer=producer,
        status=status,
    )
    artifact = artifact_envelope(
        schema=SCHEMA,
        artifact_id=artifact_id,
        function_ref=region["function_ref"],
        input_fingerprint=region["input_fingerprint"],
        producer=producer,
        evidence_refs=[evidence["id"]],
        status=status,
        payload={
            "region_ref": region["id"],
            "domain_ref": contract["id"],
            "node_context_states": node_states,
            "convergence_evidence": convergence,
            "resource_budget": resource_budget,
            "termination": termination,
        },
    )
    validate_propagation(artifact, region=region, contract=contract)
    return artifact, evidence


def _run_one(sfir_payload, region, binding, contract, limits):
    cache = {}
    queue = deque()
    queued = set()
    counters = {key: 0 for key in COUNTER_FIELDS}
    diagnostics = []
    cutoff = None
    pending = None

    def stop(counter, limit, contribution=None):
        nonlocal cutoff, pending
        cutoff = canonicalize({"counter": counter, "limit": limit})
        pending = contribution

    def contribute(node_ref, context, incoming, origin):
        nonlocal pending
        pending_record = canonicalize({
            "node_ref": node_ref, "context": context, "state": incoming, "origin": origin,
        })
        if counters["contributions"] >= int(limits["max_contributions"]):
            stop("max_contributions", limits["max_contributions"], pending_record)
            return False
        key = _cache_key(node_ref, context)
        if key not in cache and len(cache) >= int(limits["max_cache_entries"]):
            stop("max_cache_entries", limits["max_cache_entries"], pending_record)
            return False
        counters["contributions"] += 1
        if key not in cache:
            normalized = normalize_state(incoming, region, contract)
            cache[key] = {"node_ref": node_ref, "context": canonicalize(context), "state": normalized}
            counters["cache_entries"] = len(cache)
            counters["state_updates"] += 1
            queue.append(key)
            queued.add(key)
            counters["enqueues"] += 1
            return True
        counters["joins"] += 1
        current = cache[key]["state"]
        merged = join_states(current, incoming, region, contract)
        if not state_leq(current, context, merged, context, region, contract):
            raise _EngineInvariant("P1-T3 join result does not subsume cached state")
        if stabilized(current, context, merged, context, region, contract):
            counters["stable_contributions"] += 1
            return True
        cache[key]["state"] = merged
        counters["state_updates"] += 1
        if key not in queued:
            if counters["requeues"] >= int(limits["max_requeues"]):
                stop("max_requeues", limits["max_requeues"], canonicalize({
                    "node_ref": node_ref,
                    "context": context,
                    "state": merged,
                    "origin": {"kind": "CACHE_REPROCESS_REQUIRED", "prior_origin": origin},
                }))
                return False
            queue.append(key)
            queued.add(key)
            counters["enqueues"] += 1
            counters["requeues"] += 1
        return True

    prepared = _prepare_sfir(sfir_payload, region)
    for seed in prepared["seeds"]:
        if not contribute(
            seed["target_ref"], binding["context"], binding["initial_state"],
            {"kind": "REGION_ENTRY", "entry": seed["entry"]},
        ):
            break

    while queue and cutoff is None:
        if counters["processed_items"] >= int(limits["max_processed_items"]):
            stop("max_processed_items", limits["max_processed_items"])
            break
        key = queue.popleft()
        queued.remove(key)
        item = cache[key]
        counters["processed_items"] += 1
        node_id = _block_id(item["node_ref"])
        outgoing_state, local_diagnostics, metrics = _transfer_node(
            item["state"], node_id, prepared, region, contract
        )
        diagnostics.extend(local_diagnostics)
        counters["local_transfers"] += metrics["local_transfers"]
        counters["identity_facts"] += metrics["identity_facts"]
        for edge_item in prepared["outgoing"].get(node_id, []):
            context = item["context"]
            arm_ref = edge_item["observed_arm_ref"]
            if arm_ref is not None:
                context = update_context(context, region, arm_ref, contract)
                counters["observed_arm_edges"] += 1
            else:
                counters["ordinary_edges"] += 1
            if not contribute(
                edge_item["target_ref"], context, outgoing_state,
                {"kind": "CFG_EDGE", "edge": _edge_key(edge_item["edge"])},
            ):
                break

    if cutoff is not None:
        diagnostics.append(diagnostic(
            "TRUNCATED", "deterministic propagation resource limit reached",
            affected_refs=[region["id"]],
            scope={"counter": cutoff["counter"], "limit": cutoff["limit"]},
        ))
        termination = "RESOURCE_LIMIT"
        reason = f"resource limit {cutoff['counter']} reached"
    else:
        if queue:
            raise _EngineInvariant("fixed point requested with nonempty worklist")
        termination = "FIXED_POINT"
        reason = "worklist drained after all queued contributions stabilized"
    return _artifact(
        region, contract, limits, cache, counters, prepared["seeds"], queue,
        termination, reason, binding["initial_state"]["upstream_status"],
        diagnostics, cutoff=cutoff, pending=pending,
    )


def _failed_one(region, binding, contract, limits, termination, reason):
    code = "UNSUPPORTED" if termination == "UNSUPPORTED" else "ERROR"
    item = diagnostic(
        code, reason, affected_refs=[region["id"]],
        scope={"stage": "P1-T4", "region_ref": region["id"]},
    )
    counters = {key: 0 for key in COUNTER_FIELDS}
    seeds = [{"entry": item} for item in region["payload"]["entries"]]
    return _artifact(
        region, contract, limits, {}, counters, seeds, deque(), termination, reason,
        binding["initial_state"]["upstream_status"], [item],
    )


def propagate_regions(sfir_payload, upstream_result, contract, *, resource_limits):
    """Produce one AbstractPropagation per consumable P1-T2 region.

    Contract/intake violations are raised explicitly.  Once a valid binding is
    available, an engine-level unsupported construct or invariant failure is
    mapped to that region's UNSUPPORTED or ERROR artifact respectively.
    """
    validate_contract(contract)
    limits = normalize_resource_limits(resource_limits)
    intake = consume_regions(upstream_result, contract)
    regions = {item["id"]: item for item in upstream_result["regions"]}
    propagations = []
    evidence = []
    for binding in intake["bindings"]:
        region = regions[binding["region_ref"]]
        try:
            artifact, record = _run_one(sfir_payload, region, binding, contract, limits)
        except _EngineUnsupported as exc:
            artifact, record = _failed_one(region, binding, contract, limits, "UNSUPPORTED", str(exc))
        except (_EngineInvariant, ContractValidationError) as exc:
            artifact, record = _failed_one(region, binding, contract, limits, "ERROR", str(exc))
        except Exception as exc:  # explicit ERROR artifact; never reinterpret a bug as UNKNOWN/fixed point
            artifact, record = _failed_one(
                region, binding, contract, limits, "ERROR",
                f"unexpected {type(exc).__name__}: {exc}",
            )
        propagations.append(artifact)
        evidence.append(record)
    propagations.sort(key=lambda item: item["id"])
    evidence.sort(key=lambda item: item["id"])
    diagnostics = list(intake["status"]["diagnostics"])
    diagnostics.extend(
        item for artifact in propagations for item in artifact["status"]["diagnostics"]
    )
    partial = (
        intake["status"]["completion"] != "COMPLETE"
        or any(item["status"]["completion"] != "COMPLETE" for item in propagations)
        or any(item["payload"]["termination"] != "FIXED_POINT" for item in propagations)
    )
    status = analysis_status(
        proof="UNKNOWN" if partial or not propagations else "CANDIDATE",
        completion="PARTIAL" if partial else "COMPLETE",
        diagnostics=_set(diagnostics),
    )
    return canonicalize({
        "domain_ref": contract["id"],
        "intake_outcome": intake["outcome"],
        "propagations": propagations,
        "evidence_records": evidence,
        "status": status,
        "intake": intake,
    })


def validate_propagation(artifact, *, region=None, contract=None):
    validate_artifact_envelope(artifact)
    if artifact["schema"] != SCHEMA or set(artifact["payload"]) != PAYLOAD_FIELDS:
        raise ContractValidationError("AbstractPropagation schema/payload mismatch")
    payload = artifact["payload"]
    if payload["termination"] not in TERMINATIONS:
        raise ContractValidationError("invalid AbstractPropagation termination")
    if region is not None:
        validate_flattened_region(region)
        if (
            payload["region_ref"] != region["id"]
            or artifact["function_ref"] != region["function_ref"]
            or artifact["input_fingerprint"] != region["input_fingerprint"]
        ):
            raise ContractValidationError("AbstractPropagation region scope mismatch")
    if contract is not None:
        validate_contract(contract)
        if payload["domain_ref"] != contract["id"]:
            raise ContractValidationError("AbstractPropagation domain scope mismatch")
    budget = payload["resource_budget"]
    if not isinstance(budget, Mapping) or set(budget) != {"policy", "limits", "used", "cutoff"}:
        raise ContractValidationError("invalid AbstractPropagation resource_budget")
    limits = normalize_resource_limits(budget["limits"])
    if budget["policy"] != "deterministic-counters-only" or set(budget["used"]) != set(COUNTER_FIELDS):
        raise ContractValidationError("unsupported resource budget policy/counters")
    if artifact["producer"] != _producer(payload["domain_ref"], limits):
        raise ContractValidationError("AbstractPropagation producer/config mismatch")
    expected_id = stable_identity("abstract-propagation", {
        "region_ref": payload["region_ref"], "domain_ref": payload["domain_ref"],
        "config": _config(payload["domain_ref"], limits),
    })
    if artifact["id"] != expected_id:
        raise ContractValidationError("AbstractPropagation identity mismatch")
    if not isinstance(payload["node_context_states"], list):
        raise ContractValidationError("node_context_states must be a list")
    seen = set()
    for item in payload["node_context_states"]:
        if set(item) != {"node_ref", "context", "state", "status", "evidence_refs"}:
            raise ContractValidationError("invalid node/context state entry")
        validate_source_ref(item["node_ref"])
        if item["node_ref"]["source_kind"] != "CFG_BLOCK":
            raise ContractValidationError("node/context entry is not a CFG block")
        validate_analysis_status(item["status"])
        key = _cache_key(item["node_ref"], item["context"])
        if key in seen:
            raise ContractValidationError("duplicate node/context cache entry")
        seen.add(key)
        if region is not None and contract is not None:
            validate_state(item["state"], region, contract)
            if not item["state"]["reached"]:
                raise ContractValidationError("unreached states must be omitted from propagation cache")
            if item["status"] != state_status(item["state"]):
                raise ContractValidationError("node/context state status mismatch")
            if not stabilized(item["state"], item["context"], item["state"], item["context"], region, contract):
                raise ContractValidationError("noncanonical node/context state")
    convergence = payload["convergence_evidence"]
    if not isinstance(convergence, Mapping):
        raise ContractValidationError("convergence_evidence must be an object")
    if (
        convergence.get("region_ref") != payload["region_ref"]
        or convergence.get("domain_ref") != payload["domain_ref"]
        or str(convergence.get("final_node_context_count")) != str(len(payload["node_context_states"]))
        or convergence.get("config_ref") != content_digest(_config(payload["domain_ref"], limits))
    ):
        raise ContractValidationError("convergence evidence scope/count/config mismatch")
    worklist = convergence.get("worklist") or {}
    drained = worklist.get("drained") is True
    frontier = worklist.get("frontier")
    if not isinstance(frontier, list):
        raise ContractValidationError("convergence frontier must be a list")
    if payload["termination"] == "FIXED_POINT" and (not drained or frontier):
        raise ContractValidationError("FIXED_POINT requires a drained empty frontier")
    if payload["termination"] != "FIXED_POINT" and drained:
        raise ContractValidationError("non-fixed termination cannot claim a drained fixed point")
    if payload["termination"] == "RESOURCE_LIMIT" and budget["cutoff"] is None:
        raise ContractValidationError("RESOURCE_LIMIT requires cutoff evidence")
    if payload["termination"] != "RESOURCE_LIMIT" and budget["cutoff"] is not None:
        raise ContractValidationError("cutoff evidence is exclusive to RESOURCE_LIMIT")


def node_context_states(propagation, *, node_ref=None):
    """Consumer view over canonical cache entries; it derives no new semantics."""
    validate_propagation(propagation)
    items = propagation["payload"]["node_context_states"]
    if node_ref is None:
        return canonicalize(items)
    validate_source_ref(node_ref)
    return canonicalize([
        item for item in items if canonical_json(item["node_ref"]) == canonical_json(node_ref)
    ])


def query_node_context_state(propagation, node_ref, context):
    """Return the sole exact cache entry or None (which means unreached, not UNKNOWN)."""
    matches = [
        item for item in node_context_states(propagation, node_ref=node_ref)
        if _context_key(item["context"]) == _context_key(context)
    ]
    if len(matches) > 1:
        raise ContractValidationError("noncanonical duplicate node/context entries")
    return matches[0] if matches else None


def validate_result_evidence(result):
    """Check that propagation evidence refs resolve in the P1-T4 sidecar."""
    records = {item["id"]: item for item in result["evidence_records"]}
    for record in records.values():
        validate_evidence_record(record)
    for artifact in result["propagations"]:
        validate_propagation(artifact)
        if any(ref not in records for ref in artifact["evidence_refs"]):
            raise ContractValidationError("unresolved AbstractPropagation evidence reference")
