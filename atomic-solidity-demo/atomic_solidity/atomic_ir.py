from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from atomic_solidity.diagnostics import Diagnostic


@dataclass
class SourceInfo:
    ast_id: int | None
    src: str | None
    source_file: str | None
    original_node_type: str | None


@dataclass
class AtomicVariable:
    name: str
    ast_id: int
    type: str | None
    kind: str


@dataclass
class AtomicOperation:
    id: str
    kind: str
    inputs: list[str]
    output: str | None
    attributes: dict[str, Any]
    source: SourceInfo
    effects: list[str] = field(default_factory=list)
    may_revert: bool = False


@dataclass
class BasicBlock:
    id: str
    operations: list[AtomicOperation] = field(default_factory=list)
    terminator: AtomicOperation | None = None
    predecessors: list[str] = field(default_factory=list)
    successors: list[str] = field(default_factory=list)


@dataclass
class AtomicFunction:
    name: str
    ast_id: int
    parameters: list[AtomicVariable]
    return_parameters: list[AtomicVariable]
    blocks: list[BasicBlock]
    entry_block: str


@dataclass
class AtomicContract:
    name: str
    ast_id: int
    functions: list[AtomicFunction]


@dataclass
class AtomicSourceFile:
    path: str
    contracts: list[AtomicContract]


@dataclass
class AtomicProgram:
    compiler_version: str
    source_files: list[AtomicSourceFile]
    diagnostics: list[Diagnostic]


@dataclass(frozen=True)
class LocationRef:
    id: str
    kind: str
    name: str
    declaration_ast_id: int | None
    type: str | None
    source: SourceInfo
    attributes: dict[str, Any] = field(default_factory=dict)
