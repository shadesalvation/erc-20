#!/usr/bin/env python3
"""
Path-sensitive MemorySSA for one Yul InlineAssembly CFG.

The tracker keeps all analysis state inside a single assembly block. It does
not rewrite Solidity or Yul source. Each reachable CFG path carries current
Yul value definitions and memory reaching definitions so downstream recovery
can query the state at a semantic sink.
"""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from assembly_ast_cfg import (
    AssemblyAstBlock,
    CFGEdge,
    YulCFG,
    build_yul_cfg,
    compile_source_ast,
    discover_solc,
    extract_inline_assembly_blocks,
    yul_children,
    yul_expression,
    yul_statement_text,
)


Json = dict[str, Any]
MEMORY_WRITES = {"mstore", "mstore8"}
MEMORY_COPIES = {"calldatacopy", "codecopy", "returndatacopy", "extcodecopy", "mcopy"}
HARD_SINKS = {
    "sstore", "tstore", "log0", "log1", "log2", "log3", "log4",
    "call", "staticcall", "delegatecall", "callcode", "return", "revert",
    "selfdestruct", "create", "create2",
}
MAX_PATH_STATES = 256


@dataclass(frozen=True)
class MemoryDefinition:
    version: str
    node_id: int
    address: str
    value: str
    kind: str
    origin_src: str
    memory_before: dict[str, "MemoryDefinition"] = field(compare=False, hash=False, repr=False)


@dataclass(frozen=True)
class ValueDefinition:
    version: str
    node_id: int
    name: str
    expression: Json = field(compare=False, hash=False, repr=False)
    origin_src: str
    memory_before: dict[str, MemoryDefinition] = field(compare=False, hash=False, repr=False)
    values_before: dict[str, "ValueDefinition"] = field(compare=False, hash=False, repr=False)


@dataclass
class PathState:
    predicates: tuple[str, ...] = ()
    memory: dict[str, MemoryDefinition] = field(default_factory=dict)
    values: dict[str, ValueDefinition] = field(default_factory=dict)
    trace: tuple[int, ...] = ()

    def clone(self) -> "PathState":
        return PathState(self.predicates, dict(self.memory), dict(self.values), self.trace)

    def fingerprint(self) -> tuple[Any, ...]:
        return (
            self.predicates,
            tuple(sorted((key, value.version) for key, value in self.memory.items())),
            tuple(sorted((key, value.version) for key, value in self.values.items())),
        )


@dataclass
class NodeObservation:
    node_id: int
    node_type: str
    text: str
    src: str
    incoming: list[PathState] = field(default_factory=list)
    outgoing: list[PathState] = field(default_factory=list)
    effect: str | None = None


@dataclass
class MemorySSAResult:
    block: AssemblyAstBlock
    cfg: YulCFG
    node_ast: dict[int, Json]
    observations: dict[int, NodeObservation]
    memory_definitions: dict[str, MemoryDefinition]
    value_definitions: dict[str, ValueDefinition]
    truncated: bool = False

    def states_at(self, node_id: int) -> list[PathState]:
        return self.observations.get(node_id, NodeObservation(node_id, "", "", "")).incoming


def yul_nodes(root: Json) -> list[Json]:
    result: list[Json] = []

    def visit(node: Json) -> None:
        result.append(node)
        for child in yul_children(node):
            visit(child)

    visit(root)
    return result


def direct_call(node: Json | None) -> tuple[str | None, list[Json]]:
    if not isinstance(node, dict) or node.get("nodeType") != "YulFunctionCall":
        return None, []
    function = node.get("functionName")
    if not isinstance(function, dict) or function.get("nodeType") != "YulIdentifier":
        return None, []
    return function.get("name"), list(node.get("arguments", []))


def statement_expression(node: Json) -> Json | None:
    node_type = node.get("nodeType")
    if node_type == "YulExpressionStatement":
        return node.get("expression")
    if node_type in {"YulVariableDeclaration", "YulAssignment"}:
        return node.get("value")
    return None


def assigned_names(node: Json) -> list[str]:
    if node.get("nodeType") == "YulVariableDeclaration":
        return [item.get("name") for item in node.get("variables", []) if item.get("name")]
    if node.get("nodeType") == "YulAssignment":
        return [item.get("name") for item in node.get("variableNames", []) if item.get("name")]
    return []


def normalize_expression(text: str) -> str:
    return "".join(text.split()).replace("0x20", "32").replace("0X20", "32")


def address_key(node: Json) -> str:
    return normalize_expression(yul_expression(node))


def predicate_for_edge(edge: CFGEdge) -> str | None:
    label = edge.label
    if label.startswith("true: "):
        return label.removeprefix("true: ").strip()
    if label.startswith("false: "):
        return label.removeprefix("false: ").strip()
    if label.startswith("case: ") or label == "default":
        return label
    return None


def node_ast_mapping(block: AssemblyAstBlock, cfg: YulCFG) -> dict[int, Json]:
    by_src: dict[str, list[Json]] = defaultdict(list)
    for node in yul_nodes(block.yul_ast):
        by_src[str(node.get("src", ""))].append(node)

    mapping: dict[int, Json] = {}
    for cfg_node in cfg.nodes:
        matches = by_src.get(cfg_node.src, [])
        if not matches:
            continue
        for candidate in matches:
            if yul_statement_text(candidate) == cfg_node.text:
                mapping[cfg_node.node_id] = candidate
                break
        else:
            mapping[cfg_node.node_id] = matches[0]
    return mapping


class MemorySSAAnalyzer:
    def __init__(self, block: AssemblyAstBlock, max_states: int = MAX_PATH_STATES) -> None:
        self.block = block
        self.cfg = build_yul_cfg(block.yul_ast)
        self.node_ast = node_ast_mapping(block, self.cfg)
        self.max_states = max_states
        self.memory_counter = 0
        self.value_counter = 0
        self.memory_definitions: dict[str, MemoryDefinition] = {}
        self.value_definitions: dict[str, ValueDefinition] = {}
        self.observations = {
            node.node_id: NodeObservation(node.node_id, node.kind, node.text, node.src)
            for node in self.cfg.nodes
        }
        self.edges_by_source: dict[int, list[CFGEdge]] = defaultdict(list)
        for edge in self.cfg.edges:
            self.edges_by_source[edge.source].append(edge)
        self.truncated = False

    def new_memory_definition(
        self,
        node_id: int,
        address: str,
        value: str,
        kind: str,
        src: str,
        memory_before: dict[str, MemoryDefinition],
    ) -> MemoryDefinition:
        self.memory_counter += 1
        definition = MemoryDefinition(
            f"mem_{self.memory_counter}",
            node_id,
            address,
            value,
            kind,
            src,
            dict(memory_before),
        )
        self.memory_definitions[definition.version] = definition
        return definition

    def new_value_definition(
        self,
        node_id: int,
        name: str,
        expression: Json,
        src: str,
        state: PathState,
    ) -> ValueDefinition:
        self.value_counter += 1
        definition = ValueDefinition(
            f"{name}__ssa{self.value_counter}",
            node_id,
            name,
            expression,
            src,
            dict(state.memory),
            dict(state.values),
        )
        self.value_definitions[definition.version] = definition
        return definition

    def transfer(self, node_id: int, incoming: PathState) -> PathState:
        state = incoming.clone()
        state.trace = state.trace + (node_id,)
        ast_node = self.node_ast.get(node_id)
        observation = self.observations[node_id]
        if ast_node is None:
            return state

        expression = statement_expression(ast_node)
        call_name, arguments = direct_call(expression)
        if call_name in MEMORY_WRITES and len(arguments) >= 2:
            address = address_key(arguments[0])
            value = yul_expression(arguments[1])
            definition = self.new_memory_definition(
                node_id,
                address,
                value,
                call_name,
                str(ast_node.get("src", "")),
                state.memory,
            )
            state.memory[address] = definition
            observation.effect = f"{definition.version}: {call_name}({address}, {value})"
            return state

        if call_name in MEMORY_COPIES and arguments:
            address = address_key(arguments[0])
            definition = self.new_memory_definition(
                node_id,
                address,
                f"{call_name}(...)",
                "copy",
                str(ast_node.get("src", "")),
                state.memory,
            )
            state.memory[address] = definition
            state.memory["<unknown-copy-range>"] = definition
            observation.effect = f"{definition.version}: {call_name} writes unknown range at {address}"
            return state

        names = assigned_names(ast_node)
        if names and isinstance(expression, dict):
            versions = []
            for name in names:
                definition = self.new_value_definition(
                    node_id,
                    name,
                    expression,
                    str(ast_node.get("src", "")),
                    state,
                )
                state.values[name] = definition
                versions.append(definition.version)
            observation.effect = "value defs: " + ", ".join(versions)
        elif call_name in HARD_SINKS:
            observation.effect = f"hard sink: {call_name}"
        return state

    def analyze(self) -> MemorySSAResult:
        queue: deque[tuple[int, PathState]] = deque([(self.cfg.entry, PathState())])
        seen: set[tuple[int, tuple[Any, ...]]] = set()
        processed = 0

        while queue:
            node_id, incoming = queue.popleft()
            fingerprint = (node_id, incoming.fingerprint())
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            processed += 1
            if processed > self.max_states:
                self.truncated = True
                break

            observation = self.observations[node_id]
            observation.incoming.append(incoming)
            outgoing = self.transfer(node_id, incoming)
            observation.outgoing.append(outgoing)

            for edge in self.edges_by_source.get(node_id, []):
                next_state = outgoing.clone()
                predicate = predicate_for_edge(edge)
                if predicate and predicate not in next_state.predicates:
                    next_state.predicates = next_state.predicates + (predicate,)
                queue.append((edge.target, next_state))

        return MemorySSAResult(
            self.block,
            self.cfg,
            self.node_ast,
            self.observations,
            self.memory_definitions,
            self.value_definitions,
            self.truncated,
        )


def analyze_block(block: AssemblyAstBlock, max_states: int = MAX_PATH_STATES) -> MemorySSAResult:
    return MemorySSAAnalyzer(block, max_states).analyze()


def format_state(state: PathState) -> str:
    predicate = " && ".join(state.predicates) or "entry"
    memory = ", ".join(f"{key}={definition.version}" for key, definition in sorted(state.memory.items())) or "-"
    values = ", ".join(f"{key}={definition.version}" for key, definition in sorted(state.values.items())) or "-"
    return f"path=[{predicate}] memory=[{memory}] values=[{values}]"


def format_memory_ssa(result: MemorySSAResult) -> str:
    block = result.block
    lines = [
        f"AssemblyBlock {block.block_id}: {block.context.label()}",
        "MemoryTrackerScope: assembly_block_isolated + cfg_path_memory_ssa",
        f"PathStateTruncated: {result.truncated}",
        "MemoryDefinitions:",
    ]
    for definition in result.memory_definitions.values():
        lines.append(
            f"  {definition.version} = {definition.kind}({definition.address}, {definition.value}) "
            f"origin=N{definition.node_id} src={definition.origin_src}"
        )
    lines.append("ValueDefinitions:")
    for definition in result.value_definitions.values():
        lines.append(
            f"  {definition.version} = {yul_expression(definition.expression)} "
            f"origin=N{definition.node_id} src={definition.origin_src}"
        )
    lines.append("NodeStates:")
    for node in result.cfg.nodes:
        observation = result.observations[node.node_id]
        if not observation.incoming and not observation.outgoing:
            continue
        lines.append(f"  N{node.node_id} [{node.kind}] {node.text}")
        if observation.effect:
            lines.append(f"    effect: {observation.effect}")
        for state in observation.incoming:
            lines.append(f"    in:  {format_state(state)}")
        for state in observation.outgoing:
            lines.append(f"    out: {format_state(state)}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build path-sensitive MemorySSA for each inline assembly block.")
    parser.add_argument("source", help="Solidity source file.")
    parser.add_argument("-o", "--output", default="assembly_memory_ssa.txt", help="Text report path.")
    parser.add_argument("--solc-bin", help="solc executable.")
    parser.add_argument("--max-states", type=int, default=MAX_PATH_STATES, help="Maximum CFG path states per block.")
    args = parser.parse_args()

    source = Path(args.source)
    solc_bin = discover_solc(args.solc_bin)
    if not source.is_file():
        print(f"Source file not found: {source}", file=sys.stderr)
        return 2
    if solc_bin is None:
        print("solc executable not found.", file=sys.stderr)
        return 2

    try:
        ast = compile_source_ast(source, solc_bin)
        blocks = extract_inline_assembly_blocks(ast, source)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    reports = [format_memory_ssa(analyze_block(block, args.max_states)) for block in blocks]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n\n".join(reports) + "\n", encoding="utf-8")
    print(f"Wrote MemorySSA report: {output}")
    print(f"Assembly blocks found: {len(blocks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
