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
    LVALUE_WRITE_KINDS = {"Assignment", "Delete", "Binary", "Unary"}
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
                    node_type=attrs.get("slither_node_type"),
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

        self._attach_cfg_predecessors(operations, control)
        self._classify_phi_operations(
            operations,
            control,
            function_id=str(getattr(unit, "function_id", "")),
        )
        self._link_atomic_dependencies(operations)
        operations = self._materialize_storage_reads(operations)
        atoms_by_block: dict[str, list[Json]] = {}
        for atom in operations:
            atoms_by_block.setdefault(str(atom.get("cfg_block_id") or ""), []).append(atom)
        for block in solidity_blocks:
            block_id = str(block.get("block_id") or "")
            block.setdefault("attrs", {})["solidity_atomic_ops"] = atoms_by_block.get(block_id, [])

        return {
            "schema": "s-seir-solidity-atomic-operations/v2",
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
        node_type: Any,
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
        semantic_result = lvalue
        semantic_result_key = lvalue_key

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
                "container_type": left_value.get("type") if isinstance(left_value, dict) else None,
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

        if kind in self.LVALUE_WRITE_KINDS:
            # SlithIR serializes ``delete nested[key]`` as
            # ``outer_ref = delete leaf_ref``.  The variable is the location
            # being cleared; lvalue is only Slither's enclosing reference.
            if kind == "Delete":
                target = raw.get("variable") or lvalue
                semantic_result = target
                semantic_result_key = self._value_key(target)
            else:
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

        if (
            lvalue_key
            and expression
            and kind not in self.REFERENCE_KINDS
            and kind != "Delete"
        ):
            value_defs[lvalue_key] = expression

        storage_reads = self._storage_reads(raw, kind, reference_defs)

        atom.update({
            "atom_id": atom_id,
            "sequence": sequence,
            "block_order": block_order,
            "operation_order": operation_order,
            "cfg_block_id": block_id,
            "cfg_node_id": node_id,
            "cfg_node_type": node_type,
            "source_span": source_span,
            "stmt_refs": stmt_refs,
            "path_conditions": list(path_conditions),
            "condition": " && ".join(path_conditions) if path_conditions else None,
            "atomic_kind": atomic_kind,
            "semantic_atom": kind not in {"Condition", "Phi", "PhiCallback"},
            "runtime_operation": kind not in {"Condition", "Phi", "PhiCallback"},
            "result": self._source_name(semantic_result) if semantic_result else None,
            "result_ssa": semantic_result_key,
            "expression": expression,
            "resolved_reads": resolved_reads,
            "writes": writes,
            "reference_definition": reference_definition,
            "storage_access": storage_access,
            "storage_reads": storage_reads,
            "source": "slithir_ssa" if raw.get("ssa") else "slithir",
            "fact_eligible": True,
        })
        return self._clean(atom)

    @classmethod
    def _storage_reads(
        cls,
        raw: Json,
        kind: str,
        reference_defs: dict[str, Json],
    ) -> list[Json]:
        """Identify implicit storage dereferences in a SlithIR operation.

        SlithIR models an index/member access as a reference and dereferences
        that reference only when a later operation consumes it.  Materializing
        that implicit read keeps location resolution, state read, and the
        consuming computation as separate atomic operations.
        """
        if kind in cls.REFERENCE_KINDS | {"Phi", "PhiCallback", "Delete"}:
            return []
        out: list[Json] = []
        seen: set[tuple[str, str]] = set()
        for value in raw.get("read") or []:
            operand_ssa = cls._value_key(value)
            reference = cls._reference_info(value, reference_defs)
            if reference and reference.get("state_variable"):
                info = dict(reference)
            elif cls._is_state(value):
                info = {
                    "access": cls._source_name(value),
                    "state_variable": cls._state_variable_name(value),
                    "keys": [],
                    "reference_kind": "state_variable",
                    "type": value.get("type") if isinstance(value, dict) else None,
                }
            else:
                continue
            identity = (str(info.get("access") or ""), operand_ssa)
            if identity in seen:
                continue
            seen.add(identity)
            info["operand_ssa"] = operand_ssa
            info["operand"] = value
            out.append(cls._clean(info))
        return out

    @classmethod
    def _materialize_storage_reads(cls, atoms: list[Json]) -> list[Json]:
        """Insert explicit StateRead atoms before their consuming operations."""
        definition_by_result: dict[str, str] = {}
        for item in atoms:
            result = str(item.get("result_ssa") or "")
            atom_id = str(item.get("atom_id") or "")
            if result and atom_id:
                definition_by_result.setdefault(result, atom_id)
        expanded: list[Json] = []
        for atom in atoms:
            storage_reads = list(atom.get("storage_reads") or [])
            dependencies = list(atom.get("depends_on_atoms") or [])
            for index, storage in enumerate(storage_reads, start=1):
                operand_ssa = str(storage.get("operand_ssa") or "")
                access = str(storage.get("access") or "")
                read_id = f"{atom.get('atom_id')}:state_read:{index}"
                location_dependency = definition_by_result.get(operand_ssa)
                read_atom = cls._clean({
                    "atom_id": read_id,
                    "kind": "StorageRead",
                    "atomic_kind": "StateRead",
                    "semantic_atom": True,
                    "runtime_operation": True,
                    "cfg_block_id": atom.get("cfg_block_id"),
                    "cfg_node_id": atom.get("cfg_node_id"),
                    "block_order": atom.get("block_order"),
                    "source_span": atom.get("source_span"),
                    "source_expression": access,
                    "stmt_refs": list(atom.get("stmt_refs") or []),
                    "path_conditions": list(atom.get("path_conditions") or []),
                    "condition": atom.get("condition"),
                    "control_only": atom.get("control_only"),
                    "result": operand_ssa,
                    "result_ssa": operand_ssa,
                    "expression": access,
                    "resolved_reads": [access],
                    "writes": [operand_ssa] if operand_ssa else [],
                    "storage_access": {
                        key: value
                        for key, value in storage.items()
                        if key not in {"operand", "operand_ssa"}
                    },
                    "depends_on_atoms": [location_dependency] if location_dependency else [],
                    "source": atom.get("source"),
                    "implicit_slithir_operation": True,
                    "consumer_atom_id": atom.get("atom_id"),
                })
                expanded.append(read_atom)
                dependencies = [
                    dependency
                    for dependency in dependencies
                    if dependency != location_dependency
                ]
                dependencies.append(read_id)
            atom["depends_on_atoms"] = cls._unique(dependencies)
            expanded.append(atom)

        block_positions: dict[str, int] = {}
        for sequence, atom in enumerate(expanded, start=1):
            block_id = str(atom.get("cfg_block_id") or "")
            operation_order = block_positions.get(block_id, 0)
            block_positions[block_id] = operation_order + 1
            atom["sequence"] = sequence
            atom["operation_order"] = operation_order
        return expanded

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

    @classmethod
    def _block_conditions(cls, control: Json) -> dict[str, list[str]]:
        closure = control.get("control_dependency_closure") or {}
        dominators = (control.get("dominance") or {}).get("dominators") or {}
        if isinstance(closure, dict) and closure:
            return cls._closed_block_conditions(control, closure, dominators)

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
    def _closed_block_conditions(
        cls,
        control: Json,
        closure: dict[str, list[Json]],
        dominators: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        """Return predicates that are necessary to reach each CFG block.

        The raw dependency closure supplies nested controllers, but loop
        backedges can also make a later branch appear to control an earlier
        loop header.  A predicate is therefore retained only when its
        controller strictly dominates the target and the target cannot be
        reached from another branch of that controller without revisiting it.
        """
        edges = [edge for edge in control.get("edges") or [] if isinstance(edge, dict)]
        successors: dict[str, list[tuple[str, str]]] = {}
        for edge in edges:
            source = str(edge.get("from") or "")
            target = str(edge.get("to") or "")
            if source and target:
                successors.setdefault(source, []).append(
                    (target, str(edge.get("kind") or "fallthrough"))
                )

        out: dict[str, list[str]] = {}
        for block_id, dependencies in closure.items():
            block_id = str(block_id)
            strict_dominators = {
                str(value) for value in dominators.get(block_id, [])
                if str(value) != block_id
            }
            for dependency in dependencies or []:
                if not isinstance(dependency, dict):
                    continue
                controller = str(dependency.get("controller") or "")
                predicate = str(dependency.get("predicate") or dependency.get("condition") or "")
                edge_kind = str(dependency.get("edge_kind") or "")
                if (
                    not controller
                    or controller not in strict_dominators
                    or not predicate
                    or predicate == "entry"
                ):
                    continue
                if not cls._branch_predicate_is_necessary(
                    controller,
                    block_id,
                    edge_kind,
                    successors,
                ):
                    continue
                bucket = out.setdefault(block_id, [])
                if predicate not in bucket:
                    bucket.append(predicate)
        return out

    @classmethod
    def _branch_predicate_is_necessary(
        cls,
        controller: str,
        target: str,
        selected_edge_kind: str,
        successors: dict[str, list[tuple[str, str]]],
    ) -> bool:
        branches = successors.get(controller) or []
        if len(branches) < 2:
            return True
        selected_polarity = cls._edge_polarity(selected_edge_kind)
        selected = [
            node for node, kind in branches
            if cls._edge_polarity(kind) == selected_polarity
        ]
        alternatives = [
            node for node, kind in branches
            if cls._edge_polarity(kind) != selected_polarity
        ]
        if not selected or not alternatives:
            return True
        if not any(cls._reachable_without(node, target, controller, successors) for node in selected):
            return False
        return not any(
            cls._reachable_without(node, target, controller, successors)
            for node in alternatives
        )

    @staticmethod
    def _edge_polarity(edge_kind: str) -> str:
        if edge_kind in {"false", "exit", "zero"} or edge_kind.startswith("false:"):
            return "false"
        if edge_kind in {"true", "loop", "nonzero"} or edge_kind.startswith(("true:", "case:")):
            return "true"
        return edge_kind

    @staticmethod
    def _reachable_without(
        start: str,
        target: str,
        blocked: str,
        successors: dict[str, list[tuple[str, str]]],
    ) -> bool:
        work = [start]
        seen: set[str] = set()
        while work:
            current = work.pop()
            if current == target:
                return True
            if current == blocked or current in seen:
                continue
            seen.add(current)
            work.extend(node for node, _ in successors.get(current, []))
        return False

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

    @classmethod
    def _link_atomic_dependencies(cls, atoms: list[Json]) -> None:
        definitions: dict[str, str] = {}
        for atom in atoms:
            result = str(atom.get("result_ssa") or "")
            atom_id = str(atom.get("atom_id") or "")
            if result and atom_id and atom.get("fact_eligible", True):
                # Reference assignments reuse the REF produced by Index/Member.
                # Keep the defining access atom instead of replacing it with the
                # later write that consumes the same REF.
                definitions.setdefault(result, atom_id)
        previous_by_block: dict[str, str] = {}
        for atom in atoms:
            operand_keys = [
                cls._value_key(value)
                for value in atom.get("read") or []
            ]
            if atom.get("atomic_kind") == "StateWrite":
                operand_keys.append(cls._value_key(atom.get("lvalue")))
            dependencies = cls._unique([
                definitions[key]
                for key in operand_keys
                if key in definitions and definitions[key] != atom.get("atom_id")
            ])
            atom["depends_on_atoms"] = dependencies
            block_id = str(atom.get("cfg_block_id") or "")
            previous = previous_by_block.get(block_id)
            if previous:
                atom["previous_atom_id"] = previous
            if atom.get("atom_id"):
                previous_by_block[block_id] = str(atom["atom_id"])

    @classmethod
    def _classify_phi_operations(
        cls,
        atoms: list[Json],
        control: Json,
        *,
        function_id: str,
    ) -> None:
        """Keep only Phi nodes that represent a merge inside this function CFG."""
        loop_headers = cls._loop_headers(control)
        current_function = cls._normalized_function_id(function_id)
        for atom in atoms:
            if atom.get("kind") not in {"Phi", "PhiCallback"}:
                continue

            node_type = str(atom.get("cfg_node_type") or "")
            origins = [
                origin for origin in atom.get("phi_origin_nodes") or []
                if isinstance(origin, dict)
            ]
            origin_functions = {
                cls._normalized_function_id(str(origin.get("function") or ""))
                for origin in origins
                if origin.get("function")
            }
            foreign_origin = bool(
                current_function
                and any(origin != current_function for origin in origin_functions)
            )
            origin_node_ids = {
                (
                    str(origin.get("function") or ""),
                    int(origin["node_id"]) if origin.get("node_id") is not None else -1,
                )
                for origin in origins
            }
            block_id = str(atom.get("cfg_block_id") or "")

            if node_type.endswith("ENTRYPOINT"):
                role = "function_entry_input"
            elif atom.get("kind") == "PhiCallback" or atom.get("phi_callback"):
                role = "call_boundary"
            elif foreign_origin:
                role = "interprocedural"
            elif len(origin_node_ids) >= 2:
                role = "loop_carried" if (
                    block_id in loop_headers
                    or any(token in node_type for token in ("IFLOOP", "STARTLOOP", "ENDLOOP"))
                ) else "function_cfg_merge"
            elif len(origin_node_ids) == 1:
                role = "mutation_version"
            else:
                role = "unresolved"

            atom["phi_scope"] = "function" if role in {
                "function_cfg_merge", "loop_carried", "mutation_version"
            } else "analysis_boundary"
            atom["phi_role"] = role
            atom["fact_eligible"] = role in {"function_cfg_merge", "loop_carried"}
            atom["semantic_atom"] = atom["fact_eligible"]

    @classmethod
    def _loop_headers(cls, control: Json) -> set[str]:
        dominators = ((control.get("dominance") or {}).get("dominators") or {})
        headers: set[str] = set()
        for edge in control.get("edges") or []:
            if not isinstance(edge, dict):
                continue
            source = str(edge.get("from") or "")
            target = str(edge.get("to") or "")
            if source and target and target in set(dominators.get(source) or []):
                headers.add(target)
        return headers

    @staticmethod
    def _normalized_function_id(value: str) -> str:
        return "".join(str(value or "").split())

    @staticmethod
    def _attach_cfg_predecessors(atoms: list[Json], control: Json) -> None:
        predecessors: dict[str, list[str]] = {}
        for edge in control.get("edges") or []:
            if not isinstance(edge, dict):
                continue
            source = str(edge.get("from") or "")
            target = str(edge.get("to") or "")
            if not source or not target:
                continue
            bucket = predecessors.setdefault(target, [])
            if source not in bucket:
                bucket.append(source)
        for atom in atoms:
            atom["cfg_predecessor_blocks"] = predecessors.get(
                str(atom.get("cfg_block_id") or ""),
                [],
            )

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
        "schema": "s-seir-solidity-atomic-operations/v2",
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
