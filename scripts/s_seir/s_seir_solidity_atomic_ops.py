#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


Json = dict[str, Any]


class SolidityAtomicOperationExtractor:
    """Normalize SlithIR-SSA into function-scoped Solidity atomic operations.

    The extractor is intentionally a transport/normalization pass. Slither
    remains responsible for expression lowering and SSA construction; this
    pass adds stable identities, CFG context, source references and typed
    reference chains for downstream S-SEIR consumers.
    """

    REFERENCE_KINDS = {"Index", "Member"}
    VALUE_KINDS = {
        "Binary",
        "Unary",
        "TypeConversion",
        "Length",
        "Unpack",
        "InitArray",
        "NewArray",
        "NewStructure",
        "NewElementaryType",
        "NewContract",
    }
    CALL_KINDS = {
        "InternalCall",
        "InternalDynamicCall",
        "HighLevelCall",
        "LibraryCall",
        "LowLevelCall",
        "Send",
        "Transfer",
        "SolidityCall",
    }
    OPERATOR_TEXT = {
        "ADDITION": "+",
        "SUBTRACTION": "-",
        "MULTIPLICATION": "*",
        "DIVISION": "/",
        "MODULO": "%",
        "LESS": "<",
        "GREATER": ">",
        "LESS_EQUAL": "<=",
        "GREATER_EQUAL": ">=",
        "EQUAL": "==",
        "NOT_EQUAL": "!=",
        "AND": "&&",
        "OR": "||",
        "CARET": "^",
        "LEFT_SHIFT": "<<",
        "RIGHT_SHIFT": ">>",
        "BITWISE_AND": "&",
        "BITWISE_OR": "|",
        "NOT": "!",
        "BITWISE_NOT": "~",
    }

    def extract(self, unit: Any, control: Json) -> Json:
        stmt_text = {
            str(stmt.stmt_id): str(stmt.text)
            for stmt in getattr(unit, "source_statements", [])
            if getattr(stmt, "lang", None) == "solidity"
        }
        conditions = self._block_conditions(control)
        reference_defs: dict[str, Json] = {}
        value_defs: dict[str, str] = {}
        operations: list[Json] = []
        sequence = 0

        solidity_blocks = [
            block for block in control.get("blocks", [])
            if block.get("kind") == "solidity"
        ]
        for block_order, block in enumerate(solidity_blocks):
            attrs = block.setdefault("attrs", {})
            archived = attrs.get("slithir_ssa") or attrs.get("slithir") or []
            block_id = str(block.get("block_id") or "")
            block_refs = [str(ref) for ref in block.get("stmts") or []]
            block_atoms: list[Json] = []
            for operation_order, raw in enumerate(archived):
                if not isinstance(raw, dict):
                    continue
                sequence += 1
                atom = self._operation(
                    raw,
                    atom_id=f"sol_atom_{sequence}",
                    sequence=sequence,
                    block_order=block_order,
                    operation_order=operation_order,
                    block_id=block_id,
                    node_id=attrs.get("slither_node_id"),
                    source_span=attrs.get("src"),
                    block_refs=block_refs,
                    stmt_text=stmt_text,
                    path_conditions=conditions.get(block_id, []),
                    reference_defs=reference_defs,
                    value_defs=value_defs,
                )
                block_atoms.append(atom)
                operations.append(atom)
            self._mark_control_dependencies(block_atoms)
            attrs["solidity_atomic_ops"] = block_atoms

        return {
            "schema": "s-seir-solidity-atomic-operations/v1",
            "function_id": str(getattr(unit, "function_id", "")),
            "contract": str(getattr(unit, "contract", "")),
            "function": str(getattr(unit, "function", "")),
            "signature": str(getattr(unit, "signature", "")),
            "operation_count": len(operations),
            "operations": operations,
        }

    def _operation(
        self,
        raw: Json,
        *,
        atom_id: str,
        sequence: int,
        block_order: int,
        operation_order: int,
        block_id: str,
        node_id: Any,
        source_span: Any,
        block_refs: list[str],
        stmt_text: dict[str, str],
        path_conditions: list[str],
        reference_defs: dict[str, Json],
        value_defs: dict[str, str],
    ) -> Json:
        atom = dict(raw)
        kind = str(raw.get("kind") or "Unknown")
        lvalue = raw.get("lvalue")
        lvalue_key = self._value_key(lvalue)
        stmt_refs = self._operation_stmt_refs(raw, block_refs, stmt_text)
        resolved_reads = self._unique([
            self._resolve_value(value, value_defs, reference_defs)
            for value in raw.get("read") or []
        ])
        writes = self._unique([self._source_name(lvalue)] if lvalue else [])
        atomic_kind = self._atomic_kind(kind)
        expression = self._expression(raw, value_defs, reference_defs)
        reference_definition: Json | None = None
        storage_access: Json | None = None

        if kind == "Index":
            left_value = raw.get("variable_left")
            right_value = raw.get("variable_right")
            left = self._resolve_value(left_value, value_defs, reference_defs)
            right = self._resolve_value(right_value, value_defs, reference_defs)
            parent = self._reference_info(left_value, reference_defs)
            state_variable = (
                parent.get("state_variable") if parent
                else self._state_variable_name(left_value)
            )
            keys = list(parent.get("keys") or []) if parent else []
            keys.append(right)
            expression = f"{left}[{right}]"
            reference_definition = {
                "access": expression,
                "state_variable": state_variable,
                "keys": keys,
                "reference_kind": "index",
                "type": (lvalue or {}).get("type") if isinstance(lvalue, dict) else None,
            }
        elif kind == "Member":
            left_value = raw.get("variable_left")
            left = self._resolve_value(left_value, value_defs, reference_defs)
            member = self._source_name(raw.get("variable_right"))
            parent = self._reference_info(left_value, reference_defs)
            state_variable = (
                parent.get("state_variable") if parent
                else self._state_variable_name(left_value)
            )
            expression = f"{left}.{member}"
            reference_definition = {
                "access": expression,
                "state_variable": state_variable,
                "keys": list(parent.get("keys") or []) if parent else [],
                "member": member,
                "reference_kind": "member",
                "type": (lvalue or {}).get("type") if isinstance(lvalue, dict) else None,
            }

        if reference_definition and lvalue_key:
            reference_defs[lvalue_key] = reference_definition
            value_defs[lvalue_key] = expression

        if kind in {"Assignment", "Delete"}:
            target = lvalue or raw.get("variable")
            target_info = self._reference_info(target, reference_defs)
            if target_info and target_info.get("state_variable"):
                storage_access = dict(target_info)
            elif self._is_state(target):
                storage_access = {
                    "access": self._source_name(target),
                    "state_variable": self._state_variable_name(target),
                    "keys": [],
                    "reference_kind": "state_variable",
                    "type": target.get("type") if isinstance(target, dict) else None,
                }
            if storage_access:
                atomic_kind = "StateWrite"
                writes = [str(storage_access.get("access"))]

        if lvalue_key and expression and kind not in self.REFERENCE_KINDS:
            value_defs[lvalue_key] = expression

        atom.update({
            "atom_id": atom_id,
            "sequence": sequence,
            "block_order": block_order,
            "operation_order": operation_order,
            "cfg_block_id": block_id,
            "cfg_node_id": node_id,
            "source_span": source_span,
            "stmt_refs": stmt_refs,
            "path_conditions": list(path_conditions),
            "condition": " && ".join(path_conditions) if path_conditions else None,
            "atomic_kind": atomic_kind,
            "semantic_atom": kind not in {"Condition", "Phi", "PhiCallback"},
            "runtime_operation": kind not in {"Condition", "Phi", "PhiCallback"},
            "result": self._source_name(lvalue) if lvalue else None,
            "result_ssa": lvalue_key,
            "expression": expression,
            "resolved_reads": resolved_reads,
            "writes": writes,
            "reference_definition": reference_definition,
            "storage_access": storage_access,
            "source": "slithir_ssa" if raw.get("ssa") else "slithir",
        })
        return self._clean(atom)

    @classmethod
    def _atomic_kind(cls, kind: str) -> str:
        if kind in cls.REFERENCE_KINDS:
            return "ReferenceAccess"
        if kind in cls.VALUE_KINDS:
            return "ValueCompute"
        if kind == "Assignment":
            return "ValueAssign"
        if kind in {"Phi", "PhiCallback"}:
            return "PhiMerge"
        if kind == "Condition":
            return "ControlPredicate"
        if kind == "Return":
            return "Return"
        if kind == "EventCall":
            return "EventEmit"
        if kind in cls.CALL_KINDS:
            return "Call"
        if kind == "Delete":
            return "Delete"
        return "Operation"

    def _expression(
        self,
        operation: Json,
        value_defs: dict[str, str],
        reference_defs: dict[str, Json],
    ) -> str:
        kind = str(operation.get("kind") or "")
        reads = operation.get("read") or []
        resolved = [self._resolve_value(value, value_defs, reference_defs) for value in reads]
        if kind == "Assignment":
            value = operation.get("rvalue") or (reads[0] if reads else None)
            return self._resolve_value(value, value_defs, reference_defs)
        if kind == "Delete":
            return "0"
        if kind == "Binary" and len(resolved) >= 2:
            operator = self._operator(operation.get("operator"))
            return f"({resolved[0]} {operator} {resolved[1]})"
        if kind == "Unary" and resolved:
            operator = self._operator(operation.get("operator"))
            return f"({operator}{resolved[0]})"
        if kind == "TypeConversion" and resolved:
            target_type = self._source_name(operation.get("lvalue"))
            source = str(operation.get("source_expression") or "")
            return source or f"convert({resolved[0]} -> {target_type})"
        if kind == "Length" and resolved:
            return f"{resolved[0]}.length"
        if kind in {"Phi", "PhiCallback"}:
            target = self._source_name(operation.get("lvalue"))
            if target and not target.startswith(("TMP_", "REF_", "TUPLE_")):
                return target
            source_inputs = self._unique([self._source_name(value) for value in reads])
            if len(source_inputs) == 1:
                return source_inputs[0]
            return f"phi({', '.join(resolved)})"
        if kind == "Condition" and resolved:
            return resolved[0]
        if kind == "Return":
            values = [
                self._resolve_value(value, value_defs, reference_defs)
                for value in operation.get("values") or reads
            ]
            return values[0] if len(values) == 1 else f"({', '.join(values)})"
        source = str(operation.get("source_expression") or "").strip()
        return source or str(operation.get("text") or kind)

    @classmethod
    def _operator(cls, value: Any) -> str:
        text = str(value or "")
        return cls.OPERATOR_TEXT.get(text, text or "?")

    @classmethod
    def _resolve_value(
        cls,
        value: Any,
        value_defs: dict[str, str],
        reference_defs: dict[str, Json],
    ) -> str:
        key = cls._value_key(value)
        if key and key in value_defs:
            return value_defs[key]
        if key and key in reference_defs:
            return str(reference_defs[key].get("access") or cls._source_name(value))
        return cls._source_name(value)

    @classmethod
    def _reference_info(cls, value: Any, reference_defs: dict[str, Json]) -> Json | None:
        key = cls._value_key(value)
        return reference_defs.get(key) if key else None

    @staticmethod
    def _value_key(value: Any) -> str:
        if not isinstance(value, dict):
            return str(value or "")
        return str(value.get("text") or value.get("name") or "")

    @staticmethod
    def _source_name(value: Any) -> str:
        if value is None:
            return ""
        if not isinstance(value, dict):
            return str(value)
        return str(value.get("base_name") or value.get("name") or value.get("text") or "")

    @staticmethod
    def _is_state(value: Any) -> bool:
        return isinstance(value, dict) and bool(value.get("is_state"))

    @classmethod
    def _state_variable_name(cls, value: Any) -> str | None:
        return cls._source_name(value) if cls._is_state(value) else None

    @staticmethod
    def _operation_stmt_refs(raw: Json, refs: list[str], stmt_text: dict[str, str]) -> list[str]:
        if len(refs) <= 1:
            return list(refs)
        expression = " ".join(str(raw.get("source_expression") or "").rstrip(";").split())
        if not expression:
            return list(refs)
        matched = []
        for ref in refs:
            source = " ".join(str(stmt_text.get(ref) or "").rstrip(";").split())
            if source and (expression in source or source in expression):
                matched.append(ref)
        return matched or list(refs)

    @staticmethod
    def _block_conditions(control: Json) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for dependency in control.get("control_dependencies") or []:
            if not isinstance(dependency, dict):
                continue
            block_id = str(dependency.get("dependent") or "")
            predicate = str(dependency.get("predicate") or dependency.get("condition") or "")
            if not block_id or not predicate or predicate == "entry":
                continue
            bucket = out.setdefault(block_id, [])
            if predicate not in bucket:
                bucket.append(predicate)
        return out

    @classmethod
    def _mark_control_dependencies(cls, atoms: list[Json]) -> None:
        definitions = {
            str(atom.get("result_ssa")): atom
            for atom in atoms
            if atom.get("result_ssa")
        }
        required: set[str] = set()
        work = [
            cls._value_key(value)
            for atom in atoms
            if atom.get("kind") == "Condition"
            for value in atom.get("read") or []
        ]
        while work:
            key = work.pop()
            if not key or key in required:
                continue
            required.add(key)
            definition = definitions.get(key)
            if not definition:
                continue
            work.extend(cls._value_key(value) for value in definition.get("read") or [])
        for atom in atoms:
            if atom.get("result_ssa") in required or atom.get("kind") == "Condition":
                atom["control_only"] = True

    @staticmethod
    def _unique(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))

    @classmethod
    def _clean(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: cls._clean(item)
                for key, item in value.items()
                if item is not None
            }
        if isinstance(value, list):
            return [cls._clean(item) for item in value]
        return value


def build_solidity_atomic_operation_payload(
    functions: list[Any],
    *,
    source: str | None = None,
) -> Json:
    tables = [
        getattr(function, "_sseir_solidity_atomic_operations", None)
        for function in functions
    ]
    tables = [table for table in tables if isinstance(table, dict)]
    return {
        "schema": "s-seir-solidity-atomic-operations/v1",
        "source": source,
        "function_count": len(tables),
        "operation_count": sum(int(table.get("operation_count") or 0) for table in tables),
        "functions": tables,
    }


def write_solidity_atomic_operation_json(
    functions: list[Any],
    output: Path,
    *,
    source: str | None = None,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = build_solidity_atomic_operation_payload(functions, source=source)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
