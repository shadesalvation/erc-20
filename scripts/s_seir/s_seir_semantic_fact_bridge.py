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
            # Completed lifters now carry the endpoint in semantic provenance
            # and deliberately strip effect transport. Use that proved anchor
            # before the legacy effect lookup; cfg_nodes is only evidence.
            anchor = str((fact.get("semantic_provenance") or {}).get("anchor_cfg_node") or "")
            anchor = anchor or self._sink_cfg_node(fact, effect_by_id, effect_cfg_nodes)
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

        # A S-SEIR effect may represent one source endpoint reached through
        # several CFG paths.  Keep those executions distinct in the common
        # fact schema; the bridge must not collapse them merely because they
        # share a source statement or CFG block.
        facts = self._materialize_sseir_path_instances(facts, effect_by_id)
        facts.sort(key=self._sort_key)
        self._propagate_dominating_guards(facts, semantic_input.control)
        self._propagate_terminal_branch_guards(facts, semantic_input.control)
        self._link_control_predecessors(facts, block_predecessors)
        return facts

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
    def _materialize_sseir_path_instances(
        cls,
        facts: list[Json],
        effect_by_id: dict[str, Json],
    ) -> list[Json]:
        """Expand one Yul fact per S-SEIR reaching path when needed.

        S-SEIR already performs the path-sensitive analysis.  This method
        only projects its recorded ``path_states`` into separate fact
        instances.  It deliberately does not enumerate CFG paths itself.
        Candidate facts (for example ``PathConditionedEventEmit``) are
        already one-per-path when they enter the bridge and are only
        annotated here.
        """
        out: list[Json] = []
        for fact in facts:
            if str(fact.get("source_lang") or "") != "yul":
                out.append(fact)
                continue

            endpoint_effects = cls._path_endpoint_effects(fact, effect_by_id)
            path_entries = cls._effect_path_entries(endpoint_effects)
            candidate = (fact.get("evidence") or {}).get("candidate") or {}
            candidate_condition = (
                str(candidate.get("condition") or "")
                if isinstance(candidate, dict) else ""
            )

            if candidate_condition:
                matching = next(
                    (item for item in path_entries if item[2] == candidate_condition),
                    None,
                )
                effect_id, path_index, path_condition = matching or (
                    cls._endpoint_effect_id(endpoint_effects, fact),
                    0,
                    candidate_condition,
                )
                out.append(cls._path_instance(
                    fact,
                    effect_id,
                    path_index,
                    path_condition,
                    replace_condition=False,
                    duplicate_id=False,
                ))
                continue

            if len(path_entries) <= 1:
                if path_entries:
                    effect_id, path_index, path_condition = path_entries[0]
                    out.append(cls._path_instance(
                        fact,
                        effect_id,
                        path_index,
                        path_condition,
                        replace_condition=False,
                        duplicate_id=False,
                    ))
                else:
                    out.append(fact)
                continue

            for effect_id, path_index, path_condition in path_entries:
                out.append(cls._path_instance(
                    fact,
                    effect_id,
                    path_index,
                    path_condition,
                    # Require.condition is the lifted success predicate,
                    # whereas a Revert effect path is the failure route.
                    # Keep both meanings separate.
                    replace_condition=str(fact.get("kind") or "") != "Require",
                    duplicate_id=True,
                ))
        return out

    @classmethod
    def _path_endpoint_effects(
        cls,
        fact: Json,
        effect_by_id: dict[str, Json],
    ) -> list[Json]:
        evidence = fact.get("evidence") or {}
        referenced = [
            effect_by_id[effect_id]
            for effect_id in (str(value) for value in evidence.get("effects") or [])
            if effect_id in effect_by_id
        ]
        expected = cls._SINK_EFFECT_KINDS.get(str(fact.get("kind") or ""))
        if expected:
            sinks = [
                effect for effect in referenced
                if str(effect.get("kind") or "") in expected
            ]
            if sinks:
                return sinks
        return referenced

    @staticmethod
    def _effect_path_entries(effects: list[Json]) -> list[tuple[str, int, str]]:
        out: list[tuple[str, int, str]] = []
        seen: set[tuple[str, str]] = set()
        for effect in effects:
            effect_id = str(effect.get("effect_id") or "")
            attrs = effect.get("attrs") or {}
            for index, value in enumerate(attrs.get("path_states") or []):
                condition = str(value or "")
                if not condition or condition == "entry":
                    continue
                key = (effect_id, condition)
                if key in seen:
                    continue
                seen.add(key)
                out.append((effect_id, index + 1, condition))
        return out

    @staticmethod
    def _endpoint_effect_id(effects: list[Json], fact: Json) -> str:
        for effect in effects:
            effect_id = str(effect.get("effect_id") or "")
            if effect_id:
                return effect_id
        evidence = fact.get("evidence") or {}
        return str(evidence.get("overlay") or fact.get("operation_id") or fact.get("fact_id") or "endpoint")

    @classmethod
    def _path_instance(
        cls,
        fact: Json,
        effect_id: str,
        path_index: int,
        path_condition: str,
        *,
        replace_condition: bool,
        duplicate_id: bool,
    ) -> Json:
        item = dict(fact)
        path_id = f"{effect_id}:path_{path_index}" if effect_id else f"path_{path_index}"
        evidence = dict(item.get("evidence") or {})
        endpoint_id = cls._semantic_endpoint_id(item, effect_id)
        item["semantic_endpoint_id"] = endpoint_id
        item["path_id"] = path_id
        item["path_condition"] = path_condition
        item["evidence"] = {
            **evidence,
            "path_instance": {
                "semantic_endpoint_id": endpoint_id,
                "path_id": path_id,
                "condition": path_condition,
                "source": "sseir_effect_path_state",
            },
        }
        if replace_condition:
            item["condition"] = cls._combine_conditions(
                str(item.get("condition") or ""),
                path_condition,
            )
        if duplicate_id:
            old_id = str(item.get("fact_id") or "fact")
            old_operation_id = str(item.get("operation_id") or old_id)
            item["fact_id"] = f"{old_id}::{path_id}"
            item["operation_id"] = f"{old_operation_id}::{path_id}"
        return item

    @staticmethod
    def _semantic_endpoint_id(fact: Json, effect_id: str) -> str:
        evidence = fact.get("evidence") or {}
        overlay = str(evidence.get("overlay") or "")
        function_id = str(fact.get("function_id") or "")
        endpoint = effect_id or overlay or str(fact.get("operation_id") or fact.get("fact_id") or "")
        return f"{function_id}:{endpoint}" if function_id else endpoint

    @classmethod
    def _combine_conditions(cls, existing: str, path_condition: str) -> str:
        if not existing:
            return path_condition
        if not path_condition or existing == path_condition:
            return existing
        existing_parts = cls._condition_literals(existing)
        path_parts = cls._condition_literals(path_condition)
        if existing_parts and existing_parts.issubset(path_parts):
            return path_condition
        if path_parts and path_parts.issubset(existing_parts):
            return existing
        return f"({existing}) && ({path_condition})"

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
            groups = cls._operation_groups(block_facts)
            for group_index, group in enumerate(groups):
                prior_group = groups[group_index - 1] if group_index else []
                for fact in group:
                    predecessors = list(fact.get("control_predecessors") or [])
                    candidates = prior_group or cls._nearest_predecessor_facts(
                        block,
                        by_block,
                        block_predecessors,
                    )
                    for predecessor_fact in candidates:
                        if not cls._path_compatible(predecessor_fact, fact):
                            continue
                        if predecessor_fact.get("fact_id"):
                            predecessors.append(str(predecessor_fact["fact_id"]))
                    fact["control_predecessors"] = list(dict.fromkeys(predecessors))

    @classmethod
    def _operation_groups(cls, block_facts: list[Json]) -> list[list[Json]]:
        groups: list[list[Json]] = []
        for fact in block_facts:
            order = cls._local_order(fact)
            if not groups or cls._local_order(groups[-1][0]) != order:
                groups.append([fact])
            else:
                groups[-1].append(fact)
        return groups

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
                    last_order = cls._local_order(predecessor_facts[-1])
                    out.extend(
                        fact for fact in predecessor_facts
                        if cls._local_order(fact) == last_order
                    )
                else:
                    visit(predecessor)

        visit(block)
        return out

    @classmethod
    def _path_compatible(cls, left: Json, right: Json) -> bool:
        left_literals = cls._condition_literals(cls._fact_path_condition(left))
        right_literals = cls._condition_literals(cls._fact_path_condition(right))
        for expression, polarity in left_literals:
            if (expression, not polarity) in right_literals:
                return False
        return True

    @staticmethod
    def _fact_path_condition(fact: Json) -> str:
        return str(fact.get("path_condition") or fact.get("condition") or "")

    @classmethod
    def _condition_literals(cls, condition: str) -> set[tuple[str, bool]]:
        return {
            cls._condition_literal(part)
            for part in cls._split_top_level_conjunction(condition)
            if cls._condition_literal(part)[0]
        }

    @staticmethod
    def _split_top_level_conjunction(condition: str) -> list[str]:
        out: list[str] = []
        start = 0
        depth = 0
        index = 0
        while index < len(condition):
            char = condition[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth = max(0, depth - 1)
            elif depth == 0 and condition[index:index + 2] == "&&":
                part = condition[start:index].strip()
                if part:
                    out.append(part)
                start = index + 2
                index += 1
            index += 1
        part = condition[start:].strip()
        if part:
            out.append(part)
        return out

    @classmethod
    def _condition_literal(cls, value: str) -> tuple[str, bool]:
        text = cls._strip_outer_parentheses(value.strip())
        if text.startswith("!(") and text.endswith(")"):
            return cls._strip_outer_parentheses(text[2:-1]), False
        if text.startswith("!"):
            return cls._strip_outer_parentheses(text[1:]), False
        return text, True

    @staticmethod
    def _strip_outer_parentheses(value: str) -> str:
        text = value.strip()
        while text.startswith("(") and text.endswith(")"):
            depth = 0
            wraps_all = True
            for index, char in enumerate(text):
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0 and index != len(text) - 1:
                        wraps_all = False
                        break
            if not wraps_all or depth != 0:
                break
            text = text[1:-1].strip()
        return text

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
                # Path instances of one lifted endpoint are sibling facts,
                # not a sequence of independently dominating requires.
                if (
                    fact.get("semantic_endpoint_id")
                    and fact.get("semantic_endpoint_id") == require.get("semantic_endpoint_id")
                ):
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
        out.append(item)
    return out
