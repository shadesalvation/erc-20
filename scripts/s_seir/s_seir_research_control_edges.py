#!/usr/bin/env python3
"""P1-T5 high-recall candidate semantic-control reconstruction.

The implementation consumes P1-T1 SemanticAction occurrences, P1-T2
FlattenedRegions, P1-T4 AbstractPropagation caches, and the existing SFIR Fact
CFG.  It contracts carrier blocks until the first next semantic endpoint.  It
does not execute abstract transfer, solve predicates, normalize Guards, or
decide feasibility.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from typing import Any

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
    validate_evidence_record,
    validate_source_ref,
)
from s_seir_research_propagation import (
    node_context_states,
    query_node_context_state,
    validate_propagation,
    validate_result_evidence,
)
from s_seir_research_regions import validate_flattened_region


SFIR_SCHEMA = "s-seir-semantic-fact-ir/v1"
SCHEMA = "erc20-research/semantic-control-edge/v1"
IMPLEMENTATION_VERSION = "p1-t5-v1"
PAYLOAD_FIELDS = frozenset({
    "from", "to", "candidate_id", "guard_ref", "context_ref",
    "feasibility", "region_ref",
})
ENDPOINT_KINDS = frozenset({"ACTION", "ENTRY", "EXIT"})
ORIGINS = frozenset({
    "NORMAL_STRUCTURAL_CONTROL",
    "FLATTENED_PROPAGATION_CONTROL",
    "MIXED_BOUNDARY_CONTROL",
})


def _set(items: Iterable[Any]) -> list[Any]:
    return [canonicalize(item) for _, item in sorted(
        {canonical_json(item): item for item in items}.items()
    )]


def _producer(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return producer_record("P1-T5", IMPLEMENTATION_VERSION, dict(config or {}))


def _block_ref(block_id: str, fingerprint: str, function_ref: Mapping[str, Any]) -> dict[str, Any]:
    return source_ref(
        input_fingerprint=fingerprint,
        function_ref=function_ref,
        source_kind="CFG_BLOCK",
        locator={"block_id": block_id},
    )


def _edge_ref(edge: Mapping[str, Any], fingerprint: str, function_ref: Mapping[str, Any]) -> dict[str, Any]:
    return source_ref(
        input_fingerprint=fingerprint,
        function_ref=function_ref,
        source_kind="CFG_EDGE",
        locator={
            "edge_id": edge.get("edge_id"),
            "from": edge.get("from"),
            "to": edge.get("to"),
            "kind": edge.get("kind"),
        },
    )


def _entry_endpoint(function_ref: Mapping[str, Any]) -> dict[str, Any]:
    return canonicalize({"kind": "ENTRY", "function_ref": function_ref})


def _exit_endpoint(function_ref: Mapping[str, Any]) -> dict[str, Any]:
    return canonicalize({"kind": "EXIT", "function_ref": function_ref})


def _action_endpoint(action: Mapping[str, Any]) -> dict[str, Any]:
    return canonicalize({"kind": "ACTION", "action_ref": action["id"]})


def _function_key(function_ref: Mapping[str, Any]) -> str:
    return canonical_json(function_ref)


def _anchor_block(action: Mapping[str, Any]) -> str | None:
    anchor = (action.get("payload") or {}).get("control_anchor")
    if not isinstance(anchor, Mapping) or anchor.get("source_kind") != "CFG_BLOCK":
        return None
    locator = anchor.get("locator") if isinstance(anchor.get("locator"), Mapping) else {}
    value = locator.get("block_id")
    return None if value in {None, ""} else str(value)


def _semantic_id(action: Mapping[str, Any]) -> str | None:
    ref = (action.get("payload") or {}).get("semantic_ref")
    locator = ref.get("locator") if isinstance(ref, Mapping) and isinstance(ref.get("locator"), Mapping) else {}
    value = locator.get("semantic_id")
    return None if value in {None, ""} else str(value)


def _terminal(block: Mapping[str, Any]) -> bool:
    term = block.get("terminator") if isinstance(block.get("terminator"), Mapping) else {}
    return str(term.get("kind") or "").lower() in {
        "return", "revert", "stop", "selfdestruct",
    }


def _edge_key(edge: Mapping[str, Any]) -> str:
    return canonical_json({
        "edge_id": edge.get("edge_id"), "from": edge.get("from"),
        "to": edge.get("to"), "kind": edge.get("kind"),
    })


def _ref_edge_key(ref: Mapping[str, Any]) -> str:
    locator = ref.get("locator") if isinstance(ref.get("locator"), Mapping) else {}
    return canonical_json({
        "edge_id": locator.get("edge_id"), "from": locator.get("from"),
        "to": locator.get("to"), "kind": locator.get("kind"),
    })


def _ref_block_id(ref: Mapping[str, Any]) -> str:
    validate_source_ref(ref)
    if ref.get("source_kind") != "CFG_BLOCK":
        raise ContractValidationError("candidate carrier node is not a CFG_BLOCK SourceRef")
    value = (ref.get("locator") or {}).get("block_id")
    if value in {None, ""}:
        raise ContractValidationError("CFG_BLOCK SourceRef has no block_id")
    return str(value)


def _last_arm(context: Mapping[str, Any]) -> Any:
    elements = context.get("elements") if isinstance(context, Mapping) else None
    if not isinstance(elements, list) or not elements:
        return None
    last = elements[-1]
    return last.get("arm_ref") if isinstance(last, Mapping) else None


def _diagnostics(*statuses: Any) -> list[dict[str, Any]]:
    return _set(
        item
        for status in statuses
        if isinstance(status, Mapping)
        for item in status.get("diagnostics") or []
        if isinstance(item, Mapping)
    )


def _status(diagnostics: Iterable[Mapping[str, Any]], *, partial: bool = False) -> dict[str, Any]:
    items = _set(diagnostics)
    return analysis_status(
        proof="CANDIDATE",
        completion="PARTIAL" if partial or bool(items) else "COMPLETE",
        solver="NOT_RUN",
        diagnostics=items,
    )


def _guard_ref(edges: list[tuple[Mapping[str, Any], Mapping[str, Any]]]) -> Any:
    refs = _set(ref for raw, ref in edges if raw.get("guard") not in {None, ""})
    if not refs:
        return None
    if len(refs) == 1:
        return canonicalize({"kind": "UNREFINED_SOURCE_CONDITION", "source_ref": refs[0]})
    return canonicalize({"kind": "UNREFINED_SOURCE_CONDITION_SET", "source_refs": refs})


def _validate_guard_ref(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping):
        raise ContractValidationError("guard_ref must be null or unrefined source evidence")
    if value.get("kind") == "UNREFINED_SOURCE_CONDITION":
        if set(value) != {"kind", "source_ref"}:
            raise ContractValidationError("invalid single unrefined guard_ref")
        validate_source_ref(value["source_ref"])
        if value["source_ref"]["source_kind"] not in {"CFG_EDGE", "SEMANTIC_NODE"}:
            raise ContractValidationError("guard_ref must resolve to existing condition evidence")
        return
    if value.get("kind") == "UNREFINED_SOURCE_CONDITION_SET":
        if set(value) != {"kind", "source_refs"} or not isinstance(value["source_refs"], list):
            raise ContractValidationError("invalid unrefined guard_ref set")
        for ref in value["source_refs"]:
            validate_source_ref(ref)
            if ref["source_kind"] not in {"CFG_EDGE", "SEMANTIC_NODE"}:
                raise ContractValidationError("guard_ref set contains non-condition evidence")
        return
    raise ContractValidationError("P1-T5 guard_ref cannot be a normalized Guard")


def _validate_endpoint(endpoint: Any) -> None:
    if not isinstance(endpoint, Mapping) or endpoint.get("kind") not in ENDPOINT_KINDS:
        raise ContractValidationError("invalid semantic control endpoint")
    if endpoint["kind"] == "ACTION":
        if set(endpoint) != {"kind", "action_ref"} or not isinstance(endpoint["action_ref"], str):
            raise ContractValidationError("ACTION endpoint requires action_ref")
    elif set(endpoint) != {"kind", "function_ref"} or not isinstance(endpoint["function_ref"], Mapping):
        raise ContractValidationError("ENTRY/EXIT endpoint requires function_ref")


def validate_candidate_edge(edge: Mapping[str, Any]) -> None:
    """Validate the frozen candidate-stage SemanticControlEdge payload."""
    validate_artifact_envelope(edge)
    if edge.get("schema") != SCHEMA:
        raise ContractValidationError("not a frozen-v1 SemanticControlEdge")
    payload = edge.get("payload") or {}
    if set(payload) != PAYLOAD_FIELDS:
        raise ContractValidationError(
            "SemanticControlEdge fields mismatch; "
            f"missing={sorted(PAYLOAD_FIELDS.difference(payload))}, "
            f"extra={sorted(set(payload).difference(PAYLOAD_FIELDS))}"
        )
    _validate_endpoint(payload["from"])
    _validate_endpoint(payload["to"])
    if payload["candidate_id"] != edge["id"]:
        raise ContractValidationError("candidate_id must equal the artifact identity")
    if payload["feasibility"] != "UNRESOLVED":
        raise ContractValidationError("P1-T5 feasibility must remain UNRESOLVED")
    if edge["status"]["solver"] != "NOT_RUN":
        raise ContractValidationError("P1-T5 must not run a solver")
    if payload["region_ref"] is not None and not isinstance(payload["region_ref"], str):
        raise ContractValidationError("region_ref must be a FlattenedRegion id or null")
    if payload["context_ref"] is not None:
        context = payload["context_ref"]
        if not isinstance(context, Mapping) or set(context) != {
            "propagation_ref", "entry", "endpoint", "node_context_refs",
        }:
            raise ContractValidationError("invalid candidate context_ref")
        if not isinstance(context["propagation_ref"], str) or not isinstance(context["node_context_refs"], list):
            raise ContractValidationError("invalid candidate context_ref values")
        for item in context["node_context_refs"]:
            if not isinstance(item, Mapping) or set(item) != {"node_ref", "context"}:
                raise ContractValidationError("invalid node/context reference")
            validate_source_ref(item["node_ref"])
    if (payload["region_ref"] is None) != (payload["context_ref"] is None):
        raise ContractValidationError("region_ref and context_ref must be jointly present or absent")
    _validate_guard_ref(payload["guard_ref"])


class _FunctionReconstructor:
    def __init__(
        self,
        function: Mapping[str, Any],
        function_ref: Mapping[str, Any],
        actions: list[Mapping[str, Any]],
        regions: list[Mapping[str, Any]],
        propagations: Mapping[str, Mapping[str, Any]],
        fingerprint: str,
        producer: Mapping[str, Any],
        upstream_statuses: list[Mapping[str, Any]],
    ) -> None:
        self.function = function
        self.function_ref = canonicalize(function_ref)
        self.actions = sorted(actions, key=lambda item: item["id"])
        self.action_by_id = {item["id"]: item for item in self.actions}
        self.regions = sorted(regions, key=lambda item: item["id"])
        self.propagations = propagations
        self.fingerprint = fingerprint
        self.producer = producer
        self.upstream_statuses = upstream_statuses
        self.edges: dict[str, dict[str, Any]] = {}
        self.evidence: dict[str, dict[str, Any]] = {}
        self.unresolved: list[dict[str, Any]] = []
        for stage, status in zip(
            ("SEMANTIC_ACTION", "FLATTENED_REGION", "ABSTRACT_PROPAGATION"),
            upstream_statuses,
        ):
            if isinstance(status, Mapping) and (
                status.get("completion") != "COMPLETE" or status.get("diagnostics")
            ):
                self._unresolved(
                    f"UPSTREAM_{stage}_PARTIAL",
                    f"{stage} input retains partial or unresolved evidence",
                    [],
                    _diagnostics(status),
                    extra={"upstream_status": status},
                )

        cfg = function.get("fact_cfg") if isinstance(function.get("fact_cfg"), Mapping) else {}
        self.blocks = {
            str(item.get("block_id")): item
            for item in cfg.get("blocks") or []
            if isinstance(item, Mapping) and item.get("block_id") not in {None, ""}
        }
        self.raw_edges = [
            item for item in cfg.get("edges") or []
            if isinstance(item, Mapping)
            and str(item.get("from") or "") in self.blocks
            and str(item.get("to") or "") in self.blocks
        ]
        self.outgoing: dict[str, list[Mapping[str, Any]]] = {key: [] for key in self.blocks}
        self.incoming: dict[str, list[Mapping[str, Any]]] = {key: [] for key in self.blocks}
        for edge in self.raw_edges:
            self.outgoing[str(edge["from"])].append(edge)
            self.incoming[str(edge["to"])].append(edge)
        for values in list(self.outgoing.values()) + list(self.incoming.values()):
            values.sort(key=_edge_key)
        declared_entries = [
            str(item) for item in cfg.get("entry_blocks") or [] if str(item) in self.blocks
        ]
        self.entry_blocks = sorted(set(declared_entries))
        self.exit_blocks = sorted(
            block_id for block_id, block in self.blocks.items()
            if _terminal(block) or not self.outgoing.get(block_id)
        )

        self.actions_by_block: dict[str, list[Mapping[str, Any]]] = {}
        for action in self.actions:
            block_id = _anchor_block(action)
            if block_id in self.blocks:
                self.actions_by_block.setdefault(block_id, []).append(action)
            elif block_id is None:
                self._unresolved(
                    "UNANCHORED_ACTION",
                    "SemanticAction has no resolvable CFG anchor",
                    [action["id"]],
                    _diagnostics(action.get("status")),
                )
            else:
                item = diagnostic(
                    "INSUFFICIENT_EVIDENCE",
                    "SemanticAction anchor is absent from the function Fact CFG",
                    affected_refs=[action["id"]],
                    scope={"function_ref": self.function_ref, "anchor_block": block_id},
                )
                self._unresolved("MISSING_ACTION_ANCHOR", item["reason"], [action["id"]], [item])
        self.local_orders: dict[str, dict[str, Any]] = {}
        for block_id, block_actions in self.actions_by_block.items():
            self.local_orders[block_id] = self._local_order(block_id, block_actions)

        self.region_by_id = {item["id"]: item for item in self.regions}
        self.region_nodes: dict[str, set[str]] = {}
        self.region_node_refs: dict[str, dict[str, Mapping[str, Any]]] = {}
        self.region_exit_edges: dict[str, set[str]] = {}
        self.region_cases: dict[str, dict[str, Mapping[str, Any]]] = {}
        self.regions_at_core: dict[str, list[str]] = {}
        for region in self.regions:
            region_id = region["id"]
            refs = {
                _ref_block_id(ref): ref for ref in region["payload"]["region_cfg_refs"]
            }
            self.region_nodes[region_id] = set(refs)
            self.region_node_refs[region_id] = refs
            self.region_exit_edges[region_id] = {
                _ref_edge_key(item["edge_ref"])
                for item in region["payload"]["exits"]
                if isinstance(item.get("edge_ref"), Mapping)
            }
            self.region_cases[region_id] = {
                _ref_edge_key(item["edge_ref"]): item
                for item in region["payload"]["cases"]
            }
            for block_id in refs:
                self.regions_at_core.setdefault(block_id, []).append(region_id)
            if region_id not in propagations:
                item = diagnostic(
                    "INSUFFICIENT_EVIDENCE",
                    "FlattenedRegion has no matching AbstractPropagation",
                    affected_refs=[region_id],
                    scope={"function_ref": self.function_ref, "region_ref": region_id},
                )
                self._unresolved("MISSING_PROPAGATION", item["reason"], [region_id], [item])
            else:
                propagation = propagations[region_id]
                termination = propagation["payload"]["termination"]
                if termination != "FIXED_POINT" or propagation["status"]["completion"] != "COMPLETE":
                    code = "TRUNCATED" if termination == "RESOURCE_LIMIT" else termination
                    self._unresolved(
                        f"PROPAGATION_{termination}",
                        "flattened scope retains partial or unresolved propagation evidence",
                        [region_id, propagation["id"]],
                        _diagnostics(propagation.get("status")),
                        extra={
                            "termination": termination,
                            "frontier": propagation["payload"]["convergence_evidence"]["worklist"]["frontier"],
                            "mapped_code": code,
                        },
                    )

    def _unresolved(
        self,
        kind: str,
        reason: str,
        affected_refs: Iterable[Any],
        diagnostics: Iterable[Mapping[str, Any]],
        *,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        self.unresolved.append(canonicalize({
            "kind": kind,
            "reason": reason,
            "function_ref": self.function_ref,
            "affected_refs": list(affected_refs),
            "diagnostics": list(diagnostics),
            "details": dict(extra or {}),
        }))

    def _local_order(self, block_id: str, actions: list[Mapping[str, Any]]) -> dict[str, Any]:
        block = self.blocks[block_id]
        declared = block.get("semantic_ids")
        semantic_ids = [_semantic_id(action) for action in actions]
        reliable = (
            isinstance(declared, list)
            and len(declared) == len(set(map(str, declared)))
            and all(value is not None and list(map(str, declared)).count(value) == 1 for value in semantic_ids)
        )
        if reliable:
            positions = {str(value): index for index, value in enumerate(declared)}
            ordered = sorted(actions, key=lambda item: positions[_semantic_id(item)])
            return {"reliable": True, "actions": ordered, "reason": "SFIR_SEMANTIC_IDS_ORDER"}
        item = diagnostic(
            "INSUFFICIENT_EVIDENCE",
            "same-block SemanticActions lack complete unique SFIR semantic_ids order evidence",
            affected_refs=[item["id"] for item in actions],
            scope={"function_ref": self.function_ref, "block_ref": _block_ref(block_id, self.fingerprint, self.function_ref)},
        )
        self._unresolved(
            "AMBIGUOUS_LOCAL_ACTION_ORDER", item["reason"],
            [value["id"] for value in actions], [item],
        )
        return {
            "reliable": False,
            "actions": sorted(actions, key=lambda item: item["id"]),
            "reason": "AMBIGUOUS_LOCAL_ORDER",
            "diagnostic": item,
        }

    def _core_region(self, block_id: str) -> str | None:
        values = sorted(self.regions_at_core.get(block_id) or [])
        if len(values) <= 1:
            return values[0] if values else None
        item = diagnostic(
            "UNSUPPORTED",
            "overlapping FlattenedRegions make the active propagation scope ambiguous",
            affected_refs=values,
            scope={"function_ref": self.function_ref, "block_id": block_id},
        )
        self._unresolved("OVERLAPPING_REGIONS", item["reason"], values, [item])
        return ""

    def _node_ref(self, region_id: str, block_id: str) -> Mapping[str, Any] | None:
        ref = self.region_node_refs[region_id].get(block_id)
        if ref is not None:
            return ref
        region = self.region_by_id[region_id]
        for item in region["payload"]["exits"]:
            target = item.get("to_ref")
            if isinstance(target, Mapping) and _ref_block_id(target) == block_id:
                return target
        return None

    def _enter_region(self, state: Mapping[str, Any], region_id: str) -> list[dict[str, Any]]:
        prior_region = state.get("region_id")
        if state.get("has_region") and prior_region not in {None, region_id}:
            item = diagnostic(
                "UNSUPPORTED",
                "one direct candidate carrier crosses multiple FlattenedRegion scopes",
                affected_refs=[prior_region, region_id],
                scope={"function_ref": self.function_ref},
            )
            self._unresolved(
                "MULTI_REGION_CARRIER", item["reason"],
                [prior_region, region_id], [item],
            )
            return []
        propagation = self.propagations.get(region_id)
        ref = self._node_ref(region_id, state["block_id"])
        if propagation is None or ref is None:
            return []
        entries = node_context_states(propagation, node_ref=ref)
        if not entries:
            termination = propagation["payload"]["termination"]
            code = "TRUNCATED" if termination == "RESOURCE_LIMIT" else "INSUFFICIENT_EVIDENCE"
            item = diagnostic(
                code,
                "no reached node/context cache entry covers a structural region entry",
                affected_refs=[region_id, propagation["id"], ref],
                scope={"function_ref": self.function_ref, "region_ref": region_id},
            )
            self._unresolved("UNREACHED_REGION_ENTRY", item["reason"], [region_id, ref], [item])
            return []
        result = []
        for entry in entries:
            value = dict(state)
            value.update({
                "mode": "REGION",
                "region_id": region_id,
                "propagation_id": propagation["id"],
                "context": entry["context"],
                "context_entries": list(state["context_entries"]) + [{
                    "node_ref": entry["node_ref"], "context": entry["context"],
                }],
                "has_region": True,
                "boundary_transitions": list(state["boundary_transitions"]) + [{
                    "kind": "ENTER_FLATTENED_REGION",
                    "region_ref": region_id,
                    "node_ref": ref,
                    "propagation_ref": propagation["id"],
                }],
            })
            result.append(canonicalize(value))
        return sorted(result, key=lambda item: canonical_json([
            item["block_id"], item["context"], item["carrier_edge_refs"],
        ]))

    def _state_key(self, state: Mapping[str, Any]) -> str:
        return canonical_json({
            "block_id": state["block_id"],
            "mode": state["mode"],
            "region_id": state.get("region_id"),
            "context": state.get("context"),
            "cursor": state["cursor"],
            # A path alternative is the finite set of structural branch arms
            # crossed since the source endpoint.  Repeating a loop arm does
            # not create a new direct-successor alternative merely because it
            # was traversed another time.
            "branch_alternative_refs": state["branch_alternative_refs"],
        })

    def _base_state(self, block_id: str, cursor: str, *, normal: bool) -> dict[str, Any]:
        return {
            "block_id": block_id,
            "cursor": cursor,
            "mode": "NORMAL",
            "region_id": None,
            "propagation_id": None,
            "context": None,
            "context_entries": [],
            "carrier_block_refs": [],
            "carrier_edge_refs": [],
            "carrier_edges": [],
            "local_relations": [],
            "boundary_transitions": [],
            "branch_alternative_refs": [],
            "has_normal": normal,
            "has_region": False,
        }

    def _initial_states(self, source: Mapping[str, Any]) -> list[dict[str, Any]]:
        if source["kind"] == "ENTRY":
            result = []
            if not self.entry_blocks:
                item = diagnostic(
                    "INSUFFICIENT_EVIDENCE",
                    "Fact CFG has no declared resolvable entry block",
                    scope={"function_ref": self.function_ref},
                )
                self._unresolved("NO_CFG_ENTRY", item["reason"], [], [item])
                return []
            for block_id in self.entry_blocks:
                state = self._base_state(block_id, "ENTER", normal=True)
                region_id = self._core_region(block_id)
                if region_id == "":
                    continue
                if region_id is not None:
                    state["has_normal"] = False
                    result.extend(self._enter_region(state, region_id))
                else:
                    result.append(canonicalize(state))
            return result

        action = self.action_by_id[source["action_ref"]]
        block_id = _anchor_block(action)
        if block_id not in self.blocks:
            return []
        state = self._base_state(block_id, f"AFTER:{action['id']}", normal=True)
        region_id = self._core_region(block_id)
        if region_id is not None:
            state["has_normal"] = False
            return self._enter_region(state, region_id)
        return [canonicalize(state)]

    def _local_targets(self, source: Mapping[str, Any], state: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], bool, list[dict[str, Any]]]:
        order = self.local_orders.get(state["block_id"])
        if order is None:
            return [], True, []
        actions = order["actions"]
        cursor = state["cursor"]
        relation = {
            "block_ref": _block_ref(state["block_id"], self.fingerprint, self.function_ref),
            "order_evidence": order["reason"],
            "cursor": cursor,
        }
        if cursor == "ENTER":
            if order["reliable"]:
                return [actions[0]], False, [canonicalize(relation)]
            relation["alternatives"] = [item["id"] for item in actions]
            return actions, False, [canonicalize(relation)]
        if cursor.startswith("AFTER:"):
            source_id = cursor[len("AFTER:"):]
            if order["reliable"]:
                ids = [item["id"] for item in actions]
                if source_id not in ids:
                    item = diagnostic(
                        "INSUFFICIENT_EVIDENCE",
                        "source action is absent from the block's reliable local order",
                        affected_refs=[source_id],
                        scope={"function_ref": self.function_ref, "block_id": state["block_id"]},
                    )
                    self._unresolved("LOCAL_SOURCE_NOT_ORDERED", item["reason"], [source_id], [item])
                    return [], False, [canonicalize(relation)]
                index = ids.index(source_id)
                return ([actions[index + 1]] if index + 1 < len(actions) else []), index + 1 >= len(actions), [canonicalize(relation)]
            alternatives = [item for item in actions if item["id"] != source_id]
            relation["alternatives"] = [item["id"] for item in alternatives]
            # The source may be last, so structural successors remain possible.
            return alternatives, True, [canonicalize(relation)]
        return [], True, []

    def _context_ref(self, state: Mapping[str, Any]) -> Any:
        entries = state["context_entries"]
        if not state["has_region"] or not entries:
            return None
        return canonicalize({
            "propagation_ref": state["propagation_id"],
            "entry": entries[0],
            "endpoint": entries[-1],
            "node_context_refs": entries,
        })

    def _origin(self, state: Mapping[str, Any]) -> str:
        if not state["has_region"]:
            return "NORMAL_STRUCTURAL_CONTROL"
        if state["has_normal"]:
            return "MIXED_BOUNDARY_CONTROL"
        return "FLATTENED_PROPAGATION_CONTROL"

    def _emit(
        self,
        source: Mapping[str, Any],
        target: Mapping[str, Any],
        state: Mapping[str, Any],
        local_relations: Iterable[Mapping[str, Any]],
    ) -> None:
        context_ref = self._context_ref(state)
        region_ref = state.get("region_id") if context_ref is not None else None
        origin = self._origin(state)
        path_alternative = canonicalize({
            "origin": origin,
            "carrier_block_refs": state["carrier_block_refs"],
            "carrier_edge_refs": state["carrier_edge_refs"],
            "branch_alternative_refs": state["branch_alternative_refs"],
            "local_relations": list(state["local_relations"]) + list(local_relations),
            "boundary_transitions": state["boundary_transitions"],
        })
        identity_basis = canonicalize({
            "from": source,
            "to": target,
            "region_ref": region_ref,
            "context_ref": context_ref,
            "path_alternative": path_alternative,
        })
        candidate_id = stable_identity("semantic-control-edge", identity_basis)

        source_action = self.action_by_id.get(source.get("action_ref"))
        target_action = self.action_by_id.get(target.get("action_ref"))
        region = self.region_by_id.get(region_ref)
        propagation = self.propagations.get(region_ref)
        status_diagnostics = _diagnostics(
            *(self.upstream_statuses + [
                source_action.get("status") if source_action else None,
                target_action.get("status") if target_action else None,
                region.get("status") if region else None,
                propagation.get("status") if propagation else None,
            ])
        )
        partial = bool(status_diagnostics) or any(
            value is not None and value.get("completion") != "COMPLETE"
            for value in [
                source_action.get("status") if source_action else None,
                target_action.get("status") if target_action else None,
                region.get("status") if region else None,
                propagation.get("status") if propagation else None,
            ]
        )
        edge_status = _status(status_diagnostics, partial=partial)

        source_refs: list[Mapping[str, Any]] = []
        artifact_refs: list[str] = []
        if source_action is not None:
            source_refs.extend([
                source_action["payload"]["semantic_ref"],
                source_action["payload"]["control_anchor"],
            ])
            artifact_refs.append(source_action["id"])
        elif state["carrier_block_refs"]:
            source_refs.append(state["carrier_block_refs"][0])
        if target_action is not None:
            source_refs.extend([
                target_action["payload"]["semantic_ref"],
                target_action["payload"]["control_anchor"],
            ])
            artifact_refs.append(target_action["id"])
        elif state["carrier_block_refs"]:
            source_refs.append(state["carrier_block_refs"][-1])
        source_refs.extend(state["carrier_block_refs"])
        source_refs.extend(state["carrier_edge_refs"])
        if region is not None:
            artifact_refs.append(region["id"])
        if propagation is not None:
            artifact_refs.append(propagation["id"])

        evidence = evidence_record(
            kind="candidate_semantic_control_edge",
            claim={
                "predicate": "first_next_semantic_endpoint_candidate",
                "operands": {"from": source, "to": target, "candidate_id": candidate_id},
            },
            premises=[
                {"sfir_schema": SFIR_SCHEMA},
                {"compression": "FIRST_NEXT_SEMANTIC_ENDPOINT"},
                {"propagation_cache_phase": "INCOMING_BEFORE_NODE_TRANSFER"},
                {"feasibility": "UNRESOLVED"},
            ],
            source_refs=_set(ref for ref in source_refs if isinstance(ref, Mapping) and ref.get("source_kind")),
            artifact_refs=sorted(set(artifact_refs)),
            rule="p1-t5/first-next-candidate/v1",
            scope={
                "function_ref": self.function_ref,
                "region_ref": region_ref,
                "context_ref": context_ref,
            },
            assumptions=[
                "carrier CFG reachability is an over-approximated structural alternative",
                "no feasibility or normalized Guard claim is made",
            ],
            result={
                "identity_basis": identity_basis,
                "origin": origin,
                "carrier": path_alternative,
                "propagation_node_context_refs": state["context_entries"],
                "unrefined_condition_refs": [
                    ref for raw, ref in state["carrier_edges"] if raw.get("guard") not in {None, ""}
                ],
                "solver_outcome": "NOT_RUN",
            },
            reason_code="STRUCTURAL_FIRST_NEXT_CANDIDATE",
            producer=self.producer,
            status=edge_status,
        )
        edge = artifact_envelope(
            schema=SCHEMA,
            artifact_id=candidate_id,
            function_ref=self.function_ref,
            input_fingerprint=self.fingerprint,
            producer=self.producer,
            evidence_refs=[evidence["id"]],
            status=edge_status,
            payload={
                "from": source,
                "to": target,
                "candidate_id": candidate_id,
                "guard_ref": _guard_ref(state["carrier_edges"]),
                "context_ref": context_ref,
                "feasibility": "UNRESOLVED",
                "region_ref": region_ref,
            },
            extensions={},
        )
        validate_candidate_edge(edge)
        self.edges[candidate_id] = edge
        self.evidence[evidence["id"]] = evidence

    def _advance_normal(self, state: Mapping[str, Any]) -> list[dict[str, Any]]:
        result = []
        outgoing = self.outgoing.get(state["block_id"], [])
        for raw in outgoing:
            target = str(raw["to"])
            value = dict(state)
            edge_ref = _edge_ref(raw, self.fingerprint, self.function_ref)
            alternatives = list(state["branch_alternative_refs"])
            if len(outgoing) > 1:
                alternatives = _set(alternatives + [edge_ref])
            value.update({
                "block_id": target,
                "cursor": "ENTER",
                "carrier_edge_refs": list(state["carrier_edge_refs"]) + [edge_ref],
                "carrier_edges": list(state["carrier_edges"]) + [(raw, edge_ref)],
                "branch_alternative_refs": alternatives,
            })
            region_id = self._core_region(target)
            if region_id == "":
                continue
            if region_id is not None:
                result.extend(self._enter_region(value, region_id))
            else:
                result.append(canonicalize(value))
        return result

    def _matching_region_targets(
        self,
        state: Mapping[str, Any],
        raw: Mapping[str, Any],
        target_ref: Mapping[str, Any],
    ) -> list[Mapping[str, Any]]:
        propagation = self.propagations[state["region_id"]]
        key = _edge_key(raw)
        case = self.region_cases[state["region_id"]].get(key)
        if case is None:
            exact = query_node_context_state(propagation, target_ref, state["context"])
            return [] if exact is None else [exact]
        arm_ref = case["edge_ref"]
        matches = [
            item for item in node_context_states(propagation, node_ref=target_ref)
            if canonical_json(_last_arm(item["context"])) == canonical_json(arm_ref)
        ]
        return sorted(matches, key=lambda item: canonical_json([
            item["node_ref"], item["context"],
        ]))

    def _advance_region(self, state: Mapping[str, Any]) -> list[dict[str, Any]]:
        region_id = state["region_id"]
        if state["block_id"] not in self.region_nodes[region_id]:
            value = dict(state)
            value.update({
                "mode": "NORMAL", "cursor": "SKIP_LOCAL", "has_normal": True,
                "boundary_transitions": list(state["boundary_transitions"]) + [{
                    "kind": "LEAVE_FLATTENED_REGION",
                    "region_ref": region_id,
                    "block_ref": _block_ref(state["block_id"], self.fingerprint, self.function_ref),
                }],
            })
            return [canonicalize(value)]

        result = []
        eligible = [
            raw for raw in self.outgoing.get(state["block_id"], [])
            if str(raw["to"]) in self.region_nodes[region_id]
            or _edge_key(raw) in self.region_exit_edges[region_id]
        ]
        for raw in eligible:
            target = str(raw["to"])
            internal = target in self.region_nodes[region_id]
            explicit_exit = _edge_key(raw) in self.region_exit_edges[region_id]
            if not internal and not explicit_exit:
                continue
            target_ref = self._node_ref(region_id, target)
            if target_ref is None:
                continue
            for entry in self._matching_region_targets(state, raw, target_ref):
                edge_ref = _edge_ref(raw, self.fingerprint, self.function_ref)
                alternatives = list(state["branch_alternative_refs"])
                if len(eligible) > 1:
                    alternatives = _set(alternatives + [edge_ref])
                value = dict(state)
                value.update({
                    "block_id": target,
                    "cursor": "ENTER",
                    "context": entry["context"],
                    "context_entries": list(state["context_entries"]) + [{
                        "node_ref": entry["node_ref"], "context": entry["context"],
                    }],
                    "carrier_edge_refs": list(state["carrier_edge_refs"]) + [edge_ref],
                    "carrier_edges": list(state["carrier_edges"]) + [(raw, edge_ref)],
                    "branch_alternative_refs": alternatives,
                })
                result.append(canonicalize(value))
        return result

    def reconstruct_from(self, source: Mapping[str, Any]) -> None:
        queue = deque(self._initial_states(source))
        visited: set[str] = set()
        while queue:
            state = queue.popleft()
            block_id = state["block_id"]
            key = self._state_key(state)
            if key in visited:
                continue
            visited.add(key)
            state = dict(state)
            block_ref = _block_ref(block_id, self.fingerprint, self.function_ref)
            if not state["carrier_block_refs"] or canonical_json(state["carrier_block_refs"][-1]) != canonical_json(block_ref):
                state["carrier_block_refs"] = list(state["carrier_block_refs"]) + [block_ref]

            if state["mode"] == "NORMAL":
                region_id = self._core_region(block_id)
                if region_id == "":
                    continue
                if region_id is not None:
                    queue.extend(self._enter_region(state, region_id))
                    continue
            else:
                propagation = self.propagations.get(state["region_id"])
                node_ref = self._node_ref(state["region_id"], block_id)
                if propagation is None or node_ref is None:
                    continue
                if query_node_context_state(propagation, node_ref, state["context"]) is None:
                    continue

            targets, may_continue, local = self._local_targets(source, state)
            for action in targets:
                self._emit(source, _action_endpoint(action), state, local)
            if targets and not may_continue:
                continue

            state["local_relations"] = list(state["local_relations"]) + local
            if state["mode"] == "NORMAL":
                successors = self._advance_normal(state)
            else:
                successors = self._advance_region(state)
            if successors:
                queue.extend(successors)
            else:
                self._emit(source, _exit_endpoint(self.function_ref), state, [])

    def run(self) -> dict[str, Any]:
        self.reconstruct_from(_entry_endpoint(self.function_ref))
        for action in self.actions:
            if _anchor_block(action) in self.blocks:
                self.reconstruct_from(_action_endpoint(action))

        edges = [self.edges[key] for key in sorted(self.edges)]
        flattened = [item for item in edges if item["payload"]["region_ref"] is not None]
        normal = [item for item in edges if item["payload"]["region_ref"] is None]
        mixed = [
            item for item in flattened
            if self.evidence[item["evidence_refs"][0]]["result"]["origin"] == "MIXED_BOUNDARY_CONTROL"
        ]
        frontiers = []
        for region_id, propagation in sorted(self.propagations.items()):
            if region_id not in self.region_by_id:
                continue
            frontier = propagation["payload"]["convergence_evidence"]["worklist"]["frontier"]
            if frontier or propagation["payload"]["termination"] == "RESOURCE_LIMIT":
                frontiers.append({
                    "region_ref": region_id,
                    "propagation_ref": propagation["id"],
                    "termination": propagation["payload"]["termination"],
                    "frontier": frontier,
                })
        unresolved = sorted(_set(self.unresolved), key=canonical_json)
        return {
            "edges": edges,
            "evidence": [self.evidence[key] for key in sorted(self.evidence)],
            "accounting": canonicalize({
                "function_ref": self.function_ref,
                "input_action_occurrence_count": len(self.actions),
                "endpoint_scope": {
                    "entry": _entry_endpoint(self.function_ref),
                    "entry_block_refs": [
                        _block_ref(item, self.fingerprint, self.function_ref) for item in self.entry_blocks
                    ],
                    "exit": _exit_endpoint(self.function_ref),
                    "exit_block_refs": [
                        _block_ref(item, self.fingerprint, self.function_ref) for item in self.exit_blocks
                    ],
                },
                "flattened_scope_candidate_count": len(flattened),
                "normal_scope_candidate_count": len(normal),
                "mixed_boundary_candidate_count": len(mixed),
                "unresolved_scopes": unresolved,
                "partial": bool(unresolved) or bool(frontiers),
                "truncated": bool(frontiers),
                "propagation_resource_frontiers": frontiers,
                "emitted_candidate_ids": [item["id"] for item in edges],
            }),
        }


def reconstruct_candidate_control_edges(
    sfir_payload: Mapping[str, Any],
    action_result: Mapping[str, Any],
    region_result: Mapping[str, Any],
    propagation_result: Mapping[str, Any],
    *,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct candidate first-next SemanticControlEdges without refinement."""
    if not all(isinstance(item, Mapping) for item in (
        sfir_payload, action_result, region_result, propagation_result,
    )):
        raise ContractValidationError("P1-T5 requires SFIR and P1-T1/P1-T2/P1-T4 result objects")
    if sfir_payload.get("schema") != SFIR_SCHEMA:
        raise ContractValidationError("candidate reconstruction requires existing SFIR Fact CFG")
    fingerprint = str(action_result.get("input_fingerprint") or region_result.get("input_fingerprint") or "")
    if not fingerprint:
        raise ContractValidationError("candidate reconstruction requires an input fingerprint")
    if region_result.get("input_fingerprint") != fingerprint:
        raise ContractValidationError("SemanticAction/FlattenedRegion fingerprint mismatch")
    producer = _producer(config)

    actions = list(action_result.get("actions") or [])
    for action in actions:
        validate_semantic_action(action)
        if action["input_fingerprint"] != fingerprint:
            raise ContractValidationError("SemanticAction fingerprint mismatch")
    regions = list(region_result.get("regions") or [])
    for region in regions:
        validate_flattened_region(region)
        if region["input_fingerprint"] != fingerprint:
            raise ContractValidationError("FlattenedRegion fingerprint mismatch")
    propagations = list(propagation_result.get("propagations") or [])
    validate_result_evidence(propagation_result)
    regions_by_id = {item["id"]: item for item in regions}
    propagation_by_region: dict[str, Mapping[str, Any]] = {}
    for propagation in propagations:
        region_id = propagation["payload"]["region_ref"]
        region = regions_by_id.get(region_id)
        if region is None:
            raise ContractValidationError("AbstractPropagation references an unknown FlattenedRegion")
        validate_propagation(propagation, region=region)
        if region_id in propagation_by_region:
            raise ContractValidationError("multiple AbstractPropagations for one region are ambiguous")
        propagation_by_region[region_id] = propagation

    functions = [
        item for item in sfir_payload.get("functions") or [] if isinstance(item, Mapping)
    ]
    actions_by_function: dict[str, list[Mapping[str, Any]]] = {}
    function_refs: dict[str, Mapping[str, Any]] = {}
    for action in actions:
        function_id = str((action.get("function_ref") or {}).get("sfir_function_id") or "")
        actions_by_function.setdefault(function_id, []).append(action)
        function_refs[function_id] = action["function_ref"]
    regions_by_function: dict[str, list[Mapping[str, Any]]] = {}
    for region in regions:
        function_id = str((region.get("function_ref") or {}).get("sfir_function_id") or "")
        regions_by_function.setdefault(function_id, []).append(region)
        function_refs.setdefault(function_id, region["function_ref"])

    all_edges: list[dict[str, Any]] = []
    all_evidence: list[dict[str, Any]] = []
    accounting: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    seen_functions: set[str] = set()
    for function in sorted(functions, key=lambda item: canonical_json({
        "function_id": item.get("function_id"), "signature": item.get("signature"),
    })):
        function_id = str(function.get("function_id") or "")
        if function_id in seen_functions:
            raise ContractValidationError("duplicate SFIR function_id in candidate reconstruction")
        seen_functions.add(function_id)
        function_ref = function_refs.get(function_id)
        if function_ref is None:
            # No action/region producer supplied a FunctionRef.  Reuse the
            # frozen P1-T1 builder view without extracting any action.
            from s_seir_research_actions import build_function_ref
            function_ref = build_function_ref(function, fingerprint)
        function_regions = regions_by_function.get(function_id, [])
        relevant_propagations = {
            region["id"]: propagation_by_region[region["id"]]
            for region in function_regions if region["id"] in propagation_by_region
        }
        reconstructor = _FunctionReconstructor(
            function, function_ref, actions_by_function.get(function_id, []),
            function_regions, relevant_propagations, fingerprint, producer,
            [
                action_result.get("status") or {},
                region_result.get("status") or {},
                propagation_result.get("status") or {},
            ],
        )
        output = reconstructor.run()
        all_edges.extend(output["edges"])
        all_evidence.extend(output["evidence"])
        accounting.append(output["accounting"])
        unresolved.extend(output["accounting"]["unresolved_scopes"])

    missing_function_actions = [
        item["id"] for item in actions
        if str((item.get("function_ref") or {}).get("sfir_function_id") or "") not in seen_functions
    ]
    missing_function_regions = [
        item["id"] for item in regions
        if str((item.get("function_ref") or {}).get("sfir_function_id") or "") not in seen_functions
    ]
    if missing_function_actions or missing_function_regions:
        item = diagnostic(
            "INSUFFICIENT_EVIDENCE",
            "upstream artifacts reference a function absent from SFIR",
            affected_refs=missing_function_actions + missing_function_regions,
            scope={"input_fingerprint": fingerprint},
        )
        unresolved.append(canonicalize({
            "kind": "MISSING_SFIR_FUNCTION",
            "reason": item["reason"],
            "function_ref": None,
            "affected_refs": item["affected_refs"],
            "diagnostics": [item],
            "details": {},
        }))

    by_edge = {item["id"]: item for item in all_edges}
    by_evidence = {item["id"]: item for item in all_evidence}
    all_edges = [by_edge[key] for key in sorted(by_edge)]
    all_evidence = [by_evidence[key] for key in sorted(by_evidence)]
    accounting.sort(key=lambda item: _function_key(item["function_ref"]))
    unresolved = sorted(_set(unresolved), key=canonical_json)
    upstream_diagnostics = _diagnostics(
        action_result.get("status"), region_result.get("status"), propagation_result.get("status")
    )
    partial = (
        bool(unresolved)
        or bool(upstream_diagnostics)
        or any((value.get("status") or {}).get("completion") != "COMPLETE" for value in (
            action_result, region_result, propagation_result,
        ))
        or any(item["partial"] for item in accounting)
    )
    result = canonicalize({
        "producer": producer,
        "input_fingerprint": fingerprint,
        "edges": all_edges,
        "evidence_records": all_evidence,
        "accounting": accounting,
        "unresolved_scopes": unresolved,
        "status": analysis_status(
            proof="CANDIDATE" if all_edges else "UNKNOWN",
            completion="PARTIAL" if partial else "COMPLETE",
            solver="NOT_RUN",
            diagnostics=_set(upstream_diagnostics + [
                diagnostic_item
                for scope in unresolved
                for diagnostic_item in scope.get("diagnostics") or []
            ]),
        ),
    })
    validate_candidate_collection(
        result, actions=actions, regions=regions, propagations=propagations,
    )
    return result


def validate_candidate_collection(
    result: Mapping[str, Any],
    *,
    actions: Iterable[Mapping[str, Any]] | None = None,
    regions: Iterable[Mapping[str, Any]] | None = None,
    propagations: Iterable[Mapping[str, Any]] | None = None,
) -> None:
    """Validate edge identities, sidecar closure, accounting, and references."""
    required = {
        "producer", "input_fingerprint", "edges", "evidence_records",
        "accounting", "unresolved_scopes", "status",
    }
    if not isinstance(result, Mapping) or set(result) != required:
        raise ContractValidationError("invalid P1-T5 candidate result wrapper")
    if result["status"]["solver"] != "NOT_RUN":
        raise ContractValidationError("candidate collection cannot contain solver output")
    records = {item["id"]: item for item in result["evidence_records"]}
    if len(records) != len(result["evidence_records"]):
        raise ContractValidationError("duplicate candidate evidence id")
    for record in records.values():
        validate_evidence_record(record)
        if record["status"]["solver"] != "NOT_RUN":
            raise ContractValidationError("candidate evidence cannot contain solver output")
    edge_ids = set()
    for edge in result["edges"]:
        validate_candidate_edge(edge)
        if edge["id"] in edge_ids:
            raise ContractValidationError("duplicate SemanticControlEdge candidate id")
        edge_ids.add(edge["id"])
        if len(edge["evidence_refs"]) != 1 or edge["evidence_refs"][0] not in records:
            raise ContractValidationError("candidate edge evidence reference is unresolved")
        record = records[edge["evidence_refs"][0]]
        basis = (record.get("result") or {}).get("identity_basis")
        if edge["id"] != stable_identity("semantic-control-edge", basis):
            raise ContractValidationError("candidate identity does not match evidence basis")
        if edge["payload"]["from"] != basis["from"] or edge["payload"]["to"] != basis["to"]:
            raise ContractValidationError("candidate endpoint/evidence identity mismatch")
        if edge["payload"]["region_ref"] != basis["region_ref"] or edge["payload"]["context_ref"] != basis["context_ref"]:
            raise ContractValidationError("candidate region/context identity mismatch")

    action_ids = {item["id"] for item in actions or []}
    region_ids = {item["id"] for item in regions or []}
    propagation_by_id = {item["id"]: item for item in propagations or []}
    propagation_ids = set(propagation_by_id)
    for edge in result["edges"]:
        for endpoint in (edge["payload"]["from"], edge["payload"]["to"]):
            if actions is not None and endpoint["kind"] == "ACTION" and endpoint["action_ref"] not in action_ids:
                raise ContractValidationError("candidate endpoint does not resolve to a SemanticAction")
        if regions is not None and edge["payload"]["region_ref"] is not None and edge["payload"]["region_ref"] not in region_ids:
            raise ContractValidationError("candidate region_ref is unresolved")
        context = edge["payload"]["context_ref"]
        if propagations is not None and context is not None:
            if context["propagation_ref"] not in propagation_ids:
                raise ContractValidationError("candidate propagation context is unresolved")
            propagation = propagation_by_id[context["propagation_ref"]]
            if propagation["payload"]["region_ref"] != edge["payload"]["region_ref"]:
                raise ContractValidationError("candidate propagation/region scope mismatch")
            for item in context["node_context_refs"]:
                if query_node_context_state(
                    propagation, item["node_ref"], item["context"]
                ) is None:
                    raise ContractValidationError("candidate node/context evidence is unresolved")

    accounted = set()
    for item in result["accounting"]:
        required_accounting = {
            "function_ref", "input_action_occurrence_count", "endpoint_scope",
            "flattened_scope_candidate_count", "normal_scope_candidate_count",
            "mixed_boundary_candidate_count", "unresolved_scopes", "partial",
            "truncated", "propagation_resource_frontiers", "emitted_candidate_ids",
        }
        if set(item) != required_accounting:
            raise ContractValidationError("candidate accounting fields mismatch")
        ids = item["emitted_candidate_ids"]
        if ids != sorted(ids) or any(value not in edge_ids for value in ids):
            raise ContractValidationError("candidate accounting has unresolved or unstable ids")
        if accounted.intersection(ids):
            raise ContractValidationError("candidate id is accounted by multiple functions")
        accounted.update(ids)
        function_edges = [edge for edge in result["edges"] if edge["id"] in ids]
        flattened = sum(edge["payload"]["region_ref"] is not None for edge in function_edges)
        normal = len(function_edges) - flattened
        if str(item["flattened_scope_candidate_count"]) != str(flattened) or str(item["normal_scope_candidate_count"]) != str(normal):
            raise ContractValidationError("candidate accounting counts are not reproducible")
    if accounted != edge_ids:
        raise ContractValidationError("candidate accounting does not cover every emitted edge")


_UNSET = object()


def query_candidates(
    result: Mapping[str, Any],
    *,
    function_ref: Mapping[str, Any] | None = None,
    region_ref: Any = _UNSET,
    context_ref: Any = _UNSET,
    source_endpoint: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Query canonical candidates without reconstructing endpoints."""
    validate_candidate_collection(result)
    values = list(result["edges"])
    if function_ref is not None:
        key = _function_key(function_ref)
        values = [item for item in values if _function_key(item["function_ref"]) == key]
    if region_ref is not _UNSET:
        values = [item for item in values if item["payload"]["region_ref"] == region_ref]
    if context_ref is not _UNSET:
        key = canonical_json(context_ref)
        values = [item for item in values if canonical_json(item["payload"]["context_ref"]) == key]
    if source_endpoint is not None:
        key = canonical_json(source_endpoint)
        values = [item for item in values if canonical_json(item["payload"]["from"]) == key]
    return canonicalize(sorted(values, key=lambda item: item["id"]))


def candidate_accounting(
    result: Mapping[str, Any], *, function_ref: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return the stable machine-readable accounting view."""
    validate_candidate_collection(result)
    if function_ref is None:
        return canonicalize(result["accounting"])
    key = _function_key(function_ref)
    return canonicalize([
        item for item in result["accounting"] if _function_key(item["function_ref"]) == key
    ])


def serialize_candidate_result(result: Mapping[str, Any]) -> str:
    """Validate and return frozen canonical JSON for the P1-T5 wrapper."""
    validate_candidate_collection(result)
    return canonical_json(result)
