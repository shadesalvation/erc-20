#!/usr/bin/env python3
"""P1-T2: detect candidate dispatcher-based flattened regions in SFIR.

The detector is deliberately structural.  It consumes the existing Fact CFG
and P1-T1 SemanticActions, records local read/write evidence for a possible
control-state value, and never attempts value propagation, path feasibility,
Guard recovery, fixed-point interpretation, or solver work.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

from s_seir_research_actions import validate_semantic_action
from s_seir_research_contracts import (
    ContractValidationError,
    analysis_status,
    artifact_envelope,
    canonical_json,
    canonicalize,
    diagnostic,
    evidence_record,
    producer_record,
    source_ref,
    stable_identity,
    validate_artifact_envelope,
)


SFIR_SCHEMA = "s-seir-semantic-fact-ir/v1"
FLATTENED_REGION_SCHEMA = "erc20-research/flattened-region/v1"
IMPLEMENTATION_VERSION = "p1-t2-v1"
REGION_PAYLOAD_FIELDS = frozenset({
    "dispatcher_ref", "region_cfg_refs", "control_state_candidates", "cases",
    "entries", "exits", "detection_evidence",
})


def detect_flattened_regions(
    sfir_payload: Mapping[str, Any],
    action_result: Mapping[str, Any],
    *,
    input_fingerprint: str | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return P1-T2 structural candidates without modifying P1-T1 output.

    A candidate requires a multi-arm dispatcher, at least two distinct arms
    that structurally return to it, and an existing SFIR read/write association
    for a value used at the dispatcher.  These facts are candidate evidence,
    not reachability, feasibility, Guard, or execution-order proofs.
    """

    if not isinstance(sfir_payload, Mapping) or not isinstance(action_result, Mapping):
        raise ContractValidationError("region detection requires SFIR and SemanticAction result objects")
    config_value = dict(config or {})
    producer = producer_record("P1-T2", IMPLEMENTATION_VERSION, config_value)
    fingerprint = str(input_fingerprint or action_result.get("input_fingerprint") or "")
    if not fingerprint:
        raise ContractValidationError("region detection requires the P1-T1 input fingerprint")
    action_fingerprint = str(action_result.get("input_fingerprint") or "")
    if action_fingerprint and action_fingerprint != fingerprint:
        raise ContractValidationError("SFIR/action input fingerprint mismatch")

    actions = list(action_result.get("actions") or [])
    for action in actions:
        validate_semantic_action(action)
        if action.get("input_fingerprint") != fingerprint:
            raise ContractValidationError("SemanticAction fingerprint does not match detector input")

    action_input = _action_input_summary(action_result, actions)
    if sfir_payload.get("schema") != SFIR_SCHEMA:
        item = diagnostic(
            "UNSUPPORTED",
            f"unsupported input schema {sfir_payload.get('schema')!r}; expected {SFIR_SCHEMA}",
            scope={"schema": sfir_payload.get("schema")},
        )
        return canonicalize({
            "input_schema": sfir_payload.get("schema"),
            "input_fingerprint": fingerprint,
            "producer": producer,
            "regions": [],
            "evidence_records": [],
            "candidate_diagnostics": [item],
            "action_input": action_input,
            "status": analysis_status(
                proof="UNKNOWN", completion="PARTIAL", solver="NOT_RUN", diagnostics=[item]
            ),
        })

    regions: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    candidate_diagnostics: list[dict[str, Any]] = []
    functions = [item for item in sfir_payload.get("functions") or [] if isinstance(item, Mapping)]
    actions_by_function = _actions_by_function(actions)

    for function in functions:
        function_id = str(function.get("function_id") or "")
        function_actions = actions_by_function.get(function_id, [])
        result = _detect_function(
            function=function,
            function_actions=function_actions,
            fingerprint=fingerprint,
            producer=producer,
        )
        regions.extend(result["regions"])
        evidence.extend(result["evidence"])
        candidate_diagnostics.extend(result["diagnostics"])

    if not functions:
        candidate_diagnostics.append(diagnostic(
            "INSUFFICIENT_EVIDENCE", "valid SFIR payload contains no function records",
            scope={"input_fingerprint": fingerprint},
        ))

    regions.sort(key=lambda item: item["id"])
    evidence = _dedupe_by_id(evidence)
    candidate_diagnostics = _dedupe_records(candidate_diagnostics)
    upstream_partial = (
        (action_result.get("status") or {}).get("completion") != "COMPLETE"
        or bool(action_input["unanchored_action_ids"])
        or bool(action_input["unresolved_diagnostics"])
        or any(item.get("classification") == "UNRESOLVED" for item in action_input["coverage"])
    )
    partial = bool(candidate_diagnostics) or upstream_partial or not functions
    result_status = analysis_status(
        proof="CANDIDATE" if regions else "UNKNOWN",
        completion="PARTIAL" if partial else "COMPLETE",
        solver="NOT_RUN",
        diagnostics=candidate_diagnostics + _upstream_action_diagnostics(action_input),
    )
    return canonicalize({
        "input_schema": SFIR_SCHEMA,
        "input_fingerprint": fingerprint,
        "producer": producer,
        "regions": regions,
        "evidence_records": evidence,
        "candidate_diagnostics": candidate_diagnostics,
        "action_input": action_input,
        "status": result_status,
    })


def validate_flattened_region(region: Mapping[str, Any]) -> None:
    validate_artifact_envelope(region)
    if region.get("schema") != FLATTENED_REGION_SCHEMA:
        raise ContractValidationError("not a frozen-v1 FlattenedRegion")
    payload = region.get("payload") or {}
    if set(payload) != REGION_PAYLOAD_FIELDS:
        raise ContractValidationError(
            "FlattenedRegion fields mismatch; "
            f"missing={sorted(REGION_PAYLOAD_FIELDS.difference(payload))}, "
            f"extra={sorted(set(payload).difference(REGION_PAYLOAD_FIELDS))}"
        )
    if not isinstance(payload["dispatcher_ref"], Mapping):
        raise ContractValidationError("FlattenedRegion dispatcher_ref must be a SourceRef")
    for key in ("region_cfg_refs", "control_state_candidates", "cases", "entries", "exits", "detection_evidence"):
        if not isinstance(payload[key], list):
            raise ContractValidationError(f"FlattenedRegion {key} must be a list")
    if region["status"]["solver"] != "NOT_RUN":
        raise ContractValidationError("P1-T2 must not run a solver")


def _detect_function(
    *,
    function: Mapping[str, Any],
    function_actions: list[Mapping[str, Any]],
    fingerprint: str,
    producer: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    function_ref = _function_ref(function, function_actions, fingerprint)
    cfg = function.get("fact_cfg") if isinstance(function.get("fact_cfg"), Mapping) else {}
    blocks = {
        str(item.get("block_id")): item
        for item in cfg.get("blocks") or []
        if isinstance(item, Mapping) and item.get("block_id")
    }
    edges = [
        item for item in cfg.get("edges") or []
        if isinstance(item, Mapping) and str(item.get("from") or "") in blocks
        and str(item.get("to") or "") in blocks
    ]
    if not blocks:
        return {"regions": [], "evidence": [], "diagnostics": [diagnostic(
            "INSUFFICIENT_EVIDENCE", "function has no usable Fact CFG blocks",
            scope={"function_ref": function_ref},
        )]}

    outgoing = {block_id: [] for block_id in blocks}
    incoming = {block_id: [] for block_id in blocks}
    for edge in edges:
        outgoing[str(edge["from"])].append(edge)
        incoming[str(edge["to"])].append(edge)
    for collection in (*outgoing.values(), *incoming.values()):
        collection.sort(key=canonical_json)
    components = _strongly_connected_components(blocks, outgoing)
    component_by_block = {
        block_id: component for component in components for block_id in component
    }
    nodes_by_block = _nodes_by_block(function)

    raw_candidates: list[dict[str, Any]] = []
    weak_diagnostics: list[dict[str, Any]] = []
    for dispatcher_id in sorted(blocks):
        dispatch_edges = outgoing[dispatcher_id]
        if not _is_multiway_dispatch(blocks[dispatcher_id], dispatch_edges):
            continue
        component = component_by_block[dispatcher_id]
        returning = [
            edge for edge in dispatch_edges
            if str(edge["to"]) != dispatcher_id
            and _reachable(str(edge["to"]), dispatcher_id, outgoing, allowed=component)
        ]
        state_candidates = _control_state_candidates(
            dispatcher_id, component, nodes_by_block, fingerprint, function_ref
        )
        # An acyclic business switch with no returning arm is an ordinary
        # negative, not an unsupported flattening candidate.  One returning
        # arm, or multiple returning arms without state evidence, is the
        # genuinely insufficient/ambiguous boundary.
        if not returning:
            continue
        if len(returning) < 2 or not state_candidates:
            reason = (
                "candidate multi-arm dispatch lacks two structurally returning arms"
                if len(returning) < 2 else
                "candidate multi-arm dispatch lacks an SFIR-local condition read/write association"
            )
            weak_diagnostics.append(diagnostic(
                "INSUFFICIENT_EVIDENCE", reason,
                affected_refs=[_block_ref(dispatcher_id, fingerprint, function_ref)],
                scope={
                    "function_ref": function_ref,
                    "returning_arm_count": len(returning),
                    "state_candidate_count": len(state_candidates),
                },
            ))
            continue
        raw_candidates.append({
            "dispatcher_id": dispatcher_id,
            "component": component,
            "dispatch_edges": dispatch_edges,
            "returning": returning,
            "state_candidates": state_candidates,
        })

    # Two credible dispatchers in the same cyclic component make the region
    # boundary ambiguous at this structural-only stage.
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for candidate in raw_candidates:
        grouped.setdefault(tuple(sorted(candidate["component"])), []).append(candidate)

    regions: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    diagnostics = list(weak_diagnostics)
    entry_blocks = set(str(item) for item in cfg.get("entry_blocks") or [])
    for component_key in sorted(grouped):
        candidates = grouped[component_key]
        if len(candidates) > 1:
            diagnostics.append(diagnostic(
                "INSUFFICIENT_EVIDENCE",
                "multiple credible dispatchers share one cyclic component; structural boundary is ambiguous",
                affected_refs=[
                    _block_ref(item["dispatcher_id"], fingerprint, function_ref)
                    for item in candidates
                ],
                scope={"function_ref": function_ref, "candidate_count": len(candidates)},
            ))
            continue
        candidate = candidates[0]
        component = set(candidate["component"])
        external_entry_targets = {
            block_id for block_id in component
            if any(str(edge["from"]) not in component for edge in incoming[block_id])
        }
        if len(external_entry_targets) > 1:
            diagnostics.append(diagnostic(
                "UNSUPPORTED", "candidate cyclic region has multiple external entry targets (irreducible control)",
                affected_refs=[
                    _block_ref(item, fingerprint, function_ref)
                    for item in sorted(external_entry_targets)
                ],
                scope={"function_ref": function_ref, "control_shape": "MULTI_ENTRY_SCC"},
            ))
            continue
        exit_edges = [
            edge for block_id in component for edge in outgoing[block_id]
            if str(edge["to"]) not in component
        ]
        terminal_blocks = [
            block_id for block_id in component
            if not outgoing[block_id] and _is_terminal(blocks[block_id])
        ]
        if not exit_edges and not terminal_blocks:
            diagnostics.append(diagnostic(
                "UNSUPPORTED", "candidate cyclic region has no structural exit",
                affected_refs=[_block_ref(candidate["dispatcher_id"], fingerprint, function_ref)],
                scope={"function_ref": function_ref, "control_shape": "NO_EXIT"},
            ))
            continue

        built = _build_region(
            candidate=candidate,
            component=component,
            blocks=blocks,
            incoming=incoming,
            outgoing=outgoing,
            entry_blocks=entry_blocks,
            exit_edges=exit_edges,
            terminal_blocks=terminal_blocks,
            function_actions=function_actions,
            fingerprint=fingerprint,
            function_ref=function_ref,
            producer=producer,
        )
        regions.append(built["region"])
        evidence.extend(built["evidence"])
    return {"regions": regions, "evidence": evidence, "diagnostics": diagnostics}


def _build_region(
    *,
    candidate: Mapping[str, Any],
    component: set[str],
    blocks: Mapping[str, Mapping[str, Any]],
    incoming: Mapping[str, list[Mapping[str, Any]]],
    outgoing: Mapping[str, list[Mapping[str, Any]]],
    entry_blocks: set[str],
    exit_edges: list[Mapping[str, Any]],
    terminal_blocks: list[str],
    function_actions: list[Mapping[str, Any]],
    fingerprint: str,
    function_ref: Mapping[str, Any],
    producer: Mapping[str, Any],
) -> dict[str, Any]:
    dispatcher_id = str(candidate["dispatcher_id"])
    dispatcher_ref = _block_ref(dispatcher_id, fingerprint, function_ref)
    region_refs = [_block_ref(item, fingerprint, function_ref) for item in sorted(component)]
    cases = []
    returning_ids = {str(item.get("edge_id") or "") for item in candidate["returning"]}
    for edge in candidate["dispatch_edges"]:
        edge_id = str(edge.get("edge_id") or "")
        cases.append(canonicalize({
            "edge_ref": _edge_ref(edge, fingerprint, function_ref),
            "target_ref": _block_ref(str(edge["to"]), fingerprint, function_ref),
            "kind": edge.get("kind"),
            "guard": edge.get("guard"),
            "structural_relation": "RETURNS_TO_DISPATCHER" if edge_id in returning_ids else "LEAVES_CORE_OR_UNKNOWN",
        }))
    cases.sort(key=canonical_json)

    entries = []
    for block_id in sorted(component):
        for edge in incoming[block_id]:
            if str(edge["from"]) not in component:
                entries.append(canonicalize({
                    "kind": "CFG_EDGE",
                    "edge_ref": _edge_ref(edge, fingerprint, function_ref),
                    "from_ref": _block_ref(str(edge["from"]), fingerprint, function_ref),
                    "to_ref": _block_ref(block_id, fingerprint, function_ref),
                }))
        if block_id in entry_blocks and not any(str(edge["from"]) not in component for edge in incoming[block_id]):
            entries.append(canonicalize({
                "kind": "FUNCTION_ENTRY", "edge_ref": None, "from_ref": None,
                "to_ref": _block_ref(block_id, fingerprint, function_ref),
            }))
    exits = [canonicalize({
        "kind": "CFG_EDGE",
        "edge_ref": _edge_ref(edge, fingerprint, function_ref),
        "from_ref": _block_ref(str(edge["from"]), fingerprint, function_ref),
        "to_ref": _block_ref(str(edge["to"]), fingerprint, function_ref),
    }) for edge in exit_edges]
    exits.extend(canonicalize({
        "kind": "TERMINAL", "edge_ref": None,
        "from_ref": _block_ref(block_id, fingerprint, function_ref), "to_ref": None,
    }) for block_id in terminal_blocks)
    entries.sort(key=canonical_json)
    exits.sort(key=canonical_json)

    action_anchors = []
    for action in function_actions:
        anchor = action["payload"]["control_anchor"]
        block_id = _anchor_block_id(anchor)
        if block_id in component:
            action_anchors.append((action, anchor))

    evidence_records = []
    evidence_records.append(_evidence(
        producer=producer,
        kind="dispatcher_structure",
        predicate="multi_arm_dispatch_with_returning_arms",
        operands={
            "dispatcher_ref": dispatcher_ref,
            "arm_count": len(candidate["dispatch_edges"]),
            "returning_arm_count": len(candidate["returning"]),
        },
        source_refs=[dispatcher_ref] + [_edge_ref(item, fingerprint, function_ref) for item in candidate["dispatch_edges"]],
        artifact_refs=[],
        scope={"function_ref": function_ref, "region_cfg_refs": region_refs},
        result="CANDIDATE",
        reason_code="STRUCTURAL_DISPATCH_LOOP",
    ))
    evidence_records.append(_evidence(
        producer=producer,
        kind="control_state_candidate",
        predicate="dispatcher_value_has_local_region_read_write_evidence",
        operands={"candidates": candidate["state_candidates"]},
        source_refs=[
            ref for item in candidate["state_candidates"]
            for ref in item.get("source_refs") or []
        ],
        artifact_refs=[],
        scope={"function_ref": function_ref, "dispatcher_ref": dispatcher_ref},
        result="CANDIDATE",
        reason_code="LOCAL_READ_WRITE_ASSOCIATION",
    ))
    evidence_records.append(_evidence(
        producer=producer,
        kind="region_boundary",
        predicate="candidate_region_has_structural_entry_and_exit",
        operands={"entries": entries, "exits": exits},
        source_refs=[
            item for record in entries + exits
            for item in (record.get("edge_ref"), record.get("from_ref"), record.get("to_ref"))
            if isinstance(item, Mapping)
        ],
        artifact_refs=[],
        scope={"function_ref": function_ref, "region_cfg_refs": region_refs},
        result="OBSERVED",
        reason_code="STRUCTURAL_BOUNDARY",
    ))
    if action_anchors:
        evidence_records.append(_evidence(
            producer=producer,
            kind="semantic_action_location",
            predicate="semantic_actions_anchor_inside_candidate_region",
            operands={"control_anchors": [anchor for _, anchor in action_anchors]},
            source_refs=[anchor for _, anchor in action_anchors],
            artifact_refs=[action["id"] for action, _ in action_anchors],
            scope={"function_ref": function_ref, "region_cfg_refs": region_refs},
            result="OBSERVED",
            reason_code="ACTION_CONTROL_ANCHOR",
        ))
    evidence_records = _dedupe_by_id(evidence_records)

    identity_basis = canonicalize({
        "cfg_origin_set": sorted(
            [_cfg_origin(blocks[block_id]) for block_id in component], key=canonical_json
        ),
        "dispatcher_occurrence": _dispatcher_occurrence(blocks[dispatcher_id]),
    })
    region_id = stable_identity("flattened-region", identity_basis)
    status = analysis_status(
        proof="CANDIDATE", completion="COMPLETE", solver="NOT_RUN", diagnostics=[]
    )
    payload = {
        "dispatcher_ref": dispatcher_ref,
        "region_cfg_refs": region_refs,
        "control_state_candidates": candidate["state_candidates"],
        "cases": cases,
        "entries": entries,
        "exits": exits,
        "detection_evidence": [item["id"] for item in evidence_records],
    }
    region = artifact_envelope(
        schema=FLATTENED_REGION_SCHEMA,
        artifact_id=region_id,
        function_ref=function_ref,
        input_fingerprint=fingerprint,
        producer=producer,
        evidence_refs=payload["detection_evidence"],
        status=status,
        payload=payload,
        extensions={},
    )
    validate_flattened_region(region)
    return {"region": region, "evidence": evidence_records}


def _control_state_candidates(
    dispatcher_id: str,
    component: set[str],
    nodes_by_block: Mapping[str, list[Mapping[str, Any]]],
    fingerprint: str,
    function_ref: Mapping[str, Any],
) -> list[dict[str, Any]]:
    dispatcher_nodes = nodes_by_block.get(dispatcher_id, [])
    reads: dict[str, list[tuple[Mapping[str, Any], Any]]] = {}
    for node in dispatcher_nodes:
        if not _is_condition_node(node):
            continue
        for value, evidence_value in _binding_evidence(node, "reads"):
            reads.setdefault(value, []).append((node, evidence_value))
    writes: dict[str, list[tuple[Mapping[str, Any], Any]]] = {}
    for block_id in component:
        if block_id == dispatcher_id:
            continue
        for node in nodes_by_block.get(block_id, []):
            for value, evidence_value in _binding_evidence(node, "writes"):
                writes.setdefault(value, []).append((node, evidence_value))
    out = []
    for value in sorted(set(reads).intersection(writes)):
        read_nodes = _unique_nodes(item[0] for item in reads[value])
        write_nodes = _unique_nodes(item[0] for item in writes[value])
        refs = [
            _semantic_ref(item, fingerprint, function_ref)
            for item in read_nodes + write_nodes
        ]
        out.append(canonicalize({
            "candidate": {
                "kind": "SFIR_BINDING",
                "read_evidence": [item[1] for item in reads[value]],
                "write_evidence": [item[1] for item in writes[value]],
            },
            "source_refs": refs,
            "status": analysis_status(
                proof="CANDIDATE", completion="COMPLETE", solver="NOT_RUN", diagnostics=[]
            ),
        }))
    out.sort(key=canonical_json)
    return out


def _binding_evidence(node: Mapping[str, Any], direction: str) -> list[tuple[str, Any]]:
    fact_ssa = node.get("fact_ssa") if isinstance(node.get("fact_ssa"), Mapping) else {}
    out: list[tuple[str, Any]] = []
    for item in fact_ssa.get(direction) or []:
        if not isinstance(item, Mapping):
            continue
        key = _binding_key(item)
        if key:
            out.append((key, canonicalize({"source": "fact_ssa", "direction": direction, "ref": item})))
    raw_values = node.get(direction) or []
    if not out and isinstance(raw_values, list):
        for value in raw_values:
            if value is not None and str(value):
                out.append((f"raw:{value}", canonicalize({
                    "source": "semantic_node", "direction": direction, "value": value,
                })))
    if direction == "writes" and not out and node.get("lvalue") not in {None, ""}:
        value = node["lvalue"]
        out.append((f"raw:{value}", canonicalize({
            "source": "semantic_node", "direction": direction, "value": value,
        })))
    return out


def _binding_key(item: Mapping[str, Any]) -> str | None:
    # Existing SSA binding/declaration evidence is preferred.  A displayed
    # value is only a local exact-association fallback, never a name heuristic.
    for key in ("binding_id", "declaration_id", "base_name", "value"):
        if item.get(key) not in {None, ""}:
            return f"{key}:{item[key]}"
    return None


def _is_condition_node(node: Mapping[str, Any]) -> bool:
    semantic = node.get("semantic") if isinstance(node.get("semantic"), Mapping) else {}
    return bool(
        node.get("kind") in {"BranchCondition", "ValueCompute"}
        and (semantic.get("context") in {"condition", "switch"} or node.get("condition") is not None)
    )


def _is_multiway_dispatch(block: Mapping[str, Any], edges: list[Mapping[str, Any]]) -> bool:
    targets = {str(item.get("to") or "") for item in edges}
    kinds = [str(item.get("kind") or "").lower() for item in edges]
    terminator = block.get("terminator") if isinstance(block.get("terminator"), Mapping) else {}
    explicit_switch = (
        str(terminator.get("kind") or "").lower() == "switch"
        or str(terminator.get("node_kind") or "").lower() == "switch"
        or any(item.startswith("case:") or item == "default" or item.startswith("default:") for item in kinds)
    )
    return len(targets) >= 2 and (explicit_switch or len(targets) >= 3)


def _strongly_connected_components(
    blocks: Mapping[str, Mapping[str, Any]],
    outgoing: Mapping[str, list[Mapping[str, Any]]],
) -> list[set[str]]:
    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    result: list[set[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for edge in outgoing[node]:
            target = str(edge["to"])
            if target not in indices:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[target])
        if lowlinks[node] == indices[node]:
            component: set[str] = set()
            while stack:
                item = stack.pop()
                on_stack.remove(item)
                component.add(item)
                if item == node:
                    break
            result.append(component)

    for block_id in sorted(blocks):
        if block_id not in indices:
            visit(block_id)
    return result


def _reachable(
    start: str,
    target: str,
    outgoing: Mapping[str, list[Mapping[str, Any]]],
    *,
    allowed: set[str],
) -> bool:
    pending = [start]
    visited: set[str] = set()
    while pending:
        node = pending.pop()
        if node == target:
            return True
        if node in visited or node not in allowed:
            continue
        visited.add(node)
        pending.extend(
            str(edge["to"]) for edge in outgoing[node]
            if str(edge["to"]) in allowed and str(edge["to"]) not in visited
        )
    return False


def _nodes_by_block(function: Mapping[str, Any]) -> dict[str, list[Mapping[str, Any]]]:
    out: dict[str, list[Mapping[str, Any]]] = {}
    for node in function.get("semantic_nodes") or []:
        if not isinstance(node, Mapping):
            continue
        placement = node.get("placement") if isinstance(node.get("placement"), Mapping) else {}
        if placement.get("status") != "anchored" or not placement.get("anchor_block"):
            continue
        out.setdefault(str(placement["anchor_block"]), []).append(node)
    for values in out.values():
        values.sort(key=lambda item: str(item.get("semantic_id") or ""))
    return out


def _actions_by_function(actions: Iterable[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    out: dict[str, list[Mapping[str, Any]]] = {}
    for action in actions:
        function_ref = action.get("function_ref") if isinstance(action.get("function_ref"), Mapping) else {}
        out.setdefault(str(function_ref.get("sfir_function_id") or ""), []).append(action)
    for values in out.values():
        values.sort(key=lambda item: str(item.get("id") or ""))
    return out


def _action_input_summary(
    action_result: Mapping[str, Any], actions: list[Mapping[str, Any]]
) -> dict[str, Any]:
    anchored = []
    unanchored = []
    observations = []
    for action in actions:
        payload = action["payload"]
        if _anchor_block_id(payload["control_anchor"]):
            anchored.append(action["id"])
        else:
            unanchored.append(action["id"])
        # This is an input audit, not a second action collection.  It proves
        # that P1-T2 consumed the frozen P1-T1 fields while keeping replacement
        # representations provenance-only and action identity untouched.
        observations.append(canonicalize({
            "id": action["id"],
            "semantic_ref": payload["semantic_ref"],
            "control_anchor": payload["control_anchor"],
            "replacement_refs": payload["replacement_refs"],
            "evidence_refs": action["evidence_refs"],
            "status": action["status"],
        }))
    observations.sort(key=lambda item: item["id"])
    return canonicalize({
        "action_ids": sorted(action["id"] for action in actions),
        "anchored_action_ids": sorted(anchored),
        "unanchored_action_ids": sorted(unanchored),
        "observations": observations,
        "coverage": deepcopy(list(action_result.get("coverage") or [])),
        "unresolved_diagnostics": deepcopy(list(action_result.get("unresolved_diagnostics") or [])),
        "status": deepcopy(action_result.get("status")),
    })


def _upstream_action_diagnostics(action_input: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    if action_input.get("unanchored_action_ids"):
        out.append(diagnostic(
            "INSUFFICIENT_EVIDENCE",
            "one or more SemanticActions cannot be related to a Fact CFG block",
            affected_refs=action_input["unanchored_action_ids"],
            scope={"stage": "P1-T2", "relation": "control_anchor_to_region_cfg_refs"},
        ))
    if any(item.get("classification") == "UNRESOLVED" for item in action_input.get("coverage") or []):
        out.append(diagnostic(
            "UNKNOWN", "P1-T1 unresolved effect coverage is retained by region detection",
            scope={"stage": "P1-T2", "source": "SemanticAction.coverage"},
        ))
    return out


def _function_ref(
    function: Mapping[str, Any], actions: list[Mapping[str, Any]], fingerprint: str
) -> dict[str, Any]:
    if actions:
        return canonicalize(actions[0]["function_ref"])
    declaration = function.get("declaration") if isinstance(function.get("declaration"), Mapping) else {}
    name = str(function.get("function") or declaration.get("name") or "")
    return canonicalize({
        "input_fingerprint": fingerprint,
        "contract": function.get("contract"),
        "contract_declaration": function.get("contract_declaration"),
        "declaration": declaration or None,
        "canonical_signature": function.get("signature") or declaration.get("full_name") or "",
        "entry_kind": name.lower() if name.lower() in {"constructor", "fallback", "receive"} else "function",
        "sfir_function_id": function.get("function_id"),
    })


def _block_ref(block_id: str, fingerprint: str, function_ref: Mapping[str, Any]) -> dict[str, Any]:
    return source_ref(
        input_fingerprint=fingerprint, function_ref=function_ref, source_kind="CFG_BLOCK",
        locator={"block_id": block_id},
    )


def _edge_ref(edge: Mapping[str, Any], fingerprint: str, function_ref: Mapping[str, Any]) -> dict[str, Any]:
    return source_ref(
        input_fingerprint=fingerprint, function_ref=function_ref, source_kind="CFG_EDGE",
        locator={
            "edge_id": edge.get("edge_id"), "from": edge.get("from"), "to": edge.get("to"),
            "kind": edge.get("kind"),
        },
    )


def _semantic_ref(node: Mapping[str, Any], fingerprint: str, function_ref: Mapping[str, Any]) -> dict[str, Any]:
    return source_ref(
        input_fingerprint=fingerprint, function_ref=function_ref, source_kind="SEMANTIC_NODE",
        locator={
            "semantic_id": node.get("semantic_id"), "origin_id": node.get("origin_id"),
            "source_lang": node.get("source_lang"), "kind": node.get("kind"),
        },
    )


def _anchor_block_id(anchor: Any) -> str | None:
    if not isinstance(anchor, Mapping) or anchor.get("source_kind") != "CFG_BLOCK":
        return None
    locator = anchor.get("locator") if isinstance(anchor.get("locator"), Mapping) else {}
    return str(locator["block_id"]) if locator.get("block_id") else None


def _cfg_origin(block: Mapping[str, Any]) -> dict[str, Any]:
    return canonicalize({
        "kind": block.get("kind"),
        "origin": block.get("origin") if isinstance(block.get("origin"), Mapping) else {},
    })


def _dispatcher_occurrence(block: Mapping[str, Any]) -> dict[str, Any]:
    terminator = block.get("terminator") if isinstance(block.get("terminator"), Mapping) else {}
    return canonicalize({
        "cfg_origin": _cfg_origin(block),
        "terminator_kind": terminator.get("kind"),
        "node_kind": terminator.get("node_kind"),
    })


def _is_terminal(block: Mapping[str, Any]) -> bool:
    terminator = block.get("terminator") if isinstance(block.get("terminator"), Mapping) else {}
    return str(terminator.get("kind") or "").lower() in {"return", "revert", "stop", "selfdestruct"}


def _unique_nodes(nodes: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    by_key = {str(item.get("semantic_id") or canonical_json(item)): item for item in nodes}
    return [by_key[key] for key in sorted(by_key)]


def _evidence(
    *,
    producer: Mapping[str, Any],
    kind: str,
    predicate: str,
    operands: Any,
    source_refs: Iterable[Mapping[str, Any]],
    artifact_refs: Iterable[str],
    scope: Any,
    result: Any,
    reason_code: str,
) -> dict[str, Any]:
    return evidence_record(
        kind=kind,
        claim={"predicate": predicate, "operands": operands},
        premises=[{"sfir_schema": SFIR_SCHEMA}, {"analysis": "CFG_STRUCTURAL_ONLY"}],
        source_refs=list(source_refs),
        artifact_refs=list(artifact_refs),
        rule=f"p1-t2/{kind}/v1",
        scope=scope,
        assumptions=[],
        result=result,
        reason_code=reason_code,
        producer=producer,
        status=analysis_status(
            proof="CANDIDATE" if result == "CANDIDATE" else "PROVEN",
            completion="COMPLETE", solver="NOT_RUN", diagnostics=[],
        ),
    )


def _dedupe_by_id(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {item["id"]: item for item in records}
    return [by_id[key] for key in sorted(by_id)]


def _dedupe_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {canonical_json(item): item for item in records}
    return [by_key[key] for key in sorted(by_key)]
