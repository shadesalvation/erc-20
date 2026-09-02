from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any, Iterable

from .expressions import ExpressionArena
from .model import (
    BasicBlock,
    CFGEdge,
    DataObjectNode,
    FactGroup,
    LocationNode,
    SemanticFunction,
    SemanticInstruction,
    SemanticProgram,
    Terminator,
    ValueRecord,
)


Json = dict[str, Any]

_EFFECT_KINDS = {
    "StateRead", "StateWrite", "Require", "EventEmit", "ExternalCall", "LowLevelCall",
    "PrecompileCall", "StaticCall", "DelegateCall", "InternalCall", "InternalDynamicCall",
    "LibraryCall", "BuiltinCall", "ValueTransferCall", "Return", "Revert", "Delete",
    "ValueCompute",
}
_SUPPORT_KINDS = {"StorageLocationResolve", "IndexAccess", "MemberAccess", "BranchCondition"}
_TERMINALS = {"Return", "Revert", "Stop"}

_FACT_ATOMIC_KINDS = {
    "StateRead": {"StorageRead"},
    "StateWrite": {"StorageWrite"},
    "ExternalCall": {"Call", "StaticCall", "DelegateCall", "CallCode"},
    "LowLevelCall": {"Call", "StaticCall", "DelegateCall", "CallCode"},
    "PrecompileCall": {"Call", "StaticCall"},
    "StaticCall": {"StaticCall"},
    "DelegateCall": {"DelegateCall"},
    "EventEmit": {"EventLog"},
    "Return": {"Return"},
    "Revert": {"Revert"},
    "ValueCompute": {"ValueCompute", "ValueAssign", "ValueDeclare", "MemoryRead", "MemoryHash"},
}
_ATOMIC_BOUNDARIES = {
    "StorageRead", "MemoryRead", "MemoryHash", "Call", "StaticCall", "DelegateCall",
    "CallCode", "Create", "Create2", "EventLog", "Return", "Revert",
}


def _dict(value: Any) -> Json:
    if isinstance(value, dict):
        return dict(value)
    method = getattr(value, "to_dict", None)
    return dict(method()) if callable(method) else {}


def _unique(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if value not in {None, ""}))


class _ExecutionViewContext:
    """Resolve the execution-side expression from runtime effects and Yul atoms."""

    def __init__(self, raw_function: Any, function_dict: Json, facts: list[Json]) -> None:
        raw_effects = getattr(raw_function, "effects", None) or function_dict.get("effects") or []
        self.effects = {
            str(item.get("effect_id")): item
            for raw in raw_effects
            if (item := _dict(raw)).get("effect_id")
        }
        table = (
            getattr(raw_function, "_sseir_yul_atomic_operations", None)
            or function_dict.get("yul_atomic_operations")
            or {}
        )
        self.atoms = [dict(item) for item in table.get("operations") or [] if isinstance(item, dict)]
        self.atom_by_id = {
            str(item.get("atom_id")): item for item in self.atoms if item.get("atom_id")
        }
        self.atom_by_result = {
            str(item.get("result")): item for item in self.atoms if item.get("result")
        }
        self.fact_results = {
            str(fact.get("lvalue")) for fact in facts if fact.get("lvalue")
        }
        solidity_table = (
            getattr(raw_function, "_sseir_solidity_atomic_operations", None)
            or function_dict.get("solidity_atomic_operations")
            or {}
        )
        self.ssa_aliases = self._collect_ssa_aliases(solidity_table)
        self.memory_writes_by_node: dict[str, list[Json]] = defaultdict(list)
        self.effects_by_node: dict[str, list[Json]] = defaultdict(list)
        self.value_producers: dict[str, list[Json]] = defaultdict(list)
        self.boundary_output_versions: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for effect in self.effects.values():
            attrs = effect.get("attrs") or {}
            node_id = attrs.get("cfg_node_id")
            if node_id is not None:
                self.effects_by_node[str(node_id)].append(effect)
                if str(effect.get("kind") or "") in {"MemoryWrite", "MemoryCopy"}:
                    self.memory_writes_by_node[str(node_id)].append(effect)
            names = _unique([
                attrs.get("value") if str(effect.get("kind") or "") in {"MemoryRead", "MemoryHash", "StorageRead"} else None,
                *(attrs.get("targets") or []),
            ])
            for name in names:
                self.value_producers[name].append(effect)
            for name, records in (attrs.get("external_output_version_paths") or {}).items():
                for record in records or []:
                    if not isinstance(record, dict) or not record.get("version"):
                        continue
                    item = dict(record)
                    item["effect_id"] = effect.get("effect_id")
                    if item not in self.boundary_output_versions[str(name)]:
                        self.boundary_output_versions[str(name)].append(item)
                    self.ssa_aliases[str(item["version"])] = str(name)

    @classmethod
    def _collect_ssa_aliases(cls, table: Any) -> dict[str, str]:
        """Map Slither SSA spellings to their source variable without guessing."""
        aliases: dict[str, str] = {}

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                text = value.get("text") or value.get("name")
                base = value.get("base_name")
                if text and base and str(text) != str(base):
                    aliases[str(text)] = str(base)
                for item in value.values():
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(table.get("operations") if isinstance(table, dict) else table)
        return aliases

    def effects_for(self, fact: Json) -> list[Json]:
        return [
            self.effects[str(effect_id)]
            for effect_id in (fact.get("evidence") or {}).get("effects") or []
            if str(effect_id) in self.effects
        ]

    @staticmethod
    def _memory_source_nodes(value: Any) -> set[str]:
        """Collect explicit MemorySSA source nodes from a sink query.

        Only schema fields emitted by MemorySSA/SinkResolver are accepted.  A
        generic ``node_id`` is deliberately ignored because it can denote the
        sink itself rather than a reaching memory definition.
        """
        out: set[str] = set()

        def visit(item: Any, parent_key: str | None = None) -> None:
            if isinstance(item, dict):
                source = item.get("source_node_id")
                if source is not None:
                    out.add(str(source))
                definition = item.get("definition")
                if isinstance(definition, dict) and definition.get("node_id") is not None:
                    out.add(str(definition["node_id"]))
                for key, nested in item.items():
                    if key not in {"source_node_id", "definition"}:
                        visit(nested, key)
            elif isinstance(item, list):
                for nested in item:
                    visit(nested, parent_key)

        visit(value)
        return out

    def supporting_effects(self, fact: Json) -> list[Json]:
        """Return the transitive, explicitly evidenced memory support closure."""
        out: list[Json] = []
        seen: set[str] = set()
        queue = list(self.effects_for(fact))
        primary = {str(effect.get("effect_id") or "") for effect in queue}
        while queue:
            effect = queue.pop(0)
            attrs = effect.get("attrs") or {}
            related: list[Json] = []
            for node_id in self._memory_source_nodes(attrs):
                related.extend(self.memory_writes_by_node.get(node_id, []))

            kind = str(effect.get("kind") or "")
            node_id = attrs.get("cfg_node_id")
            values = _unique([
                attrs.get("value") if kind in {"ValueDef", "MemoryRead", "MemoryHash", "StorageRead"} else None,
                *(attrs.get("targets") or []),
            ])
            if node_id is not None and values:
                for peer in self.effects_by_node.get(str(node_id), []):
                    peer_attrs = peer.get("attrs") or {}
                    peer_values = set(_unique([
                        peer_attrs.get("value"),
                        *(peer_attrs.get("targets") or []),
                    ]))
                    if peer_values.intersection(values):
                        related.append(peer)

            if kind == "MemoryWrite":
                value = str(attrs.get("value") or "")
                producers = self.value_producers.get(value, [])
                write_node = int(node_id) if node_id is not None else None
                eligible = [
                    producer for producer in producers
                    if write_node is None
                    or (producer.get("attrs") or {}).get("cfg_node_id") is None
                    or int((producer.get("attrs") or {})["cfg_node_id"]) <= write_node
                ]
                if eligible:
                    nearest = max(
                        int((producer.get("attrs") or {}).get("cfg_node_id") or -1)
                        for producer in eligible
                    )
                    related.extend(
                        producer for producer in eligible
                        if int((producer.get("attrs") or {}).get("cfg_node_id") or -1) == nearest
                    )

            for source in related:
                effect_id = str(source.get("effect_id") or "")
                if not effect_id or effect_id in seen or effect_id in primary:
                    continue
                seen.add(effect_id)
                out.append(source)
                queue.append(source)
        return out

    def external_bindings_for(self, fact: Json) -> dict[str, str]:
        bindings: dict[str, str] = {}
        conflicts: set[str] = set()
        for effect in self.effects_for(fact):
            for name, version in ((effect.get("attrs") or {}).get("external_value_bindings") or {}).items():
                name = str(name)
                version = str(version)
                if name in bindings and bindings[name] != version:
                    conflicts.add(name)
                else:
                    bindings[name] = version
        for name in conflicts:
            bindings.pop(name, None)
        return bindings

    def boundary_output_bindings(self) -> dict[str, str]:
        outputs = set(self.boundary_output_versions)
        return {
            ssa_name: base
            for ssa_name, base in self.ssa_aliases.items()
            if base in outputs
        }

    def supporting_stmt_refs(self, fact: Json) -> list[str]:
        return _unique(
            ref
            for effect in self.supporting_effects(fact)
            for ref in effect.get("stmt_refs") or []
        )

    def atom_for(self, fact: Json) -> Json | None:
        effects = self.effects_for(fact)
        for effect in effects:
            atom_id = (effect.get("attrs") or {}).get("atomic_operation_id")
            if atom_id and str(atom_id) in self.atom_by_id:
                return self.atom_by_id[str(atom_id)]

        fact_kind = str(fact.get("kind") or "")
        expected = _FACT_ATOMIC_KINDS.get(fact_kind, set())
        stmt_refs = set(str(item) for item in fact.get("stmt_refs") or [])
        lvalue = str(fact.get("lvalue") or "")
        candidates: list[tuple[int, int, Json]] = []
        for atom in self.atoms:
            if stmt_refs and not stmt_refs.intersection(str(item) for item in atom.get("stmt_refs") or []):
                continue
            score = 0
            if atom.get("atomic_kind") in expected:
                score += 8
            if lvalue and atom.get("result") == lvalue:
                score += 6
            if atom.get("root_operation"):
                score += 2
            if not expected and not lvalue:
                continue
            if score:
                candidates.append((score, int(atom.get("sequence") or 0), atom))
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item[0], item[1]))[2]

    def expand_pure_value(self, value: Any, active: frozenset[str] = frozenset()) -> str:
        text = str(value)
        if text in active:
            return text
        # A materialized fact is an execution boundary. Keep its result as an
        # SSA operand so def-use links consumers to that instruction.
        if text in self.fact_results:
            return text
        atom = self.atom_by_result.get(text)
        if not atom:
            return text
        arguments = [self.expand_pure_value(item, active | {text}) for item in atom.get("arguments") or []]
        operation = str(atom.get("operation") or "")
        if operation in {"assign", "declare_assign"} and arguments:
            return arguments[0]
        if not operation:
            return text
        return f"{operation}({', '.join(arguments)})"

    def execution_expression(self, fact: Json) -> Any:
        semantic = fact.get("semantic") or {}
        explicit = semantic.get("execution_expression")
        if explicit is not None:
            return explicit
        kind = str(fact.get("kind") or "")
        atom = self.atom_for(fact)
        if atom:
            arguments = list(atom.get("arguments") or [])
            if kind in {"ExternalCall", "LowLevelCall", "PrecompileCall", "StaticCall", "DelegateCall"}:
                return atom.get("execution_expression") or atom.get("expression")
            if kind == "StateWrite" and len(arguments) >= 2:
                return self.expand_pure_value(arguments[1])
            if kind == "ValueCompute" and atom.get("operation") in {"assign", "declare_assign"} and arguments:
                return self.expand_pure_value(arguments[0])
            if kind in {"StateRead", "ValueCompute"}:
                return atom.get("execution_expression") or atom.get("expression")
        for effect in self.effects_for(fact):
            attrs = effect.get("attrs") or {}
            if kind == "StateWrite" and attrs.get("value") is not None:
                return attrs.get("value")
            if kind == "StateRead":
                if attrs.get("expression") is not None:
                    return attrs.get("expression")
                if attrs.get("slot") is not None:
                    return f"sload({attrs['slot']})"
        return fact.get("rvalue") or semantic.get("expression") or semantic.get("value")

    def execution_arguments(self, fact: Json) -> list[Any]:
        atom = self.atom_for(fact)
        if atom and str(fact.get("kind") or "") in {
            "ExternalCall", "LowLevelCall", "PrecompileCall", "StaticCall", "DelegateCall", "EventEmit",
        }:
            return list(atom.get("arguments") or [])
        return []

    def terminal_values(self, fact: Json) -> list[Any]:
        atom = self.atom_for(fact)
        if atom and atom.get("atomic_kind") in {"Return", "Revert"}:
            return list(atom.get("arguments") or [])
        return []


class SemanticIRBuilder:
    """Materialize mutable Semantic IR without re-running semantic analysis."""

    def __init__(self) -> None:
        self._instruction_counter = 0
        self._location_counter = 0
        self._data_object_counter = 0
        self._group_counter = 0

    def build(self, functions: list[Any], fact_payload: Json, *, source: str | None = None) -> SemanticProgram:
        facts_by_function: dict[str, list[Json]] = defaultdict(list)
        for fact in fact_payload.get("facts") or []:
            facts_by_function[str(fact.get("function_id") or self._fact_function_id(fact))].append(fact)
        result: list[SemanticFunction] = []
        for raw_function in functions:
            function_id = str(getattr(raw_function, "function_id", "") or _dict(raw_function).get("function_id") or "")
            result.append(self._function(raw_function, facts_by_function.get(function_id, [])))
        program = SemanticProgram(source or fact_payload.get("source"), result)
        for function in result:
            program.diagnostics.extend(
                {"function_id": function.function_id, **diagnostic}
                for diagnostic in function.diagnostics
            )
        return program

    @staticmethod
    def _fact_function_id(fact: Json) -> str:
        contract = str(fact.get("contract") or "")
        signature = str(fact.get("signature") or fact.get("function") or "")
        return f"{contract}.{signature}" if contract else signature

    def _function(self, raw_function: Any, facts: list[Json]) -> SemanticFunction:
        fn = _dict(raw_function)
        control = getattr(raw_function, "control", None) or fn.get("control") or {}
        function = SemanticFunction(
            function_id=str(getattr(raw_function, "function_id", "") or fn.get("function_id") or ""),
            contract=str(getattr(raw_function, "contract", "") or fn.get("contract") or ""),
            function=str(getattr(raw_function, "function", "") or fn.get("function") or ""),
            signature=str(getattr(raw_function, "signature", "") or fn.get("signature") or ""),
            source_statements=[_dict(item) for item in (getattr(raw_function, "source_statements", None) or fn.get("source_statements") or [])],
            fact_table={str(item.get("fact_id")): dict(item) for item in facts if item.get("fact_id")},
        )
        arena = ExpressionArena()
        self._materialize_blocks(function, control, arena)
        groups, consumed = self._group_facts(facts, control)
        function.fact_groups.extend(groups)
        primary_ids = {group.primary_fact for group in groups}
        fact_kind_by_id = {
            str(fact.get("fact_id")): str(fact.get("kind"))
            for fact in facts if fact.get("fact_id")
        }
        strongly_consumed = {
            support
            for group in groups
            if fact_kind_by_id.get(group.primary_fact) != "ValueCompute"
            for support in group.support_facts
        }
        materialized_facts = [
            fact for fact in facts
            if (
                str(fact.get("fact_id") or "") not in consumed
                or (
                    str(fact.get("fact_id") or "") in primary_ids
                    and str(fact.get("fact_id") or "") not in strongly_consumed
                )
            )
        ]
        execution = _ExecutionViewContext(raw_function, fn, materialized_facts)
        function.value_aliases = dict(execution.ssa_aliases)
        fact_by_id = {str(fact.get("fact_id")): fact for fact in facts if fact.get("fact_id")}
        supports_by_primary = {
            group.primary_fact: [fact_by_id[item] for item in group.support_facts if item in fact_by_id]
            for group in groups
        }
        terminal_facts: dict[str, list[Json]] = defaultdict(list)

        for fact in materialized_facts:
            fact_id = str(fact.get("fact_id") or "")
            if fact_id in consumed and not any(group.primary_fact == fact_id for group in groups):
                continue
            if self._detached_analysis_support(fact):
                function.unplaced_facts.append(fact_id)
                continue
            block_id = self._fact_block(fact, function.blocks)
            if not block_id:
                block_id = self._unplaced_block(function)
                function.unplaced_facts.append(fact_id)
            support = next((group.support_facts for group in groups if group.primary_fact == fact_id), [])
            origins = _unique([fact_id, *support])
            if fact.get("kind") in {"Return", "Revert"}:
                terminal_facts[block_id].append({**fact, "_origins": origins})
                continue
            instruction = self._instruction(
                fact, origins, arena, function, supports_by_primary.get(fact_id, []), execution
            )
            function.blocks[block_id].instructions.append(instruction)

        self._attach_terminal_facts(function, terminal_facts, arena, execution)
        self._materialize_boundary_output_bridges(function, execution, arena)
        self._materialize_unlifted_memory_effects(function, execution, facts, arena)
        self._prune_terminal_edges(function)
        self._attach_branch_fact_origins(function, facts, consumed, arena, execution)
        function.expressions = arena.nodes
        self._sort_instructions(function, facts)
        self._rebuild_def_use(function, aliases=function.value_aliases)
        self._validate(function)
        return function

    def _materialize_blocks(self, function: SemanticFunction, control: Json, arena: ExpressionArena) -> None:
        raw_blocks = control.get("blocks") or []
        raw_edges = control.get("edges") or []
        for raw in raw_blocks:
            block_id = str(raw.get("block_id") or "")
            if not block_id:
                continue
            function.blocks[block_id] = BasicBlock(
                block_id=block_id,
                kind=str(raw.get("kind") or "unknown"),
                stmt_refs=_unique(raw.get("stmts") or []),
                attrs={
                    "source_terminator": raw.get("terminator") or {},
                    "source_attrs": {
                        key: (raw.get("attrs") or {}).get(key)
                        for key in ("src", "text", "node_id", "node_kind", "slither_node_type", "function_loop_context")
                        if (raw.get("attrs") or {}).get(key) is not None
                    },
                },
            )
        for raw in raw_edges:
            source = str(raw.get("from") or "")
            target = str(raw.get("to") or "")
            if source not in function.blocks or target not in function.blocks:
                continue
            kind = str(raw.get("kind") or "next")
            source_kind = str((function.blocks[source].attrs.get("source_terminator") or {}).get("kind") or "")
            if kind.startswith("terminate:") or source_kind in _TERMINALS:
                continue
            predicate = self._edge_predicate(kind)
            function.edges.append(CFGEdge(source, target, kind, predicate))
        function.rebuild_cfg_links()
        outgoing: dict[str, list[CFGEdge]] = defaultdict(list)
        for edge in function.edges:
            outgoing[edge.source].append(edge)
        for block_id, block in function.blocks.items():
            raw_block = next(
                (item for item in raw_blocks if str(item.get("block_id") or "") == block_id),
                {},
            )
            atomic_operations = (raw_block.get("attrs") or {}).get("yul_atomic_operations") or []
            source_term = block.attrs.get("source_terminator") or {}
            source_kind = str(source_term.get("kind") or "")
            edges = [edge for edge in outgoing.get(block_id, []) if not edge.kind.startswith("terminate:")]
            if source_kind == "Branch" or len([edge for edge in edges if self._branch_side(edge.kind)]) >= 2:
                condition = arena.build(source_term.get("condition"), [])
                ordered = sorted(edges, key=lambda edge: 0 if self._branch_side(edge.kind) == "true" else 1)
                block.terminator = Terminator(
                    "Branch",
                    execution_condition=condition,
                    normalized_condition=condition,
                    condition=condition,
                    targets=[edge.target for edge in ordered],
                    attrs={"source": source_term},
                )
            elif source_kind in _TERMINALS:
                terminal_atom = next(
                    (
                        item for item in reversed(atomic_operations)
                        if item.get("atomic_kind") == source_kind
                    ),
                    None,
                )
                execution_values = [
                    expr for expr in (
                        arena.build(value, []) for value in (terminal_atom or {}).get("arguments") or []
                    ) if expr
                ]
                block.terminator = Terminator(
                    source_kind,
                    execution_values=execution_values,
                    attrs={"source": source_term},
                )
            elif len(edges) == 1:
                block.terminator = Terminator("Goto", targets=[edges[0].target], attrs={"edge_kind": edges[0].kind})
            elif len(edges) > 1:
                block.terminator = Terminator("Branch", targets=[edge.target for edge in edges], attrs={"unresolved_condition": True, "source": source_term})
            else:
                block.terminator = Terminator("Fallthrough", attrs={"source": source_term})

    @staticmethod
    def _edge_predicate(kind: str) -> str | None:
        if ":" not in kind:
            return None
        head, tail = kind.split(":", 1)
        return tail.strip() if head.strip() in {"true", "false"} else None

    @staticmethod
    def _branch_side(kind: str) -> str | None:
        if kind == "true" or kind.startswith("true:"):
            return "true"
        if kind == "false" or kind.startswith("false:"):
            return "false"
        return None

    def _group_facts(self, facts: list[Json], control: Json) -> tuple[list[FactGroup], set[str]]:
        groups: list[FactGroup] = []
        consumed: set[str] = set()
        supports = [fact for fact in facts if self._is_support(fact)]
        branch_blocks = {
            str(block.get("block_id")): str((block.get("terminator") or {}).get("condition") or "")
            for block in control.get("blocks") or []
            if (block.get("terminator") or {}).get("kind") == "Branch"
        }
        for fact in facts:
            if str(fact.get("kind")) not in _EFFECT_KINDS:
                continue
            related = [support for support in supports if self._supports(support, fact)]
            related = self._support_closure(related, supports)
            support_ids = _unique(item.get("fact_id") for item in related)
            if support_ids:
                groups.append(self._group(str(fact.get("fact_id")), support_ids, "support_fact_fused_into_effect"))
                consumed.update(support_ids)

        for block_id, condition in branch_blocks.items():
            branch_support = [
                fact for fact in facts
                if block_id in [str(item) for item in fact.get("cfg_nodes") or []]
                and self._is_branch_support(fact, condition)
            ]
            if not branch_support:
                continue
            support_ids = _unique(item.get("fact_id") for item in branch_support)
            groups.append(self._group(f"terminator:{block_id}", support_ids, "condition_atoms_fused_into_branch"))
            consumed.update(support_ids)
        return groups, consumed

    def _support_closure(self, seeds: list[Json], supports: list[Json]) -> list[Json]:
        """Follow explicit temporary/location definitions behind a support fact."""
        out = list(seeds)
        seen = {str(item.get("fact_id") or "") for item in out}
        changed = True
        while changed:
            changed = False
            for dependent in list(out):
                needed = _unique([
                    dependent.get("lvalue"),
                    *((dependent.get("reads") or [])),
                ])
                dependent_refs = set(dependent.get("stmt_refs") or [])
                for candidate in supports:
                    candidate_id = str(candidate.get("fact_id") or "")
                    candidate_lvalue = str(candidate.get("lvalue") or "")
                    if not candidate_id or candidate_id in seen or not candidate_lvalue:
                        continue
                    same_location_target = candidate_lvalue == str(dependent.get("lvalue") or "")
                    defines_operand = candidate_lvalue in needed
                    shared_statement = bool(dependent_refs & set(candidate.get("stmt_refs") or []))
                    if not (defines_operand or (same_location_target and shared_statement)):
                        continue
                    if not self._conditions_compatible(candidate.get("condition"), dependent.get("condition")):
                        continue
                    out.append(candidate)
                    seen.add(candidate_id)
                    changed = True
        return out

    def _group(self, primary: str, supports: list[str], reason: str) -> FactGroup:
        self._group_counter += 1
        return FactGroup(f"group_{self._group_counter}", primary, supports, reason)

    @staticmethod
    def _is_support(fact: Json) -> bool:
        if fact.get("kind") in _SUPPORT_KINDS:
            return True
        return fact.get("source_lang") == "yul" and fact.get("kind") == "ValueCompute"

    @staticmethod
    def _detached_analysis_support(fact: Json) -> bool:
        return bool(
            fact.get("kind") in {"ValueCompute", "ValuePhi", "StorageLocationResolve"}
            and not fact.get("cfg_nodes")
            and not fact.get("stmt_refs")
        )

    def _supports(self, support: Json, primary: Json) -> bool:
        if support is primary or support.get("function_id") != primary.get("function_id"):
            return False
        if not self._conditions_compatible(support.get("condition"), primary.get("condition")):
            return False
        semantic = primary.get("semantic") or {}
        slot_names = _unique([semantic.get("slot"), semantic.get("slot_key"), *((semantic.get("slot_versions") or []))])
        support_lvalue = str(support.get("lvalue") or "")
        primary_lvalue = str(primary.get("lvalue") or "")
        same_result = bool(primary_lvalue and support_lvalue == primary_lvalue)
        slot_definition = bool(support_lvalue and any(name == support_lvalue or name.startswith(f"{support_lvalue}__") for name in slot_names))
        location = semantic.get("location") or {}
        support_location = (support.get("semantic") or {}).get("location") or {}
        same_location = bool(location and support_location and self._stable(location) == self._stable(support_location))
        shared_stmt = bool(set(support.get("stmt_refs") or []) & set(primary.get("stmt_refs") or []))
        support_parent = str((support.get("semantic") or {}).get("parent_effect") or "")
        primary_effects = {
            str(effect) for effect in (primary.get("evidence") or {}).get("effects") or []
        }
        if support_parent and support_parent in primary_effects:
            return True
        if support.get("kind") in {"StorageLocationResolve", "IndexAccess", "MemberAccess"}:
            return same_location or slot_definition or shared_stmt
        rvalue = str(support.get("rvalue") or "")
        is_sink_compute = primary.get("kind") == "StateRead" and same_result and bool(re.search(r"\bsload\s*\(", rvalue))
        is_slot_compute = slot_definition and bool(re.search(r"\b(?:keccak256|sha3)\s*\(", rvalue))
        if is_sink_compute or is_slot_compute:
            return True
        if support.get("source_lang") != "yul" or support.get("kind") != "ValueCompute":
            return False
        primary_material = json.dumps({
            "condition": primary.get("condition"),
            "reads": primary.get("reads") or [],
            "rvalue": primary.get("rvalue"),
            "semantic": semantic,
        }, sort_keys=True, ensure_ascii=False)
        temp_dependency = bool(support_lvalue and re.search(rf"(?<![A-Za-z0-9_$]){re.escape(support_lvalue)}(?![A-Za-z0-9_$])", primary_material))
        normalized_support = re.sub(r"\s+|[()]", "", rvalue)
        normalized_primary = re.sub(r"\s+|[()]", "", primary_material)
        duplicate_expression = bool(shared_stmt and normalized_support and normalized_support in normalized_primary)
        return temp_dependency or duplicate_expression

    @staticmethod
    def _conditions_compatible(left: Any, right: Any) -> bool:
        left_text = str(left or "")
        right_text = str(right or "")
        return not left_text or not right_text or left_text == right_text or left_text in right_text or right_text in left_text

    @staticmethod
    def _stable(value: Any) -> str:
        return json.dumps(value, sort_keys=True, ensure_ascii=False)

    @staticmethod
    def _is_branch_support(fact: Json, condition: str) -> bool:
        if fact.get("kind") not in {"BranchCondition", "ValueCompute"}:
            return False
        semantic = fact.get("semantic") or {}
        candidate = str(semantic.get("predicate") or semantic.get("expression") or fact.get("rvalue") or "")
        if not candidate:
            return False
        normalized = lambda value: re.sub(r"\s+|[()]", "", value)
        return normalized(candidate) in normalized(condition) or normalized(condition) in normalized(candidate)

    def _instruction(
        self,
        fact: Json,
        origins: list[str],
        arena: ExpressionArena,
        function: SemanticFunction,
        support_facts: list[Json] | None = None,
        execution: _ExecutionViewContext | None = None,
    ) -> SemanticInstruction:
        self._instruction_counter += 1
        kind = str(fact.get("kind") or "OpaqueInstruction")
        semantic = fact.get("semantic") or {}
        op = self._instruction_op(kind)
        bindings = self._support_bindings(support_facts or [])
        execution_bindings = self._execution_support_bindings(support_facts or [], execution)
        if execution:
            execution_bindings = {
                **execution.external_bindings_for(fact),
                **execution_bindings,
            }
        execution_value = execution.execution_expression(fact) if execution else self._fact_execution_expression(fact)
        normalized_value = self._fact_normalized_expression(fact)
        # Support facts are fused out of the instruction stream, so their pure
        # definitions must be expanded on the execution side as well. Stateful
        # operations remain primary facts and therefore remain SSA boundaries.
        execution_expr = arena.build_with_bindings(execution_value, execution_bindings, origins)
        normalized_expr = arena.build_with_bindings(normalized_value, bindings, origins)
        execution_condition = arena.build_with_bindings(fact.get("condition"), execution_bindings, origins)
        normalized_condition = arena.build_with_bindings(
            semantic.get("condition_normalized") or fact.get("condition"), bindings, origins
        )
        location = None
        if kind in {"StateRead", "StateWrite", "Delete"}:
            location = self._location(function, semantic, fact, origins, arena)
        normalized_arguments = [
            expression_id for expression_id in (
                arena.build_with_bindings(item, bindings, origins) for item in self._fact_arguments(fact)
            ) if expression_id
        ]
        raw_execution_arguments = execution.execution_arguments(fact) if execution else []
        execution_arguments = [
            expression_id for expression_id in (
                arena.build_with_bindings(item, execution_bindings, origins)
                for item in (raw_execution_arguments or self._fact_arguments(fact))
            ) if expression_id
        ]
        data_objects: list[str] = []
        call_data = semantic.get("call_data")
        if isinstance(call_data, dict):
            data_objects.append(self._data_object(
                function,
                arena,
                call_data,
                origins=origins,
                origin_effects=_unique((fact.get("evidence") or {}).get("effects") or []),
                stmt_refs=_unique([
                    *(fact.get("stmt_refs") or []),
                    *((execution.supporting_stmt_refs(fact)) if execution else []),
                ]),
            ))
        return SemanticInstruction(
            instruction_id=f"ir_{self._instruction_counter}",
            op=op,
            result=str(fact.get("lvalue")) if fact.get("lvalue") and kind not in {"StateWrite", "Delete", "Require", "EventEmit"} else None,
            execution_expr=execution_expr,
            normalized_expr=normalized_expr,
            expression=normalized_expr,
            location=location,
            execution_arguments=execution_arguments,
            normalized_arguments=normalized_arguments,
            arguments=normalized_arguments,
            execution_condition=execution_condition,
            normalized_condition=normalized_condition,
            condition=normalized_condition,
            data_objects=data_objects,
            source_languages=_unique([fact.get("source_lang")]),
            origin_facts=origins,
            origin_effects=_unique((fact.get("evidence") or {}).get("effects") or []),
            stmt_refs=_unique([
                *(fact.get("stmt_refs") or []),
                *((execution.supporting_stmt_refs(fact)) if execution else []),
            ]),
            attrs={
                "fact_kind": kind,
                "semantic": semantic,
                "guard_conditions": fact.get("guard_conditions") or [],
                "order": fact.get("order") or {},
                "unresolved_reason": semantic.get("unresolved_reason") or fact.get("unresolved_reason"),
            },
        )

    @staticmethod
    def _support_bindings(support_facts: list[Json]) -> dict[str, Any]:
        bindings: dict[str, Any] = {}
        for support in support_facts:
            lvalue = support.get("lvalue")
            rvalue = support.get("rvalue") or (support.get("semantic") or {}).get("expression")
            if lvalue and rvalue is not None and str(lvalue) != str(rvalue):
                bindings[str(lvalue)] = rvalue
        return bindings

    @staticmethod
    def _execution_support_bindings(
        support_facts: list[Json],
        execution: _ExecutionViewContext | None,
    ) -> dict[str, Any]:
        """Build source-faithful bindings without recovered high-level data."""
        bindings: dict[str, Any] = {}
        materialized = execution.fact_results if execution else set()
        for support in support_facts:
            if support.get("source_lang") != "yul" or support.get("kind") != "ValueCompute":
                continue
            lvalue = support.get("lvalue")
            if not lvalue or str(lvalue) in materialized:
                continue
            semantic = support.get("semantic") or {}
            rvalue = (
                semantic.get("execution_expression")
                or support.get("rvalue")
                or semantic.get("expression")
            )
            if rvalue is not None and str(lvalue) != str(rvalue):
                bindings[str(lvalue)] = rvalue
        return bindings

    @staticmethod
    def _instruction_op(kind: str) -> str:
        return {
            "ValueAssign": "Assign", "ValueCompute": "Assign", "TypeConversion": "Assign",
            "ValuePhi": "Phi", "StorageLocationResolve": "LocationResolve",
            "AtomicOperation": "OpaqueInstruction", "Unknown": "OpaqueInstruction",
        }.get(kind, kind if kind else "OpaqueInstruction")

    @staticmethod
    def _fact_normalized_expression(fact: Json) -> Any:
        semantic = fact.get("semantic") or {}
        kind = str(fact.get("kind") or "")
        explicit = semantic.get("normalized_expression") or semantic.get("expression_normalized")
        if explicit is not None:
            return explicit
        if kind == "StateWrite":
            return semantic.get("value") or fact.get("rvalue")
        if kind == "Require":
            return semantic.get("guard") or semantic.get("condition") or fact.get("condition")
        if kind in {"ExternalCall", "LowLevelCall", "PrecompileCall", "StaticCall", "DelegateCall"}:
            return None
        return fact.get("rvalue") or semantic.get("expression") or semantic.get("value")

    @staticmethod
    def _fact_execution_expression(fact: Json) -> Any:
        semantic = fact.get("semantic") or {}
        return semantic.get("execution_expression") or fact.get("rvalue") or semantic.get("expression") or semantic.get("value")

    # Compatibility for callers which used the v1 helper name.
    _fact_expression = _fact_normalized_expression

    @staticmethod
    def _fact_arguments(fact: Json) -> list[Any]:
        semantic = fact.get("semantic") or {}
        for key in ("arguments", "args", "values", "inputs"):
            value = semantic.get(key)
            if isinstance(value, list):
                return value
        return []

    def _location(self, function: SemanticFunction, semantic: Json, fact: Json, origins: list[str], arena: ExpressionArena) -> str:
        raw = semantic.get("location") or {}
        access = raw.get("access") or semantic.get("access") or fact.get("lvalue") or fact.get("rvalue")
        base = raw.get("state_variable") or raw.get("base") or semantic.get("state_variable")
        keys = raw.get("keys") or semantic.get("keys") or []
        kind = str(raw.get("kind") or ("mapping" if keys else "state_variable"))
        normalized_kind = {
            "mapping": "MappingLocation", "indexed_storage": "IndexedLocation",
            "storage_member": "StructFieldLocation", "state_variable": "StateVariableLocation",
            "manual_slot": "RawStorageLocation", "raw_storage": "RawStorageLocation",
        }.get(kind, "RawStorageLocation" if not base else kind)
        key_exprs = [expr for expr in (arena.build(key, origins) for key in keys) if expr]
        slot_expr = arena.build(raw.get("slot") or semantic.get("slot"), origins)
        identity = self._stable({"kind": normalized_kind, "base": base, "keys": key_exprs, "slot": slot_expr, "access": access})
        for location in function.locations.values():
            if location.attrs.get("identity") == identity:
                location.origin_facts = _unique([*location.origin_facts, *origins])
                return location.location_id
        self._location_counter += 1
        location_id = f"loc_{self._location_counter}"
        function.locations[location_id] = LocationNode(
            location_id,
            normalized_kind,
            base=str(base) if base is not None else None,
            keys=key_exprs,
            member=raw.get("member"),
            slot=slot_expr,
            access=str(access) if access is not None else None,
            type_hint=raw.get("type"),
            origin_facts=origins,
            attrs={"identity": identity, "source_kind": kind},
        )
        return location_id

    def _data_object(
        self,
        function: SemanticFunction,
        arena: ExpressionArena,
        raw: Json,
        *,
        origins: list[str],
        origin_effects: list[str],
        stmt_refs: list[str],
    ) -> str:
        """Intern a semantic memory payload without copying query internals."""
        kind = str(raw.get("kind") or "MemorySlice")
        pointer = arena.build(raw.get("pointer"), origins, "memory_ptr")
        size = arena.build(raw.get("size"), origins, "memory_size")
        selector = arena.build(raw.get("selector"), origins, "abi_selector")
        values = [
            item for item in (arena.build(value, origins) for value in raw.get("values") or [])
            if item
        ]
        identity = self._stable({
            "kind": kind,
            "pointer": pointer,
            "size": size,
            "selector": selector,
            "values": values,
            "encoding": raw.get("encoding"),
            "complete": raw.get("complete"),
            "unresolved_reason": raw.get("unresolved_reason"),
        })
        for item in function.data_objects.values():
            if item.attrs.get("identity") != identity:
                continue
            item.origin_facts = _unique([*item.origin_facts, *origins])
            item.origin_effects = _unique([*item.origin_effects, *origin_effects])
            item.stmt_refs = _unique([*item.stmt_refs, *stmt_refs])
            return item.object_id
        self._data_object_counter += 1
        object_id = f"data_{self._data_object_counter}"
        function.data_objects[object_id] = DataObjectNode(
            object_id=object_id,
            kind=kind,
            pointer=pointer,
            size=size,
            selector=selector,
            values=values,
            encoding=raw.get("encoding"),
            complete=raw.get("complete"),
            unresolved_reason=raw.get("unresolved_reason"),
            origin_facts=list(origins),
            origin_effects=list(origin_effects),
            stmt_refs=_unique(stmt_refs),
            attrs={
                "identity": identity,
                **({"signature": raw.get("signature")} if raw.get("signature") else {}),
                **({"source_object": raw.get("source_object")} if raw.get("source_object") else {}),
            },
        )
        return object_id

    def _materialize_unlifted_memory_effects(
        self,
        function: SemanticFunction,
        execution: _ExecutionViewContext,
        facts: list[Json],
        arena: ExpressionArena,
    ) -> None:
        """Keep only memory operations which no reliable semantic fact covers.

        MemorySSA and SinkResolver remain analysis services.  Their supporting
        writes disappear from the core stream only when a fact explicitly cites
        the sink and its query explicitly cites those reaching definitions.
        """
        represented = {
            str(effect_id)
            for fact in facts
            for effect_id in (fact.get("evidence") or {}).get("effects") or []
        }
        for fact in facts:
            represented.update(
                str(effect.get("effect_id"))
                for effect in execution.supporting_effects(fact)
                if effect.get("effect_id")
            )

        keep_kinds = {"MemoryWrite", "MemoryRead", "MemoryCopy", "MemoryHash"}
        for effect in execution.effects.values():
            effect_id = str(effect.get("effect_id") or "")
            kind = str(effect.get("kind") or "")
            if not effect_id or kind not in keep_kinds or effect_id in represented:
                continue
            attrs = effect.get("attrs") or {}
            arguments: list[Any]
            result = attrs.get("result") or attrs.get("value_name")
            if kind == "MemoryWrite":
                arguments = [attrs.get("address"), attrs.get("value")]
                result = None
            elif kind == "MemoryRead":
                arguments = [attrs.get("address") or attrs.get("ptr")]
                result = result or attrs.get("value")
            elif kind == "MemoryHash":
                arguments = [attrs.get("ptr"), attrs.get("size")]
                result = result or attrs.get("value")
            else:
                arguments = attrs.get("args") or [
                    attrs.get("target") or attrs.get("destination"),
                    attrs.get("source"),
                    attrs.get("size"),
                ]
                result = None
            origins: list[str] = []
            argument_exprs = [
                expr for expr in (arena.build(value, origins) for value in arguments)
                if expr
            ]
            self._instruction_counter += 1
            instruction = SemanticInstruction(
                instruction_id=f"ir_{self._instruction_counter}",
                op=kind,
                result=str(result) if result not in {None, ""} else None,
                execution_arguments=argument_exprs,
                normalized_arguments=argument_exprs,
                arguments=argument_exprs,
                source_languages=["yul"],
                origin_effects=[effect_id],
                stmt_refs=_unique(effect.get("stmt_refs") or []),
                attrs={
                    "fact_kind": "LowLevelMemoryInstruction",
                    "unresolved_reason": "memory_effect_not_semantically_lifted",
                    "memory_op": attrs.get("write_kind") or attrs.get("op") or kind,
                },
            )
            block_id = self._effect_block(effect, function)
            function.blocks[block_id].instructions.append(instruction)

    @staticmethod
    def _effect_block(effect: Json, function: SemanticFunction) -> str:
        refs = set(str(item) for item in effect.get("stmt_refs") or [])
        node_id = (effect.get("attrs") or {}).get("cfg_node_id")
        candidates = [
            block_id for block_id, block in function.blocks.items()
            if refs.intersection(block.stmt_refs)
        ]
        if len(candidates) == 1:
            return candidates[0]
        if node_id is not None:
            for block_id, block in function.blocks.items():
                if str((block.attrs.get("source_attrs") or {}).get("node_id")) == str(node_id):
                    return block_id
        if candidates:
            return candidates[0]
        return SemanticIRBuilder._unplaced_block(function)

    @staticmethod
    def _fact_block(fact: Json, blocks: dict[str, Any]) -> str | None:
        candidates = [fact.get("anchor_cfg_node"), *(reversed(fact.get("cfg_nodes") or []))]
        for candidate in candidates:
            text = str(candidate or "")
            if text in blocks:
                return text
        return None

    @staticmethod
    def _unplaced_block(function: SemanticFunction) -> str:
        block_id = "bb_semantic_unplaced"
        if block_id not in function.blocks:
            function.blocks[block_id] = BasicBlock(block_id, "synthetic", attrs={"detached": True})
        return block_id

    def _attach_terminal_facts(
        self,
        function: SemanticFunction,
        facts: dict[str, list[Json]],
        arena: ExpressionArena,
        execution: _ExecutionViewContext | None = None,
    ) -> None:
        for block_id, candidates in facts.items():
            block = function.blocks[block_id]
            for fact in candidates:
                kind = str(fact.get("kind"))
                semantic = fact.get("semantic") or {}
                support_facts = [
                    function.fact_table[origin]
                    for origin in fact.get("_origins") or []
                    if origin != fact.get("fact_id") and origin in function.fact_table
                ]
                normalized_bindings = self._support_bindings(support_facts)
                execution_bindings = self._execution_support_bindings(support_facts, execution)
                if execution:
                    normalized_bindings = {
                        **execution.boundary_output_bindings(),
                        **normalized_bindings,
                    }
                    execution_bindings = {
                        **execution.external_bindings_for(fact),
                        **execution.boundary_output_bindings(),
                        **execution_bindings,
                    }
                values = self._fact_arguments(fact)
                if not values and semantic.get("value") is not None:
                    values = [semantic.get("value")]
                normalized_value_exprs = [
                    item for item in (
                        arena.build_with_bindings(value, normalized_bindings, fact["_origins"])
                        for value in values
                    ) if item
                ]
                execution_values = execution.terminal_values(fact) if execution else []
                execution_value_exprs = [
                    item for item in (
                        arena.build_with_bindings(value, execution_bindings, fact["_origins"])
                        for value in (execution_values or values)
                    ) if item
                ]
                if block.terminator.kind == kind or block.terminator.kind in {"Fallthrough", "Goto"}:
                    block.terminator.kind = kind
                    block.terminator.execution_values = _unique([
                        *block.terminator.execution_values, *execution_value_exprs
                    ])
                    block.terminator.normalized_values = _unique([
                        *block.terminator.normalized_values, *normalized_value_exprs
                    ])
                    block.terminator.values = list(block.terminator.normalized_values)
                    block.terminator.origin_facts = _unique([*block.terminator.origin_facts, *fact["_origins"]])
                    support_refs = execution.supporting_stmt_refs(fact) if execution else []
                    block.terminator.stmt_refs = _unique([
                        *block.terminator.stmt_refs,
                        *(fact.get("stmt_refs") or []),
                        *support_refs,
                    ])
                    block.terminator.attrs["semantic"] = semantic
                    payload_key = "return_payload" if kind == "Return" else "revert_payload"
                    payload = semantic.get(payload_key)
                    if isinstance(payload, dict):
                        block.terminator.data_objects = _unique([
                            *block.terminator.data_objects,
                            self._data_object(
                                function,
                                arena,
                                payload,
                                origins=fact["_origins"],
                                origin_effects=_unique((fact.get("evidence") or {}).get("effects") or []),
                                stmt_refs=_unique([*(fact.get("stmt_refs") or []), *support_refs]),
                            ),
                        ])
                else:
                    # A terminal fact anchored away from the source terminal is
                    # retained as an explicit instruction instead of changing CFG.
                    block.instructions.append(
                        self._instruction(fact, fact["_origins"], arena, function, execution=execution)
                    )

    def _materialize_boundary_output_bridges(
        self,
        function: SemanticFunction,
        execution: _ExecutionViewContext,
        arena: ExpressionArena,
    ) -> None:
        """Join Yul SSA writes back into Solidity-declared function variables."""
        existing_results = {
            instruction.result
            for block in function.blocks.values()
            for instruction in block.instructions
            if instruction.result
        }
        for base, records in execution.boundary_output_versions.items():
            versions = _unique(record.get("version") for record in records)
            if not versions or base in existing_results:
                continue
            effects = _unique(record.get("effect_id") for record in records)
            effect = next(
                (execution.effects[effect_id] for effect_id in effects if effect_id in execution.effects),
                None,
            )
            if effect is None:
                continue
            block_id = self._effect_block(effect, function)
            arguments = [arena.build(version, []) for version in versions]
            arguments = [item for item in arguments if item]
            self._instruction_counter += 1
            instruction = SemanticInstruction(
                instruction_id=f"ir_{self._instruction_counter}",
                op="Phi" if len(versions) > 1 else "Assign",
                result=base,
                execution_expr=arguments[0] if len(arguments) == 1 else None,
                normalized_expr=arguments[0] if len(arguments) == 1 else None,
                execution_arguments=arguments if len(arguments) > 1 else [],
                normalized_arguments=arguments if len(arguments) > 1 else [],
                source_languages=["yul", "solidity"],
                origin_effects=effects,
                stmt_refs=_unique(
                    ref
                    for effect_id in effects
                    for ref in (execution.effects.get(effect_id) or {}).get("stmt_refs") or []
                ),
                attrs={
                    "fact_kind": "AssemblyBoundaryOutput",
                    "source_variable": base,
                    "path_versions": records,
                    "reason": "yul_write_to_solidity_declared_variable",
                },
            )
            function.blocks[block_id].instructions.append(instruction)
            existing_results.add(base)

    @staticmethod
    def _prune_terminal_edges(function: SemanticFunction) -> None:
        terminals = {
            block_id for block_id, block in function.blocks.items()
            if block.terminator.kind in _TERMINALS
        }
        function.edges = [edge for edge in function.edges if edge.source not in terminals]
        function.rebuild_cfg_links()

    def _attach_branch_fact_origins(
        self,
        function: SemanticFunction,
        facts: list[Json],
        consumed: set[str],
        arena: ExpressionArena,
        execution: _ExecutionViewContext | None = None,
    ) -> None:
        for block in function.blocks.values():
            if block.terminator.kind != "Branch":
                continue
            related = [
                fact for fact in facts
                if str(fact.get("fact_id") or "") in consumed
                and block.block_id in [str(item) for item in fact.get("cfg_nodes") or []]
                and fact.get("kind") in {"BranchCondition", "ValueCompute"}
            ]
            origins = _unique(fact.get("fact_id") for fact in related)
            block.terminator.origin_facts = _unique([*block.terminator.origin_facts, *origins])
            if related:
                normalized = self._fact_normalized_expression(related[-1])
                normalized_condition = arena.build(normalized, origins)
                if normalized_condition:
                    block.terminator.normalized_condition = normalized_condition
                    block.terminator.condition = normalized_condition
                execution_bindings = execution.external_bindings_for(related[-1]) if execution else {}
                execution_condition = arena.build_with_bindings(
                    related[-1].get("rvalue"), execution_bindings, origins
                )
                if execution_condition:
                    block.terminator.execution_condition = execution_condition

    @staticmethod
    def _sort_instructions(function: SemanticFunction, facts: list[Json]) -> None:
        order = {
            str(fact.get("fact_id")): (
                int((fact.get("order") or {}).get("operation_order") or 0),
                int((fact.get("order") or {}).get("atomic_sequence") or 0),
            )
            for fact in facts
        }
        for block in function.blocks.values():
            block.instructions.sort(key=lambda instruction: min(
                (order.get(origin, (10**9, 10**9)) for origin in instruction.origin_facts),
                default=(10**9, 10**9),
            ))

    def _rebuild_def_use(
        self,
        function: SemanticFunction,
        *,
        aliases: dict[str, str] | None = None,
    ) -> None:
        """Build a function-local index from explicit SSA definitions and CFG flow.

        Slither/Yul SSA names are matched globally in a second pass, so CFG block
        serialization order cannot turn a later Phi into a synthetic input.  CFG
        reaching definitions are only used for repeated non-SSA names.  Ambiguous
        merges are reported rather than silently selecting one predecessor.
        """
        function.values.clear()
        definitions: dict[str, list[ValueRecord]] = defaultdict(list)
        definition_by_instruction: dict[str, ValueRecord] = {}

        # Pass 1: collect every explicit definition before resolving any use.
        for block in function.blocks.values():
            for instruction in block.instructions:
                if instruction.result:
                    version = len(definitions[instruction.result]) + 1
                    value_id = f"value:{instruction.result}#{version}"
                    record = ValueRecord(
                        value_id,
                        instruction.result,
                        instruction.instruction_id,
                        kind="phi" if instruction.op == "Phi" else "ssa",
                    )
                    definitions[instruction.result].append(record)
                    function.values[value_id] = record
                    definition_by_instruction[instruction.instruction_id] = record
                    instruction.result_value = value_id

        reaching_in = self._reaching_definitions(function, definition_by_instruction)
        aliases = aliases or {}

        def resolve(name: str, use: str, state: dict[str, set[str]]) -> ValueRecord:
            exact = definitions.get(name) or []
            if len(exact) == 1:
                return exact[0]
            candidate_ids = state.get(name, set()) if len(exact) > 1 else set()
            candidates = [
                definition_by_instruction[item]
                for item in sorted(candidate_ids)
                if item in definition_by_instruction
            ]
            if len(candidates) == 1:
                return candidates[0]

            base = aliases.get(name)
            base_defs = definitions.get(base or "") or []
            if not exact and len(base_defs) == 1:
                record = base_defs[0]
                if name not in record.aliases:
                    record.aliases.append(name)
                return record
            if not exact and base and not base_defs:
                value_id = f"input:{base}"
                record = function.values.setdefault(
                    value_id,
                    ValueRecord(value_id, base, kind="input"),
                )
                if name not in record.aliases:
                    record.aliases.append(name)
                return record

            if len(candidates) > 1:
                value_id = f"unresolved:{name}@{use}"
                record = function.values.setdefault(
                    value_id,
                    ValueRecord(
                        value_id,
                        name,
                        definition_candidates=[item.definition for item in candidates if item.definition],
                        kind="unresolved_merge",
                    ),
                )
                function.diagnostics.append({
                    "kind": "ambiguous_reaching_definitions",
                    "name": name,
                    "use": use,
                    "definition_candidates": record.definition_candidates,
                })
                return record

            value_id = f"input:{name}"
            return function.values.setdefault(
                value_id,
                ValueRecord(value_id, name, kind="input"),
            )

        def link_expression_refs(
            refs: Iterable[str],
            use: str,
            state: dict[str, set[str]],
        ) -> None:
            for expr_id in refs:
                for name in self._expression_variables(function, expr_id):
                    record = resolve(name, use, state)
                    if use not in record.uses:
                        record.uses.append(use)

        # Pass 2: resolve uses against exact SSA definitions or CFG reaching sets.
        for block_id, block in function.blocks.items():
            state = {name: set(items) for name, items in reaching_in.get(block_id, {}).items()}
            for instruction in block.instructions:
                expression_refs = list(instruction.expression_refs())
                location = function.locations.get(instruction.location or "")
                if location:
                    expression_refs.extend(location.keys)
                    if location.slot:
                        expression_refs.append(location.slot)
                for object_id in instruction.data_objects:
                    data_object = function.data_objects.get(object_id)
                    if data_object:
                        expression_refs.extend(data_object.expression_refs())
                link_expression_refs(expression_refs, instruction.instruction_id, state)
                if instruction.result:
                    state[instruction.result] = {instruction.instruction_id}

            terminator_refs = list(block.terminator.expression_refs())
            for object_id in block.terminator.data_objects:
                data_object = function.data_objects.get(object_id)
                if data_object:
                    terminator_refs.extend(data_object.expression_refs())
            link_expression_refs(
                terminator_refs,
                f"terminator:{block.block_id}",
                state,
            )

    @staticmethod
    def _reaching_definitions(
        function: SemanticFunction,
        definition_by_instruction: dict[str, ValueRecord],
    ) -> dict[str, dict[str, set[str]]]:
        """Compute may-reaching definitions for repeated, non-SSA names."""
        generated: dict[str, dict[str, set[str]]] = {}
        for block_id, block in function.blocks.items():
            state: dict[str, set[str]] = {}
            for instruction in block.instructions:
                record = definition_by_instruction.get(instruction.instruction_id)
                if record:
                    state[record.name] = {instruction.instruction_id}
            generated[block_id] = state

        incoming: dict[str, dict[str, set[str]]] = {block_id: {} for block_id in function.blocks}
        outgoing: dict[str, dict[str, set[str]]] = {block_id: {} for block_id in function.blocks}
        changed = True
        while changed:
            changed = False
            for block_id, block in function.blocks.items():
                merged: dict[str, set[str]] = defaultdict(set)
                for predecessor in block.predecessors:
                    for name, values in outgoing.get(predecessor, {}).items():
                        merged[name].update(values)
                new_in = {name: set(values) for name, values in merged.items()}
                new_out = {name: set(values) for name, values in new_in.items()}
                for name, values in generated.get(block_id, {}).items():
                    new_out[name] = set(values)
                if new_in != incoming[block_id] or new_out != outgoing[block_id]:
                    incoming[block_id] = new_in
                    outgoing[block_id] = new_out
                    changed = True
        return incoming

    def rebuild_def_use(self, function: SemanticFunction) -> None:
        """Rebuild Definition -> Uses after an external rewrite pass."""
        self._rebuild_def_use(function, aliases=function.value_aliases)

    def _expression_variables(self, function: SemanticFunction, expr_id: str) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        def visit(current: str) -> None:
            if current in seen:
                return
            seen.add(current)
            node = function.expressions.get(current)
            if not node:
                return
            if node.kind == "Variable" and node.name:
                out.append(node.name)
            for operand in node.operands:
                visit(operand)
        visit(expr_id)
        return _unique(out)

    @staticmethod
    def _validate(function: SemanticFunction) -> None:
        instruction_ids: set[str] = set()
        represented_facts = set(function.unplaced_facts)
        represented_facts.update(
            fact_id
            for group in function.fact_groups
            for fact_id in group.support_facts
        )
        for block in function.blocks.values():
            represented_facts.update(block.terminator.origin_facts)
            terminator_refs = _unique([
                *block.terminator.execution_expression_refs(),
                *block.terminator.normalized_expression_refs(),
            ])
            for expr_id in terminator_refs:
                if expr_id not in function.expressions:
                    function.diagnostics.append({"kind": "missing_terminator_expression", "block_id": block.block_id, "expr_id": expr_id})
            for object_id in block.terminator.data_objects:
                if object_id not in function.data_objects:
                    function.diagnostics.append({"kind": "missing_terminator_data_object", "block_id": block.block_id, "object_id": object_id})
            for target in block.terminator.targets:
                if target not in function.blocks:
                    function.diagnostics.append({"kind": "missing_terminator_target", "block_id": block.block_id, "target": target})
            for instruction in block.instructions:
                represented_facts.update(instruction.origin_facts)
                if instruction.instruction_id in instruction_ids:
                    function.diagnostics.append({"kind": "duplicate_instruction_id", "instruction_id": instruction.instruction_id})
                instruction_ids.add(instruction.instruction_id)
                instruction_refs = _unique([
                    *instruction.execution_expression_refs(),
                    *instruction.normalized_expression_refs(),
                ])
                for expr_id in instruction_refs:
                    if expr_id not in function.expressions:
                        function.diagnostics.append({"kind": "missing_expression", "instruction_id": instruction.instruction_id, "expr_id": expr_id})
                if instruction.location and instruction.location not in function.locations:
                    function.diagnostics.append({"kind": "missing_location", "instruction_id": instruction.instruction_id, "location_id": instruction.location})
                for object_id in instruction.data_objects:
                    if object_id not in function.data_objects:
                        function.diagnostics.append({"kind": "missing_instruction_data_object", "instruction_id": instruction.instruction_id, "object_id": object_id})
        for expression in function.expressions.values():
            for operand in expression.operands:
                if operand not in function.expressions:
                    function.diagnostics.append({"kind": "missing_expression_operand", "expr_id": expression.expr_id, "operand": operand})
        for location in function.locations.values():
            for expr_id in [*location.keys, *([location.slot] if location.slot else [])]:
                if expr_id not in function.expressions:
                    function.diagnostics.append({"kind": "missing_location_expression", "location_id": location.location_id, "expr_id": expr_id})
        for data_object in function.data_objects.values():
            for expr_id in data_object.expression_refs():
                if expr_id not in function.expressions:
                    function.diagnostics.append({"kind": "missing_data_object_expression", "object_id": data_object.object_id, "expr_id": expr_id})
        for edge in function.edges:
            if edge.source not in function.blocks or edge.target not in function.blocks:
                function.diagnostics.append({"kind": "dangling_edge", "source": edge.source, "target": edge.target})
        missing_facts = sorted(set(function.fact_table) - represented_facts)
        if missing_facts:
            function.diagnostics.append({"kind": "unmaterialized_facts", "fact_ids": missing_facts})


def build_semantic_ir_program(functions: list[Any], fact_payload: Json | None = None, *, source: str | None = None) -> SemanticProgram:
    if fact_payload is None:
        from semantic_fact import build_function_level_semantic_fact_payload
        fact_payload = build_function_level_semantic_fact_payload(functions, source=source)
    return SemanticIRBuilder().build(functions, fact_payload, source=source)
