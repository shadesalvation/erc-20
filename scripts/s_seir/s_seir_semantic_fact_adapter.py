#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any


@dataclass
class SemanticFact:
    fact_id: str
    kind: str
    source_lang: str
    origin: str
    function: str
    contract: str | None = None
    signature: str | None = None
    stmt_refs: list[str] = field(default_factory=list)
    cfg_nodes: list[str] = field(default_factory=list)
    condition: str | None = None
    lvalue: Any = None
    rvalue: Any = None
    reads: list[Any] = field(default_factory=list)
    writes: list[Any] = field(default_factory=list)
    semantic: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


class FactIdAllocator:
    def __init__(self, prefix: str = "fact") -> None:
        self.prefix = prefix
        self.counter = 0

    def new(self) -> str:
        self.counter += 1
        return f"{self.prefix}_{self.counter}"


class SSeirFactAdapter:
    """Convert existing S-SEIR semantic output into unified SemanticFact rows.

    This adapter is intentionally a projection layer. It does not redo
    MemorySSA, SinkResolver, slot recovery, event recovery, or call lifting.
    """

    def __init__(self) -> None:
        self.ids = FactIdAllocator()

    def build_from_payload(self, payload: Any) -> dict[str, Any]:
        functions = extract_function_dicts(payload)
        facts = [fact.to_dict() for fn in functions for fact in self.function_facts(fn)]
        return {
            "schema": "s-seir-semantic-facts/v1",
            "source": payload.get("source") if isinstance(payload, dict) else None,
            "function_count": len(functions),
            "facts": facts,
        }

    def function_facts(self, fn: dict[str, Any]) -> list[SemanticFact]:
        out: list[SemanticFact] = []
        stmt_lang = self.statement_languages(fn)
        effect_by_id = {item.get("effect_id"): item for item in fn.get("effects") or [] if isinstance(item, dict)}
        for overlay in fn.get("semantic_overlays") or []:
            if not isinstance(overlay, dict):
                continue
            out.extend(self.overlay_facts(fn, overlay, stmt_lang, effect_by_id))
        return out

    def overlay_facts(
        self,
        fn: dict[str, Any],
        overlay: dict[str, Any],
        stmt_lang: dict[str, str],
        effect_by_id: dict[str, dict[str, Any]],
    ) -> list[SemanticFact]:
        overlay = self.overlay_with_effect_refs(overlay, effect_by_id)
        kind = str(overlay.get("kind") or "")
        attrs = overlay.get("attrs") or {}
        if kind in {"PathConditionedStorageRead", "PathConditionedStorageWrite"}:
            return [
                self.storage_candidate_fact(fn, overlay, candidate, stmt_lang, effect_by_id)
                for candidate in attrs.get("candidates") or []
                if isinstance(candidate, dict)
            ]
        if kind == "PathConditionedEventEmit":
            return [
                self.event_candidate_fact(fn, overlay, candidate, stmt_lang)
                for candidate in attrs.get("candidates") or []
                if isinstance(candidate, dict)
            ]
        if kind in {"PathConditionedCustomErrorRevert", "PathConditionedRevert"}:
            return [
                self.revert_candidate_fact(fn, overlay, candidate, stmt_lang)
                for candidate in attrs.get("candidates") or []
                if isinstance(candidate, dict)
            ]
        if kind in {"PathConditionedPrecompileCall", "PathConditionedExternalCall", "PathConditionedLowLevelCall"}:
            return [
                self.call_candidate_fact(fn, overlay, candidate, stmt_lang)
                for candidate in attrs.get("candidates") or []
                if isinstance(candidate, dict)
            ]
        fact = self.plain_overlay_fact(fn, overlay, stmt_lang, effect_by_id)
        return [fact] if fact else []

    def plain_overlay_fact(
        self,
        fn: dict[str, Any],
        overlay: dict[str, Any],
        stmt_lang: dict[str, str],
        effect_by_id: dict[str, dict[str, Any]],
    ) -> SemanticFact | None:
        kind = str(overlay.get("kind") or "")
        attrs = overlay.get("attrs") or {}
        if kind in {"StateVariableRead", "MappingRead"}:
            access = attrs.get("access") or attrs.get("slot")
            target = attrs.get("target")
            return self.fact(fn, overlay, stmt_lang, "StateRead", lvalue=target, rvalue=access, reads=[access], semantic=self.state_semantic(attrs, "state_read"))
        if kind in {"StateVariableWrite", "MappingWrite"}:
            access = attrs.get("access") or attrs.get("slot")
            value = attrs.get("value") or attrs.get("value_yul")
            return self.fact(
                fn,
                overlay,
                stmt_lang,
                "StateWrite",
                lvalue=access,
                rvalue=value,
                reads=self.reads_for_value(value, attrs),
                writes=[access],
                semantic=self.state_semantic(attrs, "state_write"),
            )
        if kind == "EventEmit":
            return self.fact(fn, overlay, stmt_lang, "EventEmit", reads=flat_list(attrs.get("args")), semantic={
                "event": attrs.get("event") or attrs.get("event_name"),
                "signature": attrs.get("signature"),
                "args": attrs.get("args"),
            })
        if kind == "RequireOverlay":
            condition = attrs.get("condition") or attrs.get("nearest_condition") or attrs.get("require_like")
            return self.fact(fn, overlay, stmt_lang, "Require", condition=condition, reads=[condition], semantic={
                "condition": condition,
                "on_fail": "revert",
                "error": attrs.get("error") or attrs.get("custom_error"),
            })
        if kind in {"CustomErrorRevert", "RawRevertBytes", "RevertOverlay"}:
            return self.fact(fn, overlay, stmt_lang, "Revert", reads=flat_list(attrs.get("args")), semantic={
                "operation": "revert",
                "error": attrs.get("error") or attrs.get("custom_error"),
                "payload": attrs.get("payload") or attrs.get("revert_payload"),
                "source_object": attrs.get("source_object"),
            })
        if kind in {"ExternalCall", "LowLevelCall", "StaticCallOverlay", "DelegateCallOverlay"}:
            return self.call_fact(fn, overlay, stmt_lang, "ExternalCall")
        if kind == "PrecompileCall":
            return self.call_fact(fn, overlay, stmt_lang, "PrecompileCall")
        if kind == "InternalCall":
            return self.call_fact(fn, overlay, stmt_lang, "InternalCall")
        if kind == "ReturnValue":
            value = attrs.get("value") or attrs.get("return_value") or attrs.get("expression")
            return self.fact(fn, overlay, stmt_lang, "Return", rvalue=value, reads=[value], semantic={
                "operation": "return",
                "value": value,
                "target": attrs.get("target"),
            })
        if kind == "MemoryRegionAllocate":
            return self.fact(fn, overlay, stmt_lang, "MemoryAllocate", lvalue=attrs.get("base"), rvalue=attrs.get("new_free_pointer"), reads=[attrs.get("base")], writes=[attrs.get("new_free_pointer")], semantic={
                "base": attrs.get("base"),
                "new_free_pointer": attrs.get("new_free_pointer"),
                "stored_values": attrs.get("stored_values"),
            })
        if kind == "MemoryArrayConstruction":
            return self.fact(fn, overlay, stmt_lang, "MemoryObjectConstruct", lvalue=attrs.get("result"), rvalue=attrs.get("allocation_source"), writes=[attrs.get("result")], semantic=pick(attrs, (
                "result", "array_type", "element_type", "length_expr", "length_expr_normalized", "element_writes", "free_memory_pointer_update",
            )))
        if kind in {"StructMemoryMutation", "StructInitializationFragment", "MemoryRegionWrite", "CursorBasedMemoryWrite"}:
            target = attrs.get("target") or attrs.get("region_base") or attrs.get("object")
            value = attrs.get("value") or attrs.get("value_normalized") or attrs.get("fields")
            return self.fact(fn, overlay, stmt_lang, "MemoryObjectWrite", lvalue=target, rvalue=value, reads=flat_list(value), writes=[target], semantic=attrs)
        if kind in {"ExpressionNormalization", "EvaluationStep"}:
            target = attrs.get("target") or attrs.get("temp")
            value = attrs.get("value") or attrs.get("expression") or attrs.get("solidity_like")
            return self.fact(fn, overlay, stmt_lang, "ValueCompute", lvalue=target, rvalue=value, reads=rough_reads(value), writes=[target], semantic=pick(attrs, (
                "target", "temp", "value", "expression", "solidity_like", "call", "raw_args", "evaluated_args",
            )))
        return None

    def storage_candidate_fact(
        self,
        fn: dict[str, Any],
        overlay: dict[str, Any],
        candidate: dict[str, Any],
        stmt_lang: dict[str, str],
        effect_by_id: dict[str, dict[str, Any]],
    ) -> SemanticFact:
        attrs = overlay.get("attrs") or {}
        is_write = str(overlay.get("kind")) == "PathConditionedStorageWrite"
        access = candidate.get("access") or attrs.get("slot")
        value = candidate.get("value") or attrs.get("value") or attrs.get("value_yul")
        kind = "StateWrite" if is_write else "StateRead"
        return self.fact(
            fn,
            overlay,
            stmt_lang,
            kind,
            condition=candidate.get("condition"),
            lvalue=access if is_write else attrs.get("target"),
            rvalue=value if is_write else access,
            reads=self.reads_for_value(value, candidate) if is_write else [access],
            writes=[access] if is_write else [],
            semantic={
                **self.state_semantic(candidate, "state_write" if is_write else "state_read"),
                "candidate_status": candidate.get("status"),
                "unresolved_reason": candidate.get("unresolved_reason") or candidate.get("reason"),
            },
            extra_evidence={"candidate": clean_dict(candidate)},
        )

    def event_candidate_fact(self, fn: dict[str, Any], overlay: dict[str, Any], candidate: dict[str, Any], stmt_lang: dict[str, str]) -> SemanticFact:
        return self.fact(fn, overlay, stmt_lang, "EventEmit", condition=candidate.get("condition"), reads=flat_list(candidate.get("args")), semantic={
            "event": candidate.get("event") or (overlay.get("attrs") or {}).get("event"),
            "args": candidate.get("args"),
            "candidate_status": candidate.get("status"),
            "unresolved_reason": candidate.get("unresolved_reason") or candidate.get("reason"),
        }, extra_evidence={"candidate": clean_dict(candidate)})

    def revert_candidate_fact(self, fn: dict[str, Any], overlay: dict[str, Any], candidate: dict[str, Any], stmt_lang: dict[str, str]) -> SemanticFact:
        return self.fact(fn, overlay, stmt_lang, "Revert", condition=candidate.get("condition"), reads=flat_list(candidate.get("args")), semantic={
            "operation": "revert",
            "error": candidate.get("error") or candidate.get("custom_error") or candidate.get("selector_signature"),
            "payload": candidate.get("payload") or candidate.get("revert_payload"),
            "candidate_status": candidate.get("status"),
            "unresolved_reason": candidate.get("unresolved_reason") or candidate.get("reason"),
        }, extra_evidence={"candidate": clean_dict(candidate)})

    def call_candidate_fact(self, fn: dict[str, Any], overlay: dict[str, Any], candidate: dict[str, Any], stmt_lang: dict[str, str]) -> SemanticFact:
        base_kind = "PrecompileCall" if "Precompile" in str(overlay.get("kind")) else "ExternalCall"
        return self.fact(fn, overlay, stmt_lang, base_kind, condition=candidate.get("condition"), reads=flat_list(candidate.get("arguments") or candidate.get("args")), semantic={
            "target": candidate.get("target_solidity") or candidate.get("target") or (overlay.get("attrs") or {}).get("target"),
            "call_kind": candidate.get("call_kind") or candidate.get("op") or (overlay.get("attrs") or {}).get("op"),
            "selector": candidate.get("selector"),
            "arguments": candidate.get("arguments") or candidate.get("args"),
            "precompile": candidate.get("precompile") or (overlay.get("attrs") or {}).get("precompile"),
            "candidate_status": candidate.get("status"),
            "unresolved_reason": candidate.get("unresolved_reason") or candidate.get("reason"),
        }, extra_evidence={"candidate": clean_dict(candidate)})

    def call_fact(self, fn: dict[str, Any], overlay: dict[str, Any], stmt_lang: dict[str, str], fact_kind: str) -> SemanticFact:
        attrs = overlay.get("attrs") or {}
        return self.fact(fn, overlay, stmt_lang, fact_kind, reads=flat_list(attrs.get("arguments") or attrs.get("args")), semantic={
            "target": attrs.get("target_solidity") or attrs.get("target"),
            "call_kind": attrs.get("call_kind") or attrs.get("op") or overlay.get("kind"),
            "selector": attrs.get("selector"),
            "arguments": attrs.get("arguments") or attrs.get("args"),
            "value": attrs.get("value"),
            "precompile": attrs.get("precompile"),
            "solidity_like": attrs.get("solidity_like"),
        })

    @staticmethod
    def overlay_with_effect_refs(overlay: dict[str, Any], effect_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
        refs: list[str] = []
        path_states: list[str] = []
        for effect_id in overlay.get("effects") or []:
            effect = effect_by_id.get(effect_id)
            if not isinstance(effect, dict):
                continue
            refs.extend(str(ref) for ref in effect.get("stmt_refs") or [] if ref)
            attrs = effect.get("attrs") or {}
            path_states.extend(str(item) for item in attrs.get("path_states") or [] if item and item != "entry")
        if not refs and not path_states:
            return overlay
        out = dict(overlay)
        if not overlay.get("stmt_refs") and refs:
            out["stmt_refs"] = list(dict.fromkeys(refs))
        unique_paths = list(dict.fromkeys(path_states))
        if len(unique_paths) == 1:
            out["_sseir_effect_condition"] = unique_paths[0]
        return out

    def fact(
        self,
        fn: dict[str, Any],
        overlay: dict[str, Any],
        stmt_lang: dict[str, str],
        kind: str,
        *,
        condition: str | None = None,
        lvalue: Any = None,
        rvalue: Any = None,
        reads: list[Any] | None = None,
        writes: list[Any] | None = None,
        semantic: dict[str, Any] | None = None,
        extra_evidence: dict[str, Any] | None = None,
    ) -> SemanticFact:
        stmt_refs = list(overlay.get("stmt_refs") or [])
        source_lang = source_lang_for_refs(stmt_refs, stmt_lang)
        evidence = {
            "overlay": overlay.get("overlay_id"),
            "overlay_kind": overlay.get("kind"),
            "effects": list(overlay.get("effects") or []),
        }
        if extra_evidence:
            evidence.update(extra_evidence)
        return SemanticFact(
            fact_id=self.ids.new(),
            kind=kind,
            source_lang=source_lang,
            origin="sseir_overlay",
            function=str(fn.get("function") or ""),
            contract=fn.get("contract"),
            signature=fn.get("signature"),
            stmt_refs=stmt_refs,
            cfg_nodes=cfg_nodes_for_refs(stmt_refs, fn),
            condition=condition or overlay.get("_sseir_effect_condition"),
            lvalue=lvalue,
            rvalue=rvalue,
            reads=clean_list(reads or []),
            writes=clean_list(writes or []),
            semantic=clean_dict(semantic or {}),
            evidence=clean_dict(evidence),
        )

    @staticmethod
    def statement_languages(fn: dict[str, Any]) -> dict[str, str]:
        return {
            str(stmt.get("stmt_id")): str(stmt.get("lang"))
            for stmt in fn.get("source_statements") or []
            if isinstance(stmt, dict) and stmt.get("stmt_id")
        }

    @staticmethod
    def state_semantic(attrs: dict[str, Any], operation: str) -> dict[str, Any]:
        access = attrs.get("access") or attrs.get("slot")
        return clean_dict({
            "operation": operation,
            "access": access,
            "state_variable": attrs.get("state_variable") or attrs.get("state_var"),
            "keys": attrs.get("keys") or bracket_keys(str(access or "")),
            "storage_model": attrs.get("storage_model"),
            "slot": attrs.get("slot"),
            "slot_key": attrs.get("slot_key"),
            "slot_versions": attrs.get("slot_versions") or attrs.get("slot_keys"),
            "candidate_status": attrs.get("status"),
        })

    @staticmethod
    def reads_for_value(value: Any, attrs: dict[str, Any]) -> list[Any]:
        reads = rough_reads(value)
        value_state_read = attrs.get("value_state_read")
        if isinstance(value_state_read, dict):
            reads.append(value_state_read.get("access") or value_state_read.get("slot"))
        return clean_list(reads)


class SlitherFactAdapter:
    """Normalize Slither/SlithIR operations into SemanticFact rows.

    The adapter keeps the fact kind close to SlithIR's operation vocabulary
    and archives the original operation payload in evidence. This makes the
    Solidity line and the S-SEIR/Yul line meet at a common behavior-fact layer
    without losing Slither-specific semantics.
    """

    KIND_MAP = {
        "Assignment": "ValueAssign",
        "Binary": "BinaryOperation",
        "Unary": "UnaryOperation",
        "TypeConversion": "TypeConversion",
        "Index": "IndexAccess",
        "Member": "MemberAccess",
        "Length": "LengthRead",
        "Delete": "Delete",
        "InitArray": "ArrayLiteral",
        "NewArray": "NewArray",
        "NewContract": "NewContract",
        "NewElementaryType": "NewElementaryType",
        "NewStructure": "NewStructure",
        "Phi": "Phi",
        "PhiCallback": "PhiCallback",
        "Unpack": "TupleUnpack",
        "Nop": "Nop",
        "InternalCall": "InternalCall",
        "InternalDynamicCall": "InternalDynamicCall",
        "HighLevelCall": "ExternalCall",
        "LowLevelCall": "LowLevelCall",
        "LibraryCall": "LibraryCall",
        "SolidityCall": "BuiltinCall",
        "Condition": "BranchCondition",
        "Return": "Return",
        "EventCall": "EventEmit",
        "Send": "ValueTransferCall",
        "Transfer": "ValueTransferCall",
        "Call": "Call",
        "Operation": "Operation",
        "OperationWithLValue": "OperationWithLValue",
    }

    def __init__(self) -> None:
        self.ids = FactIdAllocator()

    def facts_from_slither_function(self, function: Any, *, ssa: bool = False) -> list[SemanticFact]:
        """Build facts directly from a Slither Function object."""
        operations: list[Any] = []
        for node in getattr(function, "nodes", []) or []:
            source = getattr(node, "irs_ssa" if ssa else "irs", None) or []
            operations.extend(source)
        return self.facts_from_operations(self.function_record(function), operations)

    def facts_from_slither(self, slither: Any, *, ssa: bool = False) -> dict[str, Any]:
        """Build a SemanticFact payload from a Slither object."""
        functions: list[Any] = []
        for contract in getattr(slither, "contracts", []) or []:
            for function in getattr(contract, "functions_and_modifiers_declared", []) or []:
                functions.append(function)
        facts = [fact.to_dict() for function in functions for fact in self.facts_from_slither_function(function, ssa=ssa)]
        return {
            "schema": "s-seir-semantic-facts/v1",
            "source": "slither",
            "function_count": len(functions),
            "facts": facts,
        }

    def facts_from_operations(self, function: dict[str, Any], operations: list[Any]) -> list[SemanticFact]:
        out: list[SemanticFact] = []
        for index, raw_op in enumerate(operations):
            op = self.operation_record(raw_op, index)
            slithir_kind = str(op.get("kind") or op.get("type") or "")
            kind = self.KIND_MAP.get(slithir_kind, "SlithIROperation")
            reads = self.operation_reads(op)
            writes = self.operation_writes(op)
            out.append(SemanticFact(
                fact_id=self.ids.new(),
                kind=kind,
                source_lang="solidity",
                origin="slither_ir",
                function=str(function.get("function") or function.get("name") or ""),
                contract=function.get("contract"),
                signature=function.get("signature"),
                stmt_refs=list(op.get("stmt_refs") or []),
                cfg_nodes=clean_list([op.get("cfg_node") or op.get("node_id")]),
                condition=op.get("condition"),
                lvalue=op.get("lvalue") or op.get("left"),
                rvalue=self.operation_rvalue(op),
                reads=reads,
                writes=writes,
                semantic=self.operation_semantic(op, kind, reads, writes),
                evidence={"slither": clean_dict(op)},
            ))
        return out

    def facts_from_sseir_control(self, fn: Any) -> list[SemanticFact]:
        """Build Solidity-side facts from SlithIR archived in FunctionSSEIR.control.

        S-SEIR keeps Slither blocks in the unified function CFG and replaces
        assembly nodes with local Yul CFG subgraphs. This method consumes only
        the Solidity blocks, so it does not duplicate Yul facts recovered by
        SSeirFactAdapter.
        """
        function = {
            "contract": getattr(fn, "contract", None),
            "function": getattr(fn, "function", None),
            "signature": getattr(fn, "signature", None),
        }
        operations: list[dict[str, Any]] = []
        block_conditions = self.control_block_conditions((getattr(fn, "control", {}) or {}).get("control_dependencies") or [])
        for block in (getattr(fn, "control", {}) or {}).get("blocks") or []:
            if block.get("kind") != "solidity":
                continue
            attrs = block.get("attrs") or {}
            block_id = block.get("block_id")
            stmt_refs = list(block.get("stmts") or [])
            archived = attrs.get("slithir_ssa") or attrs.get("slithir") or []
            using_ssa = bool(attrs.get("slithir_ssa"))
            for op in archived:
                if not isinstance(op, dict):
                    continue
                item = dict(op)
                item.setdefault("cfg_node", block_id)
                item.setdefault("node_id", attrs.get("slither_node_id"))
                item.setdefault("stmt_refs", stmt_refs)
                item.setdefault("ssa", using_ssa)
                if block_id in block_conditions:
                    item.setdefault("condition", block_conditions[block_id])
                operations.append(item)
        return self.facts_from_operations(function, operations)

    @staticmethod
    def control_block_conditions(control_dependencies: list[dict[str, Any]]) -> dict[str, str]:
        by_block: dict[str, list[str]] = {}
        for dependency in control_dependencies:
            dependent = dependency.get("dependent")
            predicate = dependency.get("predicate") or dependency.get("condition")
            if not dependent or not predicate:
                continue
            text = str(predicate)
            if text == "entry":
                continue
            bucket = by_block.setdefault(str(dependent), [])
            if text not in bucket:
                bucket.append(text)
        return {
            block_id: " && ".join(predicates)
            for block_id, predicates in by_block.items()
            if predicates
        }

    @staticmethod
    def function_record(function: Any) -> dict[str, Any]:
        contract = getattr(function, "contract_declarer", None) or getattr(function, "contract", None)
        return {
            "contract": str(getattr(contract, "name", "") or ""),
            "function": str(getattr(function, "name", function)),
            "name": str(getattr(function, "name", function)),
            "signature": str(getattr(function, "full_name", getattr(function, "signature_str", "")) or ""),
        }

    def operation_record(self, raw_op: Any, index: int = 0) -> dict[str, Any]:
        if isinstance(raw_op, dict):
            item = dict(raw_op)
            item.setdefault("order", index)
            return item
        item: dict[str, Any] = {
            "order": index,
            "kind": type(raw_op).__name__,
            "text": str(raw_op),
        }
        expression = getattr(raw_op, "expression", None)
        if expression is not None:
            item["source_expression"] = str(expression)
        node = getattr(raw_op, "node", None)
        if node is not None:
            item["node_id"] = str(getattr(node, "node_id", ""))
            item["node_type"] = str(getattr(node, "type", ""))
            item["node_expression"] = str(getattr(node, "expression", "") or "")
        for name in (
            "lvalue",
            "rvalue",
            "variable",
            "variable_left",
            "variable_right",
            "value",
            "destination",
            "call_value",
            "call_gas",
            "type_call",
            "function_name",
            "name",
            "index",
            "tuple",
            "structure_name",
            "contract_name",
            "array_type",
            "type",
        ):
            if hasattr(raw_op, name):
                value = getattr(raw_op, name)
                if value is not None:
                    item[name] = self.slither_value(value)
        for name in ("read", "arguments", "values", "init_values", "names", "rvalues", "nodes"):
            if hasattr(raw_op, name):
                values = getattr(raw_op, name)
                if values is not None:
                    if isinstance(values, (set, list, tuple)):
                        item[name] = [self.slither_value(value) for value in values]
                    else:
                        item[name] = [self.slither_value(values)]
        function = getattr(raw_op, "function", None)
        if function is not None:
            item["function"] = self.slither_callable(function)
        return item

    @classmethod
    def operation_semantic(cls, op: dict[str, Any], fact_kind: str, reads: list[Any], writes: list[Any]) -> dict[str, Any]:
        slithir_kind = op.get("kind") or op.get("type")
        semantic: dict[str, Any] = {
            "semantic_category": cls.semantic_category(fact_kind),
            "slithir_kind": slithir_kind,
            "slithir_text": op.get("text"),
            "operator": cls.scalar_text(op.get("operator") or op.get("type")),
            "arguments": cls.value_list_text(op.get("arguments")),
            "values": cls.value_list_text(op.get("values")),
            "reads": reads,
            "writes": writes,
        }
        if fact_kind in {"ExternalCall", "LowLevelCall", "LibraryCall", "InternalCall", "InternalDynamicCall", "BuiltinCall"}:
            semantic.update({
                "call_target": cls.value_text(op.get("destination") or op.get("function")),
                "function_name": cls.value_text(op.get("function_name") or op.get("name")),
                "call_value": cls.value_text(op.get("call_value")),
                "call_gas": cls.value_text(op.get("call_gas")),
                "call_type": cls.scalar_text(op.get("type_call")),
            })
        if fact_kind == "EventEmit":
            semantic["event"] = cls.value_text(op.get("name") or op.get("event"))
        if fact_kind == "ValueTransferCall":
            semantic.update({
                "destination": cls.value_text(op.get("destination")),
                "value": cls.value_text(op.get("call_value") or cls.last_argument(op)),
            })
        if fact_kind in {"IndexAccess", "MemberAccess", "LengthRead"}:
            semantic.update({
                "base": cls.value_text(op.get("variable_left") or op.get("value")),
                "index_or_member": cls.value_text(op.get("variable_right")),
            })
        if fact_kind in {"NewArray", "NewContract", "NewElementaryType", "NewStructure"}:
            semantic.update({
                "created_type": cls.value_text(op.get("array_type") or op.get("contract_name") or op.get("type") or op.get("structure_name")),
            })
        if fact_kind in {"Phi", "PhiCallback"}:
            semantic.update({
                "inputs": cls.value_list_text(op.get("rvalues")),
                "source_nodes": cls.value_list_text(op.get("nodes")),
            })
        if fact_kind == "TupleUnpack":
            semantic.update({
                "tuple": cls.value_text(op.get("tuple")),
                "index": cls.value_text(op.get("index")),
            })
        return clean_dict(semantic)

    @classmethod
    def operation_reads(cls, op: dict[str, Any]) -> list[Any]:
        explicit = op.get("reads") or op.get("read")
        if explicit:
            return clean_list(cls.value_list_text(explicit))
        kind = str(op.get("kind") or op.get("type") or "")
        fields_by_kind = {
            "Assignment": ["rvalue"],
            "Binary": ["variable_left", "variable_right"],
            "Unary": ["rvalue", "variable"],
            "TypeConversion": ["variable"],
            "Condition": ["value"],
            "Index": ["variable_left", "variable_right"],
            "Member": ["variable_left"],
            "Length": ["value"],
            "Delete": ["variable"],
            "Return": ["values"],
            "EventCall": ["arguments"],
            "HighLevelCall": ["destination", "arguments", "call_value", "call_gas"],
            "LowLevelCall": ["destination", "arguments", "call_value", "call_gas"],
            "LibraryCall": ["destination", "arguments", "call_value", "call_gas"],
            "InternalCall": ["arguments"],
            "InternalDynamicCall": ["function", "arguments", "call_value", "call_gas"],
            "SolidityCall": ["arguments"],
            "Send": ["destination", "call_value"],
            "Transfer": ["destination", "call_value"],
            "InitArray": ["init_values"],
            "NewArray": ["arguments"],
            "NewContract": ["arguments", "call_value"],
            "NewElementaryType": ["arguments"],
            "NewStructure": ["arguments"],
            "Phi": ["rvalues"],
            "PhiCallback": ["rvalues"],
            "Unpack": ["tuple"],
        }
        values: list[Any] = []
        for field_name in fields_by_kind.get(kind, ["rvalue", "expression"]):
            values.extend(flat_list(op.get(field_name)))
        return clean_list(cls.value_list_text(values) or rough_reads(op.get("text") or op.get("expression")))

    @classmethod
    def operation_writes(cls, op: dict[str, Any]) -> list[Any]:
        explicit = op.get("writes") or op.get("write")
        if explicit:
            return clean_list(cls.value_list_text(explicit))
        lvalue = op.get("lvalue") or op.get("left")
        return clean_list([cls.value_text(lvalue)])

    @classmethod
    def operation_rvalue(cls, op: dict[str, Any]) -> Any:
        kind = str(op.get("kind") or op.get("type") or "")
        if kind == "Binary":
            left = cls.value_text(op.get("variable_left"))
            right = cls.value_text(op.get("variable_right"))
            operator = cls.scalar_text(op.get("operator") or op.get("type"))
            return clean_dict({"left": left, "operator": operator, "right": right})
        if kind == "Unary":
            return clean_dict({"operator": cls.scalar_text(op.get("operator") or op.get("type")), "value": cls.value_text(op.get("rvalue") or op.get("variable"))})
        if kind in {"HighLevelCall", "LowLevelCall", "LibraryCall", "InternalCall", "InternalDynamicCall", "SolidityCall"}:
            return clean_dict({
                "call_target": cls.value_text(op.get("destination") or op.get("function")),
                "function": cls.value_text(op.get("function_name") or op.get("name")),
                "arguments": cls.value_list_text(op.get("arguments")),
            })
        if kind == "EventCall":
            return clean_dict({"event": cls.value_text(op.get("name")), "arguments": cls.value_list_text(op.get("arguments"))})
        if kind == "Return":
            return cls.value_list_text(op.get("values"))
        if kind == "InitArray":
            return cls.value_list_text(op.get("init_values"))
        return op.get("rvalue") or op.get("right") or op.get("expression") or op.get("text")

    @staticmethod
    def semantic_category(fact_kind: str) -> str:
        if fact_kind in {"BranchCondition"}:
            return "control"
        if fact_kind in {"Return"}:
            return "return"
        if fact_kind in {"EventEmit"}:
            return "event"
        if fact_kind in {"ExternalCall", "LowLevelCall", "LibraryCall", "InternalCall", "InternalDynamicCall", "BuiltinCall", "ValueTransferCall", "NewContract"}:
            return "call"
        if fact_kind in {"IndexAccess", "MemberAccess", "LengthRead", "Delete", "NewArray", "NewElementaryType", "NewStructure"}:
            return "data_access"
        if fact_kind in {"Phi", "PhiCallback"}:
            return "ssa"
        if fact_kind == "Nop":
            return "noop"
        return "value"

    @classmethod
    def slither_value(cls, value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, dict):
            return value
        if isinstance(value, (list, tuple, set)):
            return [cls.slither_value(item) for item in value]
        kind = type(value).__name__
        node_id = getattr(value, "node_id", None)
        if node_id is not None:
            return clean_dict({"kind": kind, "node_id": int(node_id), "text": str(value)})
        non_ssa = getattr(value, "non_ssa_version", None)
        value_type = getattr(value, "type", None)
        return clean_dict({
            "kind": kind,
            "text": str(value),
            "name": str(getattr(value, "name", "")),
            "base_name": str(non_ssa) if non_ssa is not None else str(getattr(value, "name", "")),
            "type": str(value_type) if value_type is not None else None,
            "is_state": kind in {"StateIRVariable", "StateVariable"},
            "is_reference": "ReferenceVariable" in kind,
            "is_constant": kind == "Constant",
            "is_solidity_builtin": kind in {"SolidityVariable", "SolidityVariableComposed"},
        })

    @classmethod
    def slither_callable(cls, value: Any) -> dict[str, Any]:
        if isinstance(value, tuple):
            return {"kind": "tuple", "text": ".".join(str(item) for item in value)}
        contract = getattr(value, "contract", None) or getattr(value, "contract_declarer", None)
        return clean_dict({
            "kind": type(value).__name__,
            "name": str(getattr(value, "name", value)),
            "full_name": str(getattr(value, "full_name", getattr(value, "name", value))),
            "canonical_name": str(getattr(value, "canonical_name", "") or ""),
            "contract": str(getattr(contract, "name", "") or ""),
        })

    @classmethod
    def value_text(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return value.get("text") or value.get("name") or value.get("full_name") or value.get("canonical_name") or value
        return value

    @classmethod
    def value_list_text(cls, value: Any) -> list[Any]:
        return [cls.value_text(item) for item in flat_list(value)]

    @classmethod
    def scalar_text(cls, value: Any) -> Any:
        if isinstance(value, list):
            return cls.value_text(value[0]) if value else None
        return cls.value_text(value)

    @classmethod
    def last_argument(cls, op: dict[str, Any]) -> Any:
        arguments = op.get("arguments")
        if isinstance(arguments, list) and arguments:
            return arguments[-1]
        return None


def extract_function_dicts(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if is_function_dict(item)]
    if not isinstance(payload, dict):
        return []
    source_entry = payload.get("source_entry")
    if isinstance(source_entry, dict) and isinstance(source_entry.get("functions"), list):
        return [item for item in source_entry["functions"] if is_function_dict(item)]
    if isinstance(payload.get("functions"), list):
        return [item for item in payload["functions"] if is_function_dict(item)]
    out: list[dict[str, Any]] = []
    for value in payload.values():
        out.extend(extract_function_dicts(value))
    return out


def is_function_dict(value: Any) -> bool:
    return isinstance(value, dict) and "function" in value and "semantic_overlays" in value


def build_function_level_semantic_fact_payload(
    functions: list[Any],
    *,
    source: str | None = None,
    result_dir: str | None = None,
) -> dict[str, Any]:
    """Build the final function-level fact view.

    Solidity source statements are lifted to high-level SemanticFact records
    from S-SEIR Solidity overlays. SlithIR SSA remains evidence/debug input,
    not the default final fact granularity. Yul inline assembly still lives in
    the S-SEIR semantic model (effects, roles, overlays) and is additionally
    projected to SemanticFact records here.
    """
    from s_seir_solidity_semantic_lifter import SoliditySemanticLifter

    solidity_lifter = SoliditySemanticLifter()
    yul_adapter = SSeirFactAdapter()
    solidity_facts: list[dict[str, Any]] = []
    yul_facts: list[dict[str, Any]] = []
    for fn in functions:
        solidity_facts.extend(solidity_lifter.facts_from_function(fn))
        fn_dict = fn.to_semantic_dict() if hasattr(fn, "to_semantic_dict") else fn
        fn_has_yul = function_dict_has_yul(fn_dict)
        for fact in yul_adapter.function_facts(fn_dict):
            item = fact.to_dict()
            if item.get("source_lang") == "solidity":
                continue
            if item.get("source_lang") in {None, "unknown"}:
                if not fn_has_yul:
                    continue
                item["source_lang"] = "yul"
            yul_facts.append(item)
    solidity_facts = dedupe_semantic_facts(solidity_facts)
    yul_facts = dedupe_semantic_facts(yul_facts)
    facts = renumber_facts(solidity_facts + yul_facts)
    solidity_count = len(solidity_facts)
    return {
        "schema": "s-seir-function-semantic-facts/v1",
        "source": source,
        "result_dir": result_dir,
        "model_boundary": {
            "processing_unit": "function",
            "solidity": "Solidity source is lifted to high-level SemanticFact rows from S-SEIR Solidity overlays; SlithIR SSA is retained as evidence/debug input.",
            "yul": "S-SEIR keeps low-level roles/effects/overlays in sseir.json and projects overlays to SemanticFact here.",
        },
        "function_count": len(functions),
        "solidity_fact_count": solidity_count,
        "yul_fact_count": len(yul_facts),
        "fact_count": len(facts),
        "solidity_facts": facts[:solidity_count],
        "yul_facts": facts[solidity_count:],
        "facts": facts,
    }


def renumber_facts(facts: list[dict[str, Any]], prefix: str = "fact") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, fact in enumerate(facts, start=1):
        item = dict(fact)
        item["fact_id"] = f"{prefix}_{index}"
        out.append(item)
    return out


def dedupe_semantic_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse duplicate projected facts while preserving combined evidence.

    S-SEIR may keep multiple low-level effects for the same high-level behavior.
    That is useful in sseir.json, but the final SemanticFact layer should expose
    the behavior once and archive all contributing evidence on that fact.
    """
    out: list[dict[str, Any]] = []
    index_by_key: dict[str, int] = {}
    for fact in facts:
        key = semantic_fact_dedupe_key(fact)
        if key not in index_by_key:
            item = dict(fact)
            item["_dedupe_count"] = 1
            out.append(item)
            index_by_key[key] = len(out) - 1
            continue
        existing = out[index_by_key[key]]
        merge_fact_evidence(existing, fact)
    for item in out:
        count = item.pop("_dedupe_count", 1)
        if count > 1:
            evidence = dict(item.get("evidence") or {})
            evidence["deduped_fact_count"] = count
            item["evidence"] = clean_dict(evidence)
    return out


def semantic_fact_dedupe_key(fact: dict[str, Any]) -> str:
    stable = {
        key: fact.get(key)
        for key in (
            "kind",
            "source_lang",
            "origin",
            "function",
            "contract",
            "signature",
            "condition",
            "lvalue",
            "rvalue",
            "reads",
            "writes",
            "semantic",
        )
    }
    return json.dumps(json_ready(stable), sort_keys=True, ensure_ascii=False)


def merge_fact_evidence(existing: dict[str, Any], duplicate: dict[str, Any]) -> None:
    existing["_dedupe_count"] = int(existing.get("_dedupe_count") or 1) + 1
    existing["stmt_refs"] = clean_list(list(existing.get("stmt_refs") or []) + list(duplicate.get("stmt_refs") or []))
    existing["cfg_nodes"] = clean_list(list(existing.get("cfg_nodes") or []) + list(duplicate.get("cfg_nodes") or []))
    existing["reads"] = clean_list(list(existing.get("reads") or []) + list(duplicate.get("reads") or []))
    existing["writes"] = clean_list(list(existing.get("writes") or []) + list(duplicate.get("writes") or []))
    existing["evidence"] = merge_evidence(existing.get("evidence") or {}, duplicate.get("evidence") or {})


def merge_evidence(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key, value in right.items():
        if key not in merged:
            merged[key] = value
            continue
        if merged[key] == value:
            continue
        merged[key] = clean_list(flat_list(merged[key]) + flat_list(value))
    return clean_dict(merged)


def source_lang_for_refs(stmt_refs: list[str], stmt_lang: dict[str, str]) -> str:
    langs = {stmt_lang.get(ref) for ref in stmt_refs if stmt_lang.get(ref)}
    if langs == {"yul"}:
        return "yul"
    if langs == {"solidity"}:
        return "solidity"
    if "yul" in langs and "solidity" in langs:
        return "mixed"
    return "unknown"


def function_dict_has_yul(fn: dict[str, Any]) -> bool:
    return any(
        isinstance(stmt, dict) and stmt.get("lang") == "yul"
        for stmt in fn.get("source_statements") or []
    )


def cfg_nodes_for_refs(stmt_refs: list[str], fn: dict[str, Any]) -> list[str]:
    out: list[str] = []
    control = fn.get("control") or {}
    for block in control.get("blocks") or []:
        block_id = block.get("block_id")
        for stmt in block.get("stmts") or []:
            if stmt in stmt_refs and block_id:
                out.append(str(block_id))
    return list(dict.fromkeys(out))


def bracket_keys(text: str) -> list[str]:
    return re.findall(r"\[([^\]]+)\]", text or "")


def rough_reads(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return clean_list([item for part in value for item in rough_reads(part)])
    if isinstance(value, dict):
        return clean_list([item for part in value.values() for item in rough_reads(part)])
    text = str(value)
    identifiers = re.findall(r"\b[A-Za-z_$][A-Za-z0-9_$]*(?:\[[^\]]+\])?", text)
    keywords = {
        "if", "else", "return", "true", "false", "uint256", "address", "bytes", "memory",
        "storage", "keccak256", "abi", "encode", "encodePacked", "low_bytes", "bytes20",
        "msg", "sender", "block", "timestamp", "gasleft",
    }
    return [item for item in identifiers if item not in keywords]


def flat_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        out: list[Any] = []
        for item in value:
            out.extend(flat_list(item))
        return out
    if isinstance(value, tuple):
        return flat_list(list(value))
    if isinstance(value, dict):
        if "value" in value:
            return flat_list(value.get("value"))
        return clean_list(list(value.values()))
    return [value]


def pick(attrs: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return clean_dict({key: attrs.get(key) for key in keys})


def clean_list(values: Any) -> list[Any]:
    if values is None:
        return []
    if not isinstance(values, list):
        values = [values]
    out: list[Any] = []
    seen: set[str] = set()
    for value in values:
        if value is None or value == "":
            continue
        marker = json.dumps(json_ready(value), sort_keys=True, ensure_ascii=False)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(value)
    return out


def clean_dict(value: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): json_ready(item)
        for key, item in value.items()
        if item is not None and item != "" and item != [] and item != {}
    }


def json_ready(value: Any) -> Any:
    if is_dataclass(value):
        return json_ready(asdict(value))
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    return value


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_ready(payload), indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Project S-SEIR semantic overlays into unified SemanticFact records.")
    parser.add_argument("input", type=Path, help="S-SEIR JSON file. Supports pipeline array output and batch source output.")
    parser.add_argument("-o", "--output", type=Path, default=Path("outputs/semantic_facts.json"))
    args = parser.parse_args()
    payload = read_json(args.input)
    facts = SSeirFactAdapter().build_from_payload(payload)
    write_json(args.output, facts)
    print(f"Wrote {args.output}")
    print(f"Semantic facts: {len(facts.get('facts') or [])}")


if __name__ == "__main__":
    main()
