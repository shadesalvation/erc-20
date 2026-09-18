"""废弃：旧 Semantic IR 对象模型；现行 SFIR pipeline 不使用。"""
from __future__ import annotations

import json
from collections import defaultdict
from copy import deepcopy
from typing import Any

from .model import BasicBlock, CFGEdge, SemanticFunction, SemanticLocation, SemanticOperation, SemanticProgram, Terminator

Json = dict[str, Any]


def _dict(value: Any) -> Json:
    if isinstance(value, dict):
        return value
    method = getattr(value, "to_dict", None)
    return method() if callable(method) else {}


class SemanticIRBuilder:
    """Build a program-shaped object graph without reinterpreting Fact meaning.

    Each input Fact becomes exactly one ``SemanticOperation``.  Support facts
    remain operations, so later deobfuscation can explicitly decide whether a
    grouping or rewrite is justified.  CFG placement uses only a fact anchor,
    a unique fact CFG node, or an unambiguous CFG dominator among the fact's
    own candidate nodes; otherwise the operation is retained as unplaced.
    """

    def build(self, functions: list[Any], fact_payload: Json, *, source: str | None = None) -> SemanticProgram:
        facts_by_function: dict[str, list[Json]] = defaultdict(list)
        for fact in fact_payload.get("facts") or []:
            if isinstance(fact, dict) and fact.get("function_id"):
                facts_by_function[str(fact["function_id"])].append(deepcopy(fact))
        semantic_functions = []
        for raw_function in functions:
            raw = _dict(raw_function)
            function_id = str(getattr(raw_function, "function_id", "") or raw.get("function_id") or "")
            semantic_functions.append(self._build_function(raw_function, facts_by_function.get(function_id, [])))
        return SemanticProgram(source or fact_payload.get("source"), semantic_functions)

    def _build_function(self, raw_function: Any, facts: list[Json]) -> SemanticFunction:
        raw = _dict(raw_function)
        control = _dict(getattr(raw_function, "control", None)) or raw.get("control") or {}
        function = SemanticFunction(
            function_id=str(getattr(raw_function, "function_id", "") or raw.get("function_id") or ""),
            contract=str(getattr(raw_function, "contract", "") or raw.get("contract") or ""),
            function=str(getattr(raw_function, "function", "") or raw.get("function") or ""),
            signature=str(getattr(raw_function, "signature", "") or raw.get("signature") or ""),
            source_statements=[_dict(item) for item in (getattr(raw_function, "source_statements", None) or raw.get("source_statements") or [])],
        )
        self._materialize_cfg(function, control)
        reachability = self._reachability(function)
        for fact in facts:
            fact_id = str(fact.get("fact_id") or "")
            if not fact_id:
                function.diagnostics.append({"kind": "invalid_fact", "reason": "missing fact_id", "fact": fact})
                continue
            location = self._location_for_fact(function, fact)
            operation = SemanticOperation.from_fact(fact, location=location)
            block_id = self._fact_block(fact, function, reachability)
            if block_id is None:
                function.unplaced_operations.append(operation)
                function.diagnostics.append({
                    "kind": "unplaced_operation",
                    "fact_id": fact_id,
                    "reason": "no unique CFG placement evidence",
                    "cfg_nodes": operation.cfg_nodes,
                })
                continue
            function.blocks[block_id].operations.append(operation)
        for block in function.blocks.values():
            block.operations.sort(key=self._operation_order)
        self._materialize_terminators(function, control)
        function.rebuild_cfg_links()
        function.rebuild_indexes()
        return function

    @staticmethod
    def _materialize_cfg(function: SemanticFunction, control: Json) -> None:
        for raw_block in control.get("blocks") or []:
            if not isinstance(raw_block, dict) or not raw_block.get("block_id"):
                continue
            block_id = str(raw_block["block_id"])
            function.blocks[block_id] = BasicBlock(
                block_id=block_id,
                kind=str(raw_block.get("kind") or "unknown"),
                source_statements=[str(item) for item in raw_block.get("stmts") or []],
                attrs=deepcopy(dict(raw_block.get("attrs") or {})),
            )
        for raw_edge in control.get("edges") or []:
            if not isinstance(raw_edge, dict) or not raw_edge.get("from") or not raw_edge.get("to"):
                continue
            kind = str(raw_edge.get("kind") or "next")
            function.edges.append(CFGEdge(str(raw_edge["from"]), str(raw_edge["to"]), kind, SemanticIRBuilder._edge_predicate(kind)))
        function.rebuild_cfg_links()

    @staticmethod
    def _edge_predicate(kind: str) -> str | None:
        return kind.split(":", 1)[1].strip() if ":" in kind else None

    @staticmethod
    def _reachability(function: SemanticFunction) -> dict[str, set[str]]:
        result: dict[str, set[str]] = {}
        for source in function.blocks:
            visited: set[str] = set(); worklist = [source]
            while worklist:
                current = worklist.pop()
                for target in function.blocks[current].successors:
                    if target not in visited:
                        visited.add(target); worklist.append(target)
            result[source] = visited | {source}
        return result

    @staticmethod
    def _fact_block(fact: Json, function: SemanticFunction, reachability: dict[str, set[str]]) -> str | None:
        anchor = str(fact.get("anchor_cfg_node") or "")
        if anchor in function.blocks:
            return anchor
        candidates = list(dict.fromkeys(str(item) for item in fact.get("cfg_nodes") or [] if str(item) in function.blocks))
        if len(candidates) == 1:
            return candidates[0]
        # Fact CFG nodes can contain the condition and merge node. The unique
        # node that reaches every candidate is the only CFG-backed placement.
        dominators = [candidate for candidate in candidates if all(other in reachability.get(candidate, set()) for other in candidates)]
        return dominators[0] if len(dominators) == 1 else None

    @staticmethod
    def _operation_order(operation: SemanticOperation) -> tuple[int, str]:
        order = operation.source_fact.get("order") or {}
        return (int(order.get("operation_order") or 0), operation.fact_id)

    def _location_for_fact(self, function: SemanticFunction, fact: Json) -> str | None:
        location = (fact.get("semantic") or {}).get("location")
        if not isinstance(location, dict):
            return None
        key = json.dumps(location, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for current in function.locations.values():
            current_key = json.dumps(current.source_location, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if current_key == key:
                if str(fact["fact_id"]) not in current.origin_fact_ids:
                    current.origin_fact_ids.append(str(fact["fact_id"]))
                return current.location_id
        location_id = f"loc_{len(function.locations) + 1}"
        function.locations[location_id] = SemanticLocation(location_id, deepcopy(location), [str(fact["fact_id"])])
        return location_id

    @staticmethod
    def _materialize_terminators(function: SemanticFunction, control: Json) -> None:
        raw_blocks = {
            str(raw.get("block_id")): raw for raw in control.get("blocks") or []
            if isinstance(raw, dict) and raw.get("block_id")
        }
        for block in function.blocks.values():
            raw_terminator = deepcopy((raw_blocks.get(block.block_id) or {}).get("terminator") or {})
            kind = str(raw_terminator.get("kind") or "")
            if kind == "Branch":
                block.terminator = Terminator("Branch", raw_terminator.get("condition"), list(block.successors), raw_terminator)
            elif kind in {"Return", "Revert", "Stop"}:
                # CFG terminal metadata is direct ControlBuilder evidence. It
                # remains distinct from any Fact that may additionally exist.
                block.terminator = Terminator(kind, targets=[], source_terminator=raw_terminator)
            elif len(block.successors) == 1:
                block.terminator = Terminator("Goto", targets=list(block.successors), source_terminator=raw_terminator)
            elif not block.successors:
                block.terminator = Terminator("Unresolved", source_terminator=raw_terminator)
            else:
                block.terminator = Terminator("Unresolved", targets=list(block.successors), source_terminator=raw_terminator)


def build_semantic_ir_program(functions: list[Any], fact_payload: Json, *, source: str | None = None) -> SemanticProgram:
    return SemanticIRBuilder().build(functions, fact_payload, source=source)
