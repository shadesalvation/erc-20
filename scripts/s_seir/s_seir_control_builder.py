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
            blocks.append(self._slither_block(block_id, node, unit))

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
                add_edge(source_id, target_id, self._slither_edge_label(node, son))

        notes.append(f"slither_nodes={len(function.nodes)}")
        notes.append(f"assembly_blocks={len(unit.assembly_blocks)}")
        notes.append("assembly_edge_policy=slither_predecessors_to_yul_entry_and_yul_exit_to_slither_successors")
        if any(loop_contexts.values()):
            notes.append("function_level_loop_context_attached_to_assembly_blocks")
        return {"blocks": blocks, "edges": edges, "notes": notes, "loop_contexts": loop_contexts}

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

    @staticmethod
    def _range_from_slither_node(node: Any) -> Range:
        sm = getattr(node, "source_mapping", None)
        start = getattr(sm, "start", None)
        length = getattr(sm, "length", None)
        if isinstance(start, int) and isinstance(length, int):
            return Range(start, start + length)
        return Range(0, 0)

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

    def _slither_block(self, block_id: str, node: Any, unit: FunctionUnit) -> dict[str, Any]:
        text = self._slither_node_text(node)
        return {
            "block_id": block_id,
            "kind": "solidity",
            "stmts": self._solidity_refs(unit, node),
            "terminator": self._slither_terminator(node, text),
            "attrs": {
                "slither_node_id": int(node.node_id),
                "slither_node_type": str(getattr(node, "type", "")),
                "src": self._src_string(self._range_from_slither_node(node)),
                "text": text,
            },
        }

    @staticmethod
    def _slither_node_text(node: Any) -> str:
        expr = getattr(node, "expression", None)
        if expr is not None:
            return str(expr)
        node_type = str(getattr(node, "type", ""))
        return node_type.split(".")[-1].lower() or "slither_node"

    def _solidity_refs(self, unit: FunctionUnit, node: Any) -> list[str]:
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
    def _slither_edge_label(node: Any, son: Any) -> str:
        if getattr(node, "son_true", None) is son:
            return "true"
        if getattr(node, "son_false", None) is son:
            return "false"
        node_type = str(getattr(node, "type", ""))
        if "ENDIF" in node_type:
            return "join"
        if "STARTLOOP" in node_type or "ENDLOOP" in node_type:
            return "loop"
        return "fallthrough"

    def _slither_terminator(self, node: Any, text: str) -> dict[str, Any]:
        node_type = str(getattr(node, "type", ""))
        if node_type.endswith("IF"):
            return {"kind": "Branch", "condition": text}
        if "RETURN" in node_type:
            return {"kind": "Return", "text": text}
        if "THROW" in node_type or text.startswith("revert"):
            return {"kind": "Revert", "text": text}
        if not getattr(node, "sons", []):
            return {"kind": "Terminal", "text": text}
        return {"kind": "Fallthrough", "text": text}

    @staticmethod
    def _yul_terminator(node: Any) -> dict[str, Any]:
        if node.kind in {"condition", "switch", "loop-condition"}:
            return {"kind": "Branch", "text": node.text, "node_kind": node.kind}
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
