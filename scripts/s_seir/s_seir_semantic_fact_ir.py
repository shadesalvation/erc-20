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
        modifier_application_links = self._modifier_application_links(built)
        direct_call_links = self._direct_call_links(built)
        dynamic_call_links = self._dynamic_call_links(built)
        contracts = self._contract_declarations(built)
        base_constructor_links = self._base_constructor_links(built, contracts)
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
                "program_links": "cross-function links reference final function FactCFGs and semantic nodes; they never inline or duplicate callee semantics",
            },
            "function_count": len(built),
            "semantic_node_count": len(semantic_nodes),
            "modifier_application_links": modifier_application_links,
            "direct_call_links": direct_call_links,
            "dynamic_call_links": dynamic_call_links,
            "contracts": contracts,
            "base_constructor_links": base_constructor_links,
            "functions": built,
        })

    @staticmethod
    def _contract_declarations(functions: list[Json]) -> list[Json]:
        """Deduplicate contract declarations archived on Slither functions.

        The pipeline remains function-level.  This table is deliberately only
        a declaration index for inheritance/constructor relations, not an
        invented inter-contract CFG or an inlining of parent constructors.
        """
        declarations: dict[str, Json] = {}
        for function in functions:
            declaration = function.get("contract_declaration") or {}
            name = str(declaration.get("name") or "")
            if not name:
                continue
            existing = declarations.get(name)
            if existing is None:
                declarations[name] = declaration
            elif existing != declaration:
                # Every function of one Slither contract should archive the
                # same contract API result.  Preserve a visible diagnostic
                # record rather than silently choosing a non-deterministic
                # representation if an upstream version violates that.
                existing["declaration_consistency"] = "inconsistent_function_archives"
        return [declarations[name] for name in sorted(declarations)]

    @staticmethod
    def _base_constructor_links(functions: list[Json], contracts: list[Json]) -> list[Json]:
        """Link Slither-resolved base constructor declarations to final SFIR.

        Slither distinguishes a base call in a constructor modifier list from
        one supplied on the contract declaration.  The former has an
        argument-bearing ``InternalCall`` in the constructor CFG; the latter
        is exposed as a contract declaration relation only.  Keep that
        distinction instead of fabricating a normal call edge for header
        syntax that Slither does not expose as an operation.
        """
        by_canonical: dict[str, list[Json]] = {}
        for function in functions:
            canonical = str((function.get("declaration") or {}).get("canonical_name") or "")
            if canonical:
                by_canonical.setdefault(canonical, []).append(function)

        links: list[Json] = []
        for function in functions:
            declaration = function.get("declaration") or {}
            if not declaration.get("is_constructor"):
                continue
            nodes = [item for item in function.get("semantic_nodes") or [] if isinstance(item, dict)]
            for ordinal, target in enumerate(declaration.get("explicit_base_constructor_calls") or [], start=1):
                canonical = str(target.get("canonical_name") or "")
                matches = by_canonical.get(canonical) or []
                call_nodes = [
                    item for item in nodes
                    if item.get("kind") == "BaseConstructorCall"
                    and str((item.get("semantic") or {}).get("function_canonical_name") or "") == canonical
                ]
                link: Json = {
                    "link_id": f"base_constructor:function:{function.get('function_id')}:{ordinal}",
                    "kind": "constructor_explicit_base_call",
                    "caller_function_id": function.get("function_id"),
                    "callee_canonical_name": canonical or None,
                    "call_semantic_ids": [item.get("semantic_id") for item in call_nodes],
                    "arguments_recorded_in_call_semantics": bool(call_nodes),
                }
                if len(matches) == 1:
                    link["resolution"] = "resolved"
                    link["callee_function_id"] = matches[0].get("function_id")
                else:
                    link["resolution"] = "unresolved_base_constructor" if not matches else "ambiguous_base_constructor"
                links.append(link)

        function_contracts = {str(function.get("contract") or "") for function in functions}
        for contract in contracts:
            contract_name = str(contract.get("name") or "")
            if contract_name not in function_contracts:
                continue
            for ordinal, target in enumerate(contract.get("explicit_base_constructor_calls") or [], start=1):
                canonical = str(target.get("canonical_name") or "")
                matches = by_canonical.get(canonical) or []
                link = {
                    "link_id": f"base_constructor:contract:{contract_name}:{ordinal}",
                    "kind": "contract_declaration_base_call",
                    "caller_contract": contract_name,
                    "callee_canonical_name": canonical or None,
                    "call_semantic_ids": [],
                    "arguments_recorded_in_call_semantics": False,
                    "source": "slither_contract.explicit_base_constructor_calls",
                }
                if len(matches) == 1:
                    link["resolution"] = "resolved"
                    link["callee_function_id"] = matches[0].get("function_id")
                else:
                    link["resolution"] = "unresolved_base_constructor" if not matches else "ambiguous_base_constructor"
                links.append(link)
        return links

    @staticmethod
    def _dynamic_call_links(functions: list[Json]) -> list[Json]:
        """Resolve dynamic call candidates only through final SSA def-use.

        Slither's InternalDynamicCall exposes a local function variable and a
        FunctionType, not a candidate set. A candidate is accepted only when
        its exact Function canonical name was preserved on a ValueAssign and
        every reaching definition through ValuePhi is resolved recursively.
        """
        declarations = {
            str((function.get("declaration") or {}).get("canonical_name") or ""): function
            for function in functions
            if (function.get("declaration") or {}).get("canonical_name")
        }
        links: list[Json] = []
        for caller in functions:
            nodes = [node for node in caller.get("semantic_nodes") or [] if isinstance(node, dict)]
            definitions: dict[str, Json] = {}
            for node in nodes:
                lvalue = str(node.get("lvalue") or "")
                if lvalue and node.get("kind") in {"ValueAssign", "ValuePhi"}:
                    definitions[lvalue] = node

            def resolve(value: str, seen: set[str]) -> tuple[set[str], bool]:
                if not value or value in seen:
                    return set(), False
                node = definitions.get(value)
                if not node:
                    return set(), False
                semantic = node.get("semantic") or {}
                if node.get("kind") == "ValueAssign":
                    canonical = str(semantic.get("function_value_canonical_name") or "")
                    return ({canonical}, True) if canonical else (set(), False)
                if node.get("kind") == "ValuePhi":
                    inputs = [str(item) for item in semantic.get("inputs") or node.get("reads") or []]
                    if not inputs:
                        return set(), False
                    candidates: set[str] = set()
                    complete = True
                    for item in inputs:
                        found, item_complete = resolve(item, seen | {value})
                        candidates.update(found)
                        complete = complete and item_complete
                    return candidates, complete and bool(candidates)
                return set(), False

            dynamic_nodes = [node for node in nodes if node.get("kind") == "InternalDynamicCall"]
            for ordinal, node in enumerate(dynamic_nodes, start=1):
                semantic = node.get("semantic") or {}
                function_ssa = str(semantic.get("dynamic_function_ssa") or "")
                candidates, complete = resolve(function_ssa, set())
                resolved = sorted(candidate for candidate in candidates if candidate in declarations)
                unresolved_targets = sorted(candidate for candidate in candidates if candidate not in declarations)
                resolution = "resolved_complete" if complete and not unresolved_targets else (
                    "partially_resolved" if resolved else "unresolved_dynamic_target"
                )
                links.append({
                    "link_id": f"dynamic_call:{caller.get('function_id')}:{ordinal}",
                    "kind": "dynamic_internal_call",
                    "caller_function_id": caller.get("function_id"),
                    "call_semantic_id": node.get("semantic_id"),
                    "function_ssa": function_ssa or None,
                    "function_type": semantic.get("function_type"),
                    "candidate_canonical_names": sorted(candidates),
                    "callee_function_ids": [declarations[item].get("function_id") for item in resolved],
                    "unresolved_candidate_canonical_names": unresolved_targets,
                    "resolution": resolution,
                })
        return links

    @staticmethod
    def _direct_call_links(functions: list[Json]) -> list[Json]:
        """Reference Slither-resolved call declarations without crossing call boundaries.

        ``InternalCall.function`` and ``LibraryCall.function`` are resolved
        Function objects in Slither. ``HighLevelCall.function`` can likewise
        name a declaration, but remains an external message-call boundary.
        Dynamic calls deliberately do not enter this table: Slither exposes a
        local function variable and FunctionType, not a callee candidate set.
        """
        by_canonical: dict[str, list[Json]] = {}
        for function in functions:
            declaration = function.get("declaration") or {}
            canonical = str(declaration.get("canonical_name") or "")
            if canonical:
                by_canonical.setdefault(canonical, []).append(function)

        relation_by_kind = {
            "InternalCall": "internal_call",
            "LibraryCall": "library_call",
            "ExternalCall": "external_call_declaration",
        }
        links: list[Json] = []
        for caller in functions:
            cfg = caller.get("fact_cfg") or {}
            block_by_id = {
                str(block.get("block_id")): block
                for block in cfg.get("blocks") or []
                if isinstance(block, dict) and block.get("block_id")
            }
            positions: dict[str, tuple[int, int, str]] = {}
            reverse_postorder = cfg.get("reverse_postorder") or {}
            for block_id, block in block_by_id.items():
                for index, semantic_id in enumerate(block.get("semantic_ids") or []):
                    positions[str(semantic_id)] = (int(reverse_postorder.get(block_id, 1 << 30)), index, block_id)
            nodes = [
                node for node in caller.get("semantic_nodes") or []
                if isinstance(node, dict) and node.get("kind") in relation_by_kind
            ]
            nodes.sort(key=lambda node: positions.get(str(node.get("semantic_id") or ""), (1 << 30, 1 << 30, "")))
            for ordinal, node in enumerate(nodes, start=1):
                semantic = node.get("semantic") or {}
                canonical = str(semantic.get("function_canonical_name") or "")
                semantic_id = str(node.get("semantic_id") or "")
                position = positions.get(semantic_id)
                link: Json = {
                    "link_id": f"direct_call:{caller.get('function_id')}:{ordinal}",
                    "kind": relation_by_kind[str(node.get("kind"))],
                    "caller_function_id": caller.get("function_id"),
                    "call_semantic_id": semantic_id,
                    "callee_canonical_name": canonical or None,
                    "arguments": list(semantic.get("arguments") or []),
                    "caller_fact_block": position[2] if position else None,
                }
                candidates = by_canonical.get(canonical) or []
                if len(candidates) == 1:
                    link["resolution"] = "resolved"
                    link["callee_function_id"] = candidates[0].get("function_id")
                else:
                    link["resolution"] = "unresolved_callee_declaration" if not candidates else "ambiguous_callee_declaration"
                links.append(link)
        return links

    @staticmethod
    def _modifier_application_links(functions: list[Json]) -> list[Json]:
        """Link final modifier facts without inventing an interprocedural CFG.

        Slither keeps the modifier body as a separate ``Modifier`` function,
        inserts only a ``MODIFIER_CALL`` node in the wrapped function, and
        represents ``_`` inside that separate CFG as ``PLACEHOLDER``.  The
        final SFIR therefore records references among these already-built
        graphs rather than splicing either graph into the other.
        """
        by_canonical: dict[str, list[Json]] = {}
        for function in functions:
            declaration = function.get("declaration") or {}
            canonical = str(declaration.get("canonical_name") or "")
            if canonical:
                by_canonical.setdefault(canonical, []).append(function)

        links: list[Json] = []
        for caller in functions:
            block_by_id = {
                str(block.get("block_id")): block
                for block in ((caller.get("fact_cfg") or {}).get("blocks") or [])
                if isinstance(block, dict) and block.get("block_id")
            }
            positions: dict[str, tuple[int, int, str]] = {}
            reverse_postorder = (caller.get("fact_cfg") or {}).get("reverse_postorder") or {}
            for block_id, block in block_by_id.items():
                for index, semantic_id in enumerate(block.get("semantic_ids") or []):
                    positions[str(semantic_id)] = (int(reverse_postorder.get(block_id, 1 << 30)), index, block_id)
            modifier_nodes = [
                node for node in caller.get("semantic_nodes") or []
                if isinstance(node, dict) and node.get("kind") == "ModifierApply"
            ]
            modifier_nodes.sort(key=lambda node: positions.get(str(node.get("semantic_id") or ""), (1 << 30, 1 << 30, "")))
            for ordinal, node in enumerate(modifier_nodes, start=1):
                semantic = node.get("semantic") or {}
                semantic_id = str(node.get("semantic_id") or "")
                canonical = str(semantic.get("modifier_function_id") or "")
                position = positions.get(semantic_id)
                link: Json = {
                    "link_id": f"modifier_apply:{caller.get('function_id')}:{ordinal}",
                    "kind": "modifier_application",
                    "caller_function_id": caller.get("function_id"),
                    "modifier_apply_semantic_id": semantic_id,
                    "modifier_function_canonical_name": canonical or None,
                    "arguments": list(semantic.get("arguments") or []),
                    "caller_continuation": SemanticFactIRBridge._modifier_caller_continuation(
                        block_by_id, position
                    ),
                }
                candidates = by_canonical.get(canonical) or []
                if len(candidates) != 1:
                    link["resolution"] = "unresolved_modifier_function" if not candidates else "ambiguous_modifier_function"
                    links.append(link)
                    continue
                modifier = candidates[0]
                modifier_cfg = modifier.get("fact_cfg") or {}
                modifier_blocks = [
                    block for block in modifier_cfg.get("blocks") or []
                    if isinstance(block, dict) and block.get("block_id")
                ]
                placeholder_blocks = [
                    str(block["block_id"])
                    for block in modifier_blocks
                    if str((block.get("terminator") or {}).get("kind") or "") == "ModifierPlaceholder"
                ]
                block_lookup = {str(block["block_id"]): block for block in modifier_blocks}
                link.update({
                    "resolution": "resolved",
                    "modifier_function_id": modifier.get("function_id"),
                    "modifier_entry_blocks": list(modifier_cfg.get("entry_blocks") or []),
                    "modifier_placeholder_blocks": placeholder_blocks,
                    "modifier_after_placeholder_blocks": list(dict.fromkeys(
                        successor
                        for block_id in placeholder_blocks
                        for successor in (block_lookup.get(block_id) or {}).get("successors") or []
                    )),
                })
                links.append(link)
        return links

    @staticmethod
    def _modifier_caller_continuation(
        block_by_id: dict[str, Json],
        position: tuple[int, int, str] | None,
    ) -> Json:
        if position is None:
            return {"status": "unresolved_modifier_apply_placement"}
        _order, index, block_id = position
        block = block_by_id.get(block_id) or {}
        semantic_ids = list(block.get("semantic_ids") or [])
        after = semantic_ids[index + 1:]
        return {
            "status": "resolved",
            "fact_block": block_id,
            "following_semantic_ids": after,
            "successor_blocks": list(block.get("successors") or []) if not after else [],
        }

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
            elif placement.get("status") != "nested_definition":
                diagnostics.append({
                    "kind": "unanchored_semantic_node",
                    "semantic_id": node["semantic_id"],
                    "reason": placement.get("reason"),
                })

        self._normalize_require_guards(fact_cfg, semantic_nodes, raw_to_fact)
        self._normalize_branch_conditions(fact_cfg, semantic_nodes)
        self._normalize_selfdestruct_terminators(fact_cfg, semantic_nodes)
        self._contract_trivial_unconditional_blocks(fact_cfg, semantic_nodes)
        # Establish a canonical order inside every original fact block before
        # any CFG fusion.  A later linear fusion appends the successor's
        # already-canonical list, which is the CFG-proven execution order.
        for block in fact_cfg["blocks"]:
            block["semantic_ids"].sort(key=lambda item: self._node_order(semantic_nodes, item))
        self._fuse_linear_semantic_blocks(fact_cfg, semantic_nodes)
        # The final SFIR is the deobfuscation boundary.  Its canonical CFG
        # therefore removes all zero-semantic transport nodes (including
        # source-level entry/exit/merge scaffolding) while retaining their
        # provenance on the redirected semantic control edge.
        self._contract_semantic_transport_blocks(fact_cfg, semantic_nodes)
        self._refresh_fact_cfg_analysis(fact_cfg)
        # Transport contraction can make two semantic blocks adjacent (notably
        # a loop body and its post expression), so run the CFG-proven linear
        # fusion once more before producing FactSSA.
        self._fuse_linear_semantic_blocks(fact_cfg, semantic_nodes)
        self._contract_semantic_transport_blocks(fact_cfg, semantic_nodes)
        self._refresh_fact_cfg_analysis(fact_cfg)
        fact_ssa, semantic_edges, boundary_links, ssa_diagnostics = self._build_fact_ssa(
            function, raw, fact_cfg, semantic_nodes, raw_to_fact
        )
        self._add_call_output_relations(semantic_nodes, semantic_edges, diagnostics)
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
            "declaration": control.get("slither_declaration"),
            "contract_declaration": control.get("slither_contract_declaration"),
            "fact_cfg": fact_cfg,
            "fact_ssa": fact_ssa,
            "semantic_nodes": semantic_nodes,
            "semantic_edges": semantic_edges,
            "boundary_links": boundary_links,
            "path_witnesses": path_witnesses,
            "diagnostics": diagnostics,
        })

    @staticmethod
    def _normalize_selfdestruct_terminators(fact_cfg: Json, nodes: list[Json]) -> None:
        """Give a terminal Slither ``selfdestruct`` its final CFG meaning.

        Slither exposes self-destruction as a SolidityCall on a normal
        expression node; the CFG leaf is otherwise just ``Terminal``.  A
        terminal SFIR block whose *last* semantic operation is the recovered
        SelfDestruct may therefore state the source-level terminal action.
        No block with a successor, or with a later fact, is rewritten.
        """
        by_semantic_id = {
            str(node.get("semantic_id") or ""): node
            for node in nodes if isinstance(node, dict)
        }
        for block in (fact_cfg.get("blocks") or []):
            if (block.get("successors") or []):
                continue
            semantic_ids = list(block.get("semantic_ids") or [])
            if not semantic_ids:
                continue
            last = by_semantic_id.get(str(semantic_ids[-1]))
            if not last or last.get("kind") != "SelfDestruct":
                continue
            terminator = block.get("terminator") or {}
            if str(terminator.get("kind") or "") not in {"Terminal", "Stop"}:
                continue
            block["terminator"] = {
                "kind": "SelfDestruct",
                "semantic_id": last.get("semantic_id"),
                "function": (last.get("semantic") or {}).get("function"),
            }

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
            context = str(semantic.get("context") or "")
            if node.get("kind") not in {"ValueCompute", "BranchCondition"} or context not in {"condition", "switch"}:
                continue
            condition = str(node.get("rvalue") or "").strip()
            block_id = (node.get("placement") or {}).get("anchor_block")
            block = by_id.get(str(block_id)) if block_id else None
            if not condition or not block or str((block.get("terminator") or {}).get("kind") or "").lower() != "branch":
                continue
            if context == "switch":
                switch_edges = [
                    item for item in semantic.get("switch_edges") or []
                    if isinstance(item, dict) and item.get("kind") and item.get("guard")
                ]
                if not switch_edges:
                    continue
                raw_discriminant = str((block.get("terminator") or {}).get("condition") or "").strip()
                if raw_discriminant.startswith("switch "):
                    raw_discriminant = raw_discriminant.removeprefix("switch ").strip()
                replacements = SemanticFactIRBridge._switch_edge_replacements(
                    edges, str(block_id), raw_discriminant, switch_edges
                )
                if not replacements:
                    continue
                block["terminator"] = {
                    "kind": "Switch",
                    "condition": condition,
                    "text": f"switch ({condition})",
                    "node_kind": "switch",
                }
                for edge in edges:
                    if edge.get("from") != block_id:
                        continue
                    replacement = replacements.get(str(edge.get("edge_id") or ""))
                    if replacement is None:
                        continue
                    edge["kind"] = str(replacement.get("kind") or edge["kind"])
                    edge["guard"] = str(replacement["guard"])
                continue
            block["terminator"]["condition"] = condition
            block["terminator"]["text"] = f"if ({condition})"
            for edge in edges:
                if edge.get("from") != block_id:
                    continue
                raw_kind = str(edge.get("kind") or "")
                edge["guard"] = SemanticFactIRBridge._edge_guard(block["terminator"], raw_kind)
                edge["kind"] = SemanticFactIRBridge._canonical_branch_edge_kind(raw_kind)

    @staticmethod
    def _canonical_branch_edge_kind(edge_kind: str) -> str:
        """Keep branch polarity while removing upstream Yul text from SFIR."""
        if edge_kind in {"true", "body"} or edge_kind.startswith("true:"):
            return "true"
        if (
            edge_kind in {"false", "exit", "zero"}
            or edge_kind.startswith("false:")
            or edge_kind.startswith("loop exit:")
        ):
            return "false"
        return edge_kind

    @staticmethod
    def _switch_edge_replacements(
        edges: list[Json],
        block_id: str,
        raw_discriminant: str,
        semantic_edges: list[Json],
    ) -> dict[str, Json]:
        """Map CFG cases to completed cases by value, never by edge order.

        The raw CFG label is recovery evidence used only while constructing
        Fact CFG.  It is compared structurally against the original switch
        discriminant, then discarded; neither S-SEIR's completed overlay nor
        final SFIR needs to retain that lower-level spelling.
        """
        cases = {
            str(item.get("case_value") or ""): item
            for item in semantic_edges
            if str(item.get("kind") or "").startswith("case:") and item.get("case_value") is not None
        }
        defaults = [item for item in semantic_edges if item.get("kind") == "default"]
        replacements: dict[str, Json] = {}
        for edge in (item for item in edges if item.get("from") == block_id):
            edge_kind = str(edge.get("kind") or "")
            if edge_kind.startswith("case:"):
                value = SemanticFactIRBridge._raw_switch_case_value(
                    edge_kind.removeprefix("case:").strip(), raw_discriminant
                )
                replacement = cases.get(value or "")
                if replacement is not None:
                    replacements[str(edge.get("edge_id") or "")] = replacement
            elif edge_kind.startswith("default:") or edge_kind == "default":
                if len(defaults) == 1:
                    replacements[str(edge.get("edge_id") or "")] = defaults[0]
        return replacements

    @staticmethod
    def _raw_switch_case_value(case_expression: str, discriminant: str) -> str | None:
        text = str(case_expression).strip()
        depth = 0
        for index in range(len(text) - 1):
            char = text[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == "=" and text[index + 1] == "=" and depth == 0:
                left, right = text[:index].strip(), text[index + 2:].strip()
                if SemanticFactIRBridge._compact_control_text(left) == SemanticFactIRBridge._compact_control_text(discriminant) and right:
                    return right
                return None
        return None

    @staticmethod
    def _compact_control_text(value: Any) -> str:
        return re.sub(r"\s+", "", str(value or ""))

    def _refresh_fact_cfg_analysis(self, fact_cfg: Json) -> None:
        """Recompute graph facts after completed predicates replace raw guards.

        The CFG shape is unchanged, but control-dependency predicates and path
        witnesses are semantic output.  They must be derived from the final
        completed terminators, not from the pre-projection Yul spelling.
        """
        analysis = self._graph_analysis(fact_cfg.get("blocks") or [], fact_cfg.get("edges") or [])
        fact_cfg["entry_blocks"] = analysis["entry_blocks"]
        fact_cfg["reverse_postorder"] = analysis["reverse_postorder"]
        fact_cfg["dominance"] = analysis["dominance"]
        fact_cfg["control_dependencies"] = analysis["control_dependencies"]

    @staticmethod
    def _rebuild_fact_cfg_adjacency(fact_cfg: Json) -> None:
        """Rebuild derived block adjacency after a semantics-preserving rewrite."""
        by_id = {str(block["block_id"]): block for block in fact_cfg.get("blocks") or []}
        for block in by_id.values():
            block["predecessors"] = []
            block["successors"] = []
        for edge in fact_cfg.get("edges") or []:
            source, target = str(edge.get("from") or ""), str(edge.get("to") or "")
            if source not in by_id or target not in by_id:
                continue
            by_id[source]["successors"].append(target)
            by_id[target]["predecessors"].append(source)
        fact_cfg["block_by_id"] = by_id

    @staticmethod
    def _is_unconditional_linear_edge(edge: Json) -> bool:
        """True only for a guard-free edge with no control-flow meaning."""
        return (
            str(edge.get("kind") or "") in {"next", "fallthrough", "join"}
            and not str(edge.get("guard") or "").strip()
        )

    def _contract_trivial_unconditional_blocks(self, fact_cfg: Json, nodes: list[Json]) -> None:
        """Remove inert Yul relay blocks before SFIR analyses consume the CFG.

        ControlBuilder intentionally retains a node for every local Yul CFG
        statement, including memory preparation statements whose only role was
        consumed while recovering a high-level storage access.  Such a node is
        not part of the final semantic language.  Contract only a proven
        one-predecessor/one-successor relay with guard-free incident edges.
        Branches, joins, loop structure, boundaries, terminals, and all
        semantic/provenance anchors remain explicit.
        """
        blocks = fact_cfg.get("blocks") or []
        edges = fact_cfg.get("edges") or []
        protected = set(fact_cfg.get("entry_blocks") or [])
        for node in nodes:
            placement = node.get("placement") or {}
            if placement.get("status") != "anchored":
                continue
            protected.add(str(placement.get("anchor_block") or ""))
            protected.update(str(item) for item in placement.get("evidence_blocks") or [])

        removed: list[Json] = []
        while True:
            self._rebuild_fact_cfg_adjacency(fact_cfg)
            by_id = fact_cfg["block_by_id"]
            candidate: Json | None = None
            incoming: Json | None = None
            outgoing: Json | None = None
            for block in fact_cfg.get("blocks") or []:
                block_id = str(block.get("block_id") or "")
                term = block.get("terminator") or {}
                node_kind = str(term.get("node_kind") or "")
                predecessors = block.get("predecessors") or []
                successors = block.get("successors") or []
                if (
                    not block_id
                    or block_id in protected
                    or block.get("semantic_ids")
                    or str(block.get("kind") or "") != "yul"
                    or node_kind in {"entry", "exit", "merge", "condition", "switch", "loop-condition", "loop-merge", "loop-post"}
                    or str(term.get("kind") or "") not in {"YulNode", "Fallthrough"}
                    or len(predecessors) != 1
                    or len(successors) != 1
                    or predecessors[0] == successors[0]
                ):
                    continue
                candidates_in = [edge for edge in fact_cfg.get("edges") or [] if edge.get("to") == block_id]
                candidates_out = [edge for edge in fact_cfg.get("edges") or [] if edge.get("from") == block_id]
                if len(candidates_in) != 1 or len(candidates_out) != 1:
                    continue
                if not self._is_unconditional_linear_edge(candidates_in[0]) or not self._is_unconditional_linear_edge(candidates_out[0]):
                    continue
                candidate, incoming, outgoing = block, candidates_in[0], candidates_out[0]
                break
            if candidate is None or incoming is None or outgoing is None:
                break

            block_id = str(candidate["block_id"])
            replacement = dict(incoming)
            replacement["to"] = outgoing["to"]
            prior_blocks = list(replacement.pop("contracted_blocks", []) or [])
            prior_edges = list(replacement.pop("contracted_edge_ids", []) or [])
            replacement["contracted_blocks"] = prior_blocks + [block_id]
            replacement["contracted_edge_ids"] = prior_edges + [str(incoming.get("edge_id")), str(outgoing.get("edge_id"))]
            # Preserve the eliminated relay in the same provenance channel
            # consumed by the final semantic transport quotient below.
            replacement["collapsed_transport"] = self._dedupe_records(
                list(replacement.get("collapsed_transport") or []) + [self._transport_summary(candidate)]
            )
            replacement["normalization"] = "trivial_unconditional_block_elimination"
            fact_cfg["edges"] = [
                edge for edge in fact_cfg.get("edges") or []
                if edge is not incoming and edge is not outgoing
            ] + [replacement]
            fact_cfg["blocks"] = [block for block in fact_cfg.get("blocks") or [] if block is not candidate]
            removed.append({
                "block_id": block_id,
                "source_block_id": (candidate.get("origin") or {}).get("source_block_id"),
                "predecessor": incoming.get("from"),
                "successor": outgoing.get("to"),
            })

        self._rebuild_fact_cfg_adjacency(fact_cfg)
        if removed:
            fact_cfg["normalization"] = {
                "kind": "trivial_unconditional_block_elimination",
                "removed_blocks": removed,
                "policy": "canonical_sfir_cfg_contracts_only_guard_free_yul_relay_blocks",
            }

    @staticmethod
    def _block_source_ids(block: Json) -> list[str]:
        """Return source-block provenance, including earlier fused blocks."""
        known = list(block.get("fused_source_blocks") or [])
        source = str((block.get("origin") or {}).get("source_block_id") or "")
        if source and source not in known:
            known.insert(0, source)
        return list(dict.fromkeys(item for item in known if item))

    @staticmethod
    def _linear_semantic_terminator(block: Json) -> bool:
        """Whether a block may be folded into a straight-line semantic block.

        The rule intentionally admits only statement/fallthrough terminators.
        It therefore cannot absorb a branch, loop header, join marker, entry
        or exit boundary, return, revert, or stop into a predecessor.
        """
        term = block.get("terminator") or {}
        return (
            str(term.get("kind") or "") in {"YulNode", "Fallthrough"}
            and str(term.get("node_kind") or "") not in {
                "entry", "exit", "merge", "condition", "switch",
                "loop-condition", "loop-merge", "loop-post", "terminal",
            }
        )

    def _fuse_linear_semantic_blocks(self, fact_cfg: Json, nodes: list[Json]) -> None:
        """Fuse only CFG-proven straight-line, same-language semantic blocks.

        A source block ``P`` and successor ``S`` are fused only when ``P`` has
        exactly that successor, ``S`` has exactly that predecessor, and their
        connector is guard-free.  Both blocks must contain final high-level
        semantics and may not be a structural/control boundary.  Thus the
        rewrite changes representation granularity only: every execution that
        reached ``P`` still evaluates P's semantic operations followed by S's.

        The surviving block retains all source-block identities in provenance;
        semantic placement evidence is re-anchored to the surviving canonical
        Fact CFG block.  Raw S-SEIR/Slither provenance remains on the semantic
        node itself, so no lower-level fact is reintroduced downstream.
        """
        node_by_id = {str(node.get("semantic_id")): node for node in nodes}
        fused: list[Json] = []

        while True:
            self._rebuild_fact_cfg_adjacency(fact_cfg)
            by_id = fact_cfg["block_by_id"]
            candidate: tuple[Json, Json, Json] | None = None
            for predecessor in fact_cfg.get("blocks") or []:
                predecessor_id = str(predecessor.get("block_id") or "")
                successors = predecessor.get("successors") or []
                if (
                    not predecessor_id
                    or predecessor_id in set(fact_cfg.get("entry_blocks") or [])
                    or len(successors) != 1
                    or not predecessor.get("semantic_ids")
                    or str(predecessor.get("kind") or "") not in {"solidity", "yul"}
                    or not self._linear_semantic_terminator(predecessor)
                ):
                    continue
                successor = by_id.get(str(successors[0]))
                if not successor:
                    continue
                successor_id = str(successor.get("block_id") or "")
                if (
                    len(successor.get("predecessors") or []) != 1
                    or successor.get("predecessors", [None])[0] != predecessor_id
                    or not successor.get("semantic_ids")
                    or str(successor.get("kind") or "") not in {"solidity", "yul"}
                    or str(predecessor.get("kind") or "") != str(successor.get("kind") or "")
                    or not self._linear_semantic_terminator(successor)
                ):
                    continue
                connector = [
                    edge for edge in fact_cfg.get("edges") or []
                    if edge.get("from") == predecessor_id and edge.get("to") == successor_id
                ]
                if len(connector) != 1 or not self._is_unconditional_linear_edge(connector[0]):
                    continue
                candidate = predecessor, successor, connector[0]
                break

            if candidate is None:
                break

            predecessor, successor, connector = candidate
            predecessor_id = str(predecessor["block_id"])
            successor_id = str(successor["block_id"])
            successor_sources = self._block_source_ids(successor)
            predecessor_sources = self._block_source_ids(predecessor)

            # The concatenation is not source-order inference: it is justified
            # by the unique, guard-free P -> S CFG edge above.
            moved_ids = list(successor.get("semantic_ids") or [])
            predecessor["semantic_ids"] = list(predecessor.get("semantic_ids") or []) + moved_ids
            predecessor["stmt_refs"] = list(dict.fromkeys(
                list(predecessor.get("stmt_refs") or []) + list(successor.get("stmt_refs") or [])
            ))
            predecessor["terminator"] = deepcopy(successor.get("terminator") or {})
            predecessor["fused_source_blocks"] = list(dict.fromkeys(predecessor_sources + successor_sources))
            predecessor["linear_fusion"] = {
                "kind": "linear_semantic_block_fusion",
                "fused_block_ids": list(dict.fromkeys(
                    list((predecessor.get("linear_fusion") or {}).get("fused_block_ids") or [predecessor_id]) + [successor_id]
                )),
                "fused_source_blocks": predecessor["fused_source_blocks"],
            }

            for semantic_id in moved_ids:
                node = node_by_id.get(str(semantic_id))
                if not node:
                    continue
                placement = node.get("placement") or {}
                placement["anchor_block"] = predecessor_id
                evidence = [predecessor_id if item == successor_id else item for item in placement.get("evidence_blocks") or []]
                placement["evidence_blocks"] = list(dict.fromkeys(evidence))
                placement["fused_from_source_blocks"] = list(dict.fromkeys(
                    list(placement.get("fused_from_source_blocks") or []) + successor_sources
                ))
                node["placement"] = placement

            # Redirect S's only possible continuation through P.  The outgoing
            # edge keeps its original identity because it still represents the
            # same source-level control transfer; P->S becomes local sequence.
            redirected = []
            for edge in fact_cfg.get("edges") or []:
                if edge is connector:
                    continue
                if edge.get("from") == successor_id:
                    edge = dict(edge)
                    edge["from"] = predecessor_id
                    edge["normalization"] = "linear_semantic_block_fusion"
                    edge["fused_block_ids"] = [predecessor_id, successor_id]
                redirected.append(edge)
            fact_cfg["edges"] = redirected
            fact_cfg["blocks"] = [block for block in fact_cfg.get("blocks") or [] if block is not successor]
            fused.append({
                "survivor_block_id": predecessor_id,
                "removed_block_id": successor_id,
                "survivor_source_blocks": predecessor["fused_source_blocks"],
                "semantic_ids": moved_ids,
            })

        self._rebuild_fact_cfg_adjacency(fact_cfg)
        if fused:
            normalization = dict(fact_cfg.get("normalization") or {})
            normalization["linear_semantic_block_fusion"] = {
                "fused_blocks": fused,
                "policy": "canonical_sfir_cfg_fuses_only_unique_guard_free_same_language_semantic_blocks",
            }
            fact_cfg["normalization"] = normalization

    @staticmethod
    def _transport_role(block: Json) -> str:
        """Classify a semantically inert CFG node without exposing its text."""
        term = block.get("terminator") or {}
        node_kind = str(term.get("node_kind") or "")
        if node_kind:
            return node_kind
        if str(block.get("kind") or "") == "solidity" and not block.get("predecessors"):
            return "solidity-entry"
        return "fallthrough"

    @staticmethod
    def _semantic_transport_block(block: Json) -> bool:
        """True iff ``block`` has no final SFIR operation or control action.

        A source Yul statement may still appear in upstream provenance, but a
        final Fact CFG block with no semantic IDs is intentionally behaviour-
        free at this layer.  Branch/terminal terminators are excluded even if
        their body happens to have no semantic node.
        """
        term = block.get("terminator") or {}
        return (
            not block.get("semantic_ids")
            and str(term.get("kind") or "") in {"YulNode", "Fallthrough"}
        )

    @staticmethod
    def _transport_summary(block: Json) -> Json:
        term = block.get("terminator") or {}
        origin = block.get("origin") or {}
        return _clean({
            "block_id": block.get("block_id"),
            "source_block_id": origin.get("source_block_id"),
            "source_lang": block.get("kind"),
            "role": SemanticFactIRBridge._transport_role(block),
        })

    @staticmethod
    def _dedupe_records(records: list[Json]) -> list[Json]:
        """Stable deduplication for provenance records with nested fields."""
        out: list[Json] = []
        seen: set[str] = set()
        for record in records:
            if not isinstance(record, dict):
                continue
            key = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if key not in seen:
                seen.add(key)
                out.append(record)
        return out

    @staticmethod
    def _boundary_transition(source: Json | None, target: Json | None) -> Json | None:
        """Record a language boundary as provenance rather than an empty block."""
        if not source or not target:
            return None
        from_lang = str(source.get("kind") or "")
        to_lang = str(target.get("kind") or "")
        if not from_lang or not to_lang or from_lang == to_lang:
            return None
        return _clean({
            "from_lang": from_lang,
            "to_lang": to_lang,
            "from_source_block": (source.get("origin") or {}).get("source_block_id"),
            "to_source_block": (target.get("origin") or {}).get("source_block_id"),
        })

    def _reanchor_collapsed_transport_evidence(
        self,
        nodes: list[Json],
        removed_block_id: str,
        successor_id: str | None,
        summary: Json,
    ) -> None:
        """Keep evidence valid after its structural carrier is erased."""
        for node in nodes:
            placement = node.get("placement") or {}
            evidence = list(placement.get("evidence_blocks") or [])
            if removed_block_id not in evidence:
                continue
            replacement = [
                successor_id if item == removed_block_id and successor_id else item
                for item in evidence
                if item != removed_block_id or successor_id
            ]
            placement["evidence_blocks"] = list(dict.fromkeys(replacement))
            placement["collapsed_transport_evidence"] = self._dedupe_records(
                list(placement.get("collapsed_transport_evidence") or []) + [summary]
            )
            node["placement"] = placement

    def _transport_edge(
        self,
        incoming: Json,
        outgoing: Json,
        candidate: Json,
        source: Json | None,
        target: Json,
    ) -> Json:
        """Redirect one incoming path across an inert block without losing it."""
        replacement = dict(incoming)
        replacement["to"] = target["block_id"]
        summary = self._transport_summary(candidate)
        transports = (
            list(incoming.get("collapsed_transport") or [])
            + list(outgoing.get("collapsed_transport") or [])
            + [summary]
        )
        replacement["collapsed_transport"] = self._dedupe_records(transports)
        transitions = [
            *list(incoming.get("boundary_transitions") or []),
            *list(outgoing.get("boundary_transitions") or []),
            self._boundary_transition(source, candidate),
            self._boundary_transition(candidate, target),
        ]
        replacement["boundary_transitions"] = self._dedupe_records(
            [item for item in transitions if item]
        )
        prior_edges = list(incoming.get("contracted_edge_ids") or [])
        replacement["contracted_edge_ids"] = list(dict.fromkeys(
            prior_edges + [str(outgoing.get("edge_id") or "")]
        ))
        replacement["normalization"] = "semantic_transport_block_elimination"
        return replacement

    def _contract_semantic_transport_blocks(self, fact_cfg: Json, nodes: list[Json]) -> None:
        """Erase all behaviour-free transport blocks from the canonical SFIR CFG.

        This is a stuttering-equivalence reduction.  For an inert block B with
        the sole unconditional transition B -> T, every P -> B -> T path is
        replaced by P -> T carrying the original edge's guard and kind.  B may
        have any number of predecessors, so a genuine empty join is reduced as
        well; FactSSA is rebuilt afterwards, relocating any resulting Phi to
        T.  Entry and exit scaffolding are treated as the zero-predecessor and
        zero-successor forms of the same rewrite.

        The eliminated source block is retained only as compact provenance on
        the edge/entry/exit it crossed.  No second CFG is emitted: the reduced
        Fact CFG is the sole downstream deobfuscation representation.
        """
        removed: list[Json] = []
        while True:
            self._rebuild_fact_cfg_adjacency(fact_cfg)
            by_id = fact_cfg["block_by_id"]
            candidate: Json | None = None
            incoming: list[Json] = []
            outgoing: Json | None = None
            implicit_exit = False

            for block in fact_cfg.get("blocks") or []:
                block_id = str(block.get("block_id") or "")
                if not block_id or not self._semantic_transport_block(block):
                    continue
                inputs = [edge for edge in fact_cfg.get("edges") or [] if edge.get("to") == block_id]
                outputs = [edge for edge in fact_cfg.get("edges") or [] if edge.get("from") == block_id]
                if len(outputs) == 1 and self._is_unconditional_linear_edge(outputs[0]):
                    target_id = str(outputs[0].get("to") or "")
                    if target_id and target_id != block_id and target_id in by_id:
                        candidate, incoming, outgoing = block, inputs, outputs[0]
                        break
                # Synthetic Yul exits have no operation and no successor.  We
                # may erase one only if every incoming source has no other
                # successor, so no branch guard disappears from the final CFG.
                if (
                    not outputs
                    and (self._transport_role(block) == "exit" or block.get("implicit_function_exit"))
                    and all(len(by_id.get(str(edge.get("from") or ""), {}).get("successors") or []) == 1 for edge in inputs)
                ):
                    candidate, incoming, outgoing, implicit_exit = block, inputs, None, True
                    break

            if candidate is None:
                break

            candidate_id = str(candidate["block_id"])
            summary = self._transport_summary(candidate)
            if implicit_exit:
                for edge in incoming:
                    source = by_id.get(str(edge.get("from") or ""))
                    if source:
                        source["implicit_function_exit"] = self._dedupe_records(
                            list(source.get("implicit_function_exit") or []) + [summary]
                        )
                    self._reanchor_collapsed_transport_evidence(nodes, candidate_id, None, summary)
                fact_cfg["edges"] = [
                    edge for edge in fact_cfg.get("edges") or [] if edge not in incoming
                ]
                exit_target = None
            else:
                assert outgoing is not None
                target = by_id[str(outgoing["to"])]
                replacements = [
                    self._transport_edge(
                        edge, outgoing, candidate, by_id.get(str(edge.get("from") or "")), target
                    )
                    for edge in incoming
                ]
                # A root transport node becomes provenance attached to its new
                # root.  This handles Slither entry -> Yul entry chains without
                # retaining either as visible goto-only labels.
                if not incoming:
                    target["collapsed_entry_transport"] = self._dedupe_records(
                        list(candidate.get("collapsed_entry_transport") or [])
                        + list(outgoing.get("collapsed_transport") or [])
                        + [summary]
                    )
                    transitions = list(candidate.get("entry_boundary_transitions") or []) + list(outgoing.get("boundary_transitions") or [])
                    boundary = self._boundary_transition(candidate, target)
                    if boundary:
                        transitions.append(boundary)
                    target["entry_boundary_transitions"] = self._dedupe_records(transitions)
                self._reanchor_collapsed_transport_evidence(nodes, candidate_id, str(target["block_id"]), summary)
                fact_cfg["edges"] = [
                    edge for edge in fact_cfg.get("edges") or [] if edge not in incoming and edge is not outgoing
                ] + replacements
                exit_target = str(target["block_id"])
            fact_cfg["blocks"] = [block for block in fact_cfg.get("blocks") or [] if block is not candidate]
            removed.append({
                "block_id": candidate_id,
                "source_block_id": (candidate.get("origin") or {}).get("source_block_id"),
                "role": summary.get("role"),
                "mode": "implicit_exit" if implicit_exit else "redirect",
                "successor": exit_target,
                "predecessor_count": len(incoming),
            })

        self._rebuild_fact_cfg_adjacency(fact_cfg)
        if removed:
            normalization = dict(fact_cfg.get("normalization") or {})
            normalization["semantic_transport_block_elimination"] = {
                "removed_blocks": removed,
                "policy": "canonical_sfir_cfg_erases_zero_semantic_unconditional_transport_blocks",
            }
            fact_cfg["normalization"] = normalization

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
            "node_kind": node_kind,
            "try_id": value.get("try_id"),
            "catch_role": value.get("catch_role"),
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
                semantic = node.get("semantic") or {}
                if isinstance(semantic, dict) and semantic.get("model_status") == "unmodeled":
                    # Preserve the node in the canonical SFIR rather than
                    # dropping a future Slither result.  The diagnostic makes
                    # its recovery boundary explicit to a deobfuscator.
                    diagnostics.append({
                        "kind": "unmodeled_slither_semantic",
                        "semantic_id": semantic_id,
                        "source_lang": source_lang,
                        "slithir_kind": semantic.get("slithir_kind")
                        or ((node.get("evidence") or {}).get("atomic_operation") or {}).get("slithir_kind"),
                        "reason": semantic.get("unmodeled_reason"),
                    })
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
        if node.get("kind") == "LocalFunctionDefinition":
            # Local Yul functions own the nested semantic CFG carried by the
            # definition itself.  They are declarations in the enclosing
            # function, not executable nodes on its Fact CFG.
            return {"status": "nested_definition", "reason": "local_function_owns_semantic_cfg"}
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
        # State facts use source-level locations such as
        # ``balances[owner]`` rather than identifier-shaped SSA temporaries.
        # Establish their bindings before the fixed point so a state read has
        # a symbolic entry version and a later state write can define the
        # exact same location.  This is deliberately based only on an already
        # recovered SFIR location, never on a physical Yul slot expression.
        self._ensure_storage_location_bindings(nodes, bindings)
        # ``semantic_ids`` is the canonical block-local order.  In particular,
        # linear fusion has already concatenated P then S under a CFG proof;
        # sorting by source offsets or generated IDs here would destroy that
        # execution order and could reverse a write/read def-use pair.
        node_by_id = {str(node.get("semantic_id")): node for node in nodes}
        block_nodes: dict[str, list[Json]] = {}
        for block in fact_cfg.get("blocks") or []:
            block_id = str(block.get("block_id") or "")
            block_nodes[block_id] = [
                node_by_id[semantic_id]
                for semantic_id in block.get("semantic_ids") or []
                if semantic_id in node_by_id
            ]
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
                if binding.get("kind") not in {"parameter", "return", "state"} and binding.get("facet") != "storage_location":
                    continue
                entry_versions[block_id][binding["binding_id"]] = define(
                    binding, block_id, {}, -1, "entry", {"engine": "source_declaration"}
                )

        node_write_versions: dict[tuple[str, int], str] = {}
        for block_id, block_semantics in block_nodes.items():
            for node in block_semantics:
                for write_index, value in enumerate(node.get("writes") or []):
                    binding = self._binding_for_node_value(node, value, bindings)
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
                    binding = self._binding_for_node_value(node, value, bindings)
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
                    binding = self._binding_for_node_value(node, value, bindings)
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

    @staticmethod
    def _add_call_output_relations(nodes: list[Json], edges: list[Json], diagnostics: list[Json]) -> None:
        """Connect S-SEIR's proved output channels without inventing SSA.

        A ``CallOutputRead`` receives a result through the EVM call-output
        memory channel, rather than through a local variable written by the
        call status operation.  The completed overlay identity is the proof
        of this relation.  Keep it as a semantic edge, not a synthetic
        FactSSA version/read.
        """
        call_producers: dict[str, str] = {}
        precompile_producers: dict[str, str] = {}
        for node in nodes:
            source = ((node.get("semantic_provenance") or {}).get("semantic_source") or {})
            overlay_id = str(source.get("overlay_id") or "") if isinstance(source, dict) else ""
            if overlay_id and node.get("kind") in {"ExternalCall", "LowLevelCall", "PrecompileCall"}:
                target = str(node.get("semantic_id") or "")
                call_producers[overlay_id] = target
                if node.get("kind") == "PrecompileCall":
                    precompile_producers[overlay_id] = target
        existing = {
            (str(edge.get("from") or ""), str(edge.get("to") or ""), str(edge.get("kind") or ""))
            for edge in edges if isinstance(edge, dict)
        }
        for node in nodes:
            semantic = node.get("semantic") or {}
            if not isinstance(semantic, dict):
                continue
            source_overlay = str(semantic.get("source_call_overlay") or "")
            relation_kind = "call_output"
            producers = call_producers
            diagnostic_kind = "unresolved_call_output_source"
            diagnostic_source_key = "source_call_overlay"
            if not source_overlay:
                source_overlay = str(semantic.get("source_precompile_overlay") or "")
                relation_kind = "precompile_output"
                producers = precompile_producers
                diagnostic_kind = "unresolved_precompile_output_source"
                diagnostic_source_key = "source_precompile_overlay"
            if not source_overlay:
                continue
            producer = producers.get(source_overlay)
            target = str(node.get("semantic_id") or "")
            if not producer:
                diagnostics.append({
                    "kind": diagnostic_kind,
                    "semantic_id": target,
                    diagnostic_source_key: source_overlay,
                })
                continue
            key = (producer, target, relation_kind)
            if key not in existing and producer != target:
                edges.append({
                    "from": producer,
                    "to": target,
                    "kind": relation_kind,
                    "source_overlay": source_overlay,
                })
                existing.add(key)

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

    def _ensure_storage_location_bindings(self, nodes: list[Json], bindings: dict[str, Json]) -> None:
        for node in nodes:
            self._storage_location_binding(node, bindings)

    def _binding_for_node_value(self, node: Json, value: Any, bindings: dict[str, Json]) -> Json | None:
        storage = self._storage_location_binding(node, bindings)
        if storage and str(value or "") == str(storage.get("access") or ""):
            return storage
        return self._binding_for_value(value, bindings)

    def _storage_location_binding(self, node: Json, bindings: dict[str, Json]) -> Json | None:
        """Return the exact source-level storage binding used by a state fact.

        A mapping/member location cannot pass the ordinary identifier grammar
        (it contains brackets), but it is still a first-class mutable value in
        SFIR.  Its identity is the fully recovered semantic location.  If the
        location was not recovered, leave it unbound rather than manufacture a
        potentially incorrect alias.
        """
        if node.get("kind") not in {"StateRead", "StateWrite"}:
            return None
        semantic = node.get("semantic") or {}
        location = semantic.get("location") if isinstance(semantic, dict) else None
        if not isinstance(location, dict):
            return None
        access = str(location.get("access") or "")
        state_variable = str(location.get("state_variable") or "")
        if not access or not state_variable:
            return None
        key = f"@storage:{access}"
        if key in bindings:
            return bindings[key]
        state = bindings.get(self._base_name(state_variable, bindings))
        root = str(state.get("binding_id")) if state else f"state:{state_variable}"
        binding = {
            "binding_id": f"{root}:storage_location:{access}",
            "base_name": access,
            "facet": "storage_location",
            "kind": "storage_location",
            "identity_status": "semantic_storage_location",
            "access": access,
            "state_variable": state_variable,
            "keys": list(location.get("keys") or []),
            "member": location.get("member"),
        }
        bindings[key] = binding
        return binding

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
