#!/usr/bin/env python3
"""
Sink-driven branch materialization for path-sensitive Yul MemorySSA.

This module does not edit source code. It recognizes value-producing semantic
materialization points (currently direct sload assignments), slices their
path-specific MemorySSA inputs, and emits a Branch-expanded Yul IR. The IR
keeps origin CFG node IDs so later storage/event/call recovery can consume it
without losing the mapping to the original AST.
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from assembly_ast_cfg import (
    AssemblyAstBlock,
    compile_source_ast,
    discover_solc,
    extract_inline_assembly_blocks,
    yul_expression,
)
from assembly_memory_ssa import (
    HARD_SINKS,
    MemoryDefinition,
    MemorySSAResult,
    PathState,
    ValueDefinition,
    analyze_block,
    assigned_names,
    direct_call,
    statement_expression,
)


Json = dict[str, Any]
MAX_SLICE_DEPTH = 48


@dataclass
class ResolvedValue:
    signature: str
    node_ids: set[int] = field(default_factory=set)
    memory_versions: set[str] = field(default_factory=set)
    value_versions: set[str] = field(default_factory=set)
    unknown: bool = False


@dataclass
class PathSlice:
    predicates: tuple[str, ...]
    resolved: ResolvedValue


@dataclass
class BranchExpansion:
    sink_node: int
    sink_target: str
    sink_text: str
    baseline: PathSlice
    branches: list[tuple[tuple[str, ...], PathSlice]]
    shared_node_ids: set[int]
    forced_node_ids: set[int]
    discarded_unknown_paths: list[PathSlice]
    linearized: bool


def integer_literal(node: Json) -> int | None:
    if not isinstance(node, dict) or node.get("nodeType") != "YulLiteral":
        return None
    value = str(node.get("value", ""))
    try:
        return int(value, 0)
    except ValueError:
        return None


def normalize(text: str) -> str:
    return "".join(text.split()).replace("0x20", "32").replace("0X20", "32")


def memory_keys(pointer: Json, length: Json) -> list[str] | None:
    count = integer_literal(length)
    if count is None or count <= 0 or count % 32 != 0:
        return None
    base = normalize(yul_expression(pointer))
    keys = []
    for offset in range(0, count, 32):
        keys.append(base if offset == 0 else normalize(f"add({base}, {offset})"))
    return keys


class SliceResolver:
    def __init__(self, result: MemorySSAResult) -> None:
        self.result = result

    def resolve(self, expression: Json, state: PathState, depth: int = 0, seen: set[str] | None = None) -> ResolvedValue:
        if depth > MAX_SLICE_DEPTH:
            return ResolvedValue("depth-limit", unknown=True)
        seen = set() if seen is None else set(seen)
        node_type = expression.get("nodeType") if isinstance(expression, dict) else None

        if node_type == "YulIdentifier":
            name = expression.get("name", "?")
            definition = state.values.get(name)
            if definition is None:
                return ResolvedValue(name)
            if definition.version in seen:
                return ResolvedValue(f"cycle({name})", unknown=True)
            seen.add(definition.version)
            before = PathState(
                memory=dict(definition.memory_before),
                values=dict(definition.values_before),
            )
            nested = self.resolve(definition.expression, before, depth + 1, seen)
            nested.node_ids.add(definition.node_id)
            nested.value_versions.add(definition.version)
            nested.signature = f"{name}={nested.signature}"
            return nested

        call_name, arguments = direct_call(expression)
        if call_name == "keccak256" and len(arguments) == 2:
            keys = memory_keys(arguments[0], arguments[1])
            if keys is None or any(key.startswith("<unknown") for key in state.memory):
                return ResolvedValue("keccak256(memory:unknown)", unknown=True)
            parts = []
            output = ResolvedValue("")
            pointer = self.resolve(arguments[0], state, depth + 1, seen)
            output.node_ids.update(pointer.node_ids)
            output.memory_versions.update(pointer.memory_versions)
            output.value_versions.update(pointer.value_versions)
            output.unknown = output.unknown or pointer.unknown
            for key in keys:
                definition = state.memory.get(key)
                if definition is None:
                    parts.append(f"{key}=unknown")
                    output.unknown = True
                    continue
                parts.append(f"{key}={definition.value}")
                output.node_ids.add(definition.node_id)
                output.memory_versions.add(definition.version)
            output.signature = "keccak256(" + ",".join(parts) + ")"
            return output

        if call_name:
            parts = []
            output = ResolvedValue("")
            for argument in arguments:
                resolved = self.resolve(argument, state, depth + 1, seen)
                parts.append(resolved.signature)
                output.node_ids.update(resolved.node_ids)
                output.memory_versions.update(resolved.memory_versions)
                output.value_versions.update(resolved.value_versions)
                output.unknown = output.unknown or resolved.unknown
            output.signature = f"{call_name}({', '.join(parts)})"
            return output

        return ResolvedValue(yul_expression(expression))


def direct_sload_assignment(ast_node: Json) -> tuple[str, Json] | None:
    names = assigned_names(ast_node)
    expression = statement_expression(ast_node)
    call_name, arguments = direct_call(expression)
    if names and call_name == "sload" and len(arguments) == 1:
        return names[0], arguments[0]
    return None


def condition_intersection(paths: list[PathSlice]) -> tuple[str, ...]:
    if not paths:
        return ()
    common = list(paths[0].predicates)
    for item in paths[1:]:
        allowed = set(item.predicates)
        common = [predicate for predicate in common if predicate in allowed]
    return tuple(common)


def branch_score(path: PathSlice) -> tuple[int, int, str]:
    positive = sum(not predicate.startswith("!(") for predicate in path.predicates)
    return (positive, len(path.predicates), " && ".join(path.predicates))


def source_order(result: MemorySSAResult, node_ids: set[int]) -> list[int]:
    def key(node_id: int) -> tuple[int, int]:
        src = result.cfg.nodes[node_id].src
        try:
            start = int(src.split(":", 1)[0])
        except (ValueError, IndexError):
            start = 10**12
        return start, node_id

    return sorted(node_ids, key=key)


def versions_to_nodes(result: MemorySSAResult, versions: set[str], table: dict[str, Any]) -> set[int]:
    return {table[version].node_id for version in versions if version in table}


def build_expansion(result: MemorySSAResult, sink_node: int, target: str, slot_expression: Json) -> BranchExpansion | None:
    resolver = SliceResolver(result)
    all_paths = [
        PathSlice(state.predicates, resolver.resolve(slot_expression, state))
        for state in result.states_at(sink_node)
    ]
    unique_by_state: dict[tuple[tuple[str, ...], str], PathSlice] = {
        (item.predicates, item.resolved.signature): item for item in all_paths
    }
    all_paths = list(unique_by_state.values())
    known_paths = [item for item in all_paths if not item.resolved.unknown]
    discarded_unknown_paths = [item for item in all_paths if item.resolved.unknown]
    if not known_paths:
        return None

    signatures = {item.resolved.signature for item in known_paths}
    linearized = len(known_paths) == 1 or (bool(discarded_unknown_paths) and len(signatures) == 1)
    if not linearized and len(signatures) < 2:
        return None

    groups: dict[str, list[PathSlice]] = defaultdict(list)
    for item in known_paths:
        groups[item.resolved.signature].append(item)
    baseline_signature = min(groups, key=lambda signature: branch_score(min(groups[signature], key=branch_score)))
    baseline = min(groups.pop(baseline_signature), key=branch_score)

    all_slices = known_paths
    common_nodes = set.intersection(*(set(item.resolved.node_ids) for item in all_slices))
    common_memory = set.intersection(*(set(item.resolved.memory_versions) for item in all_slices))
    common_values = set.intersection(*(set(item.resolved.value_versions) for item in all_slices))
    divergent_memory = set().union(*(item.resolved.memory_versions for item in all_slices)) - common_memory
    divergent_values = set().union(*(item.resolved.value_versions for item in all_slices)) - common_values
    forced = {sink_node}
    forced.update(versions_to_nodes(result, divergent_memory, result.memory_definitions))
    forced.update(versions_to_nodes(result, divergent_values, result.value_definitions))
    shared = common_nodes - forced

    branches: list[tuple[tuple[str, ...], PathSlice]] = []
    if not linearized:
        for group in groups.values():
            representative = min(group, key=branch_score)
            predicates = condition_intersection(group) or representative.predicates
            branches.append((predicates, representative))
        branches.sort(key=lambda item: (len(item[0]), item[0]))

    return BranchExpansion(
        sink_node,
        target,
        result.cfg.nodes[sink_node].text,
        baseline,
        branches,
        shared,
        forced,
        discarded_unknown_paths,
        linearized,
    )


def find_expansions(result: MemorySSAResult) -> list[BranchExpansion]:
    expansions = []
    for node_id, ast_node in result.node_ast.items():
        sink = direct_sload_assignment(ast_node)
        if sink is None:
            continue
        target, slot_expression = sink
        expansion = build_expansion(result, node_id, target, slot_expression)
        if expansion:
            expansions.append(expansion)
    return expansions


def emit_nodes(result: MemorySSAResult, node_ids: set[int], indent: str) -> list[str]:
    lines = []
    for node_id in source_order(result, node_ids):
        text = result.cfg.nodes[node_id].text
        lines.append(f"{indent}{text}; // origin: N{node_id}")
    return lines


def emit_nested_branch(result: MemorySSAResult, predicates: tuple[str, ...], node_ids: set[int], indent: str) -> list[str]:
    if not predicates:
        return emit_nodes(result, node_ids, indent)
    predicate = predicate_to_yul(predicates[0])
    lines = [f"{indent}if {predicate} {{"]
    lines.extend(emit_nested_branch(result, predicates[1:], node_ids, indent + "    "))
    lines.append(f"{indent}}}")
    return lines


def predicate_to_yul(predicate: str) -> str:
    if predicate.startswith("!(") and predicate.endswith(")"):
        return f"iszero({predicate[2:-1]})"
    return predicate


def node_span(result: MemorySSAResult, node_id: int) -> tuple[int, int]:
    src = result.cfg.nodes[node_id].src
    try:
        start_text, length_text, _file = src.split(":", 2)
        start = int(start_text)
        return start, start + int(length_text)
    except (ValueError, TypeError):
        return 0, 0


def branch_condition_nodes(result: MemorySSAResult, expansion: BranchExpansion) -> list[int]:
    branch_node_ids = set()
    for _predicates, branch in expansion.branches:
        branch_node_ids.update(branch.resolved.node_ids)
    if expansion.linearized:
        branch_node_ids.update(expansion.baseline.resolved.node_ids)

    candidates = []
    for node in result.cfg.nodes:
        if node.kind != "condition" or not node.text.startswith("if "):
            continue
        condition = node.text[3:].strip()
        start, end = node_span(result, node.node_id)
        if any(start < node_span(result, child_id)[0] < end for child_id in branch_node_ids):
            candidates.append(node.node_id)
    return candidates


def source_statement(text: str, declared_names: set[str]) -> str:
    match = re.match(r"^let\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*:=", text)
    if not match:
        return text
    name = match.group(1)
    if name in declared_names:
        return re.sub(r"^let\s+" + re.escape(name) + r"\s*:=", f"{name} :=", text, count=1)
    declared_names.add(name)
    return text


def render_nodes_for_source(
    result: MemorySSAResult,
    node_ids: set[int],
    start_offset: int,
    indent: str,
    declared_names: set[str],
) -> list[str]:
    selected = {
        node_id for node_id in node_ids
        if node_span(result, node_id)[0] >= start_offset
    }
    return [
        f"{indent}{source_statement(result.cfg.nodes[node_id].text, declared_names)}"
        for node_id in source_order(result, selected)
    ]


def render_source_branch(
    result: MemorySSAResult,
    predicates: tuple[str, ...],
    node_ids: set[int],
    start_offset: int,
    indent: str,
    declared_names: set[str],
) -> list[str]:
    if not predicates:
        return render_nodes_for_source(result, node_ids, start_offset, indent, declared_names)
    lines = [f"{indent}if {predicate_to_yul(predicates[0])} {{"]
    lines.extend(
        render_source_branch(
            result,
            predicates[1:],
            node_ids,
            start_offset,
            indent + "    ",
            declared_names,
        )
    )
    lines.append(f"{indent}}}")
    return lines


def source_rewrite_for_expansion(result: MemorySSAResult, expansion: BranchExpansion) -> tuple[int, int, str] | None:
    condition_nodes = branch_condition_nodes(result, expansion)
    selected_nodes = set(expansion.baseline.resolved.node_ids) | {expansion.sink_node}
    for _predicates, branch in expansion.branches:
        selected_nodes.update(branch.resolved.node_ids)
        selected_nodes.add(expansion.sink_node)
    if not selected_nodes:
        return None

    selected_starts = [node_span(result, node_id)[0] for node_id in selected_nodes]
    token_start = min((node_span(result, node_id)[0] for node_id in condition_nodes), default=min(selected_starts))
    rewrite_end = max(node_span(result, node_id)[1] for node_id in selected_nodes)

    line_start = result.block.source.rfind("\n", 0, token_start) + 1
    indent = result.block.source[line_start:token_start]
    declared_names: set[str] = set()

    if expansion.linearized:
        lines = render_nodes_for_source(
            result,
            set(expansion.baseline.resolved.node_ids) | {expansion.sink_node},
            token_start,
            indent,
            declared_names,
        )
    else:
        lines = render_nodes_for_source(
            result,
            set(expansion.baseline.resolved.node_ids) | {expansion.sink_node},
            token_start,
            indent,
            declared_names,
        )
        for predicates, branch in expansion.branches:
            branch_nodes = (set(branch.resolved.node_ids) - expansion.shared_node_ids) | {expansion.sink_node}
            lines.extend(
                render_source_branch(
                    result,
                    predicates,
                    branch_nodes,
                    token_start,
                    indent,
                    set(declared_names),
                )
            )

    if not lines:
        return None
    return line_start, rewrite_end, "\n".join(lines)


def rewrite_source_with_expansions(source: str, results: list[MemorySSAResult]) -> tuple[str, int]:
    rewrites: list[tuple[int, int, str]] = []
    for result in results:
        for expansion in find_expansions(result):
            rewrite = source_rewrite_for_expansion(result, expansion)
            if rewrite is not None:
                rewrites.append(rewrite)

    accepted: list[tuple[int, int, str]] = []
    for rewrite in sorted(rewrites, key=lambda item: (item[0], item[1])):
        if accepted and rewrite[0] < accepted[-1][1]:
            continue
        accepted.append(rewrite)

    rewritten = source
    for start, end, replacement in reversed(accepted):
        rewritten = rewritten[:start] + replacement + rewritten[end:]
    return rewritten, len(accepted)


def format_expansion(result: MemorySSAResult, expansion: BranchExpansion) -> list[str]:
    baseline_nodes = set(expansion.baseline.resolved.node_ids) | {expansion.sink_node}
    mode = "linearized" if expansion.linearized else "conditional"
    lines = [
        f"ConditionalMaterialization: sink=N{expansion.sink_node} target={expansion.sink_target} mode={mode}",
        f"  baseline path: {' && '.join(expansion.baseline.predicates) or 'entry'}",
        f"  baseline slot: {expansion.baseline.resolved.signature}",
        f"  DiscardedUnknownPaths: {len(expansion.discarded_unknown_paths)}",
        "  BranchExpandedYulIR:",
    ]
    lines.extend(emit_nodes(result, baseline_nodes, "    "))
    for predicates, branch in expansion.branches:
        branch_nodes = (set(branch.resolved.node_ids) - expansion.shared_node_ids) | {expansion.sink_node}
        lines.append(f"    // path: {' && '.join(predicates) or 'entry'}")
        lines.extend(emit_nested_branch(result, predicates, branch_nodes, "    "))
    lines.append(
        "  OriginSummary: shared="
        + repr(sorted(expansion.shared_node_ids))
        + " cloned="
        + repr(sorted(expansion.forced_node_ids))
    )
    return lines


def format_branch_report(results: list[MemorySSAResult]) -> str:
    lines = [
        "INFO:BranchMaterialization:input Solidity source only",
        "INFO:BranchMaterialization:output Branch-expanded Yul IR; source is not modified",
        "",
    ]
    for result in results:
        lines.append(f"AssemblyBlock {result.block.block_id}: {result.block.context.label()}")
        expansions = find_expansions(result)
        if not expansions:
            lines.append("  No path-divergent materializable sload sink.")
        for expansion in expansions:
            lines.extend(format_expansion(result, expansion))
        if result.truncated:
            lines.append("  NOTE: MemorySSA path exploration truncated; output is conservative.")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize path-divergent Yul MemorySSA reads as branch-expanded IR.")
    parser.add_argument("source", help="Solidity source file.")
    parser.add_argument("-o", "--output", default="assembly_branch_materialization.txt", help="Text report path.")
    parser.add_argument("--solc-bin", help="solc executable.")
    parser.add_argument("--max-states", type=int, default=256, help="Maximum MemorySSA path states per block.")
    parser.add_argument("--rewritten-source", help="Optional virtual Solidity source with branch-expanded assembly blocks.")
    args = parser.parse_args()

    source = Path(args.source)
    solc_bin = discover_solc(args.solc_bin)
    if not source.is_file():
        print(f"Source file not found: {source}")
        return 2
    if solc_bin is None:
        print("solc executable not found.")
        return 2

    try:
        ast = compile_source_ast(source, solc_bin)
        blocks = extract_inline_assembly_blocks(ast, source)
    except RuntimeError as exc:
        print(str(exc))
        return 1

    results = [analyze_block(block, args.max_states) for block in blocks]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(format_branch_report(results) + "\n", encoding="utf-8")
    if args.rewritten_source:
        rewritten, rewrite_count = rewrite_source_with_expansions(source.read_text(encoding="utf-8"), results)
        rewritten_path = Path(args.rewritten_source)
        rewritten_path.parent.mkdir(parents=True, exist_ok=True)
        rewritten_path.write_text(rewritten, encoding="utf-8")
        print(f"Wrote branch-expanded virtual source: {rewritten_path} ({rewrite_count} assembly rewrite(s))")
    print(f"Wrote branch materialization report: {output}")
    print(f"Assembly blocks found: {len(blocks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
