#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path as _SSEIRPath
import sys as _sseir_sys
import re
_SSEIR_ROOT = _SSEIRPath(__file__).resolve().parents[1]
for _sseir_path in (_SSEIR_ROOT / "legacy_yul", _SSEIR_ROOT / "s_seir"):
    _sseir_text = str(_sseir_path)
    if _sseir_text not in _sseir_sys.path:
        _sseir_sys.path.insert(0, _sseir_text)

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from assembly_cfg_memory_adapter import resolve_memory_read_with_loops as legacy_resolve_memory_read
from assembly_memory_ssa import analyze_block, assigned_names, statement_expression
from assembly_semantic_ir import parse_int_literal, strip_ssa
from s_seir_model import FunctionUnit
from s_seir_yul_normalize import call_parts, normalize_expr


@dataclass
class SSeirMemorySSAView:
    """S-SEIR-owned MemorySSA view backed by the legacy Yul MemorySSA engine.

    This class is intentionally thin: S-SEIR owns the analysis object, loop
    context, and query API, while reusing the existing MemorySSA algorithm and
    data structures through `backend`.
    """

    assembly_block_id: int
    backend: Any
    function_loop_context: list[dict[str, Any]]
    boundary_context: dict[str, Any] = field(default_factory=dict)
    scope: str = "s_seir_function_memoryssa_view"
    inherited_states: list[Any] = field(default_factory=list)
    bridge_facts: list[dict[str, Any]] = field(default_factory=list)
    persistent_value_names: set[str] = field(default_factory=set)
    value_phis: dict[tuple[int, str], "CFGValuePhi"] = field(default_factory=dict)
    memory_phis: dict[tuple[int, str], "CFGMemoryPhi"] = field(default_factory=dict)
    canonical_value_versions: dict[str, str] = field(default_factory=dict)
    loop_nodes_by_header: dict[int, set[int]] = field(default_factory=dict)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.backend, name)

    def states_at(self, node_id: int):
        return self.backend.states_at(node_id)

    def query_states_at(self, node_id: int) -> list[Any]:
        current_states = list(self.backend.states_at(node_id))
        if not self.inherited_states:
            return current_states
        if not current_states:
            return [clone_bridge_state(state, self.assembly_block_id, self.persistent_value_names) for state in self.inherited_states]
        out = []
        for inherited in self.inherited_states:
            for current in current_states:
                out.append(merge_inherited_state(inherited, current, self.assembly_block_id, self.persistent_value_names))
        return out

    def canonical_value_version(self, version: Any) -> str:
        text = str(version or "")
        return self.canonical_value_versions.get(text, text)

    def canonicalize_value_path_records(
        self,
        node_id: int,
        name: str,
        created: bool,
        records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Collapse abstract loop iterations onto one static SSA definition.

        A Yul assignment is one static CFG definition even when the worklist
        visits it through both the entry iteration and a widened backedge
        state.  Genuine branch alternatives remain separate because their
        path predicates differ.
        """
        normalized: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        enclosing = [
            header for header, nodes in self.loop_nodes_by_header.items()
            if node_id in nodes
        ]
        loop_phi = next(
            (
                self.value_phis[(header, name)].version
                for header in sorted(enclosing, key=lambda item: len(self.loop_nodes_by_header[item]))
                if (header, name) in self.value_phis
            ),
            None,
        )
        for record in records:
            item = dict(record)
            version = self.canonical_value_version(item.get("version"))
            if not created and loop_phi:
                definition = getattr(self.backend, "value_definitions", {}).get(version)
                definition_node = getattr(definition, "node_id", None)
                # A loop-header phi replaces values entering from outside the
                # natural loop. A definition made inside the loop remains the
                # reaching definition for uses it dominates later in the body.
                if definition_node is None or not any(
                    int(definition_node) in self.loop_nodes_by_header[header]
                    for header in enclosing
                ):
                    version = loop_phi
            item["version"] = version
            key = (version, str(item.get("local_condition") or "entry"))
            if key in seen:
                continue
            seen.add(key)
            normalized.append(item)
        return normalized

    def query_memory(self, node_id: int, pointer: str, length: str | None = None, reason: str | None = None) -> dict[str, Any]:
        result = legacy_resolve_memory_read(self.backend, node_id, pointer, length, reason)
        byte_slice = self.resolve_memory_byte_slice(node_id, pointer, length, reason)
        if byte_slice:
            result["byte_slice"] = byte_slice
            symbolic_words = words_from_byte_slice(byte_slice)
            if byte_slice.get("complete") and int(byte_slice.get("size") or 0) == 0:
                result["words"] = []
                result["complete"] = True
                result["has_unknown"] = False
                result["empty_range"] = True
            elif symbolic_words:
                result["words"] = symbolic_words
                result["complete"] = bool(byte_slice.get("complete"))
                result["has_unknown"] = not bool(byte_slice.get("complete"))
        result["owner"] = "S-SEIR"
        result["tracker_scope"] = "s_seir_memoryssa_query"
        result["assembly_block"] = self.assembly_block_id
        result["function_loop_context"] = self.function_loop_context
        if self.boundary_context:
            result["solidity_yul_boundary"] = self.boundary_context
            result["known_external_inputs"] = self.boundary_context.get("external_reads") or []
            result["external_outputs"] = self.boundary_context.get("external_writes") or []
        result["loop_alignment"] = "s_seir_controls_all_loop_contexts_legacy_yul_memoryssa_backend"
        if self.bridge_facts:
            result["function_memory_bridge"] = self.bridge_facts
        if self.function_loop_context:
            result["inside_function_level_loop"] = True
        return result

    def resolve_memory_byte_slice(self, node_id: int, pointer: str, length: str | None = None, reason: str | None = None) -> dict[str, Any] | None:
        size = self.resolve_static_int(node_id, length or "")
        if size is None or size < 0 or size > 4096:
            return None
        states = self.query_states_at(node_id)
        if not states:
            return None
        path_results = []
        for state in states:
            query_aliases = linear_aliases_for_text(pointer, state)
            slices = self.byte_slices_for_state(state, node_id, query_aliases, size)
            path_results.append({
                "path": path_text(state),
                "complete": byte_slice_complete(slices, size),
                "slices": slices,
            })
        signatures = {slice_signature(item["slices"]) for item in path_results}
        merged = path_results[0]["slices"] if len(signatures) == 1 and path_results else []
        complete = bool(path_results) and all(item["complete"] for item in path_results)
        return {
            "query_kind": "MemoryByteSliceResult",
            "tracker_scope": "s_seir_memory_byte_axis",
            "node_id": node_id,
            "reason": reason,
            "pointer": pointer,
            "length": length,
            "address_aliases": [alias for alias in linear_aliases_for_text(pointer, states[0])],
            "size": size,
            "complete": complete,
            "has_overlap": any(item.get("overlap_count", 0) > 1 for item in merged),
            "slices": merged,
            "path_slices": path_results,
            "packed_semantics": [slice_semantic(item) for item in merged] if merged else [],
        }

    def resolve_static_int(self, node_id: int, value: str | None) -> int | None:
        direct = static_int(value)
        if direct is not None:
            return direct
        text = str(value or "").strip()
        if not is_simple_identifier(text):
            return None
        for state in self.query_states_at(node_id):
            definition = getattr(state, "values", {}).get(text)
            if not definition:
                continue
            resolved = static_int(yul_expr_text(getattr(definition, "expression", None)))
            if resolved is not None:
                return resolved
        return None

    def byte_slices_for_state(self, state: Any, node_id: int, query_aliases: list[dict[str, Any]], size: int) -> list[dict[str, Any]]:
        cells: dict[int, dict[str, Any]] = {}
        writes = []
        seen_versions = set()
        for definition in getattr(state, "memory", {}).values():
            version = getattr(definition, "version", None)
            if version in seen_versions:
                continue
            seen_versions.add(version)
            def_node = getattr(definition, "node_id", None)
            if def_node is not None and int(def_node) > int(node_id):
                continue
            kind = getattr(definition, "kind", None)
            write_width = 1 if kind == "mstore8" else 32
            write_aliases = aliases_for_definition(definition)
            overlap_plan = first_alias_overlap(query_aliases, write_aliases, size, write_width)
            if overlap_plan is None:
                continue
            writes.append((int(def_node or 0), str(version), overlap_plan, definition))
        for _node, _version, overlap_plan, definition in sorted(writes, key=lambda item: (item[0], item[1])):
            kind = getattr(definition, "kind", None)
            if kind not in {"mstore", "mstore8"}:
                continue
            value = getattr(definition, "value", None)
            if kind == "mstore8":
                width = 1
                source_width = 32
                source_base_offset = 31
            else:
                width = 32
                source_width = 32
                source_base_offset = 0
            query_start = int(overlap_plan["query_offset"])
            write_relative_start = int(overlap_plan["write_relative_start"])
            overlap_size = int(overlap_plan["size"])
            for delta in range(overlap_size):
                query_offset = query_start + delta
                source_offset = source_base_offset + write_relative_start + delta
                previous = cells.get(query_offset)
                cells[query_offset] = {
                    "query_offset": query_offset,
                    "source_value": value,
                    "source_offset": source_offset,
                    "source_width": source_width,
                    "source_version": getattr(definition, "version", None),
                    "source_node_id": getattr(definition, "node_id", None),
                    "source_kind": kind,
                    "origin_src": getattr(definition, "origin_src", None),
                    "query_alias": overlap_plan.get("query_alias"),
                    "write_alias": overlap_plan.get("write_alias"),
                    "overlap_count": int((previous or {}).get("overlap_count", 0)) + 1,
                }
        return coalesce_byte_cells(cells, size)


@dataclass(frozen=True)
class CFGValuePhi:
    version: str
    header_node_id: int
    name: str
    inputs: tuple[dict[str, Any], ...]
    loop_nodes: tuple[int, ...]
    backedge_sources: tuple[int, ...]
    phi_role: str = "loop_carried"


@dataclass(frozen=True)
class CFGMemoryPhi:
    version: str
    header_node_id: int
    address: str
    inputs: tuple[dict[str, Any], ...]
    loop_nodes: tuple[int, ...]
    backedge_sources: tuple[int, ...]
    phi_role: str = "loop_carried_memory"


def loop_context_for_block(control: dict[str, Any] | None, block_id: int) -> list[dict[str, Any]]:
    contexts = (control or {}).get("loop_contexts", {})
    return contexts.get(block_id) or contexts.get(str(block_id)) or []


def boundary_context_for_block(control: dict[str, Any] | None, block_id: int) -> dict[str, Any]:
    contexts = (control or {}).get("assembly_boundaries", {})
    return contexts.get(block_id) or contexts.get(str(block_id)) or {}


def cfg_predecessors(cfg: Any) -> dict[int, set[int]]:
    out = {int(node.node_id): set() for node in cfg.nodes}
    for edge in cfg.edges:
        out.setdefault(int(edge.target), set()).add(int(edge.source))
    return out


def cfg_successors(cfg: Any) -> dict[int, set[int]]:
    out = {int(node.node_id): set() for node in cfg.nodes}
    for edge in cfg.edges:
        out.setdefault(int(edge.source), set()).add(int(edge.target))
    return out


def natural_loops(cfg: Any) -> tuple[dict[int, set[int]], dict[int, set[int]]]:
    """Build natural loops from the explicit structured-CFG backedges.

    The legacy Yul CFG labels the post-to-header edge as ``loop back``.  For
    each such edge, the conventional reverse-predecessor closure yields the
    natural loop. Multiple latches for the same header are merged.
    """
    predecessors = cfg_predecessors(cfg)
    loops: dict[int, set[int]] = {}
    latches: dict[int, set[int]] = {}
    for edge in cfg.edges:
        if str(edge.label) != "loop back":
            continue
        header = int(edge.target)
        latch = int(edge.source)
        nodes = {header, latch}
        work = [latch]
        while work:
            current = work.pop()
            for predecessor in predecessors.get(current, set()):
                if predecessor in nodes:
                    continue
                nodes.add(predecessor)
                if predecessor != header:
                    work.append(predecessor)
        loops.setdefault(header, set()).update(nodes)
        latches.setdefault(header, set()).add(latch)
    return loops, latches


def yul_identifiers(node: Any, *, function_name: bool = False) -> set[str]:
    if not isinstance(node, dict):
        return set()
    if node.get("nodeType") == "YulIdentifier":
        name = str(node.get("name") or "")
        return set() if function_name or not name else {name}
    out: set[str] = set()
    for key, value in node.items():
        if isinstance(value, dict):
            out.update(yul_identifiers(value, function_name=key == "functionName"))
        elif isinstance(value, list):
            for item in value:
                out.update(yul_identifiers(item, function_name=False))
    return out


def cfg_value_liveness(backend: Any) -> tuple[dict[int, set[str]], dict[int, set[str]]]:
    """Compute classical backward live-in/live-out sets on the Yul CFG."""
    cfg = backend.cfg
    successors = cfg_successors(cfg)
    uses: dict[int, set[str]] = {}
    definitions: dict[int, set[str]] = {}
    for cfg_node in cfg.nodes:
        node_id = int(cfg_node.node_id)
        ast_node = backend.node_ast.get(node_id)
        if not isinstance(ast_node, dict):
            uses[node_id] = set()
            definitions[node_id] = set()
            continue
        definitions[node_id] = set(assigned_names(ast_node))
        expression = statement_expression(ast_node) or ast_node.get("condition")
        uses[node_id] = yul_identifiers(expression)

    live_in = {int(node.node_id): set() for node in cfg.nodes}
    live_out = {int(node.node_id): set() for node in cfg.nodes}
    changed = True
    while changed:
        changed = False
        for cfg_node in reversed(cfg.nodes):
            node_id = int(cfg_node.node_id)
            new_out = set().union(*(live_in[item] for item in successors.get(node_id, set()))) if successors.get(node_id) else set()
            new_in = uses[node_id] | (new_out - definitions[node_id])
            if new_out != live_out[node_id] or new_in != live_in[node_id]:
                live_out[node_id] = new_out
                live_in[node_id] = new_in
                changed = True
    return live_in, live_out


def definition_paths(backend: Any, definition: Any) -> tuple[str, ...]:
    observation = backend.observations.get(int(definition.node_id))
    if observation is None:
        return ()
    paths = []
    for state in observation.outgoing:
        current = getattr(state, "values", {}).get(str(definition.name))
        if current is None or str(getattr(current, "version", "")) != str(definition.version):
            continue
        path = path_text(state)
        if path not in paths:
            paths.append(path)
    return tuple(paths)


def canonical_loop_value_versions(
    backend: Any,
    loops: dict[int, set[int]],
) -> dict[str, str]:
    """Map repeated worklist versions to one static CFG definition.

    The map is restricted to definitions at the same CFG node, for the same
    source variable, under the same path predicate. It therefore cannot merge
    definitions from distinct control-flow alternatives.
    """
    phi_versions = {
        str(definition.version)
        for definition in getattr(backend, "loop_value_phis", {}).values()
    }
    loop_nodes = set().union(*loops.values()) if loops else set()
    groups: dict[tuple[int, str, tuple[str, ...]], list[Any]] = {}
    for definition in backend.value_definitions.values():
        if int(definition.node_id) not in loop_nodes or str(definition.version) in phi_versions:
            continue
        key = (int(definition.node_id), str(definition.name), definition_paths(backend, definition))
        groups.setdefault(key, []).append(definition)

    out: dict[str, str] = {}
    for definitions in groups.values():
        if len(definitions) < 2:
            continue
        def score(definition: Any) -> tuple[int, int]:
            reaching = {
                str(getattr(item, "version", ""))
                for item in getattr(definition, "values_before", {}).values()
            }
            phi_reaching = len(reaching.intersection(phi_versions))
            suffix = re.search(r"(\d+)$", str(definition.version))
            return phi_reaching, int(suffix.group(1)) if suffix else 0

        canonical = max(definitions, key=score)
        for definition in definitions:
            out[str(definition.version)] = str(canonical.version)
    return out


def solidity_entry_value(unit: FunctionUnit, name: str) -> tuple[str, str]:
    variable = next((item for item in unit.parameters if item.name == name), None)
    if variable is not None:
        return name, "function_parameter"
    variable = next((item for item in unit.returns if item.name == name), None)
    if variable is not None:
        type_string = str(variable.type_string or "")
        if type_string == "bool":
            return "false", "solidity_named_return_default"
        if type_string.startswith("address"):
            return "address(0)", "solidity_named_return_default"
        if re.match(r"^(?:u?int|bytes\d+|enum\b)", type_string):
            return "0", "solidity_named_return_default"
        return f"default({name})", "solidity_named_return_default"
    return name, "assembly_boundary_value"


def loop_preheader_versions(backend: Any, header: int, name: str, latches: set[int]) -> list[dict[str, Any]]:
    out = []
    seen = set()
    observation = backend.observations.get(header)
    for state in getattr(observation, "incoming", []) or []:
        predecessor = int(state.trace[-1]) if getattr(state, "trace", ()) else None
        if predecessor in latches:
            continue
        definition = getattr(state, "values", {}).get(name)
        if definition is None:
            continue
        key = (predecessor, str(definition.version))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "edge": "preheader",
            "predecessor": predecessor,
            "value": str(definition.version),
            "condition": path_text(state),
        })
    return out


def loop_backedge_versions(
    backend: Any,
    name: str,
    latches: set[int],
    canonical_versions: dict[str, str],
) -> list[dict[str, Any]]:
    out = []
    seen = set()
    for latch in sorted(latches):
        observation = backend.observations.get(latch)
        for state in getattr(observation, "outgoing", []) or []:
            definition = getattr(state, "values", {}).get(name)
            if definition is None:
                continue
            version = canonical_versions.get(str(definition.version), str(definition.version))
            key = (latch, version, path_text(state))
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "edge": "backedge",
                "predecessor": latch,
                "value": version,
                "condition": path_text(state),
            })
    return out


def linear_memory_location(text: Any) -> tuple[str, int] | None:
    expression = str(text or "").strip()
    direct = static_int(expression)
    if direct is not None:
        return "<absolute>", direct
    if is_simple_identifier(expression):
        return expression, 0
    name, args = call_parts(expression)
    if name in {"add", "sub"} and len(args) == 2:
        left, right = args
        right_value = static_int(right)
        left_value = static_int(left)
        if right_value is not None:
            base = linear_memory_location(left)
            if base:
                return base[0], base[1] + (right_value if name == "add" else -right_value)
        if name == "add" and left_value is not None:
            base = linear_memory_location(right)
            if base:
                return base[0], base[1] + left_value
    return None


def memory_ranges_overlap(left: tuple[str, int, int], right: tuple[str, int, int]) -> bool:
    if left[0] != right[0]:
        return False
    return max(left[1], right[1]) < min(left[1] + left[2], right[1] + right[2])


def yul_memory_accesses(node: Any) -> tuple[list[tuple[str, int, int] | None], list[tuple[str, int, int] | None]]:
    """Return memory reads and writes in EVM evaluation order classes.

    Reads nested in a write expression are reported before the root write by
    the caller. Unknown copy/call ranges are represented by ``None`` and are
    therefore treated conservatively by memory liveness.
    """
    reads: list[tuple[str, int, int] | None] = []
    writes: list[tuple[str, int, int] | None] = []

    def location(expr: Any, width: int) -> tuple[str, int, int] | None:
        parsed = linear_memory_location(yul_expr_text(expr))
        return (parsed[0], parsed[1], width) if parsed else None

    def visit(expr: Any, *, root_statement: bool = False) -> None:
        if not isinstance(expr, dict):
            return
        name, args = call_parts(yul_expr_text(expr))
        raw_name = name or ""
        if raw_name == "mload" and len(args) == 1:
            reads.append(location((expr.get("arguments") or [None])[0], 32))
        elif raw_name == "keccak256" and len(args) == 2:
            size = static_int(args[1])
            reads.append(location((expr.get("arguments") or [None])[0], size) if size is not None else None)
        elif raw_name in {"return", "revert"} and len(args) >= 2:
            size = static_int(args[1])
            reads.append(location((expr.get("arguments") or [None])[0], size) if size is not None else None)
        elif raw_name.startswith("log") and len(args) >= 2:
            size = static_int(args[1])
            reads.append(location((expr.get("arguments") or [None])[0], size) if size is not None else None)
        elif raw_name in {"call", "callcode"} and len(args) >= 7:
            input_size = static_int(args[4])
            output_size = static_int(args[6])
            reads.append(location(expr["arguments"][3], input_size) if input_size is not None else None)
            writes.append(location(expr["arguments"][5], output_size) if output_size is not None else None)
        elif raw_name in {"staticcall", "delegatecall"} and len(args) >= 6:
            input_size = static_int(args[3])
            output_size = static_int(args[5])
            reads.append(location(expr["arguments"][2], input_size) if input_size is not None else None)
            writes.append(location(expr["arguments"][4], output_size) if output_size is not None else None)
        elif raw_name in {"calldatacopy", "codecopy", "returndatacopy", "mcopy", "extcodecopy"}:
            writes.append(None)

        for argument in reversed(expr.get("arguments") or []):
            visit(argument)
        if root_statement and raw_name in {"mstore", "mstore8"} and len(expr.get("arguments") or []) >= 2:
            writes.append(location(expr["arguments"][0], 1 if raw_name == "mstore8" else 32))

    visit(statement_expression(node), root_statement=True)
    return reads, writes


def memory_phi_live_at_header(
    backend: Any,
    header: int,
    address: str,
) -> bool:
    """Test virtual-memory liveness with a CFG worklist.

    A loop-carried memory version is required only if some path reads the
    incoming location before a definite same-location overwrite. This is the
    standard live-on-entry criterion applied to MemorySSA virtual locations.
    """
    parsed = linear_memory_location(address)
    if parsed is None:
        return True
    target = (parsed[0], parsed[1], 1)
    successors = cfg_successors(backend.cfg)
    work = [(successor, False) for successor in successors.get(header, set()) if successor != header]
    seen: set[tuple[int, bool]] = set()
    while work:
        node_id, killed = work.pop()
        marker = (node_id, killed)
        if marker in seen:
            continue
        seen.add(marker)
        ast_node = backend.node_ast.get(node_id)
        reads, writes = yul_memory_accesses(ast_node) if isinstance(ast_node, dict) else ([], [])
        if not killed:
            for read in reads:
                if read is None or memory_ranges_overlap(target, read):
                    return True
            for write in writes:
                if write is not None and memory_ranges_overlap(target, write):
                    killed = True
                    break
        for successor in successors.get(node_id, set()):
            if successor != header:
                work.append((successor, killed))
    return False


def build_cfg_loop_ssa_metadata(
    backend: Any,
    unit: FunctionUnit,
) -> tuple[
    dict[tuple[int, str], CFGValuePhi],
    dict[tuple[int, str], CFGMemoryPhi],
    dict[str, str],
    dict[int, set[int]],
]:
    loops, latches_by_header = natural_loops(backend.cfg)
    live_in, _live_out = cfg_value_liveness(backend)
    canonical_versions = canonical_loop_value_versions(backend, loops)
    value_phis: dict[tuple[int, str], CFGValuePhi] = {}
    for (header, name), definition in getattr(backend, "loop_value_phis", {}).items():
        header = int(header)
        if name not in live_in.get(header, set()):
            continue
        latches = latches_by_header.get(header, set())
        inputs = loop_preheader_versions(backend, header, name, latches)
        if not inputs:
            value, source = solidity_entry_value(unit, name)
            inputs.append({
                "edge": "preheader",
                "predecessor": None,
                "value": value,
                "source": source,
                "condition": "entry",
            })
        inputs.extend(loop_backedge_versions(backend, name, latches, canonical_versions))
        unique_inputs = []
        seen_inputs = set()
        for item in inputs:
            key = (item.get("edge"), item.get("predecessor"), item.get("value"), item.get("condition"))
            if key in seen_inputs:
                continue
            seen_inputs.add(key)
            unique_inputs.append(item)
        value_phis[(header, name)] = CFGValuePhi(
            version=str(definition.version),
            header_node_id=header,
            name=str(name),
            inputs=tuple(unique_inputs),
            loop_nodes=tuple(sorted(loops.get(header, {header}))),
            backedge_sources=tuple(sorted(latches)),
        )

    # The existing MemorySSA remains authoritative for aliasing and reaching
    # definitions. Expose its loop phis with explicit predecessor inputs; do
    # not reinterpret them as runtime mstore operations.
    memory_phis: dict[tuple[int, str], CFGMemoryPhi] = {}
    seen_memory_locations: set[tuple[int, tuple[str, int] | str]] = set()
    for (header, address), definition in sorted(getattr(backend, "loop_memory_phis", {}).items()):
        header = int(header)
        location = linear_memory_location(address) or str(address)
        memory_key = (header, location)
        if memory_key in seen_memory_locations or not memory_phi_live_at_header(backend, header, address):
            continue
        seen_memory_locations.add(memory_key)
        latches = latches_by_header.get(header, set())
        inputs = []
        for state in getattr(backend.observations.get(header), "incoming", []) or []:
            predecessor = int(state.trace[-1]) if getattr(state, "trace", ()) else None
            if predecessor in latches:
                continue
            reaching = getattr(state, "memory", {}).get(address)
            if reaching is not None:
                inputs.append({"edge": "preheader", "predecessor": predecessor, "value": str(reaching.version), "condition": path_text(state)})
        for latch in sorted(latches):
            for state in getattr(backend.observations.get(latch), "outgoing", []) or []:
                reaching = getattr(state, "memory", {}).get(address)
                if reaching is not None:
                    inputs.append({"edge": "backedge", "predecessor": latch, "value": str(reaching.version), "condition": path_text(state)})
        unique_inputs = []
        seen_inputs = set()
        for item in inputs:
            key = (item.get("edge"), item.get("predecessor"), item.get("value"), item.get("condition"))
            if key not in seen_inputs:
                seen_inputs.add(key)
                unique_inputs.append(item)
        memory_phis[(header, address)] = CFGMemoryPhi(
            version=str(definition.version),
            header_node_id=header,
            address=str(address),
            inputs=tuple(unique_inputs),
            loop_nodes=tuple(sorted(loops.get(header, {header}))),
            backedge_sources=tuple(sorted(latches)),
        )
    return value_phis, memory_phis, canonical_versions, loops


def build_memory_ssa_views(unit: FunctionUnit, control: dict[str, Any] | None = None) -> dict[int, SSeirMemorySSAView]:
    views: dict[int, SSeirMemorySSAView] = {}
    prior_snapshots: list[Any] = []
    prior_block = None
    for block in unit.assembly_blocks:
        backend = analyze_block(block)
        value_phis, memory_phis, canonical_versions, loop_nodes = build_cfg_loop_ssa_metadata(backend, unit)
        bridge_facts = []
        inherited = []
        if prior_block is not None and prior_snapshots:
            safety = classify_intervening_solidity_memory_safety(prior_block, block)
            bridge_facts.append({
                "kind": "FunctionLevelAssemblyMemoryBridge",
                "from_assembly_block": prior_block.block_id,
                "to_assembly_block": block.block_id,
                "policy": "inherit_exit_memory_snapshot" if safety["safe"] else "break_on_possible_solidity_memory_mutation",
                "safe": safety["safe"],
                "reason": safety["reason"],
            })
            if safety["safe"]:
                inherited = prior_snapshots
        views[block.block_id] = SSeirMemorySSAView(
            assembly_block_id=block.block_id,
            backend=backend,
            function_loop_context=loop_context_for_block(control, block.block_id),
            boundary_context=boundary_context_for_block(control, block.block_id),
            inherited_states=inherited,
            bridge_facts=bridge_facts,
            persistent_value_names=function_level_value_names(unit),
            value_phis=value_phis,
            memory_phis=memory_phis,
            canonical_value_versions=canonical_versions,
            loop_nodes_by_header=loop_nodes,
        )
        prior_snapshots = exit_snapshots_for_backend(backend)
        prior_block = block
    return views


def exit_snapshots_for_backend(backend: Any) -> list[Any]:
    cfg = getattr(backend, "cfg", None)
    observations = getattr(backend, "observations", {}) or {}
    exit_id = getattr(cfg, "exit", None)
    states: list[Any] = []
    if exit_id is not None and exit_id in observations:
        obs = observations[exit_id]
        states = list(getattr(obs, "outgoing", None) or getattr(obs, "incoming", None) or [])
    if not states:
        for obs in observations.values():
            outgoing = list(getattr(obs, "outgoing", None) or [])
            if outgoing:
                states = outgoing
    return states[:8]


def classify_intervening_solidity_memory_safety(previous_block: Any, next_block: Any) -> dict[str, Any]:
    prev_end = previous_block.source_range[1]
    next_start = next_block.source_range[0]
    if next_start <= prev_end:
        return {"safe": False, "reason": "overlapping_or_unordered_assembly_ranges"}
    span = previous_block.source[prev_end:next_start]
    clean = strip_comments_and_strings(span)
    if not clean.strip():
        return {"safe": True, "reason": "empty_intervening_span"}
    unsafe_patterns = [
        (r"\bassembly\b", "intervening_inline_assembly"),
        (r"\bnew\s+(?:bytes|string|[A-Za-z_$][\w$]*(?:\s*\[\s*\])?)", "memory_allocation"),
        (r"\babi\s*\.", "abi_memory_operation"),
        (r"\breturn\b", "intervening_return"),
        (r"\brevert\b", "intervening_revert"),
        (r"\bdelete\b", "delete_may_touch_storage_or_memory"),
        (r"\.(?:call|staticcall|delegatecall|callcode)\s*\(", "external_low_level_call"),
        (r"\b[A-Za-z_$][\w$]*\s*\.\s*(?:push|pop)\s*\(", "array_mutation_call"),
    ]
    for pattern, reason in unsafe_patterns:
        if re.search(pattern, clean):
            return {"safe": False, "reason": reason}
    calls = [
        match.group(1)
        for match in re.finditer(r"(?<!\.)\b([A-Za-z_$][\w$]*)\s*\(", clean)
        if match.group(1) not in {"if", "for", "while", "require", "assert", "unchecked"}
    ]
    if calls:
        return {"safe": False, "reason": "intervening_function_call:" + ",".join(sorted(set(calls))[:4])}
    return {"safe": True, "reason": "value_only_solidity_span"}


def strip_comments_and_strings(text: str) -> str:
    text = re.sub(r"//.*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)
    text = re.sub(r"'(?:\\.|[^'\\])*'", "''", text)
    return text


def function_level_value_names(unit: FunctionUnit) -> set[str]:
    names = set()
    for item in [*unit.parameters, *unit.returns, *unit.locals, *unit.state_variables]:
        name = getattr(item, "name", None)
        if name:
            names.add(str(name))
    return names


def clone_bridge_state(state: Any, target_block_id: int, persistent_value_names: set[str] | None = None) -> Any:
    memory, mapping = clone_bridge_memory(getattr(state, "memory", {}) or {}, target_block_id)
    keep = persistent_value_names or set()
    inherited_values = {
        name: definition
        for name, definition in (getattr(state, "values", {}) or {}).items()
        if str(name) in keep
    }
    return SimpleNamespace(
        predicates=tuple(getattr(state, "predicates", ()) or ()),
        memory=memory,
        values=inherited_values,
        loop_bases={},
        trace=tuple(getattr(state, "trace", ()) or ()),
        _sseir_bridge_versions=mapping,
    )


def merge_inherited_state(inherited: Any, current: Any, target_block_id: int, persistent_value_names: set[str] | None = None) -> Any:
    bridge = clone_bridge_state(inherited, target_block_id, persistent_value_names)
    memory = dict(bridge.memory)
    memory.update(getattr(current, "memory", {}) or {})
    values = dict(getattr(bridge, "values", {}) or {})
    values.update(getattr(current, "values", {}) or {})
    predicates = tuple(getattr(inherited, "predicates", ()) or ()) + tuple(getattr(current, "predicates", ()) or ())
    return SimpleNamespace(
        predicates=tuple(dict.fromkeys(predicates)),
        memory=memory,
        values=values,
        loop_bases=dict(getattr(current, "loop_bases", {}) or {}),
        trace=tuple(getattr(current, "trace", ()) or ()),
        _sseir_bridge_versions=getattr(bridge, "_sseir_bridge_versions", {}),
    )


def clone_bridge_memory(memory: dict[str, Any], target_block_id: int) -> tuple[dict[str, Any], dict[str, str]]:
    by_old_version: dict[str, Any] = {}
    version_map: dict[str, str] = {}
    out: dict[str, Any] = {}
    for key, definition in memory.items():
        old_version = str(getattr(definition, "version", key))
        clone = by_old_version.get(old_version)
        if clone is None:
            new_version = f"bridge_b{target_block_id}_{old_version}"
            version_map[old_version] = new_version
            clone = SimpleNamespace(
                version=new_version,
                node_id=-1,
                address=getattr(definition, "address", None),
                value=getattr(definition, "value", None),
                kind=getattr(definition, "kind", None),
                origin_src=getattr(definition, "origin_src", None),
                memory_before={},
                aliases=clone_aliases(getattr(definition, "aliases", ()) or ()),
                inherited_from_version=old_version,
            )
            by_old_version[old_version] = clone
        out[key] = clone
    return out, version_map


def clone_aliases(aliases: Any) -> tuple[Any, ...]:
    return tuple(
        SimpleNamespace(
            key=getattr(alias, "key", None),
            base=getattr(alias, "base", None),
            offset=getattr(alias, "offset", 0),
            expression=getattr(alias, "expression", None),
            source=f"function_bridge:{getattr(alias, 'source', '')}",
        )
        for alias in aliases
    )


def resolve_memory_read_with_loops(memory_ssa: Any, node_id: int, pointer: str, length: str | None = None, reason: str | None = None) -> dict[str, Any]:
    if hasattr(memory_ssa, "query_memory"):
        return memory_ssa.query_memory(node_id, pointer, length, reason)
    result = legacy_resolve_memory_read(memory_ssa, node_id, pointer, length, reason)
    result["owner"] = "legacy_yul"
    return result


def static_int(value: Any) -> int | None:
    return parse_int_literal(strip_ssa(str(value or "").strip()))


def yul_expr_text(node: Any) -> str:
    if isinstance(node, dict):
        node_type = node.get("nodeType")
        if node_type == "YulIdentifier":
            return str(node.get("name") or "")
        if node_type == "YulLiteral":
            return str(node.get("value") or node.get("hexValue") or "")
        if node_type == "YulFunctionCall":
            fn = node.get("functionName") or {}
            name = yul_expr_text(fn)
            args = ", ".join(yul_expr_text(arg) for arg in node.get("arguments") or [])
            return f"{name}({args})"
    return str(node or "")


def is_simple_identifier(value: Any) -> bool:
    text = str(value or "")
    return bool(text) and all(ch.isalnum() or ch in {"_", "$"} for ch in text) and not text[0].isdigit()


def path_text(state: Any) -> str:
    return " && ".join(getattr(state, "predicates", []) or []) or "entry"


def linear_aliases_for_text(expr: Any, state: Any, seen: set[str] | None = None) -> list[dict[str, Any]]:
    seen = seen or set()
    text = str(expr or "").strip()
    if not text:
        return []
    parsed = static_int(text)
    if parsed is not None:
        return [
            {"key": normalize_alias_key("0x00", parsed), "base": "0x00", "offset": parsed, "expression": alias_expression("0x00", parsed), "source": "literal-zero-base"},
            {"key": normalize_expr(text), "base": text, "offset": 0, "expression": text, "source": "literal"},
        ]
    if is_simple_identifier(text):
        aliases = [{"key": normalize_alias_key(text, 0), "base": text, "offset": 0, "expression": text, "source": "identifier"}]
        if text in seen:
            return aliases
        definition = getattr(state, "values", {}).get(text)
        if not definition:
            return aliases
        source = yul_expr_text(getattr(definition, "expression", None))
        name, _args = call_parts(source)
        if name in {"add", "sub"} or is_simple_identifier(source):
            for alias in linear_aliases_for_text(source, state, seen | {text}):
                if alias["key"] not in {item["key"] for item in aliases}:
                    item = dict(alias)
                    item["source"] = f"value:{getattr(definition, 'version', '')}"
                    aliases.append(item)
        return aliases
    name, args = call_parts(text)
    if name in {"add", "sub"} and len(args) == 2:
        left, right = args
        right_const = static_int(right)
        left_const = static_int(left)
        if right_const is not None:
            delta = right_const if name == "add" else -right_const
            return shift_aliases(linear_aliases_for_text(left, state, seen), delta, text)
        if name == "add" and left_const is not None:
            return shift_aliases(linear_aliases_for_text(right, state, seen), left_const, text)
    return [{"key": normalize_expr(text), "base": text, "offset": 0, "expression": text, "source": "symbolic"}]


def shift_aliases(aliases: list[dict[str, Any]], delta: int, original: str) -> list[dict[str, Any]]:
    out = []
    seen = set()
    for alias in aliases:
        offset = int(alias.get("offset") or 0) + delta
        key = normalize_alias_key(str(alias.get("base")), offset)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "key": key,
            "base": alias.get("base"),
            "offset": offset,
            "expression": alias_expression(str(alias.get("base")), offset),
            "source": alias.get("source"),
        })
    original_key = normalize_expr(original)
    if original_key not in seen:
        out.append({"key": original_key, "base": original, "offset": 0, "expression": original, "source": "original"})
    return out


def normalize_alias_key(base: str, offset: int) -> str:
    base_text = normalize_expr(base)
    return base_text if offset == 0 else f"{base_text} + {offset}"


def alias_expression(base: str, offset: int) -> str:
    base_text = str(base)
    return base_text if offset == 0 else f"{base_text} + {offset}"


def aliases_for_definition(definition: Any) -> list[dict[str, Any]]:
    aliases = []
    for alias in getattr(definition, "aliases", ()) or ():
        aliases.append({
            "key": getattr(alias, "key", None),
            "base": getattr(alias, "base", None),
            "offset": int(getattr(alias, "offset", 0) or 0),
            "expression": getattr(alias, "expression", None),
            "source": getattr(alias, "source", None),
        })
    if not aliases:
        address = str(getattr(definition, "address", "") or "")
        parsed = static_int(address)
        if parsed is not None:
            aliases.extend(linear_aliases_for_text(address, type("State", (), {"values": {}})()))
        elif address:
            aliases.append({"key": normalize_expr(address), "base": address, "offset": 0, "expression": address, "source": "definition-address"})
    return aliases


def first_alias_overlap(query_aliases: list[dict[str, Any]], write_aliases: list[dict[str, Any]], size: int, write_width: int) -> dict[str, Any] | None:
    for query in query_aliases:
        for write in write_aliases:
            if str(query.get("base")) != str(write.get("base")):
                continue
            query_start = int(query.get("offset") or 0)
            query_end = query_start + size
            write_start = int(write.get("offset") or 0)
            overlap_start = max(query_start, write_start)
            overlap_end = min(query_end, write_start + write_width)
            if overlap_start >= overlap_end:
                continue
            return {
                "query_offset": overlap_start - query_start,
                "write_relative_start": overlap_start - write_start,
                "size": overlap_end - overlap_start,
                "query_alias": query,
                "write_alias": write,
            }
    return None


def known_value_info(value: Any) -> dict[str, Any]:
    raw = str(value or "").strip()
    text = normalize_expr(raw)
    if not raw or raw == "unknown" or raw.startswith("unknown("):
        return {"known": False, "reason": "unknown_value"}
    if static_int(raw) is not None:
        return {"known": True, "kind": "constant", "expr": text}
    if text in {"msg.sender", "caller()"}:
        return {"known": True, "kind": "evm_builtin", "expr": "msg.sender"}
    name, _args = call_parts(raw)
    if name in {"caller", "timestamp", "gas", "address", "number", "origin", "callvalue", "calldatasize"}:
        return {"known": True, "kind": "evm_builtin", "expr": text}
    if is_simple_identifier(raw) or raw.endswith(".slot"):
        return {"known": True, "kind": "symbolic_known", "expr": text}
    return {"known": True, "kind": "derived_known", "expr": text}


def words_from_byte_slice(byte_slice: dict[str, Any]) -> list[dict[str, Any]]:
    size = int(byte_slice.get("size") or 0)
    slices = byte_slice.get("slices") or []
    if not byte_slice.get("complete") or not slices:
        return []
    if len(slices) == 1:
        item = slices[0]
        return [word_from_slice(byte_slice, item, size)]
    if all(int(item.get("size") or 0) == 32 for item in slices):
        return [word_from_slice(byte_slice, item, int(item.get("size") or 32)) for item in slices]
    return [{
        "offset": 0,
        "offset_expr": "0",
        "address_key": str((byte_slice.get("address_aliases") or [{}])[0].get("expression") or byte_slice.get("pointer")),
        "value": "abi.encodePacked(" + ", ".join(str(item.get("extraction")) for item in slices) + ")",
        "source": "byte_axis_memory_slice",
        "size": size,
        "byte_slices": slices,
        "known_value": {
            "known": all((item.get("known_value") or {}).get("known") for item in slices),
            "kind": "byte_slice_composite",
        },
    }]


def word_from_slice(byte_slice: dict[str, Any], item: dict[str, Any], size: int) -> dict[str, Any]:
    return {
        "offset": int(item.get("query_offset") or 0),
        "offset_expr": str(item.get("query_offset") or 0),
        "address_key": str((item.get("query_alias") or {}).get("expression") or byte_slice.get("pointer")),
        "value": item.get("extraction"),
        "source": "byte_axis_memory_slice",
        "size": size,
        "memory_ssa": item.get("source_version"),
        "definition": {
            "node_id": item.get("source_node_id"),
            "version": item.get("source_version"),
            "address": (item.get("write_alias") or {}).get("expression"),
            "value": item.get("source_value"),
        },
        "known_value": item.get("known_value"),
    }


def coalesce_byte_cells(cells: dict[int, dict[str, Any]], size: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    offset = 0
    while offset < size:
        cell = cells.get(offset)
        if not cell:
            offset += 1
            continue
        start = offset
        end = offset + 1
        while end < size:
            nxt = cells.get(end)
            if not nxt:
                break
            if (
                nxt.get("source_value") != cell.get("source_value")
                or nxt.get("source_version") != cell.get("source_version")
                or int(nxt.get("source_offset", -1)) != int(cell.get("source_offset", -1)) + (end - start)
                or nxt.get("source_kind") != cell.get("source_kind")
            ):
                break
            end += 1
        size2 = end - start
        item = {
            "query_offset": start,
            "size": size2,
            "source_value": cell.get("source_value"),
            "source_range": [cell.get("source_offset"), int(cell.get("source_offset")) + size2],
            "source_offset": cell.get("source_offset"),
            "source_width": cell.get("source_width"),
            "source_version": cell.get("source_version"),
            "source_node_id": cell.get("source_node_id"),
            "source_kind": cell.get("source_kind"),
            "origin_src": cell.get("origin_src"),
            "extraction": byte_extraction(cell.get("source_value"), int(cell.get("source_offset")), size2, int(cell.get("source_width") or 32)),
            "known_value": known_value_info(cell.get("source_value")),
            "overlap_count": cell.get("overlap_count", 1),
        }
        out.append(item)
        offset = end
    return out


def byte_extraction(value: Any, source_offset: int, size: int, source_width: int) -> str:
    text = normalize_expr(value)
    if source_width == 32 and source_offset == 0 and size == 32:
        return text
    if source_width == 32 and source_offset == 12 and size == 20 and is_address_like(text):
        return f"bytes20({text})"
    if source_width == 32 and source_offset + size == 32:
        return f"low_bytes({text}, {size})"
    if source_width == 32 and source_offset == 0:
        return f"high_bytes({text}, {size})"
    return f"bytes({text}, offset={source_offset}, size={size})"


def is_address_like(text: str) -> bool:
    return text in {"msg.sender", "caller()"} or text.endswith(".sender")


def slice_semantic(item: dict[str, Any]) -> str:
    return str(item.get("extraction") or "")


def slice_signature(slices: list[dict[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            item.get("query_offset"),
            item.get("size"),
            item.get("source_value"),
            tuple(item.get("source_range") or []),
            item.get("source_version"),
            item.get("extraction"),
        )
        for item in slices
    )


def byte_slice_complete(slices: list[dict[str, Any]], size: int) -> bool:
    cursor = 0
    for item in sorted(slices, key=lambda it: int(it.get("query_offset") or 0)):
        offset = int(item.get("query_offset") or 0)
        width = int(item.get("size") or 0)
        if offset > cursor:
            return False
        cursor = max(cursor, offset + width)
        if cursor >= size:
            return True
    return size == 0
