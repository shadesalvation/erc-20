from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .model import (
    BasicBlock,
    SemanticFunction,
    SemanticInstruction,
    SemanticProgram,
    Terminator,
)


def write_semantic_ir_json(
    path: Path,
    program: SemanticProgram,
    *,
    include_analysis_indexes: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            program.to_dict(include_analysis_indexes=include_analysis_indexes),
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class SemanticIRTextRenderer:
    """Render the mutable IR without changing or re-analyzing it."""

    def __init__(self, *, include_analysis_indexes: bool = False) -> None:
        self.include_analysis_indexes = include_analysis_indexes

    def render(self, program: SemanticProgram) -> str:
        lines = [
            f"SemanticProgram {program.schema}",
            f"Source: {program.source or '<unknown>'}",
            "",
        ]
        for index, function in enumerate(program.functions):
            if index:
                lines.extend(["", ""])
            lines.extend(self._function(function))
        if program.diagnostics:
            lines.extend(["", "ProgramDiagnostics:"])
            for diagnostic in program.diagnostics:
                lines.append(f"    {json.dumps(diagnostic, ensure_ascii=False, sort_keys=True)}")
        return "\n".join(lines).rstrip() + "\n"

    def _function(self, function: SemanticFunction) -> list[str]:
        lines = [
            f"Function {function.function_id}",
            f"    contract = {function.contract or '<unknown>'}",
            f"    signature = {function.signature or function.function}",
            "",
        ]
        for index, block in enumerate(function.blocks.values()):
            if index:
                lines.append("")
            lines.extend(self._block(function, block))
        if function.edges:
            lines.extend(["", "CFGEdges:"])
            for edge in function.edges:
                suffix = f" [{edge.kind}]" if edge.kind else ""
                if edge.predicate:
                    suffix += f" predicate={edge.predicate}"
                lines.append(f"    {edge.source} -> {edge.target}{suffix}")
        if function.data_objects:
            lines.extend(["", "DataObjects:"])
            for data_object in function.data_objects.values():
                values = [
                    self._expression(function, item) or "UnknownExpression()"
                    for item in data_object.values
                ]
                fields = [f"kind={data_object.kind}"]
                for name, expr_id in (
                    ("pointer", data_object.pointer),
                    ("size", data_object.size),
                    ("selector", data_object.selector),
                ):
                    rendered = self._expression(function, expr_id)
                    if rendered is not None:
                        fields.append(f"{name}={rendered}")
                if values:
                    fields.append(f"values=[{', '.join(values)}]")
                if data_object.encoding:
                    fields.append(f"encoding={data_object.encoding}")
                if data_object.complete is not None:
                    fields.append(f"complete={str(data_object.complete).lower()}")
                if data_object.unresolved_reason:
                    fields.append(f"unresolved={data_object.unresolved_reason}")
                lines.append(f"    {data_object.object_id}: " + ", ".join(fields))
        if self.include_analysis_indexes and function.values:
            lines.extend(["", "DefUse:"])
            for value in function.values.values():
                definition = value.definition or "input"
                uses = ", ".join(value.uses) if value.uses else "-"
                lines.append(f"    {value.value_id}: def={definition}, uses=[{uses}]")
        if function.unplaced_facts:
            lines.extend(["", f"UnplacedFacts: [{', '.join(function.unplaced_facts)}]"])
        if function.diagnostics:
            lines.extend(["", "Diagnostics:"])
            for diagnostic in function.diagnostics:
                lines.append(f"    {json.dumps(diagnostic, ensure_ascii=False, sort_keys=True)}")
        return lines

    def _block(self, function: SemanticFunction, block: BasicBlock) -> list[str]:
        languages = block.kind or "unknown"
        lines = [f"{block.block_id} [{languages}]:"]
        if block.predecessors:
            lines.append(f"    // predecessors: {', '.join(block.predecessors)}")
        if not block.instructions:
            lines.append("    // no semantic instructions")
        for instruction in block.instructions:
            lines.extend(self._instruction(function, instruction))
        lines.extend(self._terminator(function, block.terminator))
        return lines

    def _instruction(self, function: SemanticFunction, instruction: SemanticInstruction) -> list[str]:
        origin = ", ".join(instruction.origin_facts) or "-"
        languages = ", ".join(instruction.source_languages) or "unknown"
        lines = [
            f"    // {instruction.instruction_id} | source={languages} | origin_facts=[{origin}]"
        ]
        normalized_condition = self._expression(function, instruction.normalized_condition or instruction.condition)
        execution_condition = self._expression(function, instruction.execution_condition)
        if normalized_condition:
            lines.append(f"    @condition {normalized_condition}")
        if execution_condition and execution_condition != normalized_condition:
            lines.append(f"    @execution-condition {execution_condition}")

        expression = self._expression(function, instruction.normalized_expr or instruction.expression)
        execution_expression = self._expression(function, instruction.execution_expr)
        arguments = [
            self._expression(function, item)
            for item in (instruction.normalized_arguments or instruction.arguments)
        ]
        execution_arguments = [
            self._expression(function, item) for item in instruction.execution_arguments
        ]
        location = self._location(function, instruction.location)
        op = instruction.op

        execution_parts = []
        if execution_expression and execution_expression != expression:
            execution_parts.append(f"expr={execution_expression}")
        if execution_arguments and execution_arguments != arguments:
            execution_parts.append(f"args=[{', '.join(item or 'UnknownExpression()' for item in execution_arguments)}]")
        if execution_parts:
            lines.append(f"    @execution {op}({', '.join(execution_parts)})")

        if op == "Assign":
            text = expression or "UnknownExpression()"
        elif op == "Phi":
            text = self._call("Phi", arguments or self._present(expression))
        elif op == "StateRead":
            text = self._call("StateRead", self._present(location))
        elif op == "StateWrite":
            text = self._call("StateWrite", [*self._present(location), *self._present(expression)])
        elif op == "Delete":
            text = self._call("Delete", self._present(location))
        elif op == "Require":
            text = self._call("Require", self._present(expression))
        elif op == "EventEmit":
            semantic = instruction.attrs.get("semantic") or {}
            event = semantic.get("event") or semantic.get("name")
            text = self._call("EventEmit", [*self._present(str(event) if event else None), *arguments])
        elif op in {"ExternalCall", "LowLevelCall", "PrecompileCall", "StaticCall", "DelegateCall"}:
            semantic = instruction.attrs.get("semantic") or {}
            call_parts = []
            for name in ("target", "value", "gas", "selector_signature", "selector"):
                value = semantic.get(name)
                if value not in {None, ""}:
                    call_parts.append(f"{name}={value}")
            call_parts.extend(f"data={object_id}" for object_id in instruction.data_objects)
            text = self._call(op, call_parts)
        else:
            text = self._call(op, [*self._present(expression), *arguments])

        if instruction.result:
            text = self._assignment(instruction.result, text)
        lines.extend(self._indent(text, 4))
        if instruction.data_objects:
            lines.append(f"    @data-objects [{', '.join(instruction.data_objects)}]")
        return lines

    def _terminator(self, function: SemanticFunction, terminator: Terminator) -> list[str]:
        origin = ", ".join(terminator.origin_facts)
        if origin:
            lines = [f"    // terminator origin_facts=[{origin}]"]
        else:
            lines = []
        condition = self._expression(function, terminator.normalized_condition or terminator.condition)
        execution_condition = self._expression(function, terminator.execution_condition)
        values = [
            self._expression(function, item)
            for item in (terminator.normalized_values or terminator.values)
        ]
        execution_values = [self._expression(function, item) for item in terminator.execution_values]
        if execution_condition and execution_condition != condition:
            lines.append(f"    @execution-condition {execution_condition}")
        if execution_values and execution_values != values:
            lines.append(
                f"    @execution-values [{', '.join(item or 'UnknownExpression()' for item in execution_values)}]"
            )
        if terminator.kind == "Branch":
            text = self._call("Branch", [*self._present(condition), *terminator.targets])
        elif terminator.kind == "Goto":
            text = self._call("Goto", terminator.targets)
        elif terminator.kind in {"Return", "Revert", "Stop"}:
            text = self._call(terminator.kind, values)
        elif terminator.kind == "Fallthrough":
            text = "Fallthrough"
        else:
            text = self._call(terminator.kind, [*self._present(condition), *values, *terminator.targets])
        lines.extend(self._indent(text, 4))
        if terminator.data_objects:
            lines.append(f"    @data-objects [{', '.join(terminator.data_objects)}]")
        return lines

    def _expression(
        self,
        function: SemanticFunction,
        expr_id: str | None,
        active: frozenset[str] = frozenset(),
    ) -> str | None:
        if not expr_id:
            return None
        if expr_id in active:
            return f"Cycle({expr_id})"
        expression = function.expressions.get(expr_id)
        if not expression:
            return f"MissingExpression({expr_id})"
        if expression.kind == "Constant":
            if expression.raw:
                return expression.raw
            if isinstance(expression.value, bool):
                return str(expression.value).lower()
            return str(expression.value)
        if expression.kind == "Variable":
            return expression.name or expression.raw or expr_id
        if expression.kind == "OpaqueExpression":
            return f"OpaqueExpression({json.dumps(expression.raw or '', ensure_ascii=False)})"
        operands = [
            self._expression(function, operand, active | {expr_id}) or "UnknownExpression()"
            for operand in expression.operands
        ]
        if expression.kind == "Call":
            return self._call(expression.callee or "Call", operands)
        return self._call(expression.kind, operands)

    def _location(self, function: SemanticFunction, location_id: str | None) -> str | None:
        if not location_id:
            return None
        location = function.locations.get(location_id)
        if not location:
            return f"MissingLocation({location_id})"
        if location.kind == "StateVariableLocation":
            return self._call(location.kind, self._present(location.base or location.access))
        if location.kind == "MappingLocation":
            keys = [self._expression(function, key) or "UnknownExpression()" for key in location.keys]
            return self._call(location.kind, [
                f"base = {location.base or '<unknown>'}",
                f"keys = [{', '.join(keys)}]",
            ])
        arguments = []
        for name, value in (
            ("base", location.base),
            ("member", location.member),
            ("slot", self._expression(function, location.slot)),
            ("access", location.access),
        ):
            if value is not None:
                arguments.append(f"{name} = {value}")
        if location.keys:
            keys = [self._expression(function, key) or "UnknownExpression()" for key in location.keys]
            arguments.append(f"keys = [{', '.join(keys)}]")
        return self._call(location.kind, arguments)

    @staticmethod
    def _present(value: str | None) -> list[str]:
        return [value] if value else []

    @staticmethod
    def _assignment(result: str, expression: str) -> str:
        lines = expression.splitlines() or [expression]
        lines[0] = f"{result} = {lines[0]}"
        return "\n".join(lines)

    @staticmethod
    def _call(name: str, arguments: Iterable[str]) -> str:
        args = [str(item) for item in arguments if item not in {None, ""}]
        if not args:
            return f"{name}()"
        if all("\n" not in item for item in args) and len(name) + sum(len(item) for item in args) + 2 * len(args) <= 88:
            return f"{name}({', '.join(args)})"
        lines = [f"{name}("]
        for index, argument in enumerate(args):
            nested = argument.splitlines()
            nested = [f"    {line}" for line in nested]
            if index + 1 < len(args):
                nested[-1] += ","
            lines.extend(nested)
        lines.append(")")
        return "\n".join(lines)

    @staticmethod
    def _indent(text: str, spaces: int) -> list[str]:
        prefix = " " * spaces
        return [f"{prefix}{line}" if line else "" for line in text.splitlines()]


def render_semantic_ir_text(
    program: SemanticProgram,
    *,
    include_analysis_indexes: bool = False,
) -> str:
    return SemanticIRTextRenderer(
        include_analysis_indexes=include_analysis_indexes
    ).render(program)


def write_semantic_ir_text(
    path: Path,
    program: SemanticProgram,
    *,
    include_analysis_indexes: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_semantic_ir_text(
            program,
            include_analysis_indexes=include_analysis_indexes,
        ),
        encoding="utf-8",
    )
