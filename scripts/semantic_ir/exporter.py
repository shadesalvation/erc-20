"""JSON and audit-text views of the lossless Semantic IR object graph."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model import SemanticFunction, SemanticOperation, SemanticProgram


def write_semantic_ir_json(path: Path, program: SemanticProgram, *, include_analysis_indexes: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(program.to_dict(include_analysis_indexes=include_analysis_indexes), ensure_ascii=False, indent=2), encoding="utf-8")


class SemanticIRTextRenderer:
    """Render an audit view; it does not construct a second IR."""

    def render(self, program: SemanticProgram) -> str:
        lines = [f"SemanticProgram {program.schema}", f"Source: {program.source or '<unknown>'}", ""]
        for index, function in enumerate(program.functions):
            if index:
                lines.extend(["", ""])
            lines.extend(self._function(function))
        return "\n".join(lines).rstrip() + "\n"

    def _function(self, function: SemanticFunction) -> list[str]:
        lines = [f"Function {function.function_id}", f"    signature = {function.signature or function.function}", ""]
        for index, block in enumerate(function.blocks.values()):
            if index:
                lines.append("")
            lines.append(f"{block.block_id}:")
            for operation in block.operations:
                lines.append("    " + self._operation(operation))
            lines.append("    " + self._terminator(block.terminator))
        if function.unplaced_operations:
            lines.extend(["", "UnplacedOperations:"])
            for operation in function.unplaced_operations:
                lines.append("    " + self._operation(operation))
        lines.extend(["", "CFGEdges:"])
        for edge in function.edges:
            suffix = f" [{edge.kind}]" if edge.kind else ""
            lines.append(f"    {edge.source} -> {edge.target}{suffix}")
        if function.locations:
            lines.extend(["", "Locations:"])
            for location in function.locations.values():
                lines.append(f"    {location.location_id} = {json.dumps(location.source_location, ensure_ascii=False, sort_keys=True)} origin_facts=[{', '.join(location.origin_fact_ids)}]")
        return lines

    @staticmethod
    def _operation(operation: SemanticOperation) -> str:
        fields: list[str] = [f"{operation.operation_id} {operation.kind}", f"fact={operation.fact_id}"]
        if operation.condition is not None:
            fields.append(f"condition={SemanticIRTextRenderer._value(operation.condition)}")
        if operation.lvalue is not None:
            fields.append(f"lvalue={SemanticIRTextRenderer._value(operation.lvalue)}")
        if operation.rvalue is not None:
            fields.append(f"rvalue={SemanticIRTextRenderer._value(operation.rvalue)}")
        if operation.location:
            fields.append(f"location={operation.location}")
        if operation.semantic:
            fields.append("semantic=" + json.dumps(operation.semantic, ensure_ascii=False, sort_keys=True))
        return " | ".join(fields)

    @staticmethod
    def _terminator(terminator: Any) -> str:
        if terminator.kind == "Branch":
            return f"Branch({SemanticIRTextRenderer._value(terminator.condition)}, {', '.join(terminator.targets)})"
        if terminator.kind == "Goto":
            return f"Goto({terminator.targets[0]})"
        return terminator.kind

    @staticmethod
    def _value(value: Any) -> str:
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)


def render_semantic_ir_text(program: SemanticProgram, *, include_analysis_indexes: bool = False) -> str:
    return SemanticIRTextRenderer().render(program)


def write_semantic_ir_text(path: Path, program: SemanticProgram, *, include_analysis_indexes: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_semantic_ir_text(program, include_analysis_indexes=include_analysis_indexes), encoding="utf-8")
