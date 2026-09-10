#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path as _SSEIRPath
import os
import re
import signal
import sys as _sseir_sys
_SSEIR_ROOT = _SSEIRPath(__file__).resolve().parents[1]
for _sseir_path in (_SSEIR_ROOT / "legacy_yul", _SSEIR_ROOT / "s_seir"):
    _sseir_text = str(_sseir_path)
    if _sseir_text not in _sseir_sys.path:
        _sseir_sys.path.insert(0, _sseir_text)

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assembly_ast_cfg import build_yul_cfg, parse_src, solc_supports_option
from s_seir_model import FunctionUnit, SourceStatement


class SlitherLoadTimeout(TimeoutError):
    pass


@dataclass(frozen=True)
class Range:
    start: int
    end: int

    @property
    def valid(self) -> bool:
        return self.end > self.start

    def contains(self, other: "Range") -> bool:
        return self.valid and other.valid and self.start <= other.start and other.end <= self.end

    def overlaps(self, other: "Range") -> bool:
        return self.valid and other.valid and self.start < other.end and other.start < self.end


class ControlBuilder:
    """Build S-SEIR function-level control by combining Slither and local Yul CFG.

    Slither is used for Solidity-level function CFG. Slither's own assembly-expanded
    nodes are treated only as anchors: nodes inside an InlineAssembly source range are
    replaced by the project's local assembly_ast_cfg Yul subgraph so later MemorySSA
    and branch handling see the same Yul graph as the rest of the pipeline.
    """

    def __init__(
        self,
        source_path: Path | None = None,
        solc_bin: str | None = None,
        slither_bin: str | None = None,
        workdir: Path | None = None,
    ) -> None:
        self.source_path = source_path.resolve() if source_path else None
        self.solc_bin = solc_bin
        self.slither_bin = slither_bin
        self.workdir = workdir
        self._source_text = self.source_path.read_text(encoding="utf-8") if self.source_path and self.source_path.exists() else ""
        self._source_bytes = self.source_path.read_bytes() if self.source_path and self.source_path.exists() else b""
        self._slither_cache: Any | None = None
        self._slither_error: str | None = None
        self._slither_primary_failure: str | None = None
        self._slither_compile_mode: str | None = None
        self._slither_attempted = False
        self._last_function_match_method: str | None = None

    def build(self, unit: FunctionUnit) -> dict[str, Any]:
        if self.source_path and self.solc_bin:
            control = self._build_with_slither(unit)
            if control is not None:
                return control
        return self._build_skeleton(unit)

    def _build_with_slither(self, unit: FunctionUnit) -> dict[str, Any] | None:
        function = self._find_slither_function(unit)
        if function is None:
            return None

        blocks: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        edge_keys: set[tuple[str, str, str]] = set()
        notes = [
            "function_level_cfg_from_slither",
            "assembly_subgraph_embedded_from_assembly_ast_cfg",
            "slither_assembly_nodes_replaced_by_local_yul_cfg",
        ]
        if self._slither_compile_mode:
            notes.append(f"slither_compile_mode={self._slither_compile_mode}")
        if self._last_function_match_method:
            notes.append(f"function_match_method={self._last_function_match_method}")
        if self._slither_primary_failure:
            notes.append(f"slither_primary_failure={self._slither_primary_failure}")
        if self._slither_error:
            notes.append(f"slither_warning: {self._slither_error}")

        assembly_ranges = self._slither_assembly_ranges(function, unit)
        # Slither deliberately represents all TryStatement clauses as CATCH
        # CFG nodes, including the first (successful) ``returns`` clause.
        # Its public CFG edges therefore carry no success/catch labels.  Join
        # those nodes to the compiler AST only through their identical source
        # ranges; never infer clause roles from node numbers or CFG ordering.
        try_contexts = self._slither_try_contexts(function, unit)
        loop_contexts = self._assembly_loop_contexts(unit, assembly_ranges)
        asm_for_node: dict[int, int] = {}
        slither_block_ids: dict[int, str] = {}
        asm_entry_ids: dict[int, str] = {}
        asm_exit_ids: dict[int, str] = {}

        def add_edge(src: str, dst: str, kind: str) -> None:
            key = (src, dst, kind)
            if key not in edge_keys:
                edge_keys.add(key)
                edges.append({"from": src, "to": dst, "kind": kind})

        for node in function.nodes:
            ab_id = self._assembly_block_for_slither_node(node, assembly_ranges)
            if ab_id is not None:
                asm_for_node[int(node.node_id)] = ab_id
                continue
            block_id = f"bb_sol_slither_n{node.node_id}"
            slither_block_ids[int(node.node_id)] = block_id
            blocks.append(self._slither_block(block_id, node, unit, try_contexts))

        for ab in unit.assembly_blocks:
            cfg = build_yul_cfg(ab.yul_ast)
            asm_entry_ids[ab.block_id] = f"bb_asm{ab.block_id}_n{cfg.entry}"
            asm_exit_ids[ab.block_id] = f"bb_asm{ab.block_id}_n{cfg.exit}"
            for node in cfg.nodes:
                block_id = f"bb_asm{ab.block_id}_n{node.node_id}"
                blocks.append(
                    {
                        "block_id": block_id,
                        "kind": "yul",
                        "assembly_block": ab.block_id,
                        "stmts": self._yul_refs(unit, ab.block_id, node.text, node.src),
                        "terminator": self._yul_terminator(node),
                        "attrs": {
                            "node_id": node.node_id,
                            "node_kind": node.kind,
                            "src": node.src,
                            "text": node.text,
                            "function_loop_context": loop_contexts.get(ab.block_id, []),
                        },
                    }
                )
            for edge in cfg.edges:
                add_edge(
                    f"bb_asm{ab.block_id}_n{edge.source}",
                    f"bb_asm{ab.block_id}_n{edge.target}",
                    edge.label,
                )

        for node in function.nodes:
            node_id = int(node.node_id)
            source_asm = asm_for_node.get(node_id)
            source_id = self._representative_for_slither_node(
                node_id, slither_block_ids, asm_for_node, asm_entry_ids, asm_exit_ids, prefer_exit=True
            )
            if not source_id:
                continue
            for son in getattr(node, "sons", []):
                son_id = int(son.node_id)
                target_asm = asm_for_node.get(son_id)
                if source_asm is not None and source_asm == target_asm:
                    continue
                target_id = self._representative_for_slither_node(
                    son_id, slither_block_ids, asm_for_node, asm_entry_ids, asm_exit_ids, prefer_exit=False
                )
                if not target_id or source_id == target_id:
                    continue
                add_edge(source_id, target_id, self._slither_edge_label(node, son, try_contexts))

        notes.append(f"slither_nodes={len(function.nodes)}")
        notes.append(f"assembly_blocks={len(unit.assembly_blocks)}")
        notes.append("assembly_edge_policy=slither_predecessors_to_yul_entry_and_yul_exit_to_slither_successors")
        if any(loop_contexts.values()):
            notes.append("function_level_loop_context_attached_to_assembly_blocks")
        if try_contexts:
            notes.append("slither_try_catch_outcomes_matched_to_solc_clause_ranges")
        graph_analysis = self._unified_graph_analysis(blocks, edges)
        boundary_contexts = self._assembly_boundary_contexts(
            unit,
            blocks,
            edges,
            asm_entry_ids,
            asm_exit_ids,
            graph_analysis,
        )
        notes.extend([
            "slither_typed_use_def_archived_on_solidity_blocks",
            "unified_dominance_and_control_dependencies_computed_after_yul_subgraph_embedding",
            "solidity_yul_boundary_contexts_attached",
        ])
        return {
            "blocks": blocks,
            "edges": edges,
            "notes": notes,
            "loop_contexts": loop_contexts,
            "dominance": graph_analysis["dominance"],
            "control_dependencies": graph_analysis["control_dependencies"],
            "control_dependency_closure": graph_analysis["control_dependency_closure"],
            "typed_def_use": graph_analysis["typed_def_use"],
            "assembly_boundaries": boundary_contexts,
            "slither_declaration": self._slither_function_declaration(function),
            "slither_contract_declaration": self._slither_contract_declaration(
                getattr(function, "contract", None)
            ),
        }

    def _build_skeleton(self, unit: FunctionUnit) -> dict[str, Any]:
        blocks: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        notes = [
            "function_level_control_skeleton",
            "yul_cfg_embedded_from_assembly_ast_cfg",
            "precise_solidity_cfg_adapter_unavailable",
        ]
        if self._slither_compile_mode:
            notes.append(f"slither_compile_mode={self._slither_compile_mode}")
        if self._slither_primary_failure:
            notes.append(f"slither_primary_failure={self._slither_primary_failure}")
        if self._slither_error:
            notes.append(f"slither_error: {self._slither_error}")
        assembly_ranges = {ab.block_id: Range(*ab.source_range) for ab in unit.assembly_blocks}
        loop_contexts = self._assembly_loop_contexts(unit, assembly_ranges)
        sol = [s for s in unit.source_statements if s.lang == "solidity"]
        prev = None
        for i, stmt in enumerate(sol):
            bid = f"bb_sol_{i + 1}"
            blocks.append(
                {
                    "block_id": bid,
                    "kind": "solidity",
                    "stmts": [stmt.stmt_id],
                    "terminator": self._solidity_terminator(stmt.text),
                    "attrs": {"src": stmt.src, "fallback": True},
                }
            )
            if prev:
                edges.append({"from": prev, "to": bid, "kind": "fallthrough"})
            prev = bid
        for ab in unit.assembly_blocks:
            cfg = build_yul_cfg(ab.yul_ast)
            for n in cfg.nodes:
                blocks.append(
                    {
                        "block_id": f"bb_asm{ab.block_id}_n{n.node_id}",
                        "kind": "yul",
                        "assembly_block": ab.block_id,
                        "stmts": self._yul_refs(unit, ab.block_id, n.text, n.src),
                        "terminator": self._yul_terminator(n),
                        "attrs": {"node_id": n.node_id, "node_kind": n.kind, "src": n.src, "text": n.text, "function_loop_context": loop_contexts.get(ab.block_id, [])},
                    }
                )
            for e in cfg.edges:
                edges.append({"from": f"bb_asm{ab.block_id}_n{e.source}", "to": f"bb_asm{ab.block_id}_n{e.target}", "kind": e.label})
        return {"blocks": blocks, "edges": edges, "notes": notes, "loop_contexts": loop_contexts}

    def _assembly_loop_contexts(self, unit: FunctionUnit, assembly_ranges: dict[int, Range]) -> dict[int, list[dict[str, Any]]]:
        contexts: dict[int, list[dict[str, Any]]] = {ab.block_id: [] for ab in unit.assembly_blocks}
        loops: list[dict[str, Any]] = []

        def visit(node: Any) -> None:
            if isinstance(node, dict):
                node_type = node.get("nodeType")
                if node_type in {"ForStatement", "WhileStatement", "DoWhileStatement"}:
                    rng = Range(*parse_src(str(node.get("src", ""))))
                    condition = self._solidity_condition_text(node)
                    loops.append({
                        "loop_id": f"sol_loop_{len(loops) + 1}",
                        "kind": node_type,
                        "src": str(node.get("src", "")),
                        "range": {"start": rng.start, "end": rng.end},
                        "condition": condition,
                        "source": self._src_excerpt(rng),
                    })
                for child in node.values():
                    visit(child)
            elif isinstance(node, list):
                for item in node:
                    visit(item)

        visit(unit.ast_node.get("body") or {})
        for block_id, asm_range in assembly_ranges.items():
            for loop in loops:
                rng_data = loop.get("range", {})
                loop_range = Range(int(rng_data.get("start", 0)), int(rng_data.get("end", 0)))
                if loop_range.contains(asm_range) or loop_range.overlaps(asm_range):
                    contexts.setdefault(block_id, []).append(loop)
        return contexts

    def _solidity_condition_text(self, node: dict[str, Any]) -> str | None:
        condition = node.get("condition") or node.get("conditionExpression")
        if isinstance(condition, dict):
            return self._src_excerpt(Range(*parse_src(str(condition.get("src", ""))))) or condition.get("nodeType")
        return None

    def _src_excerpt(self, rng: Range) -> str:
        if not self._source_text or not rng.valid:
            return ""
        return " ".join(self._source_text[rng.start:rng.end].strip().split())

    def _slither_assembly_ranges(self, function: Any, unit: FunctionUnit) -> dict[int, Range]:
        """Prefer Slither's assembly anchor ranges, then fall back to solc InlineAssembly src.

        solc's InlineAssembly src may point at the Yul payload rather than the whole
        wrapper in some compiler versions. Slither's ASSEMBLY/ENDASSEMBLY nodes give
        the function-level CFG boundary we need for replacing the whole assembly
        region with the local Yul CFG subgraph.
        """
        fallback = {ab.block_id: Range(*ab.source_range) for ab in unit.assembly_blocks}
        anchors = []
        for node in getattr(function, "nodes", []):
            node_type = str(getattr(node, "type", ""))
            if node_type.endswith("ASSEMBLY") and not node_type.endswith("ENDASSEMBLY"):
                rng = self._range_from_slither_node(node)
                if rng.valid:
                    anchors.append((rng.start, rng, int(node.node_id)))
        anchors.sort()
        if not anchors:
            return fallback
        out = dict(fallback)
        for ab, (_start, rng, _node_id) in zip(unit.assembly_blocks, anchors):
            out[ab.block_id] = rng
        return out

    def _find_slither_function(self, unit: FunctionUnit) -> Any | None:
        self._last_function_match_method = None
        slither = self._load_slither()
        if slither is None:
            return None
        candidates = []
        for contract in getattr(slither, "contracts", []):
            if getattr(contract, "name", None) != unit.contract:
                continue
            for fn in getattr(contract, "functions_and_modifiers_declared", []):
                if getattr(fn, "name", None) == unit.function:
                    candidates.append(fn)
        unit_range = Range(*parse_src(str(unit.ast_node.get("src", ""))))
        source_matches = [
            fn for fn in candidates
            if self.function_source_range(fn).valid
            and unit_range.valid
            and self.function_source_range(fn).start == unit_range.start
        ]
        if len(source_matches) == 1:
            self._last_function_match_method = "source_range"
            return source_matches[0]
        canonical_signature = self.canonical_function_signature(unit.signature)
        signature_matches = [
            fn for fn in candidates
            if self.canonical_function_signature(str(getattr(fn, "full_name", ""))) == canonical_signature
        ]
        if len(signature_matches) == 1:
            self._last_function_match_method = "canonical_signature"
            return signature_matches[0]
        if len(candidates) == 1:
            self._last_function_match_method = "unique_name"
            return candidates[0]
        return None

    def _slither_function_declaration(self, function: Any) -> dict[str, Any]:
        """Archive Slither's stable Function/Modifier declaration API.

        This is declaration metadata, not a CFG operation.  In particular a
        modifier application remains an explicit semantic node: Slither models
        its body in a separate Modifier CFG around a PLACEHOLDER node.
        """
        declaration_kind = "modifier" if type(function).__name__ == "Modifier" else "function"
        modifiers = [
            self._serialize_slithir_callable(modifier)
            for modifier in (getattr(function, "modifiers", []) or [])
        ]
        # ``Function.overrides``/``overridden_by`` are Slither's resolved
        # declaration relations.  Keep them as declaration facts instead of
        # turning an override into a synthetic CFG edge: dispatch still has
        # the source call boundary and may be dynamic.
        def callables(values: list[Any]) -> list[dict[str, Any]]:
            unique: dict[str, dict[str, Any]] = {}
            for value in values:
                item = self._serialize_slithir_callable(value)
                key = str(item.get("canonical_name") or item.get("full_name") or item.get("name"))
                unique.setdefault(key, item)
            return [unique[key] for key in sorted(unique)]

        constructor_statement_targets = []
        for statement in (getattr(function, "explicit_base_constructor_calls_statements", []) or []):
            target = self._serialize_slithir_callable(getattr(statement, "modifier", None))
            constructor_statement_targets.append({
                "constructor": target,
                # Slither's ModifierStatements owns the insertion nodes; the
                # actual argument-bearing InternalCall stays in the normal
                # SlithIR archive and is lifted once as an InternalCall.
                "node_ids": [int(getattr(node, "node_id", -1)) for node in (getattr(statement, "nodes", []) or [])],
            })
        return {
            "declaration_kind": declaration_kind,
            "name": str(getattr(function, "name", "") or ""),
            "full_name": str(getattr(function, "full_name", "") or ""),
            "canonical_name": str(getattr(function, "canonical_name", "") or ""),
            "visibility": str(getattr(function, "visibility", "") or ""),
            "view": bool(getattr(function, "view", False)),
            "pure": bool(getattr(function, "pure", False)),
            "payable": bool(getattr(function, "payable", False)),
            "implemented": bool(getattr(function, "is_implemented", False)),
            "function_type": self._enum_text(getattr(function, "function_type", "")),
            "is_constructor": bool(getattr(function, "is_constructor", False)),
            "is_fallback": bool(getattr(function, "is_fallback", False)),
            "is_receive": bool(getattr(function, "is_receive", False)),
            "is_virtual": bool(getattr(function, "is_virtual", False)),
            "is_override": bool(getattr(function, "is_override", False)),
            "parameters": [
                self._serialize_slithir_value(value)
                for value in (getattr(function, "parameters", []) or [])
            ],
            "returns": [
                self._serialize_slithir_value(value)
                for value in (getattr(function, "returns", []) or [])
            ],
            "applied_modifiers": modifiers,
            "overrides": callables(getattr(function, "overrides", []) or []),
            "overridden_by": callables(getattr(function, "overridden_by", []) or []),
            "explicit_base_constructor_calls": callables(
                getattr(function, "explicit_base_constructor_calls", []) or []
            ),
            "explicit_base_constructor_call_statements": constructor_statement_targets,
        }

    def _slither_contract_declaration(self, contract: Any) -> dict[str, Any] | None:
        """Archive Slither's contract inheritance/constructor API verbatim.

        ``inheritance`` is Slither's linearized order whose first element is
        the first parent to execute.  ``immediate_inheritance`` preserves the
        source direct-parent order.  A base-constructor declaration relation
        is not a normal message-call CFG edge, so it is emitted separately and
        linked by the final SFIR program builder.
        """
        if contract is None:
            return None

        def contracts(values: list[Any]) -> list[dict[str, Any]]:
            return [
                {
                    "name": str(getattr(value, "name", "") or ""),
                }
                for value in values
            ]

        return {
            "name": str(getattr(contract, "name", "") or ""),
            "kind": str(getattr(contract, "kind", "") or ""),
            "immediate_inheritance": contracts(getattr(contract, "immediate_inheritance", []) or []),
            "inheritance": contracts(getattr(contract, "inheritance", []) or []),
            "inheritance_reverse": contracts(getattr(contract, "inheritance_reverse", []) or []),
            "explicit_base_constructor_calls": [
                self._serialize_slithir_callable(value)
                for value in (getattr(contract, "explicit_base_constructor_calls", []) or [])
            ],
        }

    def _load_slither(self) -> Any | None:
        if self._slither_cache is not None:
            return self._slither_cache
        if self._slither_attempted and self._slither_error:
            return None
        self._slither_attempted = True
        try:
            from slither.slither import Slither  # type: ignore

            kwargs: dict[str, Any] = {}
            if self.solc_bin:
                kwargs["solc"] = self.solc_bin
            try:
                self._slither_cache = self._load_slither_attempt(Slither, kwargs)
                self._slither_compile_mode = "normal"
            except Exception as primary_exc:
                if not self.can_retry_slither_via_ir(primary_exc):
                    raise
                self._slither_primary_failure = "stack_too_deep"
                retry_kwargs = dict(kwargs)
                retry_kwargs["solc_args"] = "--via-ir --optimize"
                self._slither_cache = self._load_slither_attempt(Slither, retry_kwargs)
                self._slither_compile_mode = "via_ir_retry"
            return self._slither_cache
        except Exception as exc:  # pragma: no cover - depends on local toolchain
            self._slither_error = f"{type(exc).__name__}: {exc}"
            return None

    def _load_slither_attempt(self, slither_class: Any, kwargs: dict[str, Any]) -> Any:
        timeout = self.slither_timeout_seconds()
        if timeout <= 0 or not hasattr(signal, "SIGALRM"):
            return slither_class(str(self.source_path), **kwargs)
        previous_handler = signal.getsignal(signal.SIGALRM)

        def timeout_handler(_signum: int, _frame: Any) -> None:
            raise SlitherLoadTimeout(f"Slither load exceeded {timeout}s")

        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(timeout)
        try:
            return slither_class(str(self.source_path), **kwargs)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous_handler)

    def can_retry_slither_via_ir(self, exc: Exception) -> bool:
        if "stack too deep" not in str(exc).lower() or not self.solc_bin:
            return False
        try:
            return solc_supports_option(self.solc_bin, "--via-ir")
        except Exception:
            return False

    @staticmethod
    def function_source_range(function: Any) -> Range:
        mapping = getattr(function, "source_mapping", None)
        start = getattr(mapping, "start", None)
        length = getattr(mapping, "length", None)
        if isinstance(start, int) and isinstance(length, int):
            return Range(start, start + length)
        return Range(0, 0)

    @staticmethod
    def canonical_function_signature(signature: str) -> str:
        text = re.sub(r"\b(?:struct|contract|enum)\s+", "", str(signature or ""))
        return re.sub(r"\s+", "", text)

    @staticmethod
    def slither_timeout_seconds() -> int:
        raw = os.environ.get("SSEIR_SLITHER_TIMEOUT", "45")
        try:
            return max(0, int(raw))
        except ValueError:
            return 45

    def _range_from_slither_node(self, node: Any) -> Range:
        sm = getattr(node, "source_mapping", None)
        start = getattr(sm, "start", None)
        length = getattr(sm, "length", None)
        if isinstance(start, int) and isinstance(length, int):
            return Range(self._normalized_source_offset(start), self._normalized_source_offset(start + length))
        return Range(0, 0)

    def _normalized_source_offset(self, raw_offset: int) -> int:
        """Translate Slither's raw-file byte offsets to solc standard-json offsets.

        The standard-json compiler input is produced from ``read_text`` and therefore
        has normalized newlines, while Slither may map the original CRLF file.
        """
        if not self._source_bytes:
            return raw_offset
        prefix = self._source_bytes[:max(0, raw_offset)]
        return len(prefix.replace(b"\r\n", b"\n").replace(b"\r", b"\n"))

    @staticmethod
    def _range_from_stmt(stmt: SourceStatement) -> Range:
        return Range(*parse_src(stmt.src))

    def _assembly_block_for_slither_node(self, node: Any, assembly_ranges: dict[int, Range]) -> int | None:
        node_type = str(getattr(node, "type", ""))
        rng = self._range_from_slither_node(node)
        if not rng.valid:
            return None
        for block_id, asm_range in assembly_ranges.items():
            if node_type.endswith("ASSEMBLY") or node_type.endswith("ENDASSEMBLY"):
                if asm_range.overlaps(rng):
                    return block_id
            elif asm_range.contains(rng):
                return block_id
        return None

    def _representative_for_slither_node(
        self,
        node_id: int,
        slither_block_ids: dict[int, str],
        asm_for_node: dict[int, int],
        asm_entry_ids: dict[int, str],
        asm_exit_ids: dict[int, str],
        prefer_exit: bool,
    ) -> str | None:
        if node_id in slither_block_ids:
            return slither_block_ids[node_id]
        block_id = asm_for_node.get(node_id)
        if block_id is None:
            return None
        return asm_exit_ids.get(block_id) if prefer_exit else asm_entry_ids.get(block_id)

    def _slither_block(
        self,
        block_id: str,
        node: Any,
        unit: FunctionUnit,
        try_contexts: dict[int, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        text = self._slither_node_text(node)
        return {
            "block_id": block_id,
            "kind": "solidity",
            "stmts": self._solidity_refs(unit, node),
            "terminator": self._slither_terminator(node, text, try_contexts),
            "attrs": {
                "slither_node_id": int(node.node_id),
                "slither_node_type": str(getattr(node, "type", "")),
                "src": self._src_string(self._range_from_slither_node(node)),
                "text": text,
                "slithir": self._serialize_slithir(getattr(node, "irs", []) or []),
                "slithir_ssa": self._serialize_slithir(getattr(node, "irs_ssa", []) or [], ssa=True),
                "state_variables_read": [
                    self._serialize_slithir_value(value)
                    for value in (getattr(node, "state_variables_read", []) or [])
                ],
                "state_variables_written": [
                    self._serialize_slithir_value(value)
                    for value in (getattr(node, "state_variables_written", []) or [])
                ],
                "variables_read": [
                    self._serialize_slithir_value(value)
                    for value in (getattr(node, "variables_read", []) or [])
                ],
                "variables_written": [
                    self._serialize_slithir_value(value)
                    for value in (getattr(node, "variables_written", []) or [])
                ],
                "slither_dominators": sorted(int(value.node_id) for value in (getattr(node, "dominators", set()) or set())),
                "slither_immediate_dominator": int(node.immediate_dominator.node_id) if getattr(node, "immediate_dominator", None) is not None else None,
                "slither_dominance_frontier": sorted(int(value.node_id) for value in (getattr(node, "dominance_frontier", set()) or set())),
                "slither_is_reachable": bool(getattr(node, "is_reachable", True)),
                "slither_calls": {
                    "internal": [str(value) for value in (getattr(node, "internal_calls", []) or [])],
                    "high_level": [str(value) for value in (getattr(node, "high_level_calls", []) or [])],
                    "low_level": [str(value) for value in (getattr(node, "low_level_calls", []) or [])],
                },
            },
        }

    def _serialize_slithir(self, operations: list[Any], ssa: bool = False) -> list[dict[str, Any]]:
        return [self._serialize_slithir_operation(op, index, ssa) for index, op in enumerate(operations)]

    def _serialize_slithir_operation(self, op: Any, order: int, ssa: bool) -> dict[str, Any]:
        operation_kind = type(op).__name__
        item: dict[str, Any] = {
            "order": order,
            "kind": operation_kind,
            "text": str(op),
            "ssa": ssa,
        }
        expression = getattr(op, "expression", None)
        if expression is not None:
            item["source_expression"] = str(expression)
        for name in (
            "lvalue",
            "rvalue",
            "variable",
            "variable_left",
            "variable_right",
            "destination",
            "call_value",
            "call_gas",
            "call_salt",
            "contract_name",
            "array_type",
            "structure",
            "structure_name",
            "tuple",
        ):
            value = getattr(op, name, None)
            if value is not None:
                item[name] = self._serialize_slithir_value(value)
        for name in ("read", "arguments", "values", "init_values"):
            values = getattr(op, name, None)
            if values is not None:
                item[name] = [self._serialize_slithir_value(value) for value in values]
        operation_type = getattr(op, "type", None)
        if operation_type is not None:
            item["operator"] = self._enum_text(operation_type)
        # InternalDynamicCall is intentionally not tied to a Function object:
        # Slither exposes the declared callable shape separately as
        # ``function_type``.  Preserve it verbatim instead of guessing a
        # concrete callee from the current SSA inputs.
        function_type = getattr(op, "function_type", None)
        if function_type is not None:
            item["function_type"] = str(function_type)
        node = getattr(op, "node", None)
        scope = getattr(node, "scope", None) if node is not None else None
        if scope is not None and hasattr(scope, "is_checked"):
            item["checked"] = bool(scope.is_checked)
        operation_name = getattr(op, "name", None)
        if operation_name is not None:
            item["name"] = str(operation_name)
        argument_names = getattr(op, "names", None)
        if isinstance(argument_names, (list, tuple)) and all(
            isinstance(value, str) for value in argument_names
        ):
            item["argument_names"] = list(argument_names)
        # These are semantic call/construction attributes, rather than
        # presentation-only details.  Preserve them while the SlithIR object
        # is still available: the atomic/SFIR stages must not parse ``text``
        # to recover a call kind, named arguments, or creation salt.
        for name in (
            "type_call",
            "nbr_arguments",
            "call_id",
            "is_modifier_call",
            "index",
        ):
            value = getattr(op, name, None)
            if isinstance(value, (str, int, float, bool)):
                item[name] = self._enum_text(value) if name == "type_call" else value
        function = getattr(op, "function", None)
        if function is not None:
            item["function"] = self._serialize_slithir_callable(function)
        function_name = getattr(op, "function_name", None)
        if function_name is not None:
            item["function_name"] = str(function_name)
        if operation_kind in {"Phi", "PhiCallback"}:
            item["phi_origin_nodes"] = sorted(
                (
                    self._serialize_phi_origin_node(origin)
                    for origin in (getattr(op, "nodes", set()) or set())
                ),
                key=lambda origin: (
                    str(origin.get("function") or ""),
                    int(origin["node_id"]) if origin.get("node_id") is not None else -1,
                ),
            )
            item["phi_callback"] = operation_kind == "PhiCallback"
            callback_ir = getattr(op, "callee_ir", None) if operation_kind == "PhiCallback" else None
            if callback_ir is not None:
                callback_function = getattr(callback_ir, "function", None)
                item["phi_callback_call"] = {
                    "kind": type(callback_ir).__name__,
                    "text": str(callback_ir),
                    "function": (
                        self._serialize_slithir_callable(callback_function)
                        if callback_function is not None
                        else None
                    ),
                }
        return item

    @staticmethod
    def _serialize_phi_origin_node(node: Any) -> dict[str, Any]:
        function = getattr(node, "function", None)
        return {
            "node_id": int(getattr(node, "node_id", -1)),
            "node_type": str(getattr(node, "type", "")),
            "function": str(getattr(function, "canonical_name", function) or ""),
        }

    @staticmethod
    def _enum_text(value: Any) -> str:
        enum_value = getattr(value, "value", None)
        if isinstance(enum_value, str):
            return enum_value
        name = getattr(value, "name", None)
        return str(name if name is not None else value)

    @staticmethod
    def _serialize_slithir_callable(value: Any) -> dict[str, Any]:
        contract = getattr(value, "contract", None) or getattr(value, "contract_declarer", None)
        return {
            "kind": type(value).__name__,
            "text": str(value),
            "name": str(getattr(value, "name", value)),
            "full_name": str(getattr(value, "full_name", getattr(value, "name", value))),
            "canonical_name": str(getattr(value, "canonical_name", "") or ""),
            "contract": str(getattr(contract, "name", "") or ""),
            "type": str(getattr(value, "type", "") or ""),
            "is_constructor": bool(getattr(value, "is_constructor", False)),
            "is_fallback": bool(getattr(value, "is_fallback", False)),
            "is_receive": bool(getattr(value, "is_receive", False)),
        }

    @classmethod
    def _serialize_slithir_value(
        cls,
        value: Any,
        *,
        depth: int = 0,
        seen: set[int] | None = None,
    ) -> dict[str, Any]:
        """Archive Slither variables without flattening their alias evidence.

        SlithIR's printable SSA form happens to include some pointer
        information (for example ``REF_5(-> sender_2 (-> ['accounts']))``),
        but that representation is not a stable semantic interface.  The
        structured ``points_to`` and ``refers_to`` relations are the actual
        API.  They are deliberately bounded here: an archive is evidence for
        SFIR lifting, not a recursive dump of Slither's in-memory graph.
        """
        if seen is None:
            seen = set()
        kind = type(value).__name__
        non_ssa = getattr(value, "non_ssa_version", None)
        base_name = str(non_ssa) if non_ssa is not None else str(getattr(value, "name", value))
        text = str(value)
        value_type = getattr(value, "type", None)
        item = {
            "kind": kind,
            "text": text,
            "name": str(getattr(value, "name", text)),
            "base_name": base_name,
            "type": str(value_type) if value_type is not None else None,
            "is_state": kind in {"StateIRVariable", "StateVariable"},
            "is_reference": "ReferenceVariable" in kind,
            "is_constant": kind == "Constant",
            "is_solidity_builtin": kind in {"SolidityVariable", "SolidityVariableComposed"},
        }
        # A function value used by an InternalDynamicCall is not an ordinary
        # identifier: Slither exposes its resolved declaration on the value
        # itself. Preserve that proof so the final program linker need not
        # infer a callee from a spelling such as ``addOne``.
        canonical_name = getattr(value, "canonical_name", None)
        if canonical_name:
            item["canonical_name"] = str(canonical_name)
            item["full_name"] = str(getattr(value, "full_name", "") or "")
            contract = getattr(value, "contract", None) or getattr(value, "contract_declarer", None)
            if contract is not None:
                item["contract"] = str(getattr(contract, "name", "") or "")
        index = getattr(value, "index", None)
        if isinstance(index, (str, int, float, bool)):
            item["ssa_index"] = index
        location = getattr(value, "location", None)
        if location is not None:
            item["data_location"] = str(location)
        if hasattr(value, "is_storage"):
            item["is_storage"] = bool(getattr(value, "is_storage"))

        # A cycle is unusual for SlithIR variables, but may be constructed by
        # an analysis extension.  Retain the identity above and stop rather
        # than turn a diagnostic archive into an unbounded traversal.
        marker = id(value)
        if depth >= 4 or marker in seen:
            return item
        nested_seen = set(seen)
        nested_seen.add(marker)

        points_to = getattr(value, "points_to", None)
        if points_to is not None:
            item["points_to"] = cls._serialize_slithir_value(
                points_to, depth=depth + 1, seen=nested_seen
            )
        refers_to = getattr(value, "refers_to", None)
        if refers_to:
            item["refers_to"] = [
                cls._serialize_slithir_value(target, depth=depth + 1, seen=nested_seen)
                for target in sorted(refers_to, key=str)
            ]
        return item

    @classmethod
    def _unified_graph_analysis(cls, blocks: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
        block_by_id = {str(block["block_id"]): block for block in blocks}
        node_ids = list(block_by_id)
        predecessors: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
        successors: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
        edge_kinds: dict[tuple[str, str], list[str]] = {}
        for edge in edges:
            source, target = str(edge.get("from") or ""), str(edge.get("to") or "")
            if source not in block_by_id or target not in block_by_id:
                continue
            successors[source].add(target)
            predecessors[target].add(source)
            edge_kinds.setdefault((source, target), []).append(str(edge.get("kind") or "fallthrough"))

        roots = [node_id for node_id in node_ids if not predecessors[node_id]]
        exits = [node_id for node_id in node_ids if not successors[node_id]]
        dominators = cls._fixed_point_dominators(node_ids, predecessors, roots)
        postdominators = cls._fixed_point_dominators(node_ids, successors, exits)
        immediate_dominator = cls._immediate_relation(dominators, roots)
        immediate_postdominator = cls._immediate_relation(postdominators, exits)
        dominance_frontier = cls._dominance_frontier(node_ids, predecessors, immediate_dominator)
        control_dependencies = cls._control_dependencies(
            block_by_id,
            successors,
            edge_kinds,
            immediate_postdominator,
        )
        typed_def_use = cls._typed_def_use(blocks, predecessors)

        dependencies_by_block: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in node_ids}
        for dependency in control_dependencies:
            dependencies_by_block[dependency["dependent"]].append(dependency)
        dependency_closure = {
            node_id: cls._dependency_closure(node_id, dependencies_by_block)
            for node_id in node_ids
        }
        for node_id, block in block_by_id.items():
            attrs = block.setdefault("attrs", {})
            attrs["unified_dominators"] = sorted(dominators.get(node_id, set()))
            attrs["unified_immediate_dominator"] = immediate_dominator.get(node_id)
            attrs["unified_postdominators"] = sorted(postdominators.get(node_id, set()))
            attrs["unified_immediate_postdominator"] = immediate_postdominator.get(node_id)
            attrs["unified_dominance_frontier"] = sorted(dominance_frontier.get(node_id, set()))
            attrs["control_dependencies"] = dependencies_by_block.get(node_id, [])
            attrs["control_dependency_closure"] = dependency_closure.get(node_id, [])
            attrs["typed_def_use"] = typed_def_use["blocks"].get(node_id, {})

        return {
            "dominance": {
                "roots": roots,
                "exits": exits,
                "dominators": {node_id: sorted(values) for node_id, values in dominators.items()},
                "immediate_dominator": immediate_dominator,
                "dominance_frontier": {node_id: sorted(values) for node_id, values in dominance_frontier.items()},
                "postdominators": {node_id: sorted(values) for node_id, values in postdominators.items()},
                "immediate_postdominator": immediate_postdominator,
            },
            "control_dependencies": control_dependencies,
            "control_dependency_closure": dependency_closure,
            "typed_def_use": typed_def_use,
        }

    @staticmethod
    def _dependency_closure(
        node_id: str,
        dependencies_by_block: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen_controllers: set[str] = set()

        def visit(dependent: str) -> None:
            for dependency in dependencies_by_block.get(dependent, []):
                controller = str(dependency.get("controller") or "")
                if not controller or controller in seen_controllers:
                    continue
                seen_controllers.add(controller)
                visit(controller)
                out.append(dependency)

        visit(node_id)
        return out

    @staticmethod
    def _fixed_point_dominators(
        node_ids: list[str],
        incoming: dict[str, set[str]],
        roots: list[str],
    ) -> dict[str, set[str]]:
        universe = set(node_ids)
        root_set = set(roots)
        result = {node_id: ({node_id} if node_id in root_set else set(universe)) for node_id in node_ids}
        changed = True
        while changed:
            changed = False
            for node_id in node_ids:
                if node_id in root_set:
                    continue
                parents = incoming.get(node_id, set())
                merged = set.intersection(*(result[parent] for parent in parents)) if parents else set()
                updated = {node_id, *merged}
                if updated != result[node_id]:
                    result[node_id] = updated
                    changed = True
        return result

    @staticmethod
    def _immediate_relation(relations: dict[str, set[str]], roots: list[str]) -> dict[str, str | None]:
        root_set = set(roots)
        result: dict[str, str | None] = {}
        for node_id, values in relations.items():
            if node_id in root_set:
                result[node_id] = None
                continue
            strict = [value for value in values if value != node_id]
            result[node_id] = max(strict, key=lambda value: len(relations.get(value, set())), default=None)
        return result

    @staticmethod
    def _dominance_frontier(
        node_ids: list[str],
        predecessors: dict[str, set[str]],
        immediate_dominator: dict[str, str | None],
    ) -> dict[str, set[str]]:
        frontier: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
        for node_id in node_ids:
            parents = predecessors.get(node_id, set())
            if len(parents) < 2:
                continue
            for parent in parents:
                runner: str | None = parent
                seen: set[str] = set()
                while runner is not None and runner != immediate_dominator.get(node_id) and runner not in seen:
                    seen.add(runner)
                    frontier[runner].add(node_id)
                    runner = immediate_dominator.get(runner)
        return frontier

    @classmethod
    def _control_dependencies(
        cls,
        block_by_id: dict[str, dict[str, Any]],
        successors: dict[str, set[str]],
        edge_kinds: dict[tuple[str, str], list[str]],
        immediate_postdominator: dict[str, str | None],
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen_records: set[tuple[str, str, str]] = set()
        for controller, targets in successors.items():
            terminator = block_by_id[controller].get("terminator") or {}
            condition = str(terminator.get("condition") or "").strip()
            if len(targets) < 2 or not condition:
                continue
            stop = immediate_postdominator.get(controller)
            for target in targets:
                for edge_kind in edge_kinds.get((controller, target), ["fallthrough"]):
                    predicate = cls._edge_predicate(condition, edge_kind)
                    runner: str | None = target
                    visited: set[str] = set()
                    while runner is not None and runner != stop and runner not in visited:
                        visited.add(runner)
                        key = (controller, runner, predicate)
                        if key not in seen_records:
                            seen_records.add(key)
                            out.append({
                                "controller": controller,
                                "dependent": runner,
                                "edge_kind": edge_kind,
                                "condition": condition,
                                "predicate": predicate,
                            })
                        runner = immediate_postdominator.get(runner)
        return out

    @staticmethod
    def _edge_predicate(condition: str, edge_kind: str) -> str:
        if edge_kind in {"false", "exit", "zero"} or edge_kind.startswith("false:"):
            return f"!({condition})"
        if edge_kind.startswith("case:"):
            return f"({condition}) == {edge_kind.split(':', 1)[1]}"
        return condition

    @classmethod
    def _typed_def_use(cls, blocks: list[dict[str, Any]], predecessors: dict[str, set[str]]) -> dict[str, Any]:
        block_records: dict[str, dict[str, Any]] = {}
        generated: dict[str, dict[str, set[str]]] = {}
        for block in blocks:
            block_id = str(block["block_id"])
            definitions: list[dict[str, Any]] = []
            uses: list[dict[str, Any]] = []
            attrs = block.get("attrs") or {}
            for operation in attrs.get("slithir_ssa") or []:
                lvalue = operation.get("lvalue")
                if isinstance(lvalue, dict) and lvalue.get("text"):
                    definitions.append(cls._def_use_value(lvalue, operation.get("order"), operation.get("kind")))
                for value in operation.get("read") or []:
                    if isinstance(value, dict) and value.get("text"):
                        uses.append(cls._def_use_value(value, operation.get("order"), operation.get("kind")))
            definitions = cls._dedupe_records(definitions, ("name", "version", "operation_order"))
            uses = cls._dedupe_records(uses, ("name", "version", "operation_order"))
            block_records[block_id] = {"definitions": definitions, "uses": uses}
            generated[block_id] = {}
            for definition in definitions:
                generated[block_id].setdefault(definition["name"], set()).add(definition["version"])

        reaching_in: dict[str, dict[str, set[str]]] = {block_id: {} for block_id in block_records}
        reaching_out: dict[str, dict[str, set[str]]] = {block_id: {} for block_id in block_records}
        changed = True
        while changed:
            changed = False
            for block_id in block_records:
                incoming: dict[str, set[str]] = {}
                for parent in predecessors.get(block_id, set()):
                    for name, versions in reaching_out.get(parent, {}).items():
                        incoming.setdefault(name, set()).update(versions)
                outgoing = {name: set(versions) for name, versions in incoming.items()}
                for name, versions in generated.get(block_id, {}).items():
                    outgoing[name] = set(versions)
                if incoming != reaching_in[block_id] or outgoing != reaching_out[block_id]:
                    reaching_in[block_id] = incoming
                    reaching_out[block_id] = outgoing
                    changed = True

        for block_id, record in block_records.items():
            record["reaching_definitions_in"] = {
                name: sorted(versions) for name, versions in reaching_in[block_id].items()
            }
            record["reaching_definitions_out"] = {
                name: sorted(versions) for name, versions in reaching_out[block_id].items()
            }
        return {"blocks": block_records}

    @staticmethod
    def _def_use_value(value: dict[str, Any], operation_order: Any, operation_kind: Any) -> dict[str, Any]:
        return {
            "name": str(value.get("base_name") or value.get("name") or value.get("text")),
            "version": str(value.get("text")),
            "kind": value.get("kind"),
            "type": value.get("type"),
            "is_state": bool(value.get("is_state")),
            "is_reference": bool(value.get("is_reference")),
            "operation_order": operation_order,
            "operation_kind": operation_kind,
        }

    @staticmethod
    def _dedupe_records(records: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for record in records:
            key = tuple(record.get(name) for name in keys)
            if key not in seen:
                seen.add(key)
                out.append(record)
        return out

    @classmethod
    def _assembly_boundary_contexts(
        cls,
        unit: FunctionUnit,
        blocks: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        entry_ids: dict[int, str],
        exit_ids: dict[int, str],
        graph_analysis: dict[str, Any],
    ) -> dict[int, dict[str, Any]]:
        known = {
            variable.name: variable.to_dict()
            for variable in [*unit.parameters, *unit.returns, *unit.locals, *unit.state_variables]
            if variable.name
        }
        for name, value in (getattr(unit, "constant_values", {}) or {}).items():
            known[name] = {"name": name, "kind": "constant", "type_string": value.get("type_string")}
        incoming: dict[str, list[str]] = {}
        outgoing: dict[str, list[str]] = {}
        for edge in edges:
            incoming.setdefault(str(edge.get("to")), []).append(str(edge.get("from")))
            outgoing.setdefault(str(edge.get("from")), []).append(str(edge.get("to")))
        dependency_closure = graph_analysis.get("control_dependency_closure") or {}
        typed_blocks = (graph_analysis.get("typed_def_use") or {}).get("blocks") or {}
        out: dict[int, dict[str, Any]] = {}
        for assembly in unit.assembly_blocks:
            reads, writes = cls._yul_external_symbols(assembly.yul_ast, known)
            entry = entry_ids[assembly.block_id]
            exit_id = exit_ids[assembly.block_id]
            reaching = (typed_blocks.get(entry) or {}).get("reaching_definitions_in") or {}
            out[assembly.block_id] = {
                "assembly_block": assembly.block_id,
                "entry_block": entry,
                "exit_block": exit_id,
                "solidity_predecessors": [node for node in incoming.get(entry, []) if node.startswith("bb_sol_")],
                "solidity_successors": [node for node in outgoing.get(exit_id, []) if node.startswith("bb_sol_")],
                "external_reads": [
                    {**known[name], "expressions": sorted(expressions), "reaching_ssa_versions": reaching.get(name, [])}
                    for name, expressions in sorted(reads.items())
                ],
                "external_writes": [
                    {**known[name], "expressions": sorted(expressions)}
                    for name, expressions in sorted(writes.items())
                ],
                "control_dependencies": [
                    dependency for dependency in dependency_closure.get(entry, [])
                ],
            }
        return out

    @classmethod
    def _yul_external_symbols(
        cls,
        root: dict[str, Any],
        known: dict[str, dict[str, Any]],
    ) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
        locals_: set[str] = set()
        reads: dict[str, set[str]] = {}
        writes: dict[str, set[str]] = {}

        def root_name(name: str) -> str:
            return name.split(".", 1)[0]

        def add_read(name: str) -> None:
            root_value = root_name(name)
            if root_value in known and root_value not in locals_:
                reads.setdefault(root_value, set()).add(name)

        def visit(value: Any, role: str = "read") -> None:
            if isinstance(value, list):
                for item in value:
                    visit(item, role)
                return
            if not isinstance(value, dict):
                return
            node_type = value.get("nodeType")
            if node_type == "YulIdentifier":
                name = str(value.get("name") or "")
                if not name:
                    return
                if role == "target":
                    root_value = root_name(name)
                    if root_value in known and root_value not in locals_:
                        writes.setdefault(root_value, set()).add(name)
                elif role != "function":
                    add_read(name)
                return
            if node_type == "YulVariableDeclaration":
                for variable in value.get("variables") or []:
                    if isinstance(variable, dict) and variable.get("name"):
                        locals_.add(str(variable["name"]))
                visit(value.get("value"), "read")
                return
            if node_type == "YulAssignment":
                visit(value.get("variableNames") or [], "target")
                visit(value.get("value"), "read")
                return
            if node_type == "YulFunctionCall":
                visit(value.get("functionName"), "function")
                visit(value.get("arguments") or [], "read")
                return
            if node_type == "YulFunctionDefinition":
                for field in ("parameters", "returnVariables"):
                    for variable in value.get(field) or []:
                        if isinstance(variable, dict) and variable.get("name"):
                            locals_.add(str(variable["name"]))
            for key, child in value.items():
                if key not in {"src", "nativeSrc", "name", "nodeType"}:
                    visit(child, role)

        visit(root)
        return reads, writes

    @staticmethod
    def _slither_node_text(node: Any) -> str:
        expr = getattr(node, "expression", None)
        if expr is not None:
            return str(expr)
        node_type = str(getattr(node, "type", ""))
        return node_type.split(".")[-1].lower() or "slither_node"

    def _solidity_refs(self, unit: FunctionUnit, node: Any) -> list[str]:
        if str(getattr(node, "type", "")).endswith("ENTRYPOINT"):
            return []
        rng = self._range_from_slither_node(node)
        if not rng.valid:
            return []
        exact: list[str] = []
        overlap: list[tuple[int, str]] = []
        for stmt in unit.source_statements:
            if stmt.lang != "solidity":
                continue
            sr = self._range_from_stmt(stmt)
            if not sr.valid:
                continue
            if sr.start == rng.start and sr.end == rng.end:
                exact.append(stmt.stmt_id)
            elif rng.overlaps(sr):
                overlap.append((abs(sr.start - rng.start) + abs(sr.end - rng.end), stmt.stmt_id))
        if exact:
            return exact
        return [stmt_id for _, stmt_id in sorted(overlap)[:3]]

    def _yul_refs(self, unit: FunctionUnit, block_id: int, text: str, src: str) -> list[str]:
        label = f"asm_block_{block_id}"
        exact = [s.stmt_id for s in unit.source_statements if s.lang == "yul" and s.block_id == label and s.src == src]
        if exact:
            return exact
        return [s.stmt_id for s in unit.source_statements if s.lang == "yul" and s.block_id == label and s.text == text]

    @staticmethod
    def _src_string(rng: Range) -> str:
        return f"{rng.start}:{rng.end - rng.start}:slither" if rng.valid else ""

    @staticmethod
    def _slither_edge_label(node: Any, son: Any, try_contexts: dict[int, dict[str, Any]] | None = None) -> str:
        if getattr(node, "son_true", None) is son:
            return "true"
        if getattr(node, "son_false", None) is son:
            return "false"
        node_type = str(getattr(node, "type", ""))
        if node_type.endswith("TRY"):
            context = (try_contexts or {}).get(int(getattr(son, "node_id", -1))) or {}
            if context.get("try_node_id") == int(getattr(node, "node_id", -2)):
                return str(context.get("edge_kind") or "try_outcome")
        if node_type.endswith("BREAK"):
            return "break"
        if node_type.endswith("CONTINUE"):
            return "continue"
        if "ENDIF" in node_type:
            return "join"
        if "STARTLOOP" in node_type or "ENDLOOP" in node_type:
            return "loop"
        return "fallthrough"

    def _slither_terminator(
        self,
        node: Any,
        text: str,
        try_contexts: dict[int, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        node_type = str(getattr(node, "type", ""))
        context = (try_contexts or {}).get(int(getattr(node, "node_id", -1))) or {}
        if node_type.endswith("TRY"):
            return {"kind": "Try", "text": text, "try_id": context.get("try_id")}
        if node_type.endswith("CATCH"):
            return {
                "kind": "Catch",
                "catch_role": context.get("edge_kind"),
                "try_id": context.get("try_id"),
            }
        if node_type.endswith("PLACEHOLDER"):
            return {"kind": "ModifierPlaceholder"}
        if node_type.endswith("BREAK"):
            return {"kind": "Break"}
        if node_type.endswith("CONTINUE"):
            return {"kind": "Continue"}
        if node_type.endswith("IF") or node_type.endswith("IFLOOP"):
            return {"kind": "Branch", "condition": text}
        if "RETURN" in node_type:
            return {"kind": "Return", "text": text}
        if "THROW" in node_type or text.startswith("revert"):
            return {"kind": "Revert", "text": text}
        if not getattr(node, "sons", []):
            return {"kind": "Terminal", "text": text}
        return {"kind": "Fallthrough", "text": text}

    def _slither_try_contexts(self, function: Any, unit: FunctionUnit) -> dict[int, dict[str, Any]]:
        """Recover Try/Catch outcome roles from Slither + the matching solc AST.

        Slither's parser creates a TRY node and links it to one CATCH node for
        *every* ``TryCatchClause``. Clause zero is the successful ``returns``
        scope and only later clauses are catches. Its CFG edges retain no role.
        The compiler AST retains exact source spans, error names and parameter
        types, so match only exact spans and leave ambiguity unannotated.
        """
        clauses_by_range: dict[tuple[int, int], list[dict[str, Any]]] = {}
        tries_by_range: dict[tuple[int, int], dict[str, Any]] = {}

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                if value.get("nodeType") == "TryStatement":
                    try_range = Range(*parse_src(str(value.get("src", ""))))
                    clauses = [item for item in value.get("clauses") or [] if isinstance(item, dict)]
                    if try_range.valid and clauses:
                        try_id = f"try:{try_range.start}:{try_range.end}"
                        tries_by_range[(try_range.start, try_range.end)] = {"try_id": try_id}
                        for index, clause in enumerate(clauses):
                            clause_range = Range(*parse_src(str(clause.get("src", ""))))
                            if clause_range.valid:
                                clauses_by_range.setdefault((clause_range.start, clause_range.end), []).append({
                                    "try_id": try_id,
                                    "edge_kind": self._try_clause_edge_kind(clause, index),
                                })
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(unit.ast_node.get("body") or {})
        if not clauses_by_range:
            return {}

        contexts: dict[int, dict[str, Any]] = {}
        for node in getattr(function, "nodes", []) or []:
            node_id = int(getattr(node, "node_id", -1))
            node_type = str(getattr(node, "type", ""))
            node_range = self._range_from_slither_node(node)
            key = (node_range.start, node_range.end)
            if node_type.endswith("TRY"):
                record = tries_by_range.get(key)
                if record:
                    contexts[node_id] = {"try_id": record["try_id"]}
            elif node_type.endswith("CATCH"):
                matches = clauses_by_range.get(key) or []
                if len(matches) == 1:
                    contexts[node_id] = dict(matches[0])

        for node in getattr(function, "nodes", []) or []:
            node_id = int(getattr(node, "node_id", -1))
            context = contexts.get(node_id)
            if not context or not str(getattr(node, "type", "")).endswith("TRY"):
                continue
            for son in getattr(node, "sons", []) or []:
                son_context = contexts.get(int(getattr(son, "node_id", -1)))
                if son_context and son_context.get("try_id") == context.get("try_id"):
                    son_context["try_node_id"] = node_id
        return contexts

    @staticmethod
    def _try_clause_edge_kind(clause: dict[str, Any], index: int) -> str:
        if index == 0:
            return "try_success"
        error_name = str(clause.get("errorName") or "").strip()
        if error_name:
            return f"catch:{error_name}"
        parameters = ((clause.get("parameters") or {}).get("parameters") or [])
        parameter_types = [
            str((item.get("typeDescriptions") or {}).get("typeString") or "").strip()
            for item in parameters if isinstance(item, dict)
        ]
        parameter_types = [item for item in parameter_types if item]
        return f"catch:{','.join(parameter_types)}" if parameter_types else "catch"

    @staticmethod
    def _yul_terminator(node: Any) -> dict[str, Any]:
        if node.kind in {"condition", "switch", "loop-condition"}:
            text = str(node.text or "").strip()
            condition = text[3:].strip() if text.startswith("if ") else text
            return {"kind": "Branch", "text": node.text, "condition": condition, "node_kind": node.kind}
        if node.kind in {"terminal", "leave"} or node.text.startswith(("revert", "return", "stop", "invalid", "selfdestruct")):
            if node.text.startswith("revert"):
                return {"kind": "Revert", "text": node.text, "node_kind": node.kind}
            if node.text.startswith("return"):
                return {"kind": "Return", "text": node.text, "node_kind": node.kind}
            return {"kind": "Stop", "text": node.text, "node_kind": node.kind}
        return {"kind": "YulNode", "text": node.text, "node_kind": node.kind}

    @staticmethod
    def _solidity_terminator(text: str) -> dict[str, Any]:
        t = text.strip()
        if t.startswith("if"):
            return {"kind": "Branch", "condition": t}
        if t.startswith("return"):
            return {"kind": "Return"}
        if t.startswith("revert"):
            return {"kind": "Revert"}
        return {"kind": "Fallthrough"}
