#!/usr/bin/env python3
"""Build the function-scoped Semantic Fact IR.

This module deliberately has a narrower responsibility than the S-SEIR
recovery pipeline.  Recovery keeps owning the Yul CFG, MemorySSA, SinkResolver
and low-level effects.  The bridge consumes only lifted Solidity semantics and
lifted Yul semantic overlays plus semantic-level provenance, and emits an
independent Fact CFG, final FactSSA references and typed semantic edges.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict, is_dataclass
import json
import re
from typing import Any


Json = dict[str, Any]
_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*(?:\.(?:slot|offset|address|selector|length))?$")
_VERSION_SUFFIX = re.compile(r"^(.*)_([0-9]+)$")
_LITERAL_WORDS = {
    "true", "false", "unknown", "none", "null", "address", "uint", "uint256",
    "int", "int256", "bytes", "memory", "storage", "calldata", "msg", "block",
    "add", "sub", "mul", "div", "mod",
    "and", "or", "xor", "not", "iszero", "eq", "lt", "gt", "sload", "sstore",
    "mload", "mstore", "keccak256", "calldataload", "calldatasize", "caller", "callvalue",
    "gas", "staticcall", "call", "delegatecall", "returndatasize", "extcodesize", "log0",
    "log1", "log2", "log3", "log4", "revert", "return", "stop",
}
_YUL_EFFECT_KEYS = {"effects", "effect_id", "effect_kind", "path_states", "slot_effect"}


def _dict(value: Any) -> Json:
    if isinstance(value, dict):
        return value
    method = getattr(value, "to_semantic_dict", None)
    if callable(method):
        result = method()
        return result if isinstance(result, dict) else {}
    method = getattr(value, "to_dict", None)
    if callable(method):
        result = method()
        return result if isinstance(result, dict) else {}
    if is_dataclass(value):
        return asdict(value)
    return {}


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items() if item is not None}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    return value


class SemanticFactIRBridge:
    """Materialize high-level semantic inputs into a separate Fact CFG.

    No method here accepts an ``EffectNode`` as a semantic input.  A Yul fact
    must already be an S-SEIR overlay projection with semantic provenance.
    """

    schema = "s-seir-semantic-fact-ir/v1"

    def build_program(
        self,
        functions: list[Any],
        facts_by_function: dict[str, tuple[list[Json], list[Json]]],
        *,
        source: str | None = None,
        result_dir: str | None = None,
    ) -> Json:
        built: list[Json] = []
        for function in functions:
            raw = _dict(function)
            function_id = str(getattr(function, "function_id", "") or raw.get("function_id") or "")
            solidity, yul = facts_by_function.get(function_id, ([], []))
            built.append(self.build_function(function, solidity, yul))
        semantic_nodes = [node for function in built for node in function["semantic_nodes"]]
        return _clean({
            "schema": self.schema,
            "source": source,
            "result_dir": result_dir,
            "model_boundary": {
                "processing_unit": "function",
                "name": "Semantic Fact IR",
                "yul_input": "completed S-SEIR high-level semantic overlays only; control summaries provide Fact CFG structure but never synthetic semantic definitions",
                "recovery_boundary": "Yul recovery CFG, MemorySSA, SinkResolver and low-level effects remain upstream evidence and are not Fact IR inputs",
                "ordering": "Fact CFG partial order plus block-local semantic order",
                "path_policy": "canonical semantic nodes are not path-cloned; path witnesses are derived on demand",
            },
            "function_count": len(built),
            "semantic_node_count": len(semantic_nodes),
            "functions": built,
        })

    def build_function(self, function: Any, solidity_nodes: list[Json], yul_nodes: list[Json]) -> Json:
        raw = _dict(function)
        control = _dict(getattr(function, "control", None)) or _dict(raw.get("control"))
        function_id = str(getattr(function, "function_id", "") or raw.get("function_id") or "<unknown>")
        fact_cfg, raw_to_fact = self._build_fact_cfg(function_id, control)
        diagnostics: list[Json] = []
        semantic_nodes = self._semantic_nodes(function_id, solidity_nodes, yul_nodes, diagnostics)
        for node in semantic_nodes:
            placement = self._place_node(node, raw_to_fact)
            node["placement"] = placement
            if placement.get("status") == "anchored":
                fact_cfg["block_by_id"][placement["anchor_block"]]["semantic_ids"].append(node["semantic_id"])
            else:
                diagnostics.append({
                    "kind": "unanchored_semantic_node",
                    "semantic_id": node["semantic_id"],
                    "reason": placement.get("reason"),
                })

        self._normalize_require_guards(fact_cfg, semantic_nodes, raw_to_fact)
        self._normalize_branch_conditions(fact_cfg, semantic_nodes)
        for block in fact_cfg["blocks"]:
            block["semantic_ids"].sort(key=lambda item: self._node_order(semantic_nodes, item))
        fact_ssa, semantic_edges, boundary_links, ssa_diagnostics = self._build_fact_ssa(
            function, raw, fact_cfg, semantic_nodes, raw_to_fact
        )
        diagnostics.extend(ssa_diagnostics)
        path_witnesses = self._path_witnesses(fact_cfg, semantic_nodes)
        diagnostics.extend(self._validate(function_id, fact_cfg, semantic_nodes, fact_ssa, semantic_edges))
        for block in fact_cfg["blocks"]:
            block.pop("_raw_id", None)
        fact_cfg.pop("block_by_id", None)
        return _clean({
            "function_id": function_id,
            "contract": getattr(function, "contract", None) or raw.get("contract"),
            "function": getattr(function, "function", None) or raw.get("function"),
            "signature": getattr(function, "signature", None) or raw.get("signature"),
            "fact_cfg": fact_cfg,
            "fact_ssa": fact_ssa,
            "semantic_nodes": semantic_nodes,
            "semantic_edges": semantic_edges,
            "boundary_links": boundary_links,
            "path_witnesses": path_witnesses,
            "diagnostics": diagnostics,
        })

    @staticmethod
    def _normalize_require_guards(fact_cfg: Json, nodes: list[Json], raw_to_fact: dict[str, str]) -> None:
        """Give recovered Require nodes their source-level branch meaning.

        S-SEIR correctly anchors a RequireOverlay to its empty-revert effect,
        while preserving the branch that guards it.  The lifter moves the
        SFIR node to that branch and records the original terminal anchor.
        This method rewrites only the CFG-proven direct shape
        ``branch -> revert / continuation``: true means the recovered Require
        condition holds and reaches the continuation; false reaches revert.
        All other CFG shapes are left unchanged.
        """
        by_id = fact_cfg.get("block_by_id") or {}
        edges = fact_cfg.get("edges") or []
        for node in nodes:
            if node.get("kind") != "Require":
                continue
            condition = str(node.get("condition") or "").strip()
            placement = node.get("placement") or {}
            provenance = node.get("semantic_provenance") or {}
            failure_raw = provenance.get("require_failure_cfg_node")
            failure_block = raw_to_fact.get(str(failure_raw)) if failure_raw else None
            guard_block = placement.get("anchor_block")
            if not condition or not guard_block or not failure_block or guard_block not in by_id:
                continue
            outgoing = [edge for edge in edges if edge.get("from") == guard_block]
            failing = [edge for edge in outgoing if edge.get("to") == failure_block]
            continuing = [edge for edge in outgoing if edge.get("to") != failure_block]
            if len(failing) != 1 or len(continuing) != 1:
                continue
            if not SemanticFactIRBridge._is_terminal_terminator((by_id.get(failure_block) or {}).get("terminator") or {}):
                continue
            by_id[guard_block]["terminator"] = {
                "kind": "Require",
                "condition": condition,
                "on_fail": "revert",
                "failure_predicates": (node.get("semantic") or {}).get("failure_predicates") or [],
            }
            continuing[0]["kind"] = "true"
            continuing[0]["guard"] = condition
            failing[0]["kind"] = "false"
            failing[0]["guard"] = f"!({condition})"

    @staticmethod
    def _normalize_branch_conditions(fact_cfg: Json, nodes: list[Json]) -> None:
        """Replace a raw Yul branch spelling with its completed S-SEIR form."""
        by_id = fact_cfg.get("block_by_id") or {}
        edges = fact_cfg.get("edges") or []
        for node in nodes:
            semantic = node.get("semantic") or {}
            if node.get("kind") != "ValueCompute" or semantic.get("context") != "condition":
                continue
            condition = str(node.get("rvalue") or "").strip()
            block_id = (node.get("placement") or {}).get("anchor_block")
            block = by_id.get(str(block_id)) if block_id else None
            if not condition or not block or str((block.get("terminator") or {}).get("kind") or "").lower() != "branch":
                continue
            block["terminator"]["condition"] = condition
            for edge in edges:
                if edge.get("from") != block_id:
                    continue
                edge["guard"] = SemanticFactIRBridge._edge_guard(block["terminator"], str(edge.get("kind") or ""))

    def _build_fact_cfg(self, function_id: str, control: Json) -> tuple[Json, dict[str, str]]:
        raw_blocks = [item for item in control.get("blocks") or [] if isinstance(item, dict) and item.get("block_id")]
        outgoing_kinds: dict[str, list[str]] = {}
        for edge in control.get("edges") or []:
            if not isinstance(edge, dict) or not edge.get("from"):
                continue
            outgoing_kinds.setdefault(str(edge["from"]), []).append(str(edge.get("kind") or "next"))
        raw_to_fact: dict[str, str] = {}
        blocks: list[Json] = []
        for index, raw in enumerate(raw_blocks):
            raw_id = str(raw["block_id"])
            fact_id = f"fcfg:{function_id}:{raw_id}"
            raw_to_fact[raw_id] = fact_id
            attrs = raw.get("attrs") or {}
            kind = str(raw.get("kind") or "unknown")
            engine = "slither" if kind == "solidity" else "sseir_semantic_projection" if kind == "yul" else "control"
            origin = {"engine": engine, "source_block_id": raw_id}
            if attrs.get("slither_node_id") is not None:
                origin["slither_node_id"] = attrs["slither_node_id"]
            if attrs.get("node_id") is not None:
                origin["semantic_source_node_id"] = attrs["node_id"]
            blocks.append({
                "block_id": fact_id,
                "kind": kind,
                "origin": origin,
                "stmt_refs": list(raw.get("stmts") or []),
                "terminator": self._terminator_view(
                    raw.get("terminator") or {}, outgoing_kinds.get(raw_id) or []
                ),
                "semantic_ids": [],
                "predecessors": [],
                "successors": [],
                "_raw_id": raw_id,
                "_sequence": index,
            })
        edges: list[Json] = []
        for index, raw in enumerate(control.get("edges") or []):
            if not isinstance(raw, dict):
                continue
            source = raw_to_fact.get(str(raw.get("from") or ""))
            target = raw_to_fact.get(str(raw.get("to") or ""))
            if not source or not target:
                continue
            source_block = next((item for item in raw_blocks if str(item.get("block_id")) == str(raw.get("from"))), {})
            # Recovery CFGs may retain a structural assembly-exit edge after
            # ``revert`` so that upstream analyses can model the surrounding
            # inline-assembly boundary.  It is not an executable successor in
            # the independent Fact CFG: a reverted call cannot reach either
            # later Yul statements or the Solidity continuation.
            if self._is_terminal_terminator(source_block.get("terminator") or {}):
                continue
            kind = str(raw.get("kind") or "next")
            fact_source = next((item for item in blocks if item.get("_raw_id") == str(source_block.get("block_id") or "")), {})
            edges.append({
                "edge_id": f"e{index + 1}",
                "from": source,
                "to": target,
                "kind": kind,
                "guard": self._edge_guard(fact_source.get("terminator") or {}, kind),
            })
        block_by_id = {str(block["block_id"]): block for block in blocks}
        for edge in edges:
            block_by_id[edge["from"]]["successors"].append(edge["to"])
            block_by_id[edge["to"]]["predecessors"].append(edge["from"])
        analysis = self._graph_analysis(blocks, edges)
        return ({
            "blocks": blocks,
            "edges": edges,
            "entry_blocks": analysis["entry_blocks"],
            "reverse_postorder": analysis["reverse_postorder"],
            "dominance": analysis["dominance"],
            "control_dependencies": analysis["control_dependencies"],
            "block_by_id": block_by_id,
        }, raw_to_fact)

    @staticmethod
    def _terminator_view(value: Json, outgoing_edge_kinds: list[str] | None = None) -> Json:
        node_kind = value.get("node_kind")
        condition = value.get("condition")
        # The Yul CFG marks loop headers structurally and records the real
        # predicate on their ``true:`` control edge.  Its display condition is
        # intentionally prefixed with "for condition", so SFIR must project
        # the edge payload rather than treat that label as an expression.
        if node_kind == "loop-condition":
            true_conditions = {
                kind.split(":", 1)[1].strip()
                for kind in outgoing_edge_kinds or []
                if kind.startswith("true:") and kind.split(":", 1)[1].strip()
            }
            if len(true_conditions) == 1:
                condition = next(iter(true_conditions))
        return _clean({
            "kind": value.get("kind"),
            "condition": condition,
            "text": value.get("text"),
            "node_kind": node_kind,
        })

    @staticmethod
    def _is_terminal_terminator(value: Json) -> bool:
        return str(value.get("kind") or "").strip().lower() in {"return", "revert", "stop"}

    @staticmethod
    def _edge_guard(terminator: Json, edge_kind: str) -> str | None:
        condition = str(terminator.get("condition") or "").strip()
        if not condition:
            return None
        if (
            edge_kind in {"false", "exit", "zero"}
            or edge_kind.startswith("false:")
            or edge_kind.startswith("loop exit:")
        ):
            return f"!({condition})"
        if edge_kind.startswith("case:"):
            return f"({condition}) == {edge_kind.split(':', 1)[1]}"
        if edge_kind in {"true", "body"} or edge_kind.startswith("true:"):
            return condition
        return None

    def _graph_analysis(self, blocks: list[Json], edges: list[Json]) -> Json:
        ids = [str(block["block_id"]) for block in blocks]
        predecessors = {item: set() for item in ids}
        successors = {item: set() for item in ids}
        for edge in edges:
            predecessors[edge["to"]].add(edge["from"])
            successors[edge["from"]].add(edge["to"])
        roots = [item for item in ids if not predecessors[item]] or ids[:1]
        visited: set[str] = set()
        postorder: list[str] = []

        def visit(node: str) -> None:
            if node in visited:
                return
            visited.add(node)
            for child in sorted(successors[node]):
                visit(child)
            postorder.append(node)

        for root in roots:
            visit(root)
        for node in ids:
            visit(node)
        rpo = list(reversed(postorder))
        all_nodes = set(ids)
        dominance = {node: ({node} if node in roots else set(all_nodes)) for node in ids}
        changed = True
        while changed:
            changed = False
            for node in rpo:
                if node in roots:
                    continue
                parent_sets = [dominance[parent] for parent in predecessors[node]]
                shared = set.intersection(*parent_sets) if parent_sets else set()
                updated = {node} | shared
                if updated != dominance[node]:
                    dominance[node] = updated
                    changed = True
        exit_nodes = [node for node in ids if not successors[node]] or ids[-1:]
        postdom = {node: ({node} if node in exit_nodes else set(all_nodes)) for node in ids}
        changed = True
        while changed:
            changed = False
            for node in reversed(rpo):
                if node in exit_nodes:
                    continue
                child_sets = [postdom[child] for child in successors[node]]
                shared = set.intersection(*child_sets) if child_sets else set()
                updated = {node} | shared
                if updated != postdom[node]:
                    postdom[node] = updated
                    changed = True
        ipdom: dict[str, str | None] = {}
        for node in ids:
            candidates = postdom[node] - {node}
            ipdom[node] = next(
                (candidate for candidate in candidates if all(candidate == other or candidate not in postdom[other] for other in candidates)),
                None,
            )
        block_by_id = {str(block["block_id"]): block for block in blocks}
        dependencies: list[Json] = []
        seen: set[tuple[str, str, str]] = set()
        for controller in ids:
            if len(successors[controller]) < 2:
                continue
            condition = str((block_by_id[controller].get("terminator") or {}).get("condition") or "")
            if not condition:
                continue
            stop = ipdom.get(controller)
            for edge in (item for item in edges if item["from"] == controller):
                runner = edge["to"]
                walked: set[str] = set()
                while runner and runner != stop and runner not in walked:
                    walked.add(runner)
                    predicate = edge.get("guard") or condition
                    key = (controller, runner, str(predicate))
                    if key not in seen:
                        seen.add(key)
                        dependencies.append({
                            "controller": controller,
                            "dependent": runner,
                            "edge_id": edge["edge_id"],
                            "predicate": predicate,
                        })
                    runner = ipdom.get(runner)
        return {
            "entry_blocks": roots,
            "reverse_postorder": {node: index for index, node in enumerate(rpo)},
            "dominance": {node: sorted(values) for node, values in dominance.items()},
            "control_dependencies": dependencies,
        }

    def _semantic_nodes(self, function_id: str, solidity_nodes: list[Json], yul_nodes: list[Json], diagnostics: list[Json]) -> list[Json]:
        out: list[Json] = []
        used: set[str] = set()
        for source_lang, inputs in (("solidity", solidity_nodes), ("yul", yul_nodes)):
            for index, raw in enumerate(inputs):
                if not isinstance(raw, dict):
                    continue
                node = deepcopy(raw)
                node["source_lang"] = source_lang
                if source_lang == "yul":
                    node = self._strip_yul_effect_transport(node)
                    node["reads"] = self._canonical_yul_reads(node)
                    if self._has_effect_transport(node):
                        diagnostics.append({"kind": "invalid_yul_effect_transport", "origin_id": raw.get("fact_id") or raw.get("operation_id")})
                        continue
                origin_id = str(node.get("operation_id") or node.get("fact_id") or f"{source_lang}_{index + 1}")
                semantic_id = f"sfir:{function_id}:{origin_id}"
                suffix = 2
                while semantic_id in used:
                    semantic_id = f"sfir:{function_id}:{origin_id}:{suffix}"; suffix += 1
                used.add(semantic_id)
                node["semantic_id"] = semantic_id
                node["origin_id"] = str(node.get("fact_id") or origin_id)
                node.pop("fact_id", None)
                node.pop("anchor_cfg_node", None)
                out.append(node)
        return out

    @staticmethod
    def _canonical_yul_reads(node: Json) -> list[Any]:
        """Restore compiler-visible Solidity reference facets from tokens.

        Some completed expression-normalization overlays retain a lexical read
        list such as ``["accounts", "offset"]`` for ``accounts.offset``.
        The latter is one cross-language binding, not an undeclared Yul local.
        This is a semantic-level normalization based on the completed overlay's
        expression, and does not consult or expose an EffectNode.
        """
        reads = list(node.get("reads") or [])
        expressions = [str(node.get("rvalue") or "")]
        semantic = node.get("semantic") or {}
        if isinstance(semantic, dict):
            expressions.extend(str(semantic.get(key) or "") for key in ("expression", "location"))
            expressions.extend(str(item) for item in semantic.get("raw_args") or [])
        text = " ".join(expressions)
        out: list[Any] = []
        for index, value in enumerate(reads):
            if value in {"slot", "offset", "length", "selector", "address"} and index:
                previous = reads[index - 1]
                candidate = f"{previous}.{value}"
                if isinstance(previous, str) and candidate in text:
                    if out and out[-1] == previous:
                        out[-1] = candidate
                    elif candidate not in out:
                        out.append(candidate)
                    continue
            out.append(value)
        return list(dict.fromkeys(out))

    @classmethod
    def _strip_yul_effect_transport(cls, node: Json) -> Json:
        def strip(value: Any) -> Any:
            if isinstance(value, dict):
                out: Json = {}
                for key, item in value.items():
                    if key in _YUL_EFFECT_KEYS:
                        continue
                    out[key] = strip(item)
                return out
            if isinstance(value, list):
                return [strip(item) for item in value]
            return value

        node = strip(node)
        evidence = dict(node.get("evidence") or {})
        path_instance = evidence.get("path_instance")
        if isinstance(path_instance, dict) and path_instance.get("source") == "sseir_effect_path_state":
            evidence.pop("path_instance", None)
        node["evidence"] = evidence
        node.pop("path_id", None)
        node.pop("path_condition", None)
        return node

    @classmethod
    def _has_effect_transport(cls, value: Any) -> bool:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in _YUL_EFFECT_KEYS:
                    return True
                if cls._has_effect_transport(item):
                    return True
        elif isinstance(value, list):
            return any(cls._has_effect_transport(item) for item in value)
        return False

    def _place_node(self, node: Json, raw_to_fact: dict[str, str]) -> Json:
        provenance = node.get("semantic_provenance") or {}
        raw_anchor = provenance.get("anchor_cfg_node") or provenance.get("anchor_control_block")
        if raw_anchor and str(raw_anchor) in raw_to_fact:
            evidence = self._mapped_evidence(node, raw_to_fact)
            return {"status": "anchored", "anchor_block": raw_to_fact[str(raw_anchor)], "operation_order": self._operation_order(node), "evidence_blocks": evidence}
        candidates = self._mapped_evidence(node, raw_to_fact)
        if len(candidates) == 1:
            return {"status": "anchored", "anchor_block": candidates[0], "operation_order": self._operation_order(node), "evidence_blocks": candidates}
        return {"status": "unanchored", "reason": "no unique semantic-level CFG anchor", "evidence_blocks": candidates}

    @staticmethod
    def _operation_order(node: Json) -> int:
        order = node.get("order") or {}
        return int(order.get("operation_order") or 0) if isinstance(order, dict) else 0

    def _mapped_evidence(self, node: Json, raw_to_fact: dict[str, str]) -> list[str]:
        provenance = node.get("semantic_provenance") or {}
        candidates = list(provenance.get("evidence_cfg_nodes") or []) + list(node.get("cfg_nodes") or [])
        return list(dict.fromkeys(raw_to_fact[str(item)] for item in candidates if str(item) in raw_to_fact))

    def _node_order(self, nodes: list[Json], semantic_id: str) -> tuple[int, str]:
        node = next(item for item in nodes if item["semantic_id"] == semantic_id)
        return self._operation_order(node), semantic_id

    def _build_fact_ssa(self, function: Any, raw: Json, fact_cfg: Json, nodes: list[Json], raw_to_fact: dict[str, str]) -> tuple[Json, list[Json], list[Json], list[Json]]:
        diagnostics: list[Json] = []
        bindings = self._bindings(function, raw)
        block_nodes: dict[str, list[Json]] = defaultdict(list)
        for node in nodes:
            placement = node.get("placement") or {}
            if placement.get("status") == "anchored":
                block_nodes[str(placement["anchor_block"])].append(node)
        for values in block_nodes.values():
            values.sort(key=lambda item: (self._operation_order(item), item["semantic_id"]))
        definitions: list[Json] = []
        generated: dict[str, dict[str, str]] = defaultdict(dict)
        entry_versions: dict[str, dict[str, str]] = defaultdict(dict)
        definition_by_version: dict[str, Json] = {}
        serial = 0

        def define(binding: Json, block_id: str, owner: Json, order: int, kind: str, origin: Json) -> str:
            nonlocal serial
            serial += 1
            version = f"fssa:{binding['binding_id']}:v{serial}"
            record = {
                "version": version,
                "binding_id": binding["binding_id"],
                "base_name": binding["base_name"],
                "facet": binding["facet"],
                "definition_kind": kind,
                "block_id": block_id,
                "operation_order": order,
                "owner_semantic_id": owner.get("semantic_id"),
                "origin": origin,
            }
            definitions.append(record); definition_by_version[version] = record
            generated[block_id][binding["binding_id"]] = version
            return version

        # Parameters and named return variables are explicit entry definitions.
        for block_id in fact_cfg["entry_blocks"]:
            for binding in bindings.values():
                if binding.get("kind") not in {"parameter", "return", "state"}:
                    continue
                entry_versions[block_id][binding["binding_id"]] = define(
                    binding, block_id, {}, -1, "entry", {"engine": "source_declaration"}
                )

        node_write_versions: dict[tuple[str, int], str] = {}
        for block_id, block_semantics in block_nodes.items():
            for node in block_semantics:
                for write_index, value in enumerate(node.get("writes") or []):
                    binding = self._binding_for_value(value, bindings)
                    if not binding:
                        continue
                    node_write_versions[(node["semantic_id"], write_index)] = define(
                        binding, block_id, node, self._operation_order(node), "semantic_definition",
                        {"engine": node.get("origin"), "origin_id": node.get("origin_id")},
                    )

        block_ids = [block["block_id"] for block in fact_cfg["blocks"]]
        predecessors = {block["block_id"]: set(block["predecessors"]) for block in fact_cfg["blocks"]}
        reaching_in: dict[str, dict[str, set[str]]] = {block_id: {} for block_id in block_ids}
        reaching_out: dict[str, dict[str, set[str]]] = {block_id: {} for block_id in block_ids}
        ordered_blocks = sorted(block_ids, key=lambda item: fact_cfg["reverse_postorder"].get(item, len(block_ids)))
        phis: list[Json] = []
        phi_by_block_binding: dict[tuple[str, str], str] = {}

        def ensure_phi(block_id: str, binding_id: str, versions: set[str]) -> str:
            key = (block_id, binding_id)
            existing = phi_by_block_binding.get(key)
            if existing:
                record = definition_by_version[existing]
                record["incoming_versions"] = sorted(versions)
                record["predecessor_blocks"] = sorted(predecessors[block_id])
                return existing
            version = f"fssa:{binding_id}:phi:{len(phis) + 1}"
            record = {
                "version": version, "binding_id": binding_id, "definition_kind": "phi",
                "block_id": block_id, "incoming_versions": sorted(versions),
                "predecessor_blocks": sorted(predecessors[block_id]),
            }
            phis.append(record); definition_by_version[version] = record
            phi_by_block_binding[key] = version
            return version

        changed = True
        while changed:
            changed = False
            for block_id in ordered_blocks:
                incoming: dict[str, set[str]] = defaultdict(set)
                for parent in predecessors[block_id]:
                    for binding_id, versions in reaching_out[parent].items():
                        incoming[binding_id].update(versions)
                outgoing: dict[str, set[str]] = {}
                for binding_id, versions in incoming.items():
                    # This is the SSA fixed point: a merge defines one Phi
                    # version and successors receive that version, rather
                    # than the original alternatives again.
                    selected = ensure_phi(block_id, binding_id, versions) if len(versions) > 1 else next(iter(versions))
                    outgoing[binding_id] = {selected}
                for binding_id, version in generated.get(block_id, {}).items():
                    outgoing[binding_id] = {version}
                incoming_clean = {key: set(value) for key, value in incoming.items()}
                if incoming_clean != reaching_in[block_id] or outgoing != reaching_out[block_id]:
                    reaching_in[block_id] = incoming_clean; reaching_out[block_id] = outgoing; changed = True

        semantic_edges: list[Json] = []
        edge_keys: set[tuple[str, str, str, str]] = set()
        boundary_links: list[Json] = []
        boundary_keys: set[tuple[str, str]] = set()

        def add_edge(source: str | None, target: str, kind: str, version: str) -> None:
            if not source or source == target:
                return
            key = (source, target, kind, version)
            if key not in edge_keys:
                edge_keys.add(key); semantic_edges.append({"from": source, "to": target, "kind": kind, "value_ref": version})

        for block_id in ordered_blocks:
            current = {
                binding_id: (phi_by_block_binding.get((block_id, binding_id)) or next(iter(versions)))
                for binding_id, versions in reaching_in[block_id].items() if versions
            }
            # Entry declarations are available to facts in their own entry
            # block as well as to successor blocks.  They are distinct from
            # semantic writes, which must become visible only after their
            # owning node has executed.
            current.update(entry_versions.get(block_id, {}))
            for node in block_nodes.get(block_id, []):
                read_refs: list[Json] = []
                for value in node.get("reads") or []:
                    binding = self._binding_for_value(value, bindings)
                    if not binding:
                        continue
                    version = current.get(binding["binding_id"])
                    if not version:
                        diagnostics.append({"kind": "unresolved_fact_ssa_read", "semantic_id": node["semantic_id"], "value": value})
                        read_refs.append({"value": value, "binding_id": binding["binding_id"], "status": "unknown"})
                        continue
                    read_refs.append({"value": value, "binding_id": binding["binding_id"], "version": version})
                    definition = definition_by_version.get(version) or {}
                    owner = definition.get("owner_semantic_id")
                    if definition.get("definition_kind") == "phi":
                        for incoming in definition.get("incoming_versions") or []:
                            incoming_owner = (definition_by_version.get(incoming) or {}).get("owner_semantic_id")
                            add_edge(incoming_owner, node["semantic_id"], "data_phi", incoming)
                    else:
                        owner_node = next((item for item in nodes if item["semantic_id"] == owner), None)
                        if owner_node and owner_node.get("source_lang") == "yul" and node.get("source_lang") == "solidity":
                            key = (version, node["semantic_id"])
                            if key not in boundary_keys:
                                boundary_keys.add(key)
                                boundary_links.append({
                                    "kind": "bridge_output", "definition_version": version,
                                    "from": owner,
                                    "to": node["semantic_id"], "value_ref": version,
                                })
                            add_edge(owner, node["semantic_id"], "bridge_output", version)
                        elif (definition.get("origin") or {}).get("engine") == "solidity_atomic_operation" and node.get("source_lang") == "yul":
                            add_edge(owner, node["semantic_id"], "bridge_input", version)
                        else:
                            add_edge(owner, node["semantic_id"], "data", version)
                write_refs: list[Json] = []
                for write_index, value in enumerate(node.get("writes") or []):
                    version = node_write_versions.get((node["semantic_id"], write_index))
                    binding = self._binding_for_value(value, bindings)
                    if not binding or not version:
                        continue
                    current[binding["binding_id"]] = version
                    write_refs.append({"value": value, "binding_id": binding["binding_id"], "version": version})
                node["fact_ssa"] = {"reads": read_refs, "writes": write_refs}

        return ({
            "bindings": sorted(bindings.values(), key=lambda item: item["binding_id"]),
            "definitions": definitions,
            "phis": phis,
            "reaching_definitions_in": {block: {key: sorted(value) for key, value in values.items()} for block, values in reaching_in.items()},
            "reaching_definitions_out": {block: {key: sorted(value) for key, value in values.items()} for block, values in reaching_out.items()},
        }, semantic_edges, boundary_links, diagnostics)

    def _bindings(self, function: Any, raw: Json) -> dict[str, Json]:
        values = getattr(function, "_sseir_variable_bindings", None)
        if not isinstance(values, list):
            values = raw.get("variable_bindings") or []
        out: dict[str, Json] = {}
        for item in values:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            declaration = item.get("declaration_id")
            base = str(item["name"])
            binding_id = f"decl:{declaration}:value" if declaration is not None else f"name:{base}:value"
            out[base] = {
                "binding_id": binding_id, "base_name": base, "facet": "value",
                "kind": item.get("kind"), "type": item.get("type_string"),
                "declaration_id": declaration, "identity_status": "ast_declaration" if declaration is not None else "name_scoped",
            }
            # Inline Yul exposes Solidity storage references through `.slot`
            # and `.offset`.  They are declaration facets, not fresh textual
            # symbols, and therefore receive stable entry definitions too.
            facets: list[tuple[str, str]] = []
            if item.get("kind") == "state":
                facets.extend((("slot", "storage_slot"), ("offset", "storage_offset")))
            if item.get("data_location") in {"calldata", "memory", "storage"}:
                facets.extend((("offset", "data_offset"), ("length", "length")))
            for suffix, facet in facets:
                out[f"{base}.{suffix}"] = {
                    **out[base], "binding_id": binding_id.rsplit(":", 1)[0] + f":{facet}",
                    "facet": facet,
                }
        return out

    def _binding_for_value(self, value: Any, bindings: dict[str, Json]) -> Json | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not _IDENTIFIER.match(text) or text.lower() in _LITERAL_WORDS:
            return None
        if "." in text:
            name, suffix = text.rsplit(".", 1)
            base = self._base_name(name, bindings)
            source = bindings.get(base)
            facet = {
                "slot": "storage_slot", "offset": "storage_offset", "address": "function_address",
                "selector": "function_selector", "length": "length",
            }[suffix]
            declared_facet = bindings.get(f"{base}.{suffix}")
            if declared_facet:
                return declared_facet
            if source:
                return {**source, "binding_id": source["binding_id"].rsplit(":", 1)[0] + f":{facet}", "facet": facet}
            binding = {"binding_id": f"symbol:{base}:{facet}", "base_name": base, "facet": facet, "identity_status": "name_scoped"}
            bindings[f"{base}.{suffix}"] = binding
            return binding
        base = self._base_name(text, bindings)
        if base in bindings:
            return bindings[base]
        binding = {"binding_id": f"symbol:{base}:value", "base_name": base, "facet": "value", "identity_status": "name_scoped"}
        bindings[base] = binding
        return binding

    @staticmethod
    def _base_name(value: str, bindings: dict[str, Json]) -> str:
        if value in bindings:
            return value
        match = _VERSION_SUFFIX.match(value)
        if match and match.group(1) in bindings:
            return match.group(1)
        if match and match.group(1).startswith(("TMP", "REF", "TUPLE")):
            return match.group(1)
        return value

    def _path_witnesses(self, fact_cfg: Json, nodes: list[Json]) -> list[Json]:
        dependencies_by_block: dict[str, list[Json]] = defaultdict(list)
        for item in fact_cfg.get("control_dependencies") or []:
            dependencies_by_block[str(item["dependent"])].append(item)
        out: list[Json] = []
        for node in nodes:
            placement = node.get("placement") or {}
            if placement.get("status") != "anchored":
                continue
            block_id = str(placement["anchor_block"])
            guards = [item.get("predicate") for item in dependencies_by_block.get(block_id, []) if item.get("predicate")]
            explicit = node.get("condition")
            if explicit and explicit not in guards:
                guards.append(explicit)
            if guards:
                out.append({
                    "witness_id": f"pw:{node['semantic_id']}", "semantic_id": node["semantic_id"],
                    "anchor_block": block_id, "guards": list(dict.fromkeys(guards)),
                    "control_edge_ids": [item["edge_id"] for item in dependencies_by_block.get(block_id, [])],
                    "status": "symbolic",
                })
        return out

    def _validate(self, function_id: str, fact_cfg: Json, nodes: list[Json], fact_ssa: Json, edges: list[Json]) -> list[Json]:
        diagnostics: list[Json] = []
        ids = {node["semantic_id"] for node in nodes}
        membership: dict[str, int] = defaultdict(int)
        for block in fact_cfg["blocks"]:
            for semantic_id in block["semantic_ids"]:
                membership[semantic_id] += 1
        for node in nodes:
            if (node.get("placement") or {}).get("status") == "anchored" and membership[node["semantic_id"]] != 1:
                diagnostics.append({"kind": "semantic_node_membership_error", "semantic_id": node["semantic_id"]})
            if node.get("source_lang") == "yul" and self._has_effect_transport(node):
                diagnostics.append({"kind": "yul_effect_transport_leak", "semantic_id": node["semantic_id"]})
        versions = [item["version"] for item in fact_ssa["definitions"]] + [item["version"] for item in fact_ssa["phis"]]
        if len(versions) != len(set(versions)):
            diagnostics.append({"kind": "duplicate_fact_ssa_definition", "function_id": function_id})
        for edge in edges:
            if edge["from"] not in ids or edge["to"] not in ids:
                diagnostics.append({"kind": "invalid_semantic_edge", "edge": edge})
        for block in fact_cfg["blocks"]:
            if self._is_terminal_terminator(block.get("terminator") or {}) and block.get("successors"):
                diagnostics.append({"kind": "terminal_fact_cfg_successor", "block_id": block["block_id"]})
        return diagnostics


def render_semantic_fact_ir_text(payload: Json) -> str:
    lines = [f"Semantic Fact IR {payload.get('schema')}", f"Source: {payload.get('source') or '<unknown>'}"]
    for function in payload.get("functions") or []:
        lines.extend(["", f"Function {function.get('function_id')}"])
        for block in function.get("fact_cfg", {}).get("blocks") or []:
            lines.append(f"  {block['block_id']} [{block.get('kind')}]")
            for semantic_id in block.get("semantic_ids") or []:
                node = next(item for item in function.get("semantic_nodes") or [] if item.get("semantic_id") == semantic_id)
                lines.append(f"    {semantic_id} {node.get('kind')}")
        if function.get("fact_ssa", {}).get("phis"):
            lines.append("  FactPhi:")
            for phi in function["fact_ssa"]["phis"]:
                lines.append(f"    {phi['version']} = phi({', '.join(phi['incoming_versions'])})")
    return "\n".join(lines).rstrip() + "\n"


def write_semantic_fact_ir_json(path: Any, payload: Json) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_semantic_fact_ir_text(path: Any, payload: Json) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_semantic_fact_ir_text(payload), encoding="utf-8")
