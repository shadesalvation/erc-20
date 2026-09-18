"""废弃：旧的统一 Semantic Facts 对象模型。

Semantic IR deliberately does *not* repeat semantic lifting.  A
``SemanticOperation`` is an object-shaped view of exactly one input fact; the
preserved ``source_fact`` snapshot keeps the original fact as evidence while the other
fields are the mutable work surface for later deobfuscation passes.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any

Json = dict[str, Any]


def clean(value: Any) -> Any:
    if is_dataclass(value):
        return clean(asdict(value))
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items() if item is not None}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
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
        return clean(self)


@dataclass
class SemanticLocation:
    """A shared object projection of the fact-provided location dictionary."""

    location_id: str
    source_location: Json
    origin_fact_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> Json:
        return clean(self)


@dataclass
class SemanticOperation:
    """One-to-one, mutable materialization of a single Semantic Fact."""

    operation_id: str
    fact_id: str
    kind: str
    source_lang: str | None
    origin: str | None
    fact_role: str | None
    condition: Any = None
    lvalue: Any = None
    rvalue: Any = None
    reads: list[Any] = field(default_factory=list)
    writes: list[Any] = field(default_factory=list)
    semantic: Json = field(default_factory=dict)
    evidence: Json = field(default_factory=dict)
    cfg_nodes: list[str] = field(default_factory=list)
    stmt_refs: list[str] = field(default_factory=list)
    location: str | None = None
    source_fact: Json = field(default_factory=dict)

    @classmethod
    def from_fact(cls, fact: Json, *, location: str | None = None) -> "SemanticOperation":
        fact_id = str(fact["fact_id"])
        return cls(
            operation_id=f"op_{fact_id}",
            fact_id=fact_id,
            kind=str(fact.get("kind") or "Unknown"),
            source_lang=fact.get("source_lang"),
            origin=fact.get("origin"),
            fact_role=fact.get("fact_role"),
            condition=deepcopy(fact.get("condition")),
            lvalue=deepcopy(fact.get("lvalue")),
            rvalue=deepcopy(fact.get("rvalue")),
            reads=deepcopy(list(fact.get("reads") or [])),
            writes=deepcopy(list(fact.get("writes") or [])),
            semantic=deepcopy(dict(fact.get("semantic") or {})),
            evidence=deepcopy(dict(fact.get("evidence") or {})),
            cfg_nodes=[str(item) for item in fact.get("cfg_nodes") or []],
            stmt_refs=[str(item) for item in fact.get("stmt_refs") or []],
            location=location,
            source_fact=deepcopy(fact),
        )

    def to_dict(self) -> Json:
        return clean(self)


@dataclass
class Terminator:
    """A direct function-CFG terminator, not a newly inferred semantic fact."""

    kind: str
    condition: Any = None
    targets: list[str] = field(default_factory=list)
    source_terminator: Json = field(default_factory=dict)

    def to_dict(self) -> Json:
        return clean(self)


@dataclass
class BasicBlock:
    block_id: str
    kind: str
    source_statements: list[str] = field(default_factory=list)
    operations: list[SemanticOperation] = field(default_factory=list)
    terminator: Terminator = field(default_factory=lambda: Terminator("Unresolved"))
    predecessors: list[str] = field(default_factory=list)
    successors: list[str] = field(default_factory=list)
    attrs: Json = field(default_factory=dict)

    def to_dict(self) -> Json:
        return clean({
            "block_id": self.block_id,
            "kind": self.kind,
            "source_statements": self.source_statements,
            "operations": [item.to_dict() for item in self.operations],
            "terminator": self.terminator.to_dict(),
            "predecessors": self.predecessors,
            "successors": self.successors,
            "attrs": self.attrs,
        })


@dataclass
class CFGEdge:
    source: str
    target: str
    kind: str = "next"
    predicate: str | None = None

    def to_dict(self) -> Json:
        return clean(self)


@dataclass
class SemanticFunction:
    function_id: str
    contract: str
    function: str
    signature: str
    source_statements: list[Json] = field(default_factory=list)
    blocks: dict[str, BasicBlock] = field(default_factory=dict)
    edges: list[CFGEdge] = field(default_factory=list)
    locations: dict[str, SemanticLocation] = field(default_factory=dict)
    unplaced_operations: list[SemanticOperation] = field(default_factory=list)
    diagnostics: list[Json] = field(default_factory=list)
    rewrite_history: list[RewriteRecord] = field(default_factory=list)
    operation_by_fact_id: dict[str, SemanticOperation] = field(default_factory=dict, repr=False)
    operations_by_kind: dict[str, list[str]] = field(default_factory=dict, repr=False)
    operations_by_location: dict[str, list[str]] = field(default_factory=dict, repr=False)
    operations_by_stmt_ref: dict[str, list[str]] = field(default_factory=dict, repr=False)

    def rebuild_cfg_links(self) -> None:
        for block in self.blocks.values():
            block.predecessors.clear(); block.successors.clear()
        for edge in self.edges:
            if edge.source not in self.blocks or edge.target not in self.blocks:
                continue
            if edge.target not in self.blocks[edge.source].successors:
                self.blocks[edge.source].successors.append(edge.target)
            if edge.source not in self.blocks[edge.target].predecessors:
                self.blocks[edge.target].predecessors.append(edge.source)

    def rebuild_indexes(self) -> None:
        self.operation_by_fact_id.clear(); self.operations_by_kind.clear()
        self.operations_by_location.clear(); self.operations_by_stmt_ref.clear()
        operations = [item for block in self.blocks.values() for item in block.operations] + self.unplaced_operations
        for operation in operations:
            self.operation_by_fact_id[operation.fact_id] = operation
            self.operations_by_kind.setdefault(operation.kind, []).append(operation.operation_id)
            if operation.location:
                self.operations_by_location.setdefault(operation.location, []).append(operation.operation_id)
            for stmt_ref in operation.stmt_refs:
                self.operations_by_stmt_ref.setdefault(stmt_ref, []).append(operation.operation_id)

    def get_operation(self, fact_id: str) -> SemanticOperation | None:
        return self.operation_by_fact_id.get(fact_id)

    def replace_operation(self, fact_id: str, replacement: SemanticOperation, *, pass_name: str, reason: str) -> bool:
        """Replace a mutable operation while retaining its source Fact identity."""
        previous = self.get_operation(fact_id)
        if previous is None:
            return False
        if replacement.fact_id != fact_id:
            raise ValueError("a Semantic IR rewrite cannot change fact identity")
        replacement.operation_id = previous.operation_id
        for block in self.blocks.values():
            for index, operation in enumerate(block.operations):
                if operation.fact_id == fact_id:
                    block.operations[index] = replacement
                    self.rewrite_history.append(RewriteRecord(pass_name, "replace_operation", previous.operation_id, reason, previous.to_dict(), replacement.to_dict()))
                    self.rebuild_indexes(); return True
        for index, operation in enumerate(self.unplaced_operations):
            if operation.fact_id == fact_id:
                self.unplaced_operations[index] = replacement
                self.rewrite_history.append(RewriteRecord(pass_name, "replace_operation", previous.operation_id, reason, previous.to_dict(), replacement.to_dict()))
                self.rebuild_indexes(); return True
        return False

    def remove_operation(self, fact_id: str, *, pass_name: str, reason: str) -> bool:
        """Remove only as an explicit later rewrite; retain the audit record."""
        previous = self.get_operation(fact_id)
        if previous is None:
            return False
        collections = [block.operations for block in self.blocks.values()] + [self.unplaced_operations]
        for collection in collections:
            for index, operation in enumerate(collection):
                if operation.fact_id == fact_id:
                    del collection[index]
                    self.rewrite_history.append(RewriteRecord(pass_name, "remove_operation", previous.operation_id, reason, previous.to_dict(), None))
                    self.rebuild_indexes(); return True
        return False

    def redirect_edge(self, source: str, old_target: str, new_target: str, *, pass_name: str, reason: str) -> bool:
        changed = False
        for edge in self.edges:
            if edge.source == source and edge.target == old_target:
                edge.target = new_target; changed = True
        if not changed:
            return False
        block = self.blocks.get(source)
        if block:
            block.terminator.targets = [new_target if target == old_target else target for target in block.terminator.targets]
        self.rebuild_cfg_links()
        self.rewrite_history.append(RewriteRecord(pass_name, "redirect_edge", source, reason, {"target": old_target}, {"target": new_target}))
        return True

    def remove_block(self, block_id: str, *, pass_name: str, reason: str) -> bool:
        block = self.blocks.get(block_id)
        if block is None:
            return False
        removed_edges = [edge.to_dict() for edge in self.edges if edge.source == block_id or edge.target == block_id]
        self.edges = [edge for edge in self.edges if edge.source != block_id and edge.target != block_id]
        del self.blocks[block_id]
        self.rebuild_cfg_links(); self.rebuild_indexes()
        self.rewrite_history.append(RewriteRecord(pass_name, "remove_block", block_id, reason, {"block": block.to_dict(), "edges": removed_edges}, None))
        return True

    def to_dict(self, *, include_analysis_indexes: bool = False) -> Json:
        result = {
            "function_id": self.function_id,
            "contract": self.contract,
            "function": self.function,
            "signature": self.signature,
            "source_statements": self.source_statements,
            "blocks": [block.to_dict() for block in self.blocks.values()],
            "edges": [edge.to_dict() for edge in self.edges],
            "locations": [location.to_dict() for location in self.locations.values()],
            "unplaced_operations": [item.to_dict() for item in self.unplaced_operations],
            "diagnostics": self.diagnostics,
            "rewrite_history": [item.to_dict() for item in self.rewrite_history],
        }
        if include_analysis_indexes:
            result["analysis_indexes"] = {
                "operation_by_fact_id": {fact_id: operation.operation_id for fact_id, operation in self.operation_by_fact_id.items()},
                "operations_by_kind": self.operations_by_kind,
                "operations_by_location": self.operations_by_location,
                "operations_by_stmt_ref": self.operations_by_stmt_ref,
            }
        return clean(result)


@dataclass
class SemanticProgram:
    source: str | None
    functions: list[SemanticFunction]
    schema: str = "semantic-ir/v2"
    diagnostics: list[Json] = field(default_factory=list)

    def to_dict(self, *, include_analysis_indexes: bool = False) -> Json:
        return clean({
            "schema": self.schema,
            "source": self.source,
            "function_count": len(self.functions),
            "functions": [item.to_dict(include_analysis_indexes=include_analysis_indexes) for item in self.functions],
            "diagnostics": self.diagnostics,
        })
