#!/usr/bin/env python3
"""P1-T1: project final SFIR semantic occurrences into SemanticAction.

The extractor consumes only ``s-seir-semantic-fact-ir/v1``.  It does not read
Solidity/Yul text, inspect opcodes, run a solver, recover dependencies, or
infer execution order.  Operands and provenance already present in SFIR are
retained as source-addressable references for later Module 2 consumers.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

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
    source_ref,
    stable_identity,
    validate_artifact_envelope,
)


SFIR_SCHEMA = "s-seir-semantic-fact-ir/v1"
SEMANTIC_ACTION_SCHEMA = "erc20-research/semantic-action/v1"
IMPLEMENTATION_VERSION = "p1-t1-v1"
ACTION_KINDS = frozenset({
    "StorageWrite", "ExternalCall", "EtherTransfer", "Emit", "Revert", "Return",
})
COVERAGE_CLASSIFICATIONS = frozenset({"EMITTED", "REPLACED", "UNRESOLVED"})

DIRECT_KIND_MAP = {
    "StateWrite": "StorageWrite",
    "ExternalCall": "ExternalCall",
    "LowLevelCall": "ExternalCall",
    "StaticCall": "ExternalCall",
    "DelegateCall": "ExternalCall",
    "PrecompileCall": "ExternalCall",
    "ValueTransferCall": "EtherTransfer",
    "EventEmit": "Emit",
    "Revert": "Revert",
    "Return": "Return",
}

# These SFIR kinds describe behavior but are outside the six frozen action
# kinds.  They are audited as unresolved/unsupported rather than silently
# reinterpreted or used to expand P1-T1's scope.
KNOWN_OUT_OF_SCOPE_EFFECT_KINDS = frozenset({
    "InternalCall", "InternalDynamicCall", "LibraryCall", "ModifierApply",
    "BaseConstructorCall", "BuiltinCall", "UnmodeledBuiltinCall", "NewContract",
    "SelfDestruct", "UnmodeledSlithIROperation", "UnmodeledSSeirOverlay",
})

SORTED_COLLECTION_KEYS = frozenset({
    "functions", "semantic_nodes", "blocks", "edges", "diagnostics", "definitions",
    "phis", "bindings", "semantic_edges", "boundary_links", "path_witnesses",
    "modifier_application_links", "direct_call_links", "dynamic_call_links",
    "base_constructor_links", "contracts",
})


def extract_semantic_actions(
    sfir_payload: Mapping[str, Any],
    *,
    input_fingerprint: str | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract all SemanticActions and a complete effect-occurrence audit.

    ``input_fingerprint`` should be supplied by an enclosing compiler/run
    manifest when available.  The fallback is a traversal-order-insensitive
    digest of the SFIR payload and is recorded as such in the result.
    """

    if not isinstance(sfir_payload, Mapping):
        raise ContractValidationError("SemanticAction extraction requires an SFIR object")
    config_value = dict(config or {})
    producer = producer_record("P1-T1", IMPLEMENTATION_VERSION, config_value)
    supplied_fingerprint = bool(input_fingerprint)
    fingerprint = str(input_fingerprint or sfir_input_fingerprint(sfir_payload))

    if sfir_payload.get("schema") != SFIR_SCHEMA:
        item = diagnostic(
            "UNSUPPORTED",
            f"unsupported input schema {sfir_payload.get('schema')!r}; expected {SFIR_SCHEMA}",
            affected_refs=[],
            scope={"schema": sfir_payload.get("schema")},
        )
        return canonicalize({
            "input_schema": sfir_payload.get("schema"),
            "input_fingerprint": fingerprint,
            "input_fingerprint_source": "provided" if supplied_fingerprint else "sfir_canonical_fallback",
            "producer": producer,
            "actions": [],
            "evidence_records": [],
            "coverage": [],
            "unresolved_diagnostics": [item],
            "upstream_diagnostics": [],
            "status": analysis_status(
                proof="UNKNOWN", completion="PARTIAL", solver="NOT_RUN", diagnostics=[item]
            ),
        })

    actions: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    global_diagnostics: list[dict[str, Any]] = []
    upstream_diagnostics: list[dict[str, Any]] = []

    functions = [item for item in sfir_payload.get("functions") or [] if isinstance(item, Mapping)]
    for function in functions:
        function_ref = build_function_ref(function, fingerprint)
        node_by_id = {
            str(node.get("semantic_id")): node
            for node in function.get("semantic_nodes") or []
            if isinstance(node, Mapping) and node.get("semantic_id")
        }
        block_by_id = {
            str(block.get("block_id")): block
            for block in (function.get("fact_cfg") or {}).get("blocks") or []
            if isinstance(block, Mapping) and block.get("block_id")
        }
        explicit_reverts_by_anchor: dict[str, list[Mapping[str, Any]]] = {}
        for node in node_by_id.values():
            if node.get("kind") == "Revert":
                anchor = _anchor_block(node)
                if anchor:
                    explicit_reverts_by_anchor.setdefault(anchor, []).append(node)

        function_upstream = _upstream_diagnostics(function, fingerprint, function_ref)
        upstream_diagnostics.extend(function_upstream)
        for node in node_by_id.values():
            node_result = _extract_node(
                node=node,
                function_ref=function_ref,
                fingerprint=fingerprint,
                producer=producer,
                block_by_id=block_by_id,
                explicit_reverts_by_anchor=explicit_reverts_by_anchor,
                upstream=function.get("diagnostics") or [],
                node_by_id=node_by_id,
            )
            if node_result is None:
                continue
            actions.extend(node_result["actions"])
            evidence.extend(node_result["evidence"])
            coverage.extend(node_result["coverage"])
            global_diagnostics.extend(node_result["diagnostics"])

    # Input with no function is not silently interpreted as a function with no
    # effects.  Empty functions inside a valid payload, by contrast, are a
    # complete empty extraction.
    if not functions:
        item = diagnostic(
            "INSUFFICIENT_EVIDENCE", "valid SFIR payload contains no function records",
            scope={"input_fingerprint": fingerprint},
        )
        global_diagnostics.append(item)

    actions.sort(key=lambda item: item["id"])
    evidence = _dedupe_by_id(evidence)
    coverage.sort(key=canonical_json)
    global_diagnostics = _dedupe_records(global_diagnostics)
    has_unresolved = any(item["classification"] == "UNRESOLVED" for item in coverage)
    has_partial_action = any(item["status"]["completion"] != "COMPLETE" for item in actions)
    is_partial = has_unresolved or has_partial_action or bool(global_diagnostics) or not functions
    result_status = analysis_status(
        proof="UNKNOWN" if has_unresolved or not functions else "PROVEN",
        completion="PARTIAL" if is_partial else "COMPLETE",
        solver="NOT_RUN",
        diagnostics=global_diagnostics,
    )
    return canonicalize({
        "input_schema": SFIR_SCHEMA,
        "input_fingerprint": fingerprint,
        "input_fingerprint_source": "provided" if supplied_fingerprint else "sfir_canonical_fallback",
        "producer": producer,
        "actions": actions,
        "evidence_records": evidence,
        "coverage": coverage,
        "unresolved_diagnostics": global_diagnostics,
        "upstream_diagnostics": upstream_diagnostics,
        "status": result_status,
    })


def sfir_input_fingerprint(sfir_payload: Mapping[str, Any]) -> str:
    """Digest SFIR while neutralizing known unordered container traversal."""

    return "sha256:" + content_digest(_fingerprint_view(sfir_payload))


def build_function_ref(function: Mapping[str, Any], input_fingerprint: str) -> dict[str, Any]:
    declaration = function.get("declaration") if isinstance(function.get("declaration"), Mapping) else {}
    contract_declaration = (
        function.get("contract_declaration")
        if isinstance(function.get("contract_declaration"), Mapping) else {}
    )
    signature = str(function.get("signature") or declaration.get("full_name") or "")
    name = str(function.get("function") or declaration.get("name") or "")
    lowered = name.lower()
    if lowered == "constructor":
        entry_kind = "constructor"
    elif lowered == "fallback":
        entry_kind = "fallback"
    elif lowered == "receive":
        entry_kind = "receive"
    else:
        entry_kind = "function"
    declaration_identity = _compact_identity_fields(declaration)
    contract_identity = _compact_identity_fields(contract_declaration)
    return canonicalize({
        "input_fingerprint": input_fingerprint,
        "contract": function.get("contract") or contract_declaration.get("name"),
        "contract_declaration": contract_identity or None,
        "declaration": declaration_identity or None,
        "canonical_signature": signature,
        "entry_kind": entry_kind,
        # This is a locator, not the sole identity basis.  It remains useful
        # to consumers resolving the function in the source SFIR.
        "sfir_function_id": function.get("function_id"),
    })


def validate_semantic_action(action: Mapping[str, Any]) -> None:
    validate_artifact_envelope(action)
    if action.get("schema") != SEMANTIC_ACTION_SCHEMA:
        raise ContractValidationError("not a frozen-v1 SemanticAction")
    payload = action.get("payload") or {}
    required = {"kind", "semantic_ref", "operand_refs", "control_anchor", "replacement_refs"}
    if set(payload) != required:
        raise ContractValidationError(
            f"SemanticAction fields mismatch; missing={sorted(required.difference(payload))}, "
            f"extra={sorted(set(payload).difference(required))}"
        )
    if payload["kind"] not in ACTION_KINDS:
        raise ContractValidationError(f"invalid SemanticAction kind: {payload['kind']}")
    if not isinstance(payload["operand_refs"], list) or not isinstance(payload["replacement_refs"], list):
        raise ContractValidationError("SemanticAction operand_refs/replacement_refs must be lists")


def _extract_node(
    *,
    node: Mapping[str, Any],
    function_ref: Mapping[str, Any],
    fingerprint: str,
    producer: Mapping[str, Any],
    block_by_id: Mapping[str, Mapping[str, Any]],
    explicit_reverts_by_anchor: Mapping[str, list[Mapping[str, Any]]],
    upstream: Iterable[Any],
    node_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]] | None:
    kind = str(node.get("kind") or "")
    semantic_ref = _semantic_source_ref(node, fingerprint, function_ref)
    explicit_replacement = _explicit_replacement_target(node)
    if explicit_replacement:
        replacement_ref = _semantic_ref_for_locator(
            explicit_replacement, fingerprint=fingerprint, function_ref=function_ref
        )
        evidence = _projection_evidence(
            producer=producer, source_refs=[semantic_ref, replacement_ref], function_ref=function_ref,
            semantic_ref=semantic_ref, action_kind=None, outcome="REPLACED",
            rule="p1-t1/explicit-replacement/v1", reason_code="CANONICAL_REPLACEMENT",
        )
        record = _coverage_record(
            occurrence_ref=semantic_ref, classification="REPLACED", action_ref=None,
            canonical_ref=replacement_ref, reason_code="CANONICAL_REPLACEMENT",
            evidence_refs=[evidence["id"]], status=evidence["status"],
        )
        return {"actions": [], "evidence": [evidence], "coverage": [record], "diagnostics": []}

    action_kind = DIRECT_KIND_MAP.get(kind)
    synthetic_failure = kind in {"Require", "Assert"}
    if synthetic_failure:
        duplicate = _existing_failure_revert(node, explicit_reverts_by_anchor, block_by_id)
        if duplicate is not None:
            canonical_ref = _semantic_source_ref(duplicate, fingerprint, function_ref)
            evidence = _projection_evidence(
                producer=producer, source_refs=[semantic_ref, canonical_ref], function_ref=function_ref,
                semantic_ref=semantic_ref, action_kind="Revert", outcome="REPLACED",
                rule="p1-t1/require-assert-existing-revert/v1",
                reason_code="FAILURE_EFFECT_ALREADY_CANONICAL",
            )
            return {
                "actions": [], "evidence": [evidence],
                "coverage": [_coverage_record(
                    occurrence_ref=semantic_ref, classification="REPLACED", action_ref=None,
                    canonical_ref=canonical_ref, reason_code="FAILURE_EFFECT_ALREADY_CANONICAL",
                    evidence_refs=[evidence["id"]], status=evidence["status"],
                )],
                "diagnostics": [],
            }
        if not _has_failure_projection_evidence(node, block_by_id):
            item = diagnostic(
                "INSUFFICIENT_EVIDENCE",
                f"{kind} lacks an anchored failure occurrence and condition source",
                affected_refs=[semantic_ref], scope={"function_ref": function_ref},
            )
            status = analysis_status(
                proof="UNKNOWN", completion="PARTIAL", solver="NOT_RUN", diagnostics=[item]
            )
            evidence = _projection_evidence(
                producer=producer, source_refs=[semantic_ref], function_ref=function_ref,
                semantic_ref=semantic_ref, action_kind="Revert", outcome="UNRESOLVED",
                rule="p1-t1/require-assert-failure-projection/v1",
                reason_code="INSUFFICIENT_FAILURE_EVIDENCE", status=status,
            )
            return {
                "actions": [], "evidence": [evidence],
                "coverage": [_coverage_record(
                    occurrence_ref=semantic_ref, classification="UNRESOLVED", action_ref=None,
                    canonical_ref=None, reason_code="INSUFFICIENT_FAILURE_EVIDENCE",
                    evidence_refs=[evidence["id"]], status=status,
                )],
                "diagnostics": [item],
            }
        action_kind = "Revert"

    if action_kind is None:
        if not _is_effect_like(node):
            return None
        item = diagnostic(
            "UNSUPPORTED",
            f"effect-like SFIR kind {kind or '<missing>'} is outside frozen SemanticAction kinds",
            affected_refs=[semantic_ref], scope={"function_ref": function_ref},
        )
        status = analysis_status(
            proof="UNKNOWN", completion="PARTIAL", solver="NOT_RUN", diagnostics=[item]
        )
        evidence = _projection_evidence(
            producer=producer, source_refs=[semantic_ref], function_ref=function_ref,
            semantic_ref=semantic_ref, action_kind=None, outcome="UNRESOLVED",
            rule="p1-t1/frozen-action-scope/v1", reason_code="UNSUPPORTED_ACTION_KIND",
            status=status,
        )
        return {
            "actions": [], "evidence": [evidence],
            "coverage": [_coverage_record(
                occurrence_ref=semantic_ref, classification="UNRESOLVED", action_ref=None,
                canonical_ref=None, reason_code="UNSUPPORTED_ACTION_KIND",
                evidence_refs=[evidence["id"]], status=status,
            )],
            "diagnostics": [item],
        }

    occurrence_basis, identity_scope = _occurrence_identity(node)
    action_id = stable_identity("semantic-action", {
        "function": function_ref,
        "semantic_occurrence_provenance": occurrence_basis,
        "action_kind": action_kind,
    })
    action_diagnostics = _action_diagnostics(
        node=node, action_kind=action_kind, semantic_ref=semantic_ref,
        function_ref=function_ref, identity_scope=identity_scope, upstream=upstream,
    )
    status = analysis_status(
        proof="PROVEN",
        completion="PARTIAL" if action_diagnostics else "COMPLETE",
        solver="NOT_RUN",
        diagnostics=action_diagnostics,
    )
    control_anchor = _control_anchor(node, fingerprint, function_ref)
    operand_refs = _operand_refs(node, action_kind, fingerprint, function_ref, synthetic_failure)
    replacement_refs = _replacement_refs(node, fingerprint, function_ref)
    rule = (
        "p1-t1/require-assert-failure-projection/v1"
        if synthetic_failure else "p1-t1/direct-sfir-action-projection/v1"
    )
    evidence = _projection_evidence(
        producer=producer,
        source_refs=[semantic_ref] + ([control_anchor] if _is_source_ref(control_anchor) else []),
        function_ref=function_ref,
        semantic_ref=semantic_ref,
        action_kind=action_kind,
        outcome="EMITTED",
        rule=rule,
        reason_code="SYNTHETIC_FAILURE_EFFECT" if synthetic_failure else "DIRECT_SFIR_EFFECT",
        status=status,
    )
    action = artifact_envelope(
        schema=SEMANTIC_ACTION_SCHEMA,
        artifact_id=action_id,
        function_ref=function_ref,
        input_fingerprint=fingerprint,
        producer=producer,
        evidence_refs=[evidence["id"]],
        status=status,
        payload={
            "kind": action_kind,
            "semantic_ref": semantic_ref,
            "operand_refs": operand_refs,
            "control_anchor": control_anchor,
            "replacement_refs": replacement_refs,
        },
        extensions={
            "identity_scope": identity_scope,
            "source_representation": node.get("source_lang") or "unknown",
            "projection": "require_assert_failure" if synthetic_failure else "direct_sfir",
        },
    )
    validate_semantic_action(action)
    records = [_coverage_record(
        occurrence_ref=semantic_ref, classification="EMITTED", action_ref=action_id,
        canonical_ref=semantic_ref, reason_code="SYNTHETIC_FAILURE_EFFECT" if synthetic_failure else "DIRECT_SFIR_EFFECT",
        evidence_refs=[evidence["id"]], status=status,
    )]
    # SFIR canonicalization can merge replaced high-level representations into
    # the canonical node's provenance.  Audit those occurrences as REPLACED;
    # they are never emitted as additional actions.
    for replacement_ref in replacement_refs:
        replacement_evidence = _projection_evidence(
            producer=producer, source_refs=[replacement_ref, semantic_ref], function_ref=function_ref,
            semantic_ref=replacement_ref, action_kind=action_kind, outcome="REPLACED",
            rule="p1-t1/sfir-merged-replacement/v1", reason_code="SFIR_MERGED_REPRESENTATION",
        )
        records.append(_coverage_record(
            occurrence_ref=replacement_ref, classification="REPLACED", action_ref=None,
            canonical_ref=semantic_ref, reason_code="SFIR_MERGED_REPRESENTATION",
            evidence_refs=[replacement_evidence["id"]], status=replacement_evidence["status"],
        ))
        evidence = _append_evidence(action, evidence, replacement_evidence)
    return {
        "actions": [action], "evidence": evidence if isinstance(evidence, list) else [evidence],
        "coverage": records, "diagnostics": action_diagnostics,
    }


def _append_evidence(
    action: dict[str, Any],
    primary: dict[str, Any] | list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    records = list(primary) if isinstance(primary, list) else [primary]
    records.append(extra)
    action["evidence_refs"].append(extra["id"])
    action["evidence_refs"] = sorted(set(action["evidence_refs"]))
    return records


def _operand_refs(
    node: Mapping[str, Any],
    action_kind: str,
    fingerprint: str,
    function_ref: Mapping[str, Any],
    synthetic_failure: bool,
) -> list[dict[str, Any]]:
    semantic = node.get("semantic") if isinstance(node.get("semantic"), Mapping) else {}
    out: list[dict[str, Any]] = []

    def add(role: str, path: str, value: Any, *, source_kind: str = "SEMANTIC_NODE") -> None:
        ref = source_ref(
            input_fingerprint=fingerprint,
            function_ref=function_ref,
            source_kind=source_kind,
            locator={"semantic_id": node.get("semantic_id"), "field_path": path},
        )
        item: dict[str, Any] = {
            "role": role,
            "field_path": path,
            "source_ref": ref,
            "value": deepcopy(value),
        }
        matches = _matching_fact_ssa_refs(node, value)
        if matches["reads"] or matches["writes"]:
            item["fact_ssa_refs"] = matches
        out.append(canonicalize(item))

    if action_kind == "StorageWrite":
        path, value, found = _first_present(node, (
            "semantic.location", "semantic.access", "lvalue",
        ))
        if found:
            add("storage_location", path, value, source_kind="STORAGE_ACCESS")
        path, value, found = _first_present(node, ("semantic.keys", "semantic.location.keys"))
        if found:
            add("storage_keys", path, value, source_kind="STORAGE_ACCESS")
        path, value, found = _first_present(node, ("semantic.value", "rvalue"))
        if found:
            add("update_value", path, value)
    elif action_kind == "ExternalCall":
        for role, paths in (
            ("call_kind", ("semantic.call_kind", "semantic.function", "kind")),
            ("target", ("semantic.target",)),
            ("arguments", ("semantic.arguments",)),
            ("value", ("semantic.value", "semantic.call_value")),
            ("result", ("semantic.call_status_result", "lvalue")),
            ("selector", ("semantic.selector_signature", "semantic.selector")),
        ):
            path, value, found = _first_present(node, paths)
            if found:
                add(role, path, value)
    elif action_kind == "EtherTransfer":
        for role, paths in (
            ("target", ("semantic.target",)),
            ("value", ("semantic.value",)),
            ("transfer_method", ("semantic.method",)),
            ("result", ("lvalue",)),
        ):
            path, value, found = _first_present(node, paths)
            if found:
                add(role, path, value)
    elif action_kind == "Emit":
        for role, paths in (
            ("event", ("semantic.event", "semantic.signature")),
            ("event_arguments", ("semantic.arguments", "semantic.args")),
            ("event_topics", ("semantic.topics",)),
            ("event_indexed_values", ("semantic.indexed_topic_values",)),
        ):
            path, value, found = _first_present(node, paths)
            if found:
                add(role, path, value)
    elif action_kind == "Return":
        path, value, found = _first_present(node, (
            "semantic.values", "semantic.value", "semantic.return_expression", "rvalue",
        ))
        if found:
            add("return_values", path, value)
    elif action_kind == "Revert":
        for role, paths in (
            ("failure_condition_source", ("semantic.guard", "semantic.condition", "condition")),
            ("revert_arguments", ("semantic.arguments",)),
            ("revert_payload", ("semantic.payload", "semantic.raw_payload")),
            ("revert_error", ("semantic.error", "semantic.selector")),
        ):
            path, value, found = _first_present(node, paths)
            if found:
                add(role, path, value)
        if synthetic_failure:
            provenance = node.get("semantic_provenance") or {}
            if provenance.get("require_failure_cfg_node"):
                add("failure_control_source", "semantic_provenance.require_failure_cfg_node", provenance["require_failure_cfg_node"])

    # Preserve the explicit condition occurrence for non-failure actions too;
    # this is only a source ref, never a normalized Guard or feasibility claim.
    if action_kind != "Revert" and "condition" in node:
        add("condition_source", "condition", node.get("condition"))
    return out


def _action_diagnostics(
    *,
    node: Mapping[str, Any],
    action_kind: str,
    semantic_ref: Mapping[str, Any],
    function_ref: Mapping[str, Any],
    identity_scope: str,
    upstream: Iterable[Any],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if identity_scope != "INPUT_STABLE":
        out.append(diagnostic(
            "INSUFFICIENT_EVIDENCE",
            "semantic occurrence lacks stable provenance; identity is limited to this SFIR run",
            affected_refs=[semantic_ref], scope={"function_ref": function_ref, "identity_scope": identity_scope},
        ))
    if not _anchor_block(node):
        out.append(diagnostic(
            "INSUFFICIENT_EVIDENCE", "SemanticAction has no unique SFIR control anchor",
            affected_refs=[semantic_ref], scope={"function_ref": function_ref, "field": "control_anchor"},
        ))
    semantic = node.get("semantic") if isinstance(node.get("semantic"), Mapping) else {}
    if action_kind == "StorageWrite":
        if not any(_path_present(node, path) for path in ("semantic.location", "semantic.access", "lvalue")):
            out.append(_missing_operand("storage_location", semantic_ref, function_ref))
        if not any(_path_present(node, path) for path in ("semantic.value", "rvalue")):
            out.append(_missing_operand("update_value", semantic_ref, function_ref))
    elif action_kind == "ExternalCall":
        if "target" not in semantic or semantic.get("target") in {None, ""}:
            out.append(_missing_operand("call_target", semantic_ref, function_ref))
        if "arguments" not in semantic:
            out.append(_missing_operand("call_arguments", semantic_ref, function_ref))
    elif action_kind == "EtherTransfer":
        if semantic.get("target") in {None, ""}:
            out.append(_missing_operand("transfer_target", semantic_ref, function_ref))
        if semantic.get("value") in {None, ""}:
            out.append(_missing_operand("transfer_value", semantic_ref, function_ref))
    elif action_kind == "Emit":
        if not any(key in semantic for key in ("arguments", "args")):
            out.append(_missing_operand("event_arguments", semantic_ref, function_ref))
    elif action_kind == "Revert" and str(node.get("kind")) in {"Require", "Assert"}:
        if not any(_path_present(node, path) for path in ("semantic.guard", "semantic.condition", "condition")):
            out.append(_missing_operand("failure_condition_source", semantic_ref, function_ref))

    candidate_status = str(semantic.get("candidate_status") or "").lower()
    if candidate_status and candidate_status not in {"resolved", "proven", "complete"}:
        out.append(diagnostic(
            "INSUFFICIENT_EVIDENCE",
            f"SFIR semantic candidate status is {candidate_status}",
            affected_refs=[semantic_ref], scope={"function_ref": function_ref},
        ))
    for item in upstream:
        if not isinstance(item, Mapping):
            continue
        semantic_id = item.get("semantic_id")
        if semantic_id and str(semantic_id) != str(node.get("semantic_id")):
            continue
        out.append(diagnostic(
            _diagnostic_code(item),
            f"upstream SFIR diagnostic retained: {item.get('kind') or 'unknown'}: {item.get('reason') or canonical_json(item)}",
            affected_refs=[semantic_ref], scope={"function_ref": function_ref},
        ))
    return _dedupe_records(out)


def _missing_operand(role: str, semantic_ref: Mapping[str, Any], function_ref: Mapping[str, Any]) -> dict[str, Any]:
    return diagnostic(
        "UNKNOWN", f"SFIR does not provide the {role} operand",
        affected_refs=[semantic_ref], scope={"function_ref": function_ref, "operand_role": role},
    )


def _control_anchor(
    node: Mapping[str, Any], fingerprint: str, function_ref: Mapping[str, Any]
) -> dict[str, Any]:
    anchor = _anchor_block(node)
    if anchor:
        return source_ref(
            input_fingerprint=fingerprint, function_ref=function_ref, source_kind="CFG_BLOCK",
            locator={
                "block_id": anchor,
                "placement_status": (node.get("placement") or {}).get("status"),
                "operation_order": (node.get("placement") or {}).get("operation_order"),
                "semantic_id": node.get("semantic_id"),
            },
        )
    return canonicalize({
        "state": "UNKNOWN",
        "reason_code": "UNANCHORED_SEMANTIC_OCCURRENCE",
        "semantic_ref": _semantic_source_ref(node, fingerprint, function_ref),
    })


def _occurrence_identity(node: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    provenance = node.get("semantic_provenance") if isinstance(node.get("semantic_provenance"), Mapping) else {}
    semantic_source = provenance.get("semantic_source") if isinstance(provenance.get("semantic_source"), Mapping) else {}
    evidence = node.get("evidence") if isinstance(node.get("evidence"), Mapping) else {}
    atomic = evidence.get("atomic_operation") if isinstance(evidence.get("atomic_operation"), Mapping) else {}
    stable = bool(
        atomic.get("source_span")
        or semantic_source.get("overlay_id")
        or (node.get("operation_id") and node.get("stmt_refs"))
    )
    basis = {
        "source_lang": node.get("source_lang"),
        "origin": node.get("origin"),
        "semantic_source": semantic_source or None,
        "source_span": atomic.get("source_span"),
        "operation_id": node.get("operation_id"),
        "stmt_refs": node.get("stmt_refs") or [],
        "operation_role": node.get("kind"),
    }
    if not stable:
        # The semantic id is a locator and collision discriminator only in the
        # explicitly limited run-local scope; it is never advertised as stable
        # provenance by itself.
        basis["run_local_semantic_ref"] = node.get("semantic_id") or node.get("origin_id")
    return canonicalize(basis), "INPUT_STABLE" if stable else "RUN_LOCAL"


def _replacement_refs(
    node: Mapping[str, Any], fingerprint: str, function_ref: Mapping[str, Any]
) -> list[dict[str, Any]]:
    provenance = node.get("semantic_provenance") if isinstance(node.get("semantic_provenance"), Mapping) else {}
    canonical_source = provenance.get("semantic_source")
    merged = provenance.get("merged_semantic_sources") or []
    out: list[dict[str, Any]] = []
    for item in merged:
        if not isinstance(item, Mapping) or item == canonical_source:
            continue
        out.append(source_ref(
            input_fingerprint=fingerprint, function_ref=function_ref, source_kind="SEMANTIC_NODE",
            locator={"semantic_source": item, "relation": "replaced_representation"},
        ))
    explicit = node.get("replacement_refs") or (node.get("semantic") or {}).get("replacement_refs") or []
    for item in explicit:
        out.append(_semantic_ref_for_locator(item, fingerprint=fingerprint, function_ref=function_ref))
    return _dedupe_records(out)


def _explicit_replacement_target(node: Mapping[str, Any]) -> Any:
    semantic = node.get("semantic") if isinstance(node.get("semantic"), Mapping) else {}
    provenance = node.get("semantic_provenance") if isinstance(node.get("semantic_provenance"), Mapping) else {}
    return (
        node.get("replaced_by") or node.get("canonical_semantic_ref")
        or semantic.get("replaced_by") or provenance.get("replaced_by")
    )


def _existing_failure_revert(
    node: Mapping[str, Any],
    explicit_reverts_by_anchor: Mapping[str, list[Mapping[str, Any]]],
    block_by_id: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    provenance = node.get("semantic_provenance") if isinstance(node.get("semantic_provenance"), Mapping) else {}
    failure_anchor = str(provenance.get("require_failure_cfg_node") or "")
    if not failure_anchor:
        return None
    candidate_anchors = [failure_anchor]
    for block_id, block in block_by_id.items():
        origin = block.get("origin") if isinstance(block.get("origin"), Mapping) else {}
        source_ids = [
            origin.get("source_block_id"),
            *(origin.get("source_block_ids") or []),
        ]
        if failure_anchor in {str(item) for item in source_ids if item is not None}:
            candidate_anchors.append(str(block_id))
    candidates = [
        candidate
        for anchor in dict.fromkeys(candidate_anchors)
        for candidate in explicit_reverts_by_anchor.get(anchor) or []
    ]
    return candidates[0] if len(candidates) == 1 else None


def _has_failure_projection_evidence(
    node: Mapping[str, Any], block_by_id: Mapping[str, Mapping[str, Any]]
) -> bool:
    condition = any(_path_present(node, path) for path in ("semantic.guard", "semantic.condition", "condition"))
    anchor = _anchor_block(node)
    if not condition or not anchor:
        return False
    semantic = node.get("semantic") if isinstance(node.get("semantic"), Mapping) else {}
    provenance = node.get("semantic_provenance") if isinstance(node.get("semantic_provenance"), Mapping) else {}
    terminator = (block_by_id.get(anchor) or {}).get("terminator") or {}
    return bool(
        provenance.get("require_failure_cfg_node")
        or semantic.get("on_fail") == "revert"
        or str(terminator.get("kind") or "") in {"Require", "Assert"}
        or node.get("origin") == "solidity_atomic_operation"
    )


def _is_effect_like(node: Mapping[str, Any]) -> bool:
    kind = str(node.get("kind") or "")
    if kind == "StateRead":
        return False
    semantic = node.get("semantic") if isinstance(node.get("semantic"), Mapping) else {}
    return bool(
        kind in DIRECT_KIND_MAP
        or kind in {"Require", "Assert"}
        or kind in KNOWN_OUT_OF_SCOPE_EFFECT_KINDS
        or node.get("fact_role") == "effect"
        or semantic.get("model_status") == "unmodeled"
        or str(semantic.get("operation") or "").startswith("unmodeled")
    )


def _projection_evidence(
    *,
    producer: Mapping[str, Any],
    source_refs: Iterable[Mapping[str, Any]],
    function_ref: Mapping[str, Any],
    semantic_ref: Mapping[str, Any],
    action_kind: str | None,
    outcome: str,
    rule: str,
    reason_code: str,
    status: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    evidence_status = status or analysis_status(
        proof="PROVEN" if outcome in {"EMITTED", "REPLACED"} else "UNKNOWN",
        completion="COMPLETE" if outcome in {"EMITTED", "REPLACED"} else "PARTIAL",
        solver="NOT_RUN", diagnostics=[],
    )
    return evidence_record(
        kind="semantic_action_projection",
        claim={
            "predicate": "semantic_occurrence_action_projection",
            "operands": {"semantic_ref": semantic_ref, "action_kind": action_kind, "outcome": outcome},
        },
        premises=[{"sfir_schema": SFIR_SCHEMA}, {"frozen_action_kinds": sorted(ACTION_KINDS)}],
        source_refs=source_refs,
        artifact_refs=[],
        rule=rule,
        scope={"function_ref": function_ref},
        assumptions=[],
        result={"classification": outcome, "action_kind": action_kind},
        reason_code=reason_code,
        producer=producer,
        status=evidence_status,
    )


def _coverage_record(
    *,
    occurrence_ref: Mapping[str, Any],
    classification: str,
    action_ref: str | None,
    canonical_ref: Mapping[str, Any] | None,
    reason_code: str,
    evidence_refs: Iterable[str],
    status: Mapping[str, Any],
) -> dict[str, Any]:
    if classification not in COVERAGE_CLASSIFICATIONS:
        raise ContractValidationError(f"invalid coverage classification: {classification}")
    body = canonicalize({
        "occurrence_ref": occurrence_ref,
        "classification": classification,
        "action_ref": action_ref,
        "canonical_ref": canonical_ref,
        "reason_code": reason_code,
        "evidence_refs": list(evidence_refs),
        "status": status,
    })
    return {"coverage_id": stable_identity("action-coverage", body), **body}


def _semantic_source_ref(
    node: Mapping[str, Any], fingerprint: str, function_ref: Mapping[str, Any]
) -> dict[str, Any]:
    return source_ref(
        input_fingerprint=fingerprint,
        function_ref=function_ref,
        source_kind="SEMANTIC_NODE",
        locator={
            "semantic_id": node.get("semantic_id"),
            "origin_id": node.get("origin_id"),
            "source_lang": node.get("source_lang"),
            "kind": node.get("kind"),
        },
    )


def _semantic_ref_for_locator(
    value: Any, *, fingerprint: str, function_ref: Mapping[str, Any]
) -> dict[str, Any]:
    locator = value if isinstance(value, Mapping) else {"semantic_id": value}
    return source_ref(
        input_fingerprint=fingerprint, function_ref=function_ref,
        source_kind="SEMANTIC_NODE", locator=locator,
    )


def _matching_fact_ssa_refs(node: Mapping[str, Any], value: Any) -> dict[str, list[Any]]:
    target_values = set(_scalar_values(value))
    fact_ssa = node.get("fact_ssa") if isinstance(node.get("fact_ssa"), Mapping) else {}
    result: dict[str, list[Any]] = {"reads": [], "writes": []}
    for direction in ("reads", "writes"):
        for item in fact_ssa.get(direction) or []:
            if not isinstance(item, Mapping):
                continue
            item_value = str(item.get("value") or item.get("base_name") or "")
            if item_value and item_value in target_values:
                result[direction].append(canonicalize(item))
    return result


def _scalar_values(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        return [item for nested in value.values() for item in _scalar_values(nested)]
    if isinstance(value, (list, tuple)):
        return [item for nested in value for item in _scalar_values(nested)]
    return [] if value is None else [str(value)]


def _first_present(node: Mapping[str, Any], paths: Iterable[str]) -> tuple[str, Any, bool]:
    for path in paths:
        found, value = _read_path(node, path)
        if found:
            return path, value, True
    return "", None, False


def _path_present(node: Mapping[str, Any], path: str) -> bool:
    found, value = _read_path(node, path)
    return found and value is not None and value != ""


def _read_path(node: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    value: Any = node
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return False, None
        value = value[part]
    return True, value


def _anchor_block(node: Mapping[str, Any]) -> str | None:
    placement = node.get("placement") if isinstance(node.get("placement"), Mapping) else {}
    if placement.get("status") == "anchored" and placement.get("anchor_block"):
        return str(placement["anchor_block"])
    return None


def _is_source_ref(value: Any) -> bool:
    return isinstance(value, Mapping) and value.get("source_kind") in {
        "SEMANTIC_NODE", "CFG_BLOCK", "CFG_EDGE", "DEFINITION", "USE",
        "STORAGE_ACCESS", "RECOVERY_DIAGNOSTIC",
    }


def _upstream_diagnostics(
    function: Mapping[str, Any], fingerprint: str, function_ref: Mapping[str, Any]
) -> list[dict[str, Any]]:
    out = []
    for index, item in enumerate(function.get("diagnostics") or []):
        if not isinstance(item, Mapping):
            continue
        out.append(canonicalize({
            "source_ref": source_ref(
                input_fingerprint=fingerprint, function_ref=function_ref,
                source_kind="RECOVERY_DIAGNOSTIC",
                locator={
                    "diagnostic_index": index,
                    "kind": item.get("kind"),
                    "semantic_id": item.get("semantic_id"),
                },
            ),
            "diagnostic": item,
            "mapped_code": _diagnostic_code(item),
        }))
    return out


def _diagnostic_code(item: Mapping[str, Any]) -> str:
    text = f"{item.get('kind') or ''} {item.get('reason') or ''}".lower()
    if "timeout" in text:
        return "TIMEOUT"
    if "unsupported" in text or "unmodeled" in text:
        return "UNSUPPORTED"
    if "truncat" in text:
        return "TRUNCATED"
    if "fallback" in text:
        return "FALLBACK"
    if "opaque" in text:
        return "OPAQUE"
    if "error" in text or "invalid" in text:
        return "ERROR"
    if "unanchor" in text or "insufficient" in text:
        return "INSUFFICIENT_EVIDENCE"
    return "UNKNOWN"


def _fingerprint_view(value: Any, parent_key: str | None = None) -> Any:
    if isinstance(value, Mapping):
        # Result directories and machine paths do not define the compiler
        # input.  The logical source locator is retained only when relative.
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key == "result_dir":
                continue
            if key == "source" and isinstance(item, str):
                normalized = item.replace("\\", "/")
                item = normalized.rsplit("/", 1)[-1]
            out[str(key)] = _fingerprint_view(item, str(key))
        return out
    if isinstance(value, list):
        converted = [_fingerprint_view(item, parent_key) for item in value]
        if parent_key in SORTED_COLLECTION_KEYS:
            return sorted(converted, key=canonical_json)
        return converted
    return value


def _compact_identity_fields(value: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "canonical_name", "full_name", "name", "id", "declaration_id",
        "source_mapping", "kind", "contract_kind",
    )
    return canonicalize({key: value[key] for key in keys if key in value and value[key] is not None})


def _dedupe_by_id(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {item["id"]: item for item in records}
    return [by_id[key] for key in sorted(by_id)]


def _dedupe_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {canonical_json(item): item for item in records}
    return [by_key[key] for key in sorted(by_key)]
