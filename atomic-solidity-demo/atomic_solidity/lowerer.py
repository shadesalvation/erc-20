from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atomic_solidity.ast_index import ASTIndex, DeclarationKind
from atomic_solidity.ast_loader import load_typed_ast
from atomic_solidity.atomic_ir import (
    AtomicContract,
    AtomicFunction,
    AtomicOperation,
    AtomicProgram,
    AtomicSourceFile,
    AtomicVariable,
    BasicBlock,
    LocationRef,
    SourceInfo,
)
from atomic_solidity.diagnostics import Diagnostic


@dataclass
class IdAllocator:
    value_counter: int = 0
    location_counter: int = 0
    block_counter: int = 0
    operation_counter: int = 0

    def value(self) -> str:
        result = f"v{self.value_counter}"
        self.value_counter += 1
        return result

    def location(self) -> str:
        result = f"loc{self.location_counter}"
        self.location_counter += 1
        return result

    def block(self) -> str:
        result = f"block_{self.block_counter}"
        self.block_counter += 1
        return result

    def operation(self) -> str:
        result = f"op_{self.operation_counter}"
        self.operation_counter += 1
        return result


class LoweringContext:
    def __init__(
        self,
        *,
        index: ASTIndex,
        source_file: str,
        diagnostics: list[Diagnostic],
        ids: IdAllocator,
    ) -> None:
        self.index = index
        self.source_file = source_file
        self.diagnostics = diagnostics
        self.ids = ids
        entry = BasicBlock(id=self.ids.block())
        self.blocks: list[BasicBlock] = [entry]
        self.current_block = entry
        self.unchecked_depth = 0
        self.loop_targets: list[tuple[BasicBlock, BasicBlock]] = []

    def source_info(self, node: dict[str, Any] | None) -> SourceInfo:
        if not node:
            return SourceInfo(None, None, self.source_file, None)
        return SourceInfo(
            ast_id=node.get("id") if isinstance(node.get("id"), int) else None,
            src=node.get("src"),
            source_file=self.source_file,
            original_node_type=node.get("nodeType"),
        )

    def common_attributes(self, node: dict[str, Any] | None) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        if not node:
            return attrs
        if "typeDescriptions" in node:
            attrs["type_descriptions"] = node["typeDescriptions"]
            attrs["type"] = node["typeDescriptions"].get("typeString")
        if "referencedDeclaration" in node:
            attrs["referenced_declaration"] = node["referencedDeclaration"]
        if "nodeType" in node:
            attrs["node_type"] = node["nodeType"]
        return attrs

    def emit(
        self,
        kind: str,
        *,
        inputs: list[str] | None = None,
        output: str | None = None,
        attributes: dict[str, Any] | None = None,
        source_node: dict[str, Any] | None = None,
        effects: list[str] | None = None,
        may_revert: bool = False,
        terminator: bool = False,
    ) -> AtomicOperation:
        merged_attributes = self.common_attributes(source_node)
        if attributes:
            merged_attributes.update(attributes)
        op = AtomicOperation(
            id=self.ids.operation(),
            kind=kind,
            inputs=inputs or [],
            output=output,
            attributes=merged_attributes,
            source=self.source_info(source_node),
            effects=effects or [],
            may_revert=may_revert,
        )
        if terminator:
            self.current_block.terminator = op
        else:
            self.current_block.operations.append(op)
        return op

    def diagnostic(
        self,
        code: str,
        message: str,
        node: dict[str, Any] | None,
        *,
        severity: str = "warning",
        details: dict[str, Any] | None = None,
    ) -> None:
        self.diagnostics.append(
            Diagnostic(
                code=code,
                message=message,
                severity=severity,
                node_type=node.get("nodeType") if node else None,
                ast_id=node.get("id") if node and isinstance(node.get("id"), int) else None,
                src=node.get("src") if node else None,
                source_file=self.source_file,
                details=details,
            )
        )

    def new_block(self) -> BasicBlock:
        block = BasicBlock(id=self.ids.block())
        self.blocks.append(block)
        return block

    def add_edge(self, start: BasicBlock, end: BasicBlock) -> None:
        if end.id not in start.successors:
            start.successors.append(end.id)
        if start.id not in end.predecessors:
            end.predecessors.append(start.id)

    def jump_to(self, target: BasicBlock, source_node: dict[str, Any] | None = None) -> None:
        if self.current_block.terminator is None:
            self.terminate_with_jump(target, source_node)
        self.current_block = target

    def terminate_with_jump(
        self, target: BasicBlock, source_node: dict[str, Any] | None = None
    ) -> None:
        if self.current_block.terminator is not None:
            return
        self.emit(
            "JUMP",
            attributes={"target": target.id},
            source_node=source_node,
            terminator=True,
        )
        self.add_edge(self.current_block, target)

    def branch_to(
        self,
        condition: str,
        true_block: BasicBlock,
        false_block: BasicBlock,
        source_node: dict[str, Any] | None,
    ) -> None:
        self.emit(
            "BRANCH",
            inputs=[condition],
            attributes={"true_block": true_block.id, "false_block": false_block.id},
            source_node=source_node,
            terminator=True,
        )
        self.add_edge(self.current_block, true_block)
        self.add_edge(self.current_block, false_block)

    def read_location(self, location: LocationRef, source_node: dict[str, Any]) -> str:
        output = self.ids.value()
        if location.kind == "storage":
            self.emit(
                "STORAGE_READ",
                inputs=[location.id],
                output=output,
                attributes=location.attributes,
                source_node=source_node,
                effects=["storage_read"],
            )
        else:
            self.emit(
                "READ_LOCAL",
                output=output,
                attributes={
                    "name": location.name,
                    "declaration_ast_id": location.declaration_ast_id,
                    "declaration_kind": location.kind,
                    "type": location.type,
                },
                source_node=source_node,
            )
        return output

    def write_location(
        self, location: LocationRef, value: str, source_node: dict[str, Any]
    ) -> None:
        if location.kind == "storage":
            self.emit(
                "STORAGE_WRITE",
                inputs=[location.id, value],
                attributes=location.attributes,
                source_node=source_node,
                effects=["storage_write"],
            )
        else:
            self.emit(
                "WRITE_LOCAL",
                inputs=[value],
                attributes={
                    "name": location.name,
                    "declaration_ast_id": location.declaration_ast_id,
                    "declaration_kind": location.kind,
                    "type": location.type,
                },
                source_node=source_node,
                effects=["local_write"],
            )


def lower_file(source_path: Path) -> AtomicProgram:
    loaded = load_typed_ast(source_path)
    diagnostics = list(loaded.diagnostics)
    index = ASTIndex(loaded.ast, loaded.contracts)
    ids = IdAllocator()
    contracts: list[AtomicContract] = []
    for node in loaded.ast.get("nodes", []):
        if node.get("nodeType") != "ContractDefinition":
            continue
        functions = [
            _lower_function(function, index, loaded.source_name, diagnostics, ids)
            for function in node.get("nodes", [])
            if function.get("nodeType") == "FunctionDefinition" and function.get("body")
        ]
        contracts.append(
            AtomicContract(
                name=node.get("name", "<anonymous>"),
                ast_id=node.get("id", -1),
                functions=functions,
            )
        )
    return AtomicProgram(
        compiler_version=loaded.compiler_version,
        source_files=[AtomicSourceFile(path=loaded.source_name, contracts=contracts)],
        diagnostics=diagnostics,
    )


def _atomic_variable(node: dict[str, Any], kind: DeclarationKind) -> AtomicVariable:
    type_string = node.get("typeDescriptions", {}).get("typeString")
    return AtomicVariable(
        name=node.get("name") or f"<unnamed:{node.get('id')}>",
        ast_id=node.get("id", -1),
        type=type_string,
        kind=kind.value,
    )


def _lower_function(
    node: dict[str, Any],
    index: ASTIndex,
    source_file: str,
    diagnostics: list[Diagnostic],
    ids: IdAllocator,
) -> AtomicFunction:
    from atomic_solidity.statements import lower_statement

    context = LoweringContext(
        index=index, source_file=source_file, diagnostics=diagnostics, ids=ids
    )
    for statement in node.get("body", {}).get("statements", []):
        lower_statement(statement, context)
        if context.current_block.terminator and context.current_block is context.blocks[-1]:
            break
    parameters = [
        _atomic_variable(parameter, DeclarationKind.PARAMETER)
        for parameter in node.get("parameters", {}).get("parameters", [])
    ]
    return_parameters = [
        _atomic_variable(parameter, DeclarationKind.RETURN_PARAMETER)
        for parameter in node.get("returnParameters", {}).get("parameters", [])
    ]
    return AtomicFunction(
        name=node.get("name") or "<fallback>",
        ast_id=node.get("id", -1),
        parameters=parameters,
        return_parameters=return_parameters,
        blocks=context.blocks,
        entry_block=context.blocks[0].id,
    )
