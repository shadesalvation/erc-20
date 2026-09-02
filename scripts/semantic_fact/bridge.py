#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


Json = dict[str, Any]


@dataclass
class FunctionSemanticInput:
    """Source-neutral context used to organize already-lifted facts."""

    function_id: str
    control: Json
    effects: list[Json] = field(default_factory=list)

    @classmethod
    def from_function(cls, function: Any) -> "FunctionSemanticInput":
        function_id = str(getattr(function, "function_id", "") or "")
        control = getattr(function, "control", {}) or {}
        effects = getattr(function, "effects", []) or []
        if isinstance(function, dict):
            function_id = str(function.get("function_id") or function_id)
            control = function.get("control") or control
            effects = function.get("effects") or effects
        return cls(
            function_id,
            control,
            [cls._effect_dict(effect) for effect in effects],
        )

    @staticmethod
    def _effect_dict(effect: Any) -> Json:
        if isinstance(effect, dict):
            return dict(effect)
        to_dict = getattr(effect, "to_dict", None)
        if callable(to_dict):
            value = to_dict()
            return dict(value) if isinstance(value, dict) else {}
        return {
            "effect_id": getattr(effect, "effect_id", None),
            "kind": getattr(effect, "kind", None),
            "stmt_refs": list(getattr(effect, "stmt_refs", []) or []),
            "attrs": dict(getattr(effect, "attrs", {}) or {}),
        }


class SemanticFactBridge:
    """Place Solidity and Yul facts on one function-level CFG partial order."""

    _SINK_EFFECT_KINDS: dict[str, tuple[str, ...]] = {
        "StorageLocationResolve": ("MemoryHash",),
        "StateRead": ("StorageRead",),
        "StateWrite": ("StorageWrite",),
        "EventEmit": ("EventLog",),
        "Revert": ("Revert",),
        "Return": ("Return",),
        "Require": ("Require", "Revert"),
        "ExternalCall": ("Call", "StaticCall", "DelegateCall", "CallCode"),
        "LowLevelCall": ("Call", "StaticCall", "DelegateCall", "CallCode"),
        "PrecompileCall": ("Call", "StaticCall", "DelegateCall", "CallCode"),
        "StaticCall": ("StaticCall",),
        "DelegateCall": ("DelegateCall",),
    }

    def merge_function_facts(
        self,
        semantic_input: FunctionSemanticInput,
        solidity_facts: list[Json],
        yul_facts: list[Json],
    ) -> list[Json]:
        facts = [dict(fact) for fact in solidity_facts + yul_facts]
        if not facts:
            return []

        block_order = self._cfg_reverse_postorder(semantic_input.control)
        block_predecessors = self._block_predecessors(semantic_input.control)
        effect_by_id = {
            str(effect.get("effect_id")): effect
            for effect in semantic_input.effects
            if effect.get("effect_id")
        }
        effect_cfg_nodes = self._effect_cfg_nodes(
            semantic_input.control,
            semantic_input.effects,
        )
        for fact in facts:
            fact["function_id"] = semantic_input.function_id
            anchor = self._sink_cfg_node(fact, effect_by_id, effect_cfg_nodes)
            if anchor:
                fact["anchor_cfg_node"] = anchor
            cfg_node = self._primary_cfg_node(fact)
            local_order = self._local_order(fact)
            fact["order"] = {
                "kind": "cfg_partial_order",
                "cfg_block_order": block_order.get(cfg_node, len(block_order)),
                "operation_order": local_order,
            }
            fact.pop("depends_on", None)
            fact.setdefault("control_predecessors", [])
            fact["cfg_predecessor_blocks"] = block_predecessors.get(cfg_node, [])

        self._bind_yul_outputs_to_solidity_returns(facts, semantic_input.control)
        facts.sort(key=self._sort_key)
        self._propagate_dominating_guards(facts, semantic_input.control)
        self._propagate_terminal_branch_guards(facts, semantic_input.control)
        self._link_control_predecessors(facts, block_predecessors)
        return facts

    @classmethod
    def _block_dominators(cls, control: Json) -> dict[str, set[str]]:
        blocks = {
            str(block.get("block_id")): block
            for block in control.get("blocks") or []
            if isinstance(block, dict) and block.get("block_id")
        }
        predecessors = {block_id: set() for block_id in blocks}
        for edge in control.get("edges") or []:
            if not isinstance(edge, dict):
                continue
            source = str(edge.get("from") or "")
            target = str(edge.get("to") or "")
            if source not in blocks or target not in blocks or cls._is_terminal_block(blocks[source]):
                continue
            predecessors[target].add(source)
        roots = [block_id for block_id, preds in predecessors.items() if not preds]
        all_blocks = set(blocks)
        dominators = {
            block_id: ({block_id} if block_id in roots else set(all_blocks))
            for block_id in blocks
        }
        changed = True
        while changed:
            changed = False
            for block_id in blocks:
                if block_id in roots:
                    continue
                preds = predecessors[block_id]
                common = set.intersection(*(dominators[pred] for pred in preds)) if preds else set()
                updated = {block_id} | common
                if updated != dominators[block_id]:
                    dominators[block_id] = updated
                    changed = True
        return dominators

    @classmethod
    def _bind_yul_outputs_to_solidity_returns(cls, facts: list[Json], control: Json) -> None:
        """Join Yul writes to Solidity named returns at the function boundary.

        Only a unique nearest producer whose CFG block dominates the return is
        accepted. This mirrors ordinary SSA def-use construction and avoids
        selecting a source merely because it appears earlier in source text.
        """
        dominators = cls._block_dominators(control)
        producers: dict[str, list[Json]] = {}
        for fact in facts:
            if fact.get("source_lang") != "yul" or fact.get("kind") not in {"ValueCompute", "ValuePhi"}:
                continue
            semantic = fact.get("semantic") or {}
            source_target = str(semantic.get("source_target") or "")
            if source_target and fact.get("lvalue"):
                producers.setdefault(source_target, []).append(fact)

        for fact in facts:
            if fact.get("source_lang") != "solidity" or fact.get("kind") != "Return":
                continue
            semantic = fact.get("semantic") or {}
            source_values = [str(value) for value in semantic.get("resolved_operands") or []]
            if len(source_values) != 1:
                continue
            source_name = source_values[0]
            target_block = cls._primary_cfg_node(fact)
            if not target_block:
                continue
            candidates = []
            for producer in producers.get(source_name, []):
                producer_block = cls._primary_cfg_node(producer)
                if producer_block and producer_block in dominators.get(target_block, set()):
                    candidates.append(producer)
            if not candidates:
                continue
            depth = {
                id(candidate): len(dominators.get(cls._primary_cfg_node(candidate), set()))
                for candidate in candidates
            }
            best_depth = max(depth.values())
            nearest = [candidate for candidate in candidates if depth[id(candidate)] == best_depth]
            if len(nearest) != 1:
                continue
            producer = nearest[0]
            version = producer.get("lvalue")
            fact["rvalue"] = version
            fact["reads"] = [version]
            semantic["values"] = [version]
            semantic["yul_boundary_binding"] = {
                "source_name": source_name,
                "producer_fact": producer.get("fact_id"),
                "producer_cfg_node": cls._primary_cfg_node(producer),
                "proof": "producer_dominates_return",
            }

    @staticmethod
    def _block_predecessors(control: Json) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        block_by_id = {
            str(block.get("block_id")): block
            for block in control.get("blocks") or []
            if isinstance(block, dict) and block.get("block_id")
        }
        for edge in control.get("edges") or []:
            if not isinstance(edge, dict):
                continue
            source = str(edge.get("from") or "")
            target = str(edge.get("to") or "")
            if not source or not target:
                continue
            if SemanticFactBridge._is_terminal_block(block_by_id.get(source) or {}):
                continue
            bucket = out.setdefault(target, [])
            if source not in bucket:
                bucket.append(source)
        return out

    @staticmethod
    def _cfg_reverse_postorder(control: Json) -> dict[str, int]:
        block_by_id = {
            str(block.get("block_id")): block
            for block in control.get("blocks") or []
            if isinstance(block, dict) and block.get("block_id")
        }
        nodes = [
            str(block.get("block_id") or "")
            for block in control.get("blocks") or []
            if isinstance(block, dict) and block.get("block_id")
        ]
        successors: dict[str, list[str]] = {node: [] for node in nodes}
        predecessors: dict[str, list[str]] = {node: [] for node in nodes}
        for edge in control.get("edges") or []:
            if not isinstance(edge, dict):
                continue
            source = str(edge.get("from") or "")
            target = str(edge.get("to") or "")
            if source not in successors or target not in predecessors:
                continue
            if SemanticFactBridge._is_terminal_block(block_by_id.get(source) or {}):
                continue
            if target not in successors[source]:
                successors[source].append(target)
            if source not in predecessors[target]:
                predecessors[target].append(source)

        roots = [node for node in nodes if not predecessors[node]]
        visited: set[str] = set()
        postorder: list[str] = []

        def visit(node: str) -> None:
            if node in visited:
                return
            visited.add(node)
            for successor in successors.get(node, []):
                visit(successor)
            postorder.append(node)

        for root in roots:
            visit(root)
        for node in nodes:
            visit(node)
        ordered = list(reversed(postorder))
        return {node: index for index, node in enumerate(ordered)}

    @staticmethod
    def _primary_cfg_node(fact: Json) -> str:
        anchor = fact.get("anchor_cfg_node")
        if anchor:
            return str(anchor)
        nodes = fact.get("cfg_nodes") or []
        return str(nodes[0]) if nodes else ""

    @classmethod
    def _sink_cfg_node(
        cls,
        fact: Json,
        effect_by_id: dict[str, Json],
        effect_cfg_nodes: dict[str, str],
    ) -> str:
        """Return the execution node of a fact's semantic endpoint.

        ``cfg_nodes`` is an evidence set and may begin with a slot/hash
        definition.  Ordering must instead use the effect that performs the
        fact itself, such as StorageWrite for StateWrite.  The mapping is
        deliberately closed: unsupported fact kinds keep their existing CFG
        node instead of inferring an endpoint.
        """
        expected = cls._SINK_EFFECT_KINDS.get(str(fact.get("kind") or ""))
        if not expected:
            return ""
        evidence = fact.get("evidence") or {}
        effect_ids = [str(value) for value in evidence.get("effects") or []]
        for effect_id in reversed(effect_ids):
            effect = effect_by_id.get(effect_id) or {}
            if str(effect.get("kind") or "") not in expected:
                continue
            node = effect_cfg_nodes.get(effect_id)
            if node:
                return node
        return ""

    @staticmethod
    def _effect_cfg_nodes(control: Json, effects: list[Json]) -> dict[str, str]:
        """Map effects to unified CFG blocks using node id plus stmt refs."""
        blocks = [
            block for block in control.get("blocks") or []
            if isinstance(block, dict) and block.get("block_id")
        ]
        out: dict[str, str] = {}
        for effect in effects:
            effect_id = str(effect.get("effect_id") or "")
            if not effect_id:
                continue
            attrs = effect.get("attrs") or {}
            node_id = attrs.get("cfg_node_id")
            refs = {str(ref) for ref in effect.get("stmt_refs") or [] if ref}
            exact: list[str] = []
            node_matches: list[str] = []
            ref_matches: list[str] = []
            for block in blocks:
                block_id = str(block.get("block_id"))
                block_attrs = block.get("attrs") or {}
                block_refs = {str(ref) for ref in block.get("stmts") or [] if ref}
                node_matches_id = (
                    node_id is not None
                    and (
                        block_attrs.get("node_id") == node_id
                        or block_id.endswith(f"_n{node_id}")
                    )
                )
                refs_overlap = bool(refs and refs.intersection(block_refs))
                if node_matches_id and refs_overlap:
                    exact.append(block_id)
                elif node_matches_id:
                    node_matches.append(block_id)
                elif refs_overlap:
                    ref_matches.append(block_id)
            candidates = exact or node_matches or ref_matches
            if len(candidates) == 1:
                out[effect_id] = candidates[0]
        return out

    @staticmethod
    def _local_order(
        fact: Json,
    ) -> int:
        order = fact.get("order") or {}
        if isinstance(order, dict) and order.get("operation_order") is not None:
            return int(order["operation_order"])
        return 0

    @staticmethod
    def _sort_key(fact: Json) -> tuple[Any, ...]:
        order = fact.get("order") or {}
        return (
            int(order.get("cfg_block_order") or 0),
            int(order.get("operation_order") or 0),
            0 if fact.get("source_lang") == "solidity" else 1,
            str(fact.get("operation_id") or fact.get("fact_id") or ""),
        )

    @classmethod
    def _link_control_predecessors(
        cls,
        facts: list[Json],
        block_predecessors: dict[str, list[str]],
    ) -> None:
        by_block: dict[str, list[Json]] = {}
        for fact in facts:
            block = cls._primary_cfg_node(fact)
            if block:
                by_block.setdefault(block, []).append(fact)

        for block_facts in by_block.values():
            block_facts.sort(key=cls._sort_key)

        for block, block_facts in by_block.items():
            for index, fact in enumerate(block_facts):
                predecessors = list(fact.get("control_predecessors") or [])
                if index:
                    previous = block_facts[index - 1].get("fact_id")
                    if previous:
                        predecessors.append(str(previous))
                else:
                    for predecessor_fact in cls._nearest_predecessor_facts(
                        block,
                        by_block,
                        block_predecessors,
                    ):
                        if predecessor_fact.get("fact_id"):
                            predecessors.append(str(predecessor_fact["fact_id"]))
                fact["control_predecessors"] = list(dict.fromkeys(predecessors))

    @classmethod
    def _nearest_predecessor_facts(
        cls,
        block: str,
        by_block: dict[str, list[Json]],
        block_predecessors: dict[str, list[str]],
    ) -> list[Json]:
        out: list[Json] = []
        visited: set[str] = set()

        def visit(current: str) -> None:
            for predecessor in block_predecessors.get(current, []):
                if predecessor in visited:
                    continue
                visited.add(predecessor)
                predecessor_facts = by_block.get(predecessor) or []
                if predecessor_facts:
                    out.append(predecessor_facts[-1])
                else:
                    visit(predecessor)

        visit(block)
        return out

    @classmethod
    def _propagate_dominating_guards(cls, facts: list[Json], control: Json) -> None:
        dominators = cls._dominators(control)
        requires = [
            fact for fact in facts
            if fact.get("kind") == "Require" and cls._require_guard(fact)
        ]
        if not requires:
            return
        # Guard propagation must use each require's own predicate.  Mutating a
        # later require with an earlier guard must not turn that combined result
        # into a new guard and duplicate the earlier predicate downstream.
        require_conditions = {
            id(fact): cls._require_guard(fact)
            for fact in requires
        }
        for fact in facts:
            fact_block = cls._primary_cfg_node(fact)
            if not fact_block:
                continue
            guards: list[str] = []
            for require in requires:
                if require is fact:
                    continue
                require_block = cls._primary_cfg_node(require)
                if not require_block or require_block not in dominators.get(fact_block, set()):
                    continue
                if require_block == fact_block and cls._sort_key(require) >= cls._sort_key(fact):
                    continue
                guard = require_conditions[id(require)]
                if guard and guard not in guards:
                    guards.append(guard)
            if not guards:
                continue
            fact["guard_conditions"] = guards
            conditions = []
            existing = str(fact.get("condition") or "")
            if existing:
                conditions.append(existing)
            conditions.extend(guard for guard in guards if guard not in conditions)
            fact["condition"] = " && ".join(f"({condition})" for condition in conditions)

    @staticmethod
    def _require_guard(fact: Json) -> str:
        semantic = fact.get("semantic") or {}
        return str(semantic.get("guard") or semantic.get("condition") or fact.get("condition") or "")

    @classmethod
    def _propagate_terminal_branch_guards(cls, facts: list[Json], control: Json) -> None:
        """Carry the surviving edge predicate past a terminal branch arm.

        This is CFG organization, not semantic lifting. For
        ``if (C) revert; S`` only the false edge can reach ``S``, so facts for
        ``S`` execute under ``!(C)`` even when Slither keeps a syntactic edge
        from the revert node to the join block.
        """
        blocks = {
            str(block.get("block_id")): block
            for block in control.get("blocks") or []
            if isinstance(block, dict) and block.get("block_id")
        }
        edges_by_source: dict[str, list[Json]] = {}
        for edge in control.get("edges") or []:
            if isinstance(edge, dict) and edge.get("from") and edge.get("to"):
                edges_by_source.setdefault(str(edge["from"]), []).append(edge)
        dominators = cls._dominators(control)
        for controller, block in blocks.items():
            terminator = block.get("terminator") or {}
            if terminator.get("kind") != "Branch":
                continue
            condition = str(terminator.get("condition") or "")
            if not condition or condition == "endif":
                continue
            branch_edges = [
                edge for edge in edges_by_source.get(controller, [])
                if edge.get("kind") in {"true", "false"}
            ]
            if len(branch_edges) != 2:
                continue
            terminal_edges = [
                edge for edge in branch_edges
                if cls._is_terminal_block(blocks.get(str(edge.get("to"))) or {})
            ]
            if len(terminal_edges) != 1:
                continue
            live_edge = next(edge for edge in branch_edges if edge is not terminal_edges[0])
            live_block = str(live_edge.get("to") or "")
            guard = condition if live_edge.get("kind") == "true" else f"!({condition})"
            for fact in facts:
                fact_block = cls._primary_cfg_node(fact)
                if not fact_block or live_block not in dominators.get(fact_block, set()):
                    continue
                cls._append_guard(fact, guard)

    @staticmethod
    def _is_terminal_block(block: Json) -> bool:
        terminator = block.get("terminator") or {}
        return str(terminator.get("kind") or "") in {"Return", "Revert", "Stop"}

    @staticmethod
    def _append_guard(fact: Json, guard: str) -> None:
        guards = list(fact.get("guard_conditions") or [])
        if guard not in guards:
            guards.append(guard)
        fact["guard_conditions"] = guards
        conditions: list[str] = []
        existing = str(fact.get("condition") or "")
        if existing:
            conditions.append(existing)
        conditions.extend(item for item in guards if item not in conditions)
        fact["condition"] = (
            conditions[0]
            if len(conditions) == 1
            else " && ".join(f"({item})" for item in conditions)
        )

    @staticmethod
    def _dominators(control: Json) -> dict[str, set[str]]:
        nodes = [
            str(block.get("block_id") or "")
            for block in control.get("blocks") or []
            if isinstance(block, dict) and block.get("block_id")
        ]
        predecessors: dict[str, set[str]] = {node: set() for node in nodes}
        block_by_id = {
            str(block.get("block_id")): block
            for block in control.get("blocks") or []
            if isinstance(block, dict) and block.get("block_id")
        }
        for edge in control.get("edges") or []:
            if not isinstance(edge, dict):
                continue
            source = str(edge.get("from") or "")
            target = str(edge.get("to") or "")
            if SemanticFactBridge._is_terminal_block(block_by_id.get(source) or {}):
                continue
            if source in predecessors and target in predecessors:
                predecessors[target].add(source)
        roots = {node for node in nodes if not predecessors[node]}
        all_nodes = set(nodes)
        dom = {
            node: ({node} if node in roots else set(all_nodes))
            for node in nodes
        }
        changed = True
        while changed:
            changed = False
            for node in nodes:
                if node in roots:
                    continue
                incoming = predecessors[node]
                shared = set.intersection(*(dom[parent] for parent in incoming)) if incoming else set()
                updated = {node} | shared
                if updated != dom[node]:
                    dom[node] = updated
                    changed = True
        return dom

def renumber_and_relink_facts(facts: list[Json], prefix: str = "fact") -> list[Json]:
    id_map: dict[str, str] = {}
    for index, fact in enumerate(facts, start=1):
        old_id = str(fact.get("fact_id") or f"anonymous_{index}")
        id_map[old_id] = f"{prefix}_{index}"

    out: list[Json] = []
    for index, fact in enumerate(facts, start=1):
        item = dict(fact)
        item["fact_id"] = f"{prefix}_{index}"
        for field in ("control_predecessors",):
            item[field] = [
                id_map.get(str(value), str(value))
                for value in item.get(field) or []
            ]
        semantic = item.get("semantic")
        if isinstance(semantic, dict):
            binding = semantic.get("yul_boundary_binding")
            if isinstance(binding, dict) and binding.get("producer_fact"):
                binding = dict(binding)
                old_producer = str(binding["producer_fact"])
                binding["producer_fact"] = id_map.get(old_producer, old_producer)
                semantic = dict(semantic)
                semantic["yul_boundary_binding"] = binding
                item["semantic"] = semantic
        out.append(item)
    return out
