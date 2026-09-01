from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


Json = dict[str, Any]


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value


@dataclass
class RewriteRecord:
    pass_name: str
    action: str
    target_id: str
    reason: str
    before: Any = None
    after: Any = None

    def to_dict(self) -> Json:
        return _clean(asdict(self))


@dataclass
class ExpressionNode:
    expr_id: str
    kind: str
    operands: list[str] = field(default_factory=list)
    value: Any = None
    name: str | None = None
    operator: str | None = None
    callee: str | None = None
    type_hint: str | None = None
    raw: str | None = None
    origin_facts: list[str] = field(default_factory=list)
    attrs: Json = field(default_factory=dict)
    rewrite_history: list[RewriteRecord] = field(default_factory=list)

    def to_dict(self) -> Json:
        result = asdict(self)
        result["rewrite_history"] = [item.to_dict() for item in self.rewrite_history]
        return _clean(result)


@dataclass
class LocationNode:
    location_id: str
    kind: str
    base: str | None = None
    keys: list[str] = field(default_factory=list)
    member: str | None = None
    slot: str | None = None
    access: str | None = None
    type_hint: str | None = None
    origin_facts: list[str] = field(default_factory=list)
    attrs: Json = field(default_factory=dict)
    rewrite_history: list[RewriteRecord] = field(default_factory=list)

    def to_dict(self) -> Json:
        result = asdict(self)
        result["rewrite_history"] = [item.to_dict() for item in self.rewrite_history]
        return _clean(result)


@dataclass
class DataObjectNode:
    """Structured data carried through memory without exposing MemorySSA internals."""

    object_id: str
    kind: str
    pointer: str | None = None
    size: str | None = None
    selector: str | None = None
    values: list[str] = field(default_factory=list)
    encoding: str | None = None
    complete: bool | None = None
    unresolved_reason: str | None = None
    origin_facts: list[str] = field(default_factory=list)
    origin_effects: list[str] = field(default_factory=list)
    stmt_refs: list[str] = field(default_factory=list)
    attrs: Json = field(default_factory=dict)
    rewrite_history: list[RewriteRecord] = field(default_factory=list)

    def expression_refs(self) -> Iterable[str]:
        for expr_id in (self.pointer, self.size, self.selector, *self.values):
            if expr_id:
                yield expr_id

    def to_dict(self) -> Json:
        result = asdict(self)
        result["rewrite_history"] = [item.to_dict() for item in self.rewrite_history]
        return _clean(result)


@dataclass
class SemanticInstruction:
    instruction_id: str
    op: str
    result: str | None = None
    result_value: str | None = None
    execution_expr: str | None = None
    normalized_expr: str | None = None
    expression: str | None = None
    location: str | None = None
    execution_arguments: list[str] = field(default_factory=list)
    normalized_arguments: list[str] = field(default_factory=list)
    arguments: list[str] = field(default_factory=list)
    execution_condition: str | None = None
    normalized_condition: str | None = None
    condition: str | None = None
    data_objects: list[str] = field(default_factory=list)
    source_languages: list[str] = field(default_factory=list)
    origin_facts: list[str] = field(default_factory=list)
    origin_effects: list[str] = field(default_factory=list)
    stmt_refs: list[str] = field(default_factory=list)
    attrs: Json = field(default_factory=dict)
    rewrite_history: list[RewriteRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        # ``expression`` / ``arguments`` / ``condition`` remain normalized-view
        # aliases for consumers of semantic-ir/v1.
        self.normalized_expr = self.normalized_expr or self.expression
        self.execution_expr = self.execution_expr or self.expression or self.normalized_expr
        self.expression = self.normalized_expr
        self.normalized_arguments = self.normalized_arguments or list(self.arguments)
        self.execution_arguments = self.execution_arguments or list(self.arguments or self.normalized_arguments)
        self.arguments = list(self.normalized_arguments)
        self.normalized_condition = self.normalized_condition or self.condition
        self.execution_condition = self.execution_condition or self.condition or self.normalized_condition
        self.condition = self.normalized_condition

    def execution_expression_refs(self) -> Iterable[str]:
        if self.execution_expr:
            yield self.execution_expr
        if self.execution_condition:
            yield self.execution_condition
        yield from self.execution_arguments

    def normalized_expression_refs(self) -> Iterable[str]:
        if self.normalized_expr:
            yield self.normalized_expr
        if self.normalized_condition:
            yield self.normalized_condition
        yield from self.normalized_arguments

    def expression_refs(self) -> Iterable[str]:
        yield from self.execution_expression_refs()

    def to_dict(self) -> Json:
        result = asdict(self)
        result["rewrite_history"] = [item.to_dict() for item in self.rewrite_history]
        return _clean(result)


@dataclass
class Terminator:
    kind: str
    execution_condition: str | None = None
    normalized_condition: str | None = None
    condition: str | None = None
    targets: list[str] = field(default_factory=list)
    execution_values: list[str] = field(default_factory=list)
    normalized_values: list[str] = field(default_factory=list)
    values: list[str] = field(default_factory=list)
    data_objects: list[str] = field(default_factory=list)
    origin_facts: list[str] = field(default_factory=list)
    stmt_refs: list[str] = field(default_factory=list)
    attrs: Json = field(default_factory=dict)
    rewrite_history: list[RewriteRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.normalized_condition = self.normalized_condition or self.condition
        self.execution_condition = self.execution_condition or self.condition or self.normalized_condition
        self.condition = self.normalized_condition
        self.normalized_values = self.normalized_values or list(self.values)
        self.execution_values = self.execution_values or list(self.values or self.normalized_values)
        self.values = list(self.normalized_values)

    def execution_expression_refs(self) -> Iterable[str]:
        if self.execution_condition:
            yield self.execution_condition
        yield from self.execution_values

    def normalized_expression_refs(self) -> Iterable[str]:
        if self.normalized_condition:
            yield self.normalized_condition
        yield from self.normalized_values

    def expression_refs(self) -> Iterable[str]:
        yield from self.execution_expression_refs()

    def to_dict(self) -> Json:
        result = asdict(self)
        result["rewrite_history"] = [item.to_dict() for item in self.rewrite_history]
        return _clean(result)


@dataclass
class BasicBlock:
    block_id: str
    kind: str
    instructions: list[SemanticInstruction] = field(default_factory=list)
    terminator: Terminator = field(default_factory=lambda: Terminator("Fallthrough"))
    predecessors: list[str] = field(default_factory=list)
    successors: list[str] = field(default_factory=list)
    stmt_refs: list[str] = field(default_factory=list)
    attrs: Json = field(default_factory=dict)

    def to_dict(self) -> Json:
        return _clean({
            "block_id": self.block_id,
            "kind": self.kind,
            "instructions": [item.to_dict() for item in self.instructions],
            "terminator": self.terminator.to_dict(),
            "predecessors": self.predecessors,
            "successors": self.successors,
            "stmt_refs": self.stmt_refs,
            "attrs": self.attrs,
        })


@dataclass
class CFGEdge:
    source: str
    target: str
    kind: str
    predicate: str | None = None

    def to_dict(self) -> Json:
        return _clean(asdict(self))


@dataclass
class ValueRecord:
    value_id: str
    name: str
    definition: str | None = None
    definition_candidates: list[str] = field(default_factory=list)
    uses: list[str] = field(default_factory=list)
    type_hint: str | None = None
    aliases: list[str] = field(default_factory=list)
    kind: str = "ssa"

    def to_dict(self) -> Json:
        return _clean(asdict(self))


@dataclass
class FactGroup:
    group_id: str
    primary_fact: str
    support_facts: list[str]
    reason: str

    def to_dict(self) -> Json:
        return asdict(self)


@dataclass
class SemanticFunction:
    function_id: str
    contract: str
    function: str
    signature: str
    source_statements: list[Json] = field(default_factory=list)
    fact_table: dict[str, Json] = field(default_factory=dict)
    blocks: dict[str, BasicBlock] = field(default_factory=dict)
    edges: list[CFGEdge] = field(default_factory=list)
    expressions: dict[str, ExpressionNode] = field(default_factory=dict)
    locations: dict[str, LocationNode] = field(default_factory=dict)
    data_objects: dict[str, DataObjectNode] = field(default_factory=dict)
    values: dict[str, ValueRecord] = field(default_factory=dict)
    # Runtime-only bridge from Slither SSA spellings to source variable names.
    # It is an analysis index, not part of the core deobfuscation IR.
    value_aliases: dict[str, str] = field(default_factory=dict, repr=False)
    fact_groups: list[FactGroup] = field(default_factory=list)
    unplaced_facts: list[str] = field(default_factory=list)
    rewrite_history: list[RewriteRecord] = field(default_factory=list)
    diagnostics: list[Json] = field(default_factory=list)

    def instruction(self, instruction_id: str) -> SemanticInstruction | None:
        for block in self.blocks.values():
            for instruction in block.instructions:
                if instruction.instruction_id == instruction_id:
                    return instruction
        return None

    def replace_expression(
        self,
        expr_id: str,
        replacement: ExpressionNode,
        *,
        pass_name: str,
        reason: str,
    ) -> None:
        old = self.expressions.get(expr_id)
        replacement.expr_id = expr_id
        record = RewriteRecord(
            pass_name,
            "replace_expression",
            expr_id,
            reason,
            old.to_dict() if old else None,
            replacement.to_dict(),
        )
        replacement.rewrite_history = list(old.rewrite_history if old else []) + [record]
        self.expressions[expr_id] = replacement
        self.rewrite_history.append(record)

    def replace_instruction(
        self,
        instruction_id: str,
        replacement: SemanticInstruction,
        *,
        pass_name: str,
        reason: str,
    ) -> bool:
        for block in self.blocks.values():
            for index, old in enumerate(block.instructions):
                if old.instruction_id != instruction_id:
                    continue
                replacement.instruction_id = instruction_id
                record = RewriteRecord(
                    pass_name,
                    "replace_instruction",
                    instruction_id,
                    reason,
                    old.to_dict(),
                    replacement.to_dict(),
                )
                replacement.rewrite_history = list(old.rewrite_history) + [record]
                block.instructions[index] = replacement
                self.rewrite_history.append(record)
                return True
        return False

    def remove_instruction(self, instruction_id: str, *, pass_name: str, reason: str) -> bool:
        for block in self.blocks.values():
            for index, old in enumerate(block.instructions):
                if old.instruction_id != instruction_id:
                    continue
                block.instructions.pop(index)
                self.rewrite_history.append(RewriteRecord(
                    pass_name,
                    "remove_instruction",
                    instruction_id,
                    reason,
                    old.to_dict(),
                    None,
                ))
                return True
        return False

    def redirect_edge(
        self,
        source: str,
        old_target: str,
        new_target: str,
        *,
        pass_name: str,
        reason: str,
    ) -> bool:
        changed = False
        for edge in self.edges:
            if edge.source == source and edge.target == old_target:
                edge.target = new_target
                changed = True
        if not changed:
            return False
        block = self.blocks.get(source)
        if block:
            block.terminator.targets = [new_target if item == old_target else item for item in block.terminator.targets]
        self.rewrite_history.append(RewriteRecord(
            pass_name,
            "redirect_edge",
            source,
            reason,
            {"target": old_target},
            {"target": new_target},
        ))
        self.rebuild_cfg_links()
        return True

    def remove_block(self, block_id: str, *, pass_name: str, reason: str) -> bool:
        block = self.blocks.get(block_id)
        if not block:
            return False
        touching = [edge.to_dict() for edge in self.edges if edge.source == block_id or edge.target == block_id]
        self.edges = [edge for edge in self.edges if edge.source != block_id and edge.target != block_id]
        del self.blocks[block_id]
        self.rewrite_history.append(RewriteRecord(
            pass_name,
            "remove_block",
            block_id,
            reason,
            {"block": block.to_dict(), "edges": touching},
            None,
        ))
        self.rebuild_cfg_links()
        return True

    def rebuild_cfg_links(self) -> None:
        for block in self.blocks.values():
            block.predecessors.clear()
            block.successors.clear()
        for edge in self.edges:
            source = self.blocks.get(edge.source)
            target = self.blocks.get(edge.target)
            if source and edge.target not in source.successors:
                source.successors.append(edge.target)
            if target and edge.source not in target.predecessors:
                target.predecessors.append(edge.source)

    def to_dict(self, *, include_analysis_indexes: bool = False) -> Json:
        result = {
            "function_id": self.function_id,
            "contract": self.contract,
            "function": self.function,
            "signature": self.signature,
            "source_statements": self.source_statements,
            "fact_table": list(self.fact_table.values()),
            "blocks": [block.to_dict() for block in self.blocks.values()],
            "edges": [edge.to_dict() for edge in self.edges],
            "expressions": [expression.to_dict() for expression in self.expressions.values()],
            "locations": [location.to_dict() for location in self.locations.values()],
            "data_objects": [item.to_dict() for item in self.data_objects.values()],
            "fact_groups": [group.to_dict() for group in self.fact_groups],
            "unplaced_facts": self.unplaced_facts,
            "rewrite_history": [item.to_dict() for item in self.rewrite_history],
            "diagnostics": self.diagnostics,
        }
        if include_analysis_indexes:
            result["analysis_indexes"] = {
                "def_use": [value.to_dict() for value in self.values.values()],
                "value_aliases": dict(self.value_aliases),
            }
        return _clean(result)


@dataclass
class SemanticProgram:
    source: str | None
    functions: list[SemanticFunction]
    schema: str = "semantic-ir/v2"
    diagnostics: list[Json] = field(default_factory=list)

    def to_dict(self, *, include_analysis_indexes: bool = False) -> Json:
        return _clean({
            "schema": self.schema,
            "source": self.source,
            "function_count": len(self.functions),
            "functions": [
                function.to_dict(include_analysis_indexes=include_analysis_indexes)
                for function in self.functions
            ],
            "diagnostics": self.diagnostics,
        })
