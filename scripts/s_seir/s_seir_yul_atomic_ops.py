#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

from assembly_ast_cfg import build_yul_cfg, yul_children, yul_expression, yul_statement_text
from assembly_memory_ssa import node_ast_mapping, yul_function_definitions
from s_seir_yul_normalize import normalize_expr


Json = dict[str, Any]

_VOID_CALLS = {
    "mstore", "mstore8", "sstore", "calldatacopy", "codecopy", "returndatacopy",
    "mcopy", "extcodecopy", "log0", "log1", "log2", "log3", "log4", "return",
    "revert", "stop", "invalid", "selfdestruct",
}
_ATOMIC_KINDS = {
    "mload": "MemoryRead",
    "mstore": "MemoryWrite",
    "mstore8": "MemoryWrite",
    "calldatacopy": "MemoryCopy",
    "codecopy": "MemoryCopy",
    "returndatacopy": "MemoryCopy",
    "mcopy": "MemoryCopy",
    "extcodecopy": "MemoryCopy",
    "keccak256": "MemoryHash",
    "sload": "StorageRead",
    "sstore": "StorageWrite",
    "call": "Call",
    "staticcall": "StaticCall",
    "delegatecall": "DelegateCall",
    "callcode": "CallCode",
    "create": "Create",
    "create2": "Create2",
    "log0": "EventLog",
    "log1": "EventLog",
    "log2": "EventLog",
    "log3": "EventLog",
    "log4": "EventLog",
    "return": "Return",
    "revert": "Revert",
    "stop": "Stop",
    "invalid": "Invalid",
    "selfdestruct": "SelfDestruct",
}
_SIDE_EFFECT_KINDS = {
    "MemoryWrite", "MemoryCopy", "StorageWrite", "Call", "StaticCall", "DelegateCall",
    "CallCode", "Create", "Create2", "EventLog", "Return", "Revert", "Stop", "Invalid",
    "SelfDestruct", "ControlTransfer",
}
_SINK_KINDS = {
    "MemoryRead", "MemoryHash", "StorageRead", "StorageWrite", "Call", "StaticCall",
    "DelegateCall", "CallCode", "Create", "Create2", "EventLog", "Return", "Revert",
}
_SKIP_CFG_KINDS = {"entry", "exit", "merge", "loop-merge", "loop-post", "function-definition"}


def _safe(value: Any) -> str:
    text = re.sub(r"\W+", "_", str(value or "stmt")).strip("_") or "stmt"
    return f"s_{text}" if text[0].isdigit() else text


def _unique(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if value not in {None, ""}))


@dataclass
class YulAtomicOperation:
    atom_id: str
    function_id: str
    assembly_block_id: int
    cfg_node_id: int
    cfg_block_id: str
    yul_function: str | None
    stmt_ref: str | None
    stmt_refs: list[str]
    source_span: str
    source_statement: str
    source_node_type: str
    context: str
    operation: str
    atomic_kind: str
    result: str | None
    results: list[str]
    arguments: list[str]
    raw_arguments: list[str]
    expression: str
    execution_expression: str
    normalized_expression: str
    operation_order: int
    dependencies: list[str] = field(default_factory=list)
    side_effect: bool = False
    semantic_sink: bool = False
    control_only: bool = False
    root_operation: bool = False

    def to_dict(self) -> Json:
        return asdict(self)


@dataclass
class _ExprValue:
    text: str
    producer: str | None = None


class _StatementAtomizer:
    def __init__(
        self,
        *,
        function_id: str,
        assembly_block_id: int,
        cfg_node_id: int,
        stmt_ref: str | None,
        statement: Json,
        context: str,
        yul_function: str | None = None,
        cfg_block_id: str | None = None,
    ) -> None:
        self.function_id = function_id
        self.assembly_block_id = assembly_block_id
        self.cfg_node_id = cfg_node_id
        self.cfg_block_id = cfg_block_id or f"bb_asm{assembly_block_id}_n{cfg_node_id}"
        self.yul_function = yul_function
        self.stmt_ref = stmt_ref
        self.statement = statement
        self.context = context
        self.counter = 0
        self.operations: list[YulAtomicOperation] = []
        scope = f"{_safe(yul_function)}_" if yul_function else ""
        self.prefix = scope + _safe(stmt_ref or f"asm_b{assembly_block_id}_n{cfg_node_id}")

    def atomize(self) -> tuple[list[YulAtomicOperation], str | None]:
        node_type = str(self.statement.get("nodeType") or "")
        final: _ExprValue | None = None
        if node_type in {"YulVariableDeclaration", "YulAssignment"}:
            value = self.statement.get("value")
            targets = self._assigned_names(self.statement)
            if isinstance(value, dict):
                final = self._lower_expr(value, root=True)
                self._emit_assignment(targets, final, declaration=node_type == "YulVariableDeclaration")
            elif targets:
                self._emit(
                    operation="declare" if node_type == "YulVariableDeclaration" else "assign",
                    atomic_kind="ValueDeclare" if node_type == "YulVariableDeclaration" else "ValueAssign",
                    result=targets[0] if len(targets) == 1 else None,
                    results=targets,
                    arguments=[],
                    raw_arguments=[],
                    expression=yul_statement_text(self.statement),
                    normalized_expression=yul_statement_text(self.statement),
                    control_only=False,
                    root_operation=True,
                )
        elif node_type == "YulExpressionStatement":
            expression = self.statement.get("expression")
            if isinstance(expression, dict):
                final = self._lower_expr(expression, root=True, discard_result=True)
        elif node_type == "YulIf":
            final = self._lower_expr(self.statement.get("condition"), root=True)
            self._emit_control("branch", "BranchCondition", final)
        elif node_type == "YulSwitch":
            final = self._lower_expr(self.statement.get("expression"), root=True)
            self._emit_control("switch", "SwitchCondition", final)
        elif node_type == "YulForLoop":
            final = self._lower_expr(self.statement.get("condition"), root=True)
            self._emit_control("loop_condition", "LoopCondition", final)
        elif node_type in {"YulBreak", "YulContinue", "YulLeave"}:
            op = node_type.removeprefix("Yul").lower()
            self._emit(
                operation=op,
                atomic_kind="ControlTransfer",
                result=None,
                results=[],
                arguments=[],
                raw_arguments=[],
                expression=op,
                normalized_expression=op,
                side_effect=True,
                control_only=True,
                root_operation=True,
            )
        return self.operations, final.text if final else None

    def _lower_expr(self, node: Any, *, root: bool = False, discard_result: bool = False) -> _ExprValue:
        if not isinstance(node, dict):
            return _ExprValue(yul_expression(node))
        if node.get("nodeType") != "YulFunctionCall":
            return _ExprValue(yul_expression(node))

        function_name = yul_expression(node.get("functionName"))
        raw_nodes = list(node.get("arguments") or [])
        evaluated: list[_ExprValue | None] = [None] * len(raw_nodes)
        for index in range(len(raw_nodes) - 1, -1, -1):
            evaluated[index] = self._lower_expr(raw_nodes[index])
        values = [item or _ExprValue(yul_expression(raw_nodes[index])) for index, item in enumerate(evaluated)]
        evaluated_args = [item.text for item in values]
        raw_args = [yul_expression(item) for item in raw_nodes]
        expression = f"{function_name}({', '.join(evaluated_args)})"
        raw_expression = f"{function_name}({', '.join(raw_args)})"
        atomic_kind = _ATOMIC_KINDS.get(function_name, "ValueCompute")
        returns_value = function_name not in _VOID_CALLS and not discard_result
        result = self._new_temp() if returns_value else None
        normalized_context = self.context if root else "value"
        atom = self._emit(
            operation=function_name,
            atomic_kind=atomic_kind,
            result=result,
            results=[result] if result else [],
            arguments=evaluated_args,
            raw_arguments=raw_args,
            expression=expression,
            normalized_expression=normalize_expr(expression, context=normalized_context),
            dependencies=[item.producer for item in values if item.producer],
            side_effect=atomic_kind in _SIDE_EFFECT_KINDS,
            semantic_sink=atomic_kind in _SINK_KINDS,
            root_operation=root,
            source_span=str(node.get("src") or self.statement.get("src") or ""),
        )
        return _ExprValue(result or raw_expression, atom.atom_id)

    def _emit_assignment(self, targets: list[str], value: _ExprValue, *, declaration: bool) -> None:
        if not targets:
            return
        operation = "declare_assign" if declaration else "assign"
        self._emit(
            operation=operation,
            atomic_kind="ValueDeclare" if declaration else "ValueAssign",
            result=targets[0] if len(targets) == 1 else None,
            results=targets,
            arguments=[value.text],
            raw_arguments=[yul_expression(self.statement.get("value"))],
            expression=f"{', '.join(targets)} := {value.text}",
            normalized_expression=f"{', '.join(targets)} = {normalize_expr(value.text)}",
            dependencies=[value.producer] if value.producer else [],
            root_operation=True,
        )

    def _emit_control(self, operation: str, atomic_kind: str, value: _ExprValue) -> None:
        self._emit(
            operation=operation,
            atomic_kind=atomic_kind,
            result=None,
            results=[],
            arguments=[value.text],
            raw_arguments=[value.text],
            expression=f"{operation}({value.text})",
            normalized_expression=normalize_expr(value.text, context="condition"),
            dependencies=[value.producer] if value.producer else [],
            control_only=True,
            root_operation=True,
        )

    def _emit(
        self,
        *,
        operation: str,
        atomic_kind: str,
        result: str | None,
        results: list[str],
        arguments: list[str],
        raw_arguments: list[str],
        expression: str,
        normalized_expression: str,
        dependencies: list[str] | None = None,
        side_effect: bool = False,
        semantic_sink: bool = False,
        control_only: bool = False,
        root_operation: bool = False,
        source_span: str | None = None,
    ) -> YulAtomicOperation:
        self.counter += 1
        atom = YulAtomicOperation(
            atom_id=f"yul_atom_b{self.assembly_block_id}_{self.prefix}_{self.cfg_node_id}_{self.counter}",
            function_id=self.function_id,
            assembly_block_id=self.assembly_block_id,
            cfg_node_id=self.cfg_node_id,
            cfg_block_id=self.cfg_block_id,
            yul_function=self.yul_function,
            stmt_ref=self.stmt_ref,
            stmt_refs=[self.stmt_ref] if self.stmt_ref else [],
            source_span=source_span or str(self.statement.get("src") or ""),
            source_statement=yul_statement_text(self.statement),
            source_node_type=str(self.statement.get("nodeType") or ""),
            context=self.context,
            operation=operation,
            atomic_kind=atomic_kind,
            result=result,
            results=list(results),
            arguments=list(arguments),
            raw_arguments=list(raw_arguments),
            expression=expression,
            execution_expression=expression,
            normalized_expression=normalized_expression,
            operation_order=self.counter,
            dependencies=_unique(dependencies or []),
            side_effect=side_effect,
            semantic_sink=semantic_sink,
            control_only=control_only,
            root_operation=root_operation,
        )
        self.operations.append(atom)
        return atom

    def _new_temp(self) -> str:
        return f"__sseir_eval_{self.prefix}_{self.counter + 1}"

    @staticmethod
    def _assigned_names(node: Json) -> list[str]:
        field_name = "variables" if node.get("nodeType") == "YulVariableDeclaration" else "variableNames"
        return [
            rendered
            for item in node.get(field_name) or []
            if isinstance(item, dict) and (rendered := yul_expression(item)) not in {"", "?"}
        ]


class YulAtomicOperationExtractor:
    """Lower Yul AST expressions into one-operation records before semantic lifting."""

    schema = "s-seir-yul-atomic-operations/v1"

    def extract(self, unit: Any) -> Json:
        all_operations: list[Json] = []
        block_payloads: list[Json] = []
        lookup = self._statement_lookup(unit)
        sequence = 0
        for block in unit.assembly_blocks:
            cfg = build_yul_cfg(block.yul_ast)
            mapping = node_ast_mapping(block, cfg)
            operations: list[Json] = []
            node_evaluations: list[Json] = []
            local_function_payloads: list[Json] = []
            for cfg_node in cfg.nodes:
                if cfg_node.kind in _SKIP_CFG_KINDS:
                    continue
                statement = mapping.get(cfg_node.node_id)
                if not isinstance(statement, dict):
                    continue
                stmt_ref = self._stmt_ref(lookup, block.block_id, statement)
                context = self._context(statement)
                atomizer = _StatementAtomizer(
                    function_id=unit.function_id,
                    assembly_block_id=block.block_id,
                    cfg_node_id=cfg_node.node_id,
                    stmt_ref=stmt_ref,
                    statement=statement,
                    context=context,
                )
                atoms, final = atomizer.atomize()
                if not atoms:
                    continue
                atom_dicts = []
                for atom in atoms:
                    sequence += 1
                    item = atom.to_dict()
                    item["sequence"] = sequence
                    atom_dicts.append(item)
                    operations.append(item)
                    all_operations.append(item)
                call_steps = [self._evaluation_step(item) for item in atom_dicts if self._is_call_atom(item)]
                node_evaluations.append({
                    "cfg_node_id": cfg_node.node_id,
                    "cfg_block_id": f"bb_asm{block.block_id}_n{cfg_node.node_id}",
                    "stmt_ref": stmt_ref,
                    "context": context,
                    "final": final,
                    "final_normalized": normalize_expr(final, context=context) if final else None,
                    "steps": call_steps,
                    "atom_ids": [item["atom_id"] for item in atom_dicts],
                })
            definitions = {
                str(item.get("name") or "<anonymous>"): item
                for item in yul_function_definitions(block.yul_ast)
            }
            for function_name, function_cfg in cfg.function_cfgs.items():
                definition = definitions.get(function_name) or {}
                body = definition.get("body") if isinstance(definition, dict) else None
                if not isinstance(body, dict):
                    continue
                local_mapping = node_ast_mapping(SimpleNamespace(yul_ast=body), function_cfg)
                local_operations: list[Json] = []
                local_evaluations: list[Json] = []
                namespace = f"bb_asm{block.block_id}_fn_{_safe(function_name)}"
                for cfg_node in function_cfg.nodes:
                    if cfg_node.kind in _SKIP_CFG_KINDS:
                        continue
                    statement = local_mapping.get(cfg_node.node_id)
                    if not isinstance(statement, dict):
                        continue
                    stmt_ref = self._stmt_ref(lookup, block.block_id, statement)
                    context = self._context(statement)
                    atomizer = _StatementAtomizer(
                        function_id=unit.function_id,
                        assembly_block_id=block.block_id,
                        cfg_node_id=cfg_node.node_id,
                        cfg_block_id=f"{namespace}_n{cfg_node.node_id}",
                        yul_function=function_name,
                        stmt_ref=stmt_ref,
                        statement=statement,
                        context=context,
                    )
                    atoms, final = atomizer.atomize()
                    if not atoms:
                        continue
                    atom_dicts = []
                    for atom in atoms:
                        sequence += 1
                        item = atom.to_dict()
                        item["sequence"] = sequence
                        atom_dicts.append(item)
                        local_operations.append(item)
                        operations.append(item)
                        all_operations.append(item)
                    local_evaluations.append({
                        "cfg_node_id": cfg_node.node_id,
                        "cfg_block_id": f"{namespace}_n{cfg_node.node_id}",
                        "stmt_ref": stmt_ref,
                        "context": context,
                        "final": final,
                        "final_normalized": normalize_expr(final, context=context) if final else None,
                        "steps": [self._evaluation_step(item) for item in atom_dicts if self._is_call_atom(item)],
                        "atom_ids": [item["atom_id"] for item in atom_dicts],
                    })
                local_function_payloads.append({
                    "name": function_name,
                    "operations": local_operations,
                    "node_evaluations": local_evaluations,
                })
            block_payloads.append({
                "assembly_block_id": block.block_id,
                "operations": operations,
                "node_evaluations": node_evaluations,
                "local_functions": local_function_payloads,
            })
        return {
            "schema": self.schema,
            "function_id": unit.function_id,
            "contract": unit.contract,
            "signature": unit.signature,
            "evaluation_model": "yul_ast_right_to_left_function_call_arguments",
            "assembly_blocks": block_payloads,
            "operations": all_operations,
        }

    @staticmethod
    def attach_to_control(control: Json, table: Json) -> None:
        operations_by_block: dict[str, list[Json]] = {}
        for operation in table.get("operations") or []:
            operations_by_block.setdefault(str(operation.get("cfg_block_id") or ""), []).append(operation)
        for block in control.get("blocks") or []:
            block_id = str(block.get("block_id") or "")
            if block_id in operations_by_block:
                block.setdefault("attrs", {})["yul_atomic_operations"] = operations_by_block[block_id]

    @staticmethod
    def evaluation_for_node(table: Json | None, assembly_block_id: int, cfg_node_id: int) -> Json | None:
        for block in (table or {}).get("assembly_blocks") or []:
            if int(block.get("assembly_block_id", -1)) != int(assembly_block_id):
                continue
            for item in block.get("node_evaluations") or []:
                if int(item.get("cfg_node_id", -1)) != int(cfg_node_id):
                    continue
                return {
                    "evaluation_model": "yul_ast_right_to_left_function_call_arguments",
                    "atomization_model": "yul_atomic_operation_table",
                    "final": item.get("final"),
                    "final_normalized": item.get("final_normalized"),
                    "steps": list(item.get("steps") or []),
                    "atom_ids": list(item.get("atom_ids") or []),
                }
        return None

    @staticmethod
    def operations_for_node(table: Json | None, assembly_block_id: int, cfg_node_id: int) -> list[Json]:
        return [
            item for item in (table or {}).get("operations") or []
            if int(item.get("assembly_block_id", -1)) == int(assembly_block_id)
            and int(item.get("cfg_node_id", -1)) == int(cfg_node_id)
            and item.get("yul_function") is None
        ]

    @classmethod
    def assignment_for_node(
        cls,
        table: Json | None,
        assembly_block_id: int,
        cfg_node_id: int,
    ) -> Json | None:
        """Return the canonical root binding emitted by Yul atomization."""
        assignments = [
            item
            for item in cls.operations_for_node(table, assembly_block_id, cfg_node_id)
            if item.get("atomic_kind") in {"ValueAssign", "ValueDeclare"}
            and item.get("root_operation")
        ]
        return assignments[0] if len(assignments) == 1 else None

    @staticmethod
    def _statement_lookup(unit: Any) -> dict[tuple[int, str, str], str]:
        out: dict[tuple[int, str, str], str] = {}
        for stmt in unit.source_statements:
            if stmt.lang != "yul":
                continue
            block_id = int((stmt.origin or {}).get("assembly_block") or str(stmt.block_id or "0").removeprefix("asm_block_") or 0)
            out[(block_id, stmt.src, stmt.text)] = stmt.stmt_id
        return out

    @staticmethod
    def _stmt_ref(lookup: dict[tuple[int, str, str], str], block_id: int, statement: Json) -> str | None:
        src = str(statement.get("src") or "")
        text = yul_statement_text(statement)
        exact = lookup.get((block_id, src, text))
        if exact:
            return exact
        for (candidate_block, candidate_src, _candidate_text), stmt_ref in lookup.items():
            if candidate_block == block_id and candidate_src == src:
                return stmt_ref
        return None

    @staticmethod
    def _context(statement: Json) -> str:
        node_type = statement.get("nodeType")
        if node_type == "YulIf":
            return "condition"
        if node_type == "YulForLoop":
            return "loop_condition"
        if node_type == "YulSwitch":
            return "switch"
        if node_type in {"YulVariableDeclaration", "YulAssignment"}:
            return "value"
        return "statement"

    @staticmethod
    def _is_call_atom(atom: Json) -> bool:
        return atom.get("atomic_kind") not in {
            "ValueAssign", "ValueDeclare", "BranchCondition", "LoopCondition", "SwitchCondition",
            "ControlTransfer",
        }

    @staticmethod
    def _evaluation_step(atom: Json) -> Json:
        return {
            "order": atom.get("operation_order"),
            "atom_id": atom.get("atom_id"),
            "temp": atom.get("result"),
            "expression": atom.get("execution_expression") or atom.get("expression"),
            "execution_expression": atom.get("execution_expression") or atom.get("expression"),
            "expression_normalized": atom.get("normalized_expression"),
            "call": atom.get("operation"),
            "raw_args": list(atom.get("raw_arguments") or []),
            "evaluated_args": list(atom.get("arguments") or []),
            "dependencies": list(atom.get("dependencies") or []),
        }


def build_yul_atomic_operation_payload(functions: list[Any], *, source: str | None = None) -> Json:
    tables = [
        table for function in functions
        if (table := getattr(function, "_sseir_yul_atomic_operations", None)) is not None
    ]
    return {
        "schema": "s-seir-yul-atomic-operation-program/v1",
        "source": source,
        "functions": tables,
    }


def write_yul_atomic_operation_json(functions: list[Any], path: Path, *, source: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(build_yul_atomic_operation_payload(functions, source=source), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
