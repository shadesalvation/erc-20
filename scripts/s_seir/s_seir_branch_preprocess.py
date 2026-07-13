#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path as _SSEIRPath
import sys as _sseir_sys

_SSEIR_ROOT = _SSEIRPath(__file__).resolve().parents[1]
for _sseir_path in (_SSEIR_ROOT / "legacy_yul", _SSEIR_ROOT / "s_seir"):
    _sseir_text = str(_sseir_path)
    if _sseir_text not in _sseir_sys.path:
        _sseir_sys.path.insert(0, _sseir_text)

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from assembly_ast_cfg import (
    compile_source_ast,
    discover_solc,
    extract_inline_assembly_blocks,
    yul_expression,
)
from assembly_branch_materialization import (
    BranchExpansion,
    PathSlice,
    ResolvedValue,
    SliceResolver,
    branch_score,
    condition_intersection,
    find_expansions,
    format_expansion,
    memory_keys,
    node_span,
    render_source_branch,
    source_rewrite_for_expansion,
)
from assembly_memory_ssa import (
    MemorySSAResult,
    PathState,
    analyze_block,
    direct_call,
    statement_expression,
)


Json = dict[str, Any]


@dataclass
class BranchPreprocessResult:
    source_path: Path
    rewritten_source: str
    rewrite_count: int
    report: str
    used_preprocessed_source: bool
    flattened: bool = False
    imported_files: list[str] = field(default_factory=list)
    preprocessed_files: list[str] = field(default_factory=list)


def build_branch_preprocessed_source(source: Path, solc_bin: str, destination: Path) -> BranchPreprocessResult:
    ast = compile_source_ast(source, solc_bin)
    blocks = extract_inline_assembly_blocks(ast, source)
    results = [analyze_block(block) for block in blocks]
    rewritten, rewrite_count, report = rewrite_source_with_memory_sink_expansions(
        source.read_text(encoding="utf-8"),
        results,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(rewritten, encoding="utf-8")
    return BranchPreprocessResult(
        source_path=destination,
        rewritten_source=rewritten,
        rewrite_count=rewrite_count,
        report=report,
        used_preprocessed_source=rewrite_count > 0,
        preprocessed_files=[str(source.resolve())],
    )


def build_branch_preprocessed_analysis_source(source: Path, solc_bin: str, destination: Path) -> BranchPreprocessResult:
    """Build the branch-preprocessed source used by S-SEIR.

    Single-file sources keep the legacy behavior. Sources with imports are
    recursively resolved, branch-preprocessed per source unit, and flattened
    into `destination` so later S-SEIR passes can analyze the rewritten code as
    one Solidity source.
    """

    include_paths = solc_include_paths()
    original_text = source.read_text(encoding="utf-8")
    if not import_specs(original_text):
        return build_branch_preprocessed_source(source, solc_bin, destination)

    flattener = BranchPreprocessFlattener(source.resolve(), solc_bin, destination, include_paths)
    flattened_text, report, rewrite_count, imported_files, preprocessed_files = flattener.flatten()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(flattened_text, encoding="utf-8")
    return BranchPreprocessResult(
        source_path=destination,
        rewritten_source=flattened_text,
        rewrite_count=rewrite_count,
        report=report,
        used_preprocessed_source=rewrite_count > 0,
        flattened=True,
        imported_files=[str(path) for path in imported_files],
        preprocessed_files=[str(path) for path in preprocessed_files],
    )


IMPORT_RE = re.compile(
    r"(?m)^[ \t]*import\s+(?:(?:[^;\"']*?\s+from\s+)?[\"']([^\"']+)[\"']|[\"']([^\"']+)[\"'])\s*;",
    re.S,
)

SPDX_RE = re.compile(r"(?m)^[ \t]*//[ \t]*SPDX-License-Identifier:[^\n]*(?:\n|$)")


def import_specs(source_text: str) -> list[str]:
    return [match.group(1) or match.group(2) for match in IMPORT_RE.finditer(source_text)]


def solc_include_paths() -> list[Path]:
    paths = []
    for item in os.environ.get("SSEIR_SOLC_INCLUDE_PATHS", "").split(os.pathsep):
        if not item.strip():
            continue
        path = Path(item).resolve()
        if path.exists():
            paths.append(path)
    return paths


def strip_spdx(source_text: str) -> str:
    return SPDX_RE.sub("", source_text).lstrip("\n")


def safe_part_name(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9_.@+-]+", "_", str(path.resolve())).strip("._") or path.stem


class BranchPreprocessFlattener:
    def __init__(self, entry: Path, solc_bin: str, destination: Path, include_paths: list[Path]) -> None:
        self.entry = entry.resolve()
        self.solc_bin = solc_bin
        self.destination = destination
        self.include_paths = [path.resolve() for path in include_paths]
        self.preprocessed_cache: dict[Path, BranchPreprocessResult] = {}
        self.flattened: set[Path] = set()
        self.imported_files: list[Path] = []
        self.preprocessed_files: list[Path] = []

    def flatten(self) -> tuple[str, str, int, list[Path], list[Path]]:
        body = self.inline_file(self.entry)
        text = (
            "// SPDX-License-Identifier: MIXED\n"
            "/* S-SEIR flattened branch-preprocessed source. */\n"
            f"/* Entry: {self.entry} */\n\n"
            f"{body.rstrip()}\n"
        )
        reports = [
            "INFO:SSEIRBranchPreprocessFlatten:input Solidity source with imports",
            "INFO:SSEIRBranchPreprocessFlatten:algorithm recursive import resolution + per-file branch preprocess",
            f"INFO:SSEIRBranchPreprocessFlatten:entry {self.entry}",
            "",
        ]
        rewrite_count = 0
        for path, result in self.preprocessed_cache.items():
            rewrite_count += result.rewrite_count
            reports.append(f"FlattenedSourceUnit: {path}")
            reports.append(f"  BranchRewrites: {result.rewrite_count}")
            reports.append(result.report.rstrip())
            reports.append("")
        return text, "\n".join(reports), rewrite_count, self.imported_files, self.preprocessed_files

    def inline_file(self, source: Path) -> str:
        source = source.resolve()
        if source in self.flattened:
            return f"/* S-SEIR flatten: duplicate import skipped: {source} */\n"
        self.flattened.add(source)
        if source != self.entry:
            self.imported_files.append(source)
        result = self.preprocess_file(source)
        text = strip_spdx(result.rewritten_source)

        def replace_import(match: re.Match[str]) -> str:
            spec = match.group(1) or match.group(2)
            imported = self.resolve_import(spec, source)
            return (
                f"\n/* S-SEIR flatten import: {spec} -> {imported} */\n"
                f"{self.inline_file(imported).rstrip()}\n"
                f"/* S-SEIR end import: {spec} */\n"
            )

        return IMPORT_RE.sub(replace_import, text)

    def preprocess_file(self, source: Path) -> BranchPreprocessResult:
        source = source.resolve()
        cached = self.preprocessed_cache.get(source)
        if cached is not None:
            return cached
        parts_dir = self.destination.with_suffix("").parent / (self.destination.stem + ".parts")
        part_destination = parts_dir / f"{safe_part_name(source)}.sol"
        try:
            result = build_branch_preprocessed_source(source, self.solc_bin, part_destination)
        except Exception as exc:
            if source == self.entry:
                raise
            raw = source.read_text(encoding="utf-8")
            part_destination.parent.mkdir(parents=True, exist_ok=True)
            part_destination.write_text(raw, encoding="utf-8")
            result = BranchPreprocessResult(
                source_path=part_destination,
                rewritten_source=raw,
                rewrite_count=0,
                report=(
                    "INFO:SSEIRBranchPreprocess:imported source kept raw\n"
                    f"INFO:SSEIRBranchPreprocess:reason {type(exc).__name__}: {exc}"
                ),
                used_preprocessed_source=False,
                preprocessed_files=[],
            )
        self.preprocessed_cache[source] = result
        self.preprocessed_files.append(source)
        return result

    def resolve_import(self, spec: str, importer: Path) -> Path:
        candidates: list[Path] = []
        spec_path = Path(spec)
        if spec_path.is_absolute():
            candidates.append(spec_path)
        elif spec.startswith("."):
            candidates.append(importer.parent / spec)
        else:
            candidates.append(importer.parent / spec)
            candidates.extend(path / spec for path in self.include_paths)
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved.is_file():
                return resolved
        searched = "\n".join(f"  - {candidate}" for candidate in candidates)
        raise FileNotFoundError(f"Could not resolve import {spec!r} from {importer}\nSearched:\n{searched}")


def rewrite_source_with_memory_sink_expansions(
    source: str,
    results: list[MemorySSAResult],
) -> tuple[str, int, str]:
    rewrites: list[tuple[int, int, str]] = []
    report_lines = [
        "INFO:SSEIRBranchPreprocess:input Solidity source only",
        "INFO:SSEIRBranchPreprocess:algorithm legacy BranchExpansion + MemorySSA path slices",
        "INFO:SSEIRBranchPreprocess:extra sinks log0-log4/call-data memory sinks",
        "",
    ]
    for result in results:
        expansions = find_all_expansions(result)
        report_lines.append(f"AssemblyBlock {result.block.block_id}: {result.block.context.label()}")
        if not expansions:
            report_lines.append("  No path-divergent materializable memory sink.")
        for expansion in expansions:
            report_lines.extend(format_expansion(result, expansion))
            if expansion.linearized and not expansion.discarded_unknown_paths:
                report_lines.append("  SourceRewrite: skipped linearized non-divergent expansion")
                continue
            rewrite = source_rewrite_for_memory_sink_expansion(result, expansion)
            if rewrite is None:
                rewrite = source_rewrite_for_expansion(result, expansion)
            if rewrite is not None:
                rewrites.append(rewrite)
        report_lines.append("")

    accepted: list[tuple[int, int, str]] = []
    for rewrite in sorted(rewrites, key=lambda item: (item[0], item[1])):
        if accepted and rewrite[0] < accepted[-1][1]:
            continue
        accepted.append(rewrite)

    rewritten = source
    for start, end, replacement in reversed(accepted):
        rewritten = rewritten[:start] + replacement + rewritten[end:]
    return rewritten, len(accepted), "\n".join(report_lines)


def find_all_expansions(result: MemorySSAResult) -> list[BranchExpansion]:
    expansions = list(find_expansions(result))
    existing_sinks = {expansion.sink_node for expansion in expansions}
    for node_id, ast_node in result.node_ast.items():
        if node_id in existing_sinks:
            continue
        sink = memory_sink_read(result, node_id, ast_node)
        if sink is None:
            continue
        target, pointer, length = sink
        expansion = build_memory_sink_expansion(result, node_id, target, pointer, length)
        if expansion is not None:
            expansions.append(expansion)
    expansions.sort(key=lambda item: item.sink_node)
    return expansions


def memory_sink_read(result: MemorySSAResult, node_id: int, ast_node: Json) -> tuple[str, Json, Json] | None:
    expression = statement_expression(ast_node)
    call_name, arguments = direct_call(expression)
    if call_name and call_name.startswith("log") and call_name[3:].isdigit() and len(arguments) >= 2:
        return call_name, arguments[0], arguments[1]

    # Keep call-family support for direct Yul expression sinks only. Calls nested
    # inside condition nodes are left to the legacy revert/call overlay because
    # replacing a full YulIf requires preserving the body as well as the sink.
    if call_name in {"call", "callcode"} and len(arguments) >= 7:
        return call_name, arguments[3], arguments[4]
    if call_name in {"staticcall", "delegatecall"} and len(arguments) >= 6:
        return call_name, arguments[2], arguments[3]
    if call_name in {"return", "revert"} and len(arguments) >= 2:
        return call_name, arguments[0], arguments[1]
    return None


def source_rewrite_for_memory_sink_expansion(
    result: MemorySSAResult,
    expansion: BranchExpansion,
) -> tuple[int, int, str] | None:
    if not is_generic_memory_sink(expansion.sink_target):
        return None
    start, end = node_span(result, expansion.sink_node)
    if start <= 0 or end <= start:
        return None
    line_start = result.block.source.rfind("\n", 0, start) + 1
    indent = result.block.source[line_start:start]
    lines: list[str] = []

    slices: list[tuple[tuple[str, ...], PathSlice]] = [(expansion.baseline.predicates, expansion.baseline)]
    slices.extend(expansion.branches)
    for predicates, path in slices:
        branch_nodes = (set(path.resolved.node_ids) - expansion.shared_node_ids) | {expansion.sink_node}
        lines.extend(render_source_branch(result, predicates, branch_nodes, 0, indent, set()))
    if not lines:
        return None
    return line_start, end, "\n".join(lines)


def is_generic_memory_sink(target: str) -> bool:
    return (
        target.startswith("log")
        or target in {"call", "callcode", "staticcall", "delegatecall", "return", "revert"}
    )


def build_memory_sink_expansion(
    result: MemorySSAResult,
    sink_node: int,
    target: str,
    pointer: Json,
    length: Json,
) -> BranchExpansion | None:
    resolver = MemorySinkResolver(result)
    all_paths = [
        PathSlice(state.predicates, resolver.resolve_memory_range(pointer, length, state, target))
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

    groups: dict[str, list[PathSlice]] = {}
    for item in known_paths:
        groups.setdefault(item.resolved.signature, []).append(item)
    baseline_signature = min(groups, key=lambda signature: branch_score(min(groups[signature], key=branch_score)))
    baseline = min(groups.pop(baseline_signature), key=branch_score)

    all_slices = known_paths
    common_nodes = set.intersection(*(set(item.resolved.node_ids) for item in all_slices))
    common_memory = set.intersection(*(set(item.resolved.memory_versions) for item in all_slices))
    common_values = set.intersection(*(set(item.resolved.value_versions) for item in all_slices))
    divergent_memory = set().union(*(item.resolved.memory_versions for item in all_slices)) - common_memory
    divergent_values = set().union(*(item.resolved.value_versions for item in all_slices)) - common_values
    forced = {sink_node}
    forced.update(
        result.memory_definitions[version].node_id
        for version in divergent_memory
        if version in result.memory_definitions
    )
    forced.update(
        result.value_definitions[version].node_id
        for version in divergent_values
        if version in result.value_definitions
    )
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


class MemorySinkResolver:
    def __init__(self, result: MemorySSAResult) -> None:
        self.result = result
        self.value_resolver = SliceResolver(result)

    def resolve_memory_range(self, pointer: Json, length: Json, state: PathState, target: str) -> ResolvedValue:
        keys = memory_keys(pointer, length)
        output = ResolvedValue("")
        pointer_value = self.value_resolver.resolve(pointer, state)
        output.node_ids.update(pointer_value.node_ids)
        output.memory_versions.update(pointer_value.memory_versions)
        output.value_versions.update(pointer_value.value_versions)
        output.unknown = output.unknown or pointer_value.unknown
        if keys is None:
            output.signature = f"{target}(memory:unknown-range)"
            output.unknown = True
            return output
        parts = []
        for key in keys:
            definition = state.memory.get(key)
            if definition is None:
                parts.append(f"{key}=unknown")
                output.unknown = True
                continue
            parts.append(f"{key}={definition.value}")
            output.node_ids.add(definition.node_id)
            output.memory_versions.add(definition.version)
        output.signature = f"{target}(" + ",".join(parts) + ")"
        return output


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build S-SEIR branch-preprocessed Solidity source.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--solc-bin")
    parser.add_argument("--output", type=Path, default=Path("outputs/sseir.branch_preprocessed.sol"))
    parser.add_argument("--report-output", type=Path, default=Path("outputs/sseir.branch_preprocessed.txt"))
    args = parser.parse_args()
    solc = args.solc_bin or discover_solc(None)
    result = build_branch_preprocessed_source(args.source, solc, args.output)
    args.report_output.parent.mkdir(parents=True, exist_ok=True)
    args.report_output.write_text(result.report, encoding="utf-8")
    print(f"Wrote {args.output}")
    print(f"Wrote {args.report_output}")
    print(f"Branch rewrites: {result.rewrite_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
