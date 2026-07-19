#!/usr/bin/env python3
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re
from typing import Any

from s_seir_model import EffectNode, FunctionSSEIR, SemanticOverlay, SourceStatement
from s_seir_yul_normalize import normalize_expr


def write_solidity_like_text(functions: list[FunctionSSEIR], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_solidity_like_text(functions), encoding="utf-8")


def render_solidity_like_text(functions: list[FunctionSSEIR]) -> str:
    lines: list[str] = [
        "S-SEIR Solidity-like Assembly View",
        "Note: derived view only; source is not modified and output is not compiled.",
        "",
    ]
    for fn in functions:
        if not any(stmt.lang == "yul" for stmt in fn.source_statements):
            continue
        renderer = SolidityLikeRenderer(fn)
        lines.extend(renderer.render_function())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


class SolidityLikeRenderer:
    """Render a source-ordered Solidity-like view from S-SEIR overlays/effects.

    This is intentionally a projection of the S-SEIR graph, not a second
    analysis pipeline. Statement selection follows the legacy recovery priority:
    require/event/call/storage overlays first, expression normalization next,
    memory effects next, and raw Yul as the final fallback.
    """

    def __init__(self, fn: FunctionSSEIR):
        self.fn = fn
        self.stmt_by_id = {stmt.stmt_id: stmt for stmt in fn.source_statements}
        self.effects_by_ref = self._effects_by_ref(fn.effects)
        self.effect_by_id = {effect.effect_id: effect for effect in fn.effects}
        self.overlays_by_ref = self._overlays_by_anchor(fn.semantic_overlays)
        self.condition_aliases = self._condition_aliases(fn.effects)
        self.skip_branch_conditions = self._skip_branch_conditions(fn.semantic_overlays)
        self.memory_construction_notes = self._memory_construction_notes(fn.semantic_overlays)
        self.struct_field_lines_by_effect = self._struct_field_lines_by_effect(fn.semantic_overlays)
        self.loop_groups = self._loop_groups()

    def render_function(self) -> list[str]:
        lines = [f"Function {self.fn.contract}.{self.fn.signature}"]
        source_text = self.function_source_text()
        if source_text:
            lines.append("  SourceFunction")
            lines.append("  ```solidity")
            for line in source_text.rstrip().splitlines():
                lines.append(f"  {line}")
            lines.append("  ```")
            solidity_like = self.function_solidity_like_text(source_text)
            if solidity_like:
                lines.append("  SolidityLikeFunction")
                lines.append("  ```solidity")
                for line in solidity_like.rstrip().splitlines():
                    lines.append(f"  {line}")
                lines.append("  ```")
                return lines
        lines.extend(self.render_assembly_blocks())
        return lines

    def render_assembly_blocks(self) -> list[str]:
        lines: list[str] = []
        block_ids = []
        for stmt in self.fn.source_statements:
            if stmt.lang == "yul" and stmt.block_id and stmt.block_id not in block_ids:
                block_ids.append(stmt.block_id)
        for block_id in block_ids:
            lines.append(f"  AssemblyBlock {block_id}")
            lines.append("  ```solidity")
            lines.append("  assembly /* s-seir solidity-like view */ {")
            for line in self.render_assembly_block_body(block_id):
                lines.append(f"      {line}")
            lines.append("  }")
            lines.append("  ```")
        return lines

    def render_assembly_block_body(self, block_id: str) -> list[str]:
        yul_stmts = [s for s in self.fn.source_statements if s.lang == "yul" and s.block_id == block_id]
        if not yul_stmts:
            return []
        consumed: set[str] = set()
        body_lines: list[str] = list(self.memory_construction_notes)
        for stmt in yul_stmts:
            if stmt.stmt_id in consumed:
                continue
            loop_group = self.loop_groups.get(stmt.stmt_id)
            if loop_group:
                rendered = self.render_loop_group(loop_group, yul_stmts)
                consumed.update(loop_group["consumed_stmt_ids"])
                body_lines.extend(rendered)
                continue
            rendered = self.render_stmt(stmt)
            if rendered is None:
                continue
            comment_index = self.comment_line_index(rendered)
            for index, line in enumerate(rendered):
                comment = f" // yul: {stmt.text}" if index == comment_index else ""
                body_lines.append(f"{line}{comment}")
        return self.coalesce_adjacent_condition_blocks(body_lines)

    def function_solidity_like_text(self, source_text: str) -> str | None:
        block_ids = []
        for stmt in self.fn.source_statements:
            if stmt.lang == "yul" and stmt.block_id and stmt.block_id not in block_ids:
                block_ids.append(stmt.block_id)
        if not block_ids:
            return None
        ranges = self.find_assembly_ranges(source_text)
        if not ranges:
            return None
        result = source_text
        replacements: list[tuple[int, int, str]] = []
        for block_id, (start, end) in zip(block_ids, ranges):
            indent = self.line_indent(source_text, start)
            replacement_lines = ["assembly /* s-seir solidity-like view */ {"]
            replacement_lines.extend(f"{indent}    {line}" for line in self.render_assembly_block_body(block_id))
            replacement_lines.append(f"{indent}}}")
            replacements.append((start, end, "\n".join(replacement_lines)))
        for start, end, replacement in reversed(replacements):
            result = result[:start] + replacement + result[end:]
        return result

    @staticmethod
    def line_indent(text: str, index: int) -> str:
        line_start = text.rfind("\n", 0, index) + 1
        match = re.match(r"[ \t]*", text[line_start:index])
        return match.group(0) if match else ""

    @classmethod
    def find_assembly_ranges(cls, source_text: str) -> list[tuple[int, int]]:
        ranges: list[tuple[int, int]] = []
        index = 0
        while index < len(source_text):
            match = re.search(r"\bassembly\b", source_text[index:])
            if not match:
                break
            start = index + match.start()
            if not cls.in_code_context(source_text, start):
                index = start + len("assembly")
                continue
            brace = source_text.find("{", start)
            if brace < 0:
                break
            end = cls.matching_brace_end(source_text, brace)
            if end is None:
                break
            ranges.append((start, end))
            index = end
        return ranges

    @staticmethod
    def in_code_context(source_text: str, index: int) -> bool:
        state = "code"
        i = 0
        while i < index:
            ch = source_text[i]
            nxt = source_text[i + 1] if i + 1 < len(source_text) else ""
            if state == "code":
                if ch == "/" and nxt == "/":
                    state = "line_comment"
                    i += 2
                    continue
                if ch == "/" and nxt == "*":
                    state = "block_comment"
                    i += 2
                    continue
                if ch in {'"', "'"}:
                    state = f"string_{ch}"
            elif state == "line_comment":
                if ch == "\n":
                    state = "code"
            elif state == "block_comment":
                if ch == "*" and nxt == "/":
                    state = "code"
                    i += 2
                    continue
            elif state.startswith("string_"):
                quote = state[-1]
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    state = "code"
            i += 1
        return state == "code"

    @staticmethod
    def matching_brace_end(source_text: str, brace_index: int) -> int | None:
        depth = 0
        state = "code"
        i = brace_index
        while i < len(source_text):
            ch = source_text[i]
            nxt = source_text[i + 1] if i + 1 < len(source_text) else ""
            if state == "code":
                if ch == "/" and nxt == "/":
                    state = "line_comment"
                    i += 2
                    continue
                if ch == "/" and nxt == "*":
                    state = "block_comment"
                    i += 2
                    continue
                if ch in {'"', "'"}:
                    state = f"string_{ch}"
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return i + 1
            elif state == "line_comment":
                if ch == "\n":
                    state = "code"
            elif state == "block_comment":
                if ch == "*" and nxt == "/":
                    state = "code"
                    i += 2
                    continue
            elif state.startswith("string_"):
                quote = state[-1]
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    state = "code"
            i += 1
        return None

    def function_source_text(self) -> str | None:
        source = getattr(self.fn, "_sseir_function_source", None)
        if not isinstance(source, dict):
            return None
        text = str(source.get("text") or "").strip("\n")
        return text or None

    def render_loop_group(self, loop_group: dict[str, Any], yul_stmts: list[SourceStatement]) -> list[str]:
        lines: list[str] = []
        stmt_by_id = {stmt.stmt_id: stmt for stmt in yul_stmts}
        suppress = [loop_group["condition"]]

        for stmt_id in loop_group["pre_stmt_ids"]:
            stmt = stmt_by_id.get(stmt_id)
            if not stmt:
                continue
            rendered = self.render_stmt(stmt)
            if not rendered:
                continue
            lines.extend(self.attach_yul_comments(rendered, stmt))

        post_clause = self.loop_post_clause(loop_group, yul_stmts)
        if post_clause is not None:
            lines.append(f"for (; {normalize_expr(loop_group['condition'], context='condition')}; {post_clause}) {{ // yul: for")
        else:
            lines.append(f"while ({normalize_expr(loop_group['condition'], context='condition')}) {{ // yul: for")
        body_lines: list[str] = []
        for stmt_id in loop_group["body_stmt_ids"]:
            stmt = stmt_by_id.get(stmt_id)
            if not stmt:
                continue
            rendered = self.render_stmt(stmt, suppress_predicates=suppress)
            if not rendered:
                continue
            for line in self.attach_yul_comments(rendered, stmt):
                body_lines.append(f"    {line}")
        lines.extend(self.coalesce_adjacent_condition_blocks(body_lines))
        lines.append("}")
        return lines

    def loop_post_clause(self, loop_group: dict[str, Any], yul_stmts: list[SourceStatement]) -> str | None:
        stmt_by_id = {stmt.stmt_id: stmt for stmt in yul_stmts}
        clauses: list[str] = []
        for stmt_id in loop_group.get("post_stmt_ids") or []:
            stmt = stmt_by_id.get(stmt_id)
            if not stmt:
                continue
            line = self.render_stmt_unconditioned(stmt)
            if not line:
                return None
            clauses.append(line.rstrip(";"))
        return "; ".join(clauses) if clauses else ""

    def render_stmt_unconditioned(self, stmt: SourceStatement) -> str | None:
        overlays = self.overlays_by_ref.get(stmt.stmt_id, [])
        for line in self.overlay_lines(overlays):
            return line
        expression = self.expression_normalization(overlays)
        if expression:
            return expression
        memory = self.memory_write_line(stmt)
        if memory:
            return memory
        if self.branch_effect(stmt) is not None or self.is_loop_scaffold(stmt):
            return None
        return self.raw_statement_line(stmt)

    def attach_yul_comments(self, rendered: list[str], stmt: SourceStatement) -> list[str]:
        out: list[str] = []
        comment_index = self.comment_line_index(rendered)
        for index, line in enumerate(rendered):
            comment = f" // yul: {stmt.text}" if index == comment_index else ""
            out.append(f"{line}{comment}")
        return out

    def render_stmt(self, stmt: SourceStatement, suppress_predicates: list[str] | None = None) -> list[str] | None:
        overlays = self.overlays_by_ref.get(stmt.stmt_id, [])
        if any(o.kind == "RequireOverlay" and o.attrs.get("elided_by_native_precompile") for o in overlays):
            return None
        evaluation_lines = self.evaluation_step_lines(overlays)
        if evaluation_lines:
            return self.with_path_condition(stmt, overlays, evaluation_lines, suppress_predicates=suppress_predicates)
        for line in self.overlay_lines(overlays):
            return self.with_path_condition(stmt, overlays, [line], suppress_predicates=suppress_predicates)

        branch = self.branch_effect(stmt)
        if branch is not None:
            return None

        expression = self.expression_normalization(overlays)
        if expression:
            return self.with_path_condition(stmt, overlays, [expression], suppress_predicates=suppress_predicates)

        memory = self.memory_write_line(stmt)
        if memory:
            return self.with_path_condition(stmt, overlays, [memory], suppress_predicates=suppress_predicates)

        if self.is_loop_scaffold(stmt):
            return None
        return self.with_path_condition(stmt, overlays, [self.raw_statement_line(stmt)], suppress_predicates=suppress_predicates)

    @staticmethod
    def comment_line_index(lines: list[str]) -> int:
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped and stripped not in {"{", "}"} and not stripped.startswith("if "):
                return index
        return 0

    def with_path_condition(self, stmt: SourceStatement, overlays: list[SemanticOverlay], lines: list[str], suppress_predicates: list[str] | None = None) -> list[str]:
        if any(overlay.kind == "RequireOverlay" for overlay in overlays):
            return lines
        condition = self.statement_condition(stmt, overlays, suppress_predicates=suppress_predicates)
        if not condition:
            return lines
        out = [f"if ({condition}) {{"]
        out.extend(f"    {line}" for line in lines)
        out.append("}")
        return out

    @classmethod
    def coalesce_adjacent_condition_blocks(cls, lines: list[str]) -> list[str]:
        """Merge adjacent `if (same_condition)` blocks in the rendered view.

        This is a presentation-only pass. It preserves statement order and only
        merges blocks when their opening line is textually identical, including
        indentation, so nested scopes and different path conditions stay
        separate.
        """

        out: list[str] = []
        index = 0
        while index < len(lines):
            current = cls.parse_if_block(lines, index)
            if current is None:
                out.append(lines[index])
                index += 1
                continue

            opener, inner, closer, end_index = current
            index = end_index + 1
            while index < len(lines):
                next_block = cls.parse_if_block(lines, index)
                if next_block is None or next_block[0] != opener:
                    break
                inner.extend(next_block[1])
                index = next_block[3] + 1

            out.append(opener)
            out.extend(inner)
            out.append(closer)
        return out

    @staticmethod
    def parse_if_block(lines: list[str], index: int) -> tuple[str, list[str], str, int] | None:
        if index >= len(lines):
            return None
        opener = lines[index]
        if not re.match(r"^\s*if \(.+\) \{$", opener):
            return None

        depth = 0
        for cursor in range(index, len(lines)):
            depth += lines[cursor].count("{")
            depth -= lines[cursor].count("}")
            if depth == 0:
                if cursor == index:
                    return None
                return opener, list(lines[index + 1 : cursor]), lines[cursor], cursor
        return None

    def statement_condition(self, stmt: SourceStatement, overlays: list[SemanticOverlay], suppress_predicates: list[str] | None = None) -> str | None:
        states: list[str] = []
        for effect in self.effects_by_ref.get(stmt.stmt_id, []):
            for state in effect.attrs.get("path_states") or []:
                self.add_path_state(states, state)
        for overlay in overlays:
            for effect_id in overlay.effects:
                effect = self.effect_by_id.get(effect_id)
                if not effect:
                    continue
                for state in effect.attrs.get("path_states") or []:
                    self.add_path_state(states, state)
            for state in overlay.attrs.get("path_states") or []:
                self.add_path_state(states, state)
        if not states:
            return None
        states.sort(key=lambda item: len(self.path_parts(item)), reverse=True)
        if len(states) == 1:
            return self.normalize_path_state(states[0], suppress_predicates=suppress_predicates)
        normalized = [
            self.normalize_path_state(state, suppress_predicates=suppress_predicates)
            for state in states
        ]
        normalized = [item for item in normalized if item]
        if not normalized:
            return None
        if len(normalized) == 1:
            return normalized[0]
        return " || ".join(f"({item})" for item in normalized)

    @staticmethod
    def add_path_state(states: list[str], state: Any) -> None:
        text = str(state or "").strip()
        if not text or text == "entry" or text in states:
            return
        states.append(text)

    @staticmethod
    def path_parts(state: str) -> list[str]:
        return [part.strip() for part in str(state).split(" && ") if part.strip()]

    def normalize_path_state(self, state: str, suppress_predicates: list[str] | None = None) -> str:
        parts = []
        for part in self.path_parts(state):
            if self.is_suppressed_predicate(part, suppress_predicates or []):
                continue
            parts.append(self.normalize_predicate(part))
        return " && ".join(parts)

    @staticmethod
    def is_suppressed_predicate(predicate: str, suppress_predicates: list[str]) -> bool:
        text = predicate.strip()
        normalized = normalize_expr(text[2:-1], context="condition") if text.startswith("!(") and text.endswith(")") else normalize_expr(text, context="condition")
        for item in suppress_predicates:
            raw = str(item).strip()
            if text == raw:
                return True
            if normalized == normalize_expr(raw, context="condition"):
                return True
        return False

    def normalize_predicate(self, predicate: str) -> str:
        text = predicate.strip()
        if text.startswith("!(") and text.endswith(")"):
            inner = text[2:-1].strip()
            alias = self.condition_aliases.get(inner)
            if alias:
                return f"!({alias})"
            return f"!({normalize_expr(inner, context='condition')})"
        alias = self.condition_aliases.get(text)
        if alias:
            return alias
        return normalize_expr(text, context="condition")

    @staticmethod
    def is_loop_scaffold(stmt: SourceStatement) -> bool:
        text = stmt.text.strip()
        node_type = stmt.origin.get("nodeType") if isinstance(stmt.origin, dict) else None
        return text == "for" or node_type == "YulForLoop"

    @staticmethod
    def raw_statement_line(stmt: SourceStatement) -> str:
        text = stmt.text.strip()
        if text in {"break", "continue", "leave"}:
            return f"{text};"
        return text

    def _loop_groups(self) -> dict[str, dict[str, Any]]:
        blocks = self.fn.control.get("blocks", []) if isinstance(self.fn.control, dict) else []
        edges = self.fn.control.get("edges", []) if isinstance(self.fn.control, dict) else []
        block_by_id = {block.get("block_id"): block for block in blocks}
        out_edges: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in edges:
            out_edges[edge.get("from")].append(edge)

        stmt_by_id = {stmt.stmt_id: stmt for stmt in self.fn.source_statements if stmt.lang == "yul"}
        stmt_order = {stmt.stmt_id: index for index, stmt in enumerate(stmt_by_id.values())}
        groups: dict[str, dict[str, Any]] = {}

        for block in blocks:
            terminator = block.get("terminator") or {}
            attrs = block.get("attrs") or {}
            if terminator.get("node_kind") != "loop-condition":
                continue
            loop_stmt_ids = [stmt_id for stmt_id in block.get("stmts") or [] if stmt_id in stmt_by_id]
            if not loop_stmt_ids:
                continue
            loop_stmt_id = loop_stmt_ids[0]
            condition = self.loop_condition(block, out_edges)
            if not condition:
                continue
            loop_range = self.src_range(attrs.get("src"))
            body_blocks = self.loop_body_blocks(block.get("block_id"), out_edges, block_by_id)
            post_blocks = self.loop_post_blocks(block.get("block_id"), out_edges, block_by_id, body_blocks)
            body_stmt_ids = self.stmts_in_blocks(body_blocks, block_by_id, stmt_by_id)
            post_stmt_ids = self.stmts_in_blocks(post_blocks, block_by_id, stmt_by_id)
            body_stmt_ids.difference_update(post_stmt_ids)
            if not body_stmt_ids:
                continue
            pre_stmt_ids = []
            for stmt in stmt_by_id.values():
                if stmt.stmt_id == loop_stmt_id or stmt.stmt_id in body_stmt_ids or stmt.stmt_id in post_stmt_ids:
                    continue
                if loop_range and self.src_inside(stmt.src, loop_range):
                    pre_stmt_ids.append(stmt.stmt_id)
            body_stmt_ids = sorted(body_stmt_ids, key=lambda item: stmt_order.get(item, 10**9))
            post_stmt_ids = sorted(post_stmt_ids, key=lambda item: stmt_order.get(item, 10**9))
            pre_stmt_ids = sorted(pre_stmt_ids, key=lambda item: stmt_order.get(item, 10**9))
            groups[loop_stmt_id] = {
                "loop_stmt_id": loop_stmt_id,
                "condition": condition,
                "pre_stmt_ids": pre_stmt_ids,
                "body_stmt_ids": body_stmt_ids,
                "post_stmt_ids": post_stmt_ids,
                "consumed_stmt_ids": set([loop_stmt_id, *pre_stmt_ids, *body_stmt_ids, *post_stmt_ids]),
            }
        return groups

    @staticmethod
    def loop_condition(block: dict[str, Any], out_edges: dict[str, list[dict[str, Any]]]) -> str | None:
        for edge in out_edges.get(block.get("block_id"), []):
            kind = str(edge.get("kind") or "")
            if kind.startswith("true: "):
                return kind.removeprefix("true: ").strip()
        text = str((block.get("terminator") or {}).get("text") or "")
        if text.startswith("for condition "):
            return text.removeprefix("for condition ").strip()
        return None

    @staticmethod
    def loop_body_blocks(loop_block_id: str, out_edges: dict[str, list[dict[str, Any]]], block_by_id: dict[str, dict[str, Any]]) -> set[str]:
        starts = [
            edge.get("to")
            for edge in out_edges.get(loop_block_id, [])
            if str(edge.get("kind") or "").startswith("true: ")
        ]
        body: set[str] = set()
        stack = [item for item in starts if item]
        while stack:
            block_id = stack.pop()
            if not block_id or block_id in body or block_id == loop_block_id:
                continue
            block = block_by_id.get(block_id)
            if not block:
                continue
            node_kind = (block.get("terminator") or {}).get("node_kind")
            if node_kind in {"loop-merge", "loop-post"}:
                continue
            body.add(block_id)
            for edge in out_edges.get(block_id, []):
                kind = str(edge.get("kind") or "")
                target = edge.get("to")
                if kind == "loop back" or target == loop_block_id:
                    continue
                stack.append(target)
        return body

    @staticmethod
    def loop_post_blocks(loop_block_id: str, out_edges: dict[str, list[dict[str, Any]]], block_by_id: dict[str, dict[str, Any]], body_blocks: set[str]) -> set[str]:
        post_starts = []
        for block_id in body_blocks:
            for edge in out_edges.get(block_id, []):
                target = edge.get("to")
                target_block = block_by_id.get(target)
                if target_block and (target_block.get("terminator") or {}).get("node_kind") == "loop-post":
                    post_starts.append(target)
        out: set[str] = set()
        stack = list(post_starts)
        while stack:
            block_id = stack.pop()
            if not block_id or block_id in out or block_id == loop_block_id:
                continue
            block = block_by_id.get(block_id)
            if not block:
                continue
            node_kind = (block.get("terminator") or {}).get("node_kind")
            if node_kind == "loop-merge":
                continue
            out.add(block_id)
            for edge in out_edges.get(block_id, []):
                target = edge.get("to")
                if target == loop_block_id or str(edge.get("kind") or "") == "loop back":
                    continue
                stack.append(target)
        return out

    @staticmethod
    def stmts_in_blocks(block_ids: set[str], block_by_id: dict[str, dict[str, Any]], stmt_by_id: dict[str, SourceStatement]) -> set[str]:
        out: set[str] = set()
        for block_id in block_ids:
            for stmt_id in block_by_id.get(block_id, {}).get("stmts") or []:
                stmt = stmt_by_id.get(stmt_id)
                if stmt and not SolidityLikeRenderer.is_loop_scaffold(stmt):
                    out.add(stmt_id)
        return out

    @staticmethod
    def src_range(src: Any) -> tuple[int, int] | None:
        parts = str(src or "").split(":")
        if len(parts) < 2:
            return None
        try:
            start = int(parts[0])
            length = int(parts[1])
        except ValueError:
            return None
        return start, start + length

    @classmethod
    def src_inside(cls, src: Any, outer: tuple[int, int]) -> bool:
        inner = cls.src_range(src)
        if not inner:
            return False
        return outer[0] <= inner[0] and inner[1] <= outer[1]

    @staticmethod
    def _effects_by_ref(effects: list[EffectNode]) -> dict[str, list[EffectNode]]:
        out: dict[str, list[EffectNode]] = defaultdict(list)
        for effect in effects:
            for ref in effect.stmt_refs:
                out[ref].append(effect)
        return out

    def _overlays_by_anchor(self, overlays: list[SemanticOverlay]) -> dict[str, list[SemanticOverlay]]:
        out: dict[str, list[SemanticOverlay]] = defaultdict(list)
        for overlay in overlays:
            anchor = self.overlay_anchor(overlay)
            if anchor:
                out[anchor].append(overlay)
        return out

    def overlay_anchor(self, overlay: SemanticOverlay) -> str | None:
        refs = [ref for ref in overlay.stmt_refs if self.is_yul_ref(ref)]
        if not refs:
            return None
        if overlay.kind == "MappingSlot":
            return refs[0]
        return refs[-1]

    @staticmethod
    def _condition_aliases(effects: list[EffectNode]) -> dict[str, str]:
        aliases: dict[str, str] = {}
        for effect in effects:
            if effect.kind != "Branch":
                continue
            condition = str(effect.attrs.get("condition") or "").strip()
            final = str(effect.attrs.get("condition_final_temp") or "").strip()
            if condition and final:
                aliases[condition] = final
        return aliases

    @staticmethod
    def _memory_construction_notes(overlays: list[SemanticOverlay]) -> list[str]:
        notes: list[str] = []
        for overlay in overlays:
            if overlay.kind not in {"StructInitializationFragment", "ReturnMemoryStructConstruction"}:
                continue
            target = overlay.attrs.get("target")
            type_string = overlay.attrs.get("type") or overlay.attrs.get("struct_type")
            reason = overlay.attrs.get("reason")
            notes.append(f"/* struct initialization fragment {target}: {type_string}; {reason} */")
        return notes

    @staticmethod
    def _struct_field_lines_by_effect(overlays: list[SemanticOverlay]) -> dict[str, str]:
        out: dict[str, str] = {}
        for overlay in overlays:
            if overlay.kind == "StructFieldWrite":
                effect_id = overlay.attrs.get("write_effect") or (overlay.effects[0] if overlay.effects else None)
                line = overlay.attrs.get("solidity_like")
                if effect_id and line:
                    out[str(effect_id)] = str(line)
                continue
            if overlay.kind in {"StructMutationFragment", "StructMemoryMutation"}:
                for mutation in overlay.attrs.get("mutations") or []:
                    effect_id = mutation.get("write_effect")
                    line = mutation.get("solidity_like")
                    if effect_id and line:
                        out[str(effect_id)] = str(line)
                continue
            if overlay.kind not in {"StructInitializationFragment", "ReturnMemoryStructConstruction"}:
                continue
            target = overlay.attrs.get("target")
            for binding in overlay.attrs.get("fields") or []:
                field = binding.get("field") or {}
                effect_id = binding.get("write_effect")
                name = field.get("name")
                value = binding.get("value")
                if target and name and effect_id:
                    out[str(effect_id)] = f"{target}.{name} = {normalize_expr(value)};"
        return out

    def is_yul_ref(self, ref: str) -> bool:
        stmt = self.stmt_by_id.get(ref)
        return bool(stmt and stmt.lang == "yul")

    @staticmethod
    def _skip_branch_conditions(overlays: list[SemanticOverlay]) -> set[str]:
        out: set[str] = set()
        for overlay in overlays:
            if overlay.kind != "RequireOverlay":
                continue
            attrs = overlay.attrs
            nearest = attrs.get("nearest_condition")
            if nearest:
                out.add(str(nearest))
            for condition in attrs.get("require_conditions") or []:
                if condition and not str(condition).startswith("!("):
                    out.add(str(condition))
        return out

    def overlay_lines(self, overlays: list[SemanticOverlay]) -> list[str]:
        for kind in (
            "EventEmit",
            "PrecompileOutputRead",
            "PrecompileCall",
            "RawReturnData",
            "RequireOverlay",
            "CustomErrorRevert",
            "MappingWrite",
            "MappingRead",
            "StateVariableWrite",
            "StateVariableRead",
            "PathConditionedStorageWrite",
            "PathConditionedStorageRead",
            "StoragePointerSlotBinding",
            "CalldataWordRead",
            "AddressHasCode",
            "AddressCodeSize",
            "StructFieldRead",
            "MappingSlot",
        ):
            for overlay in overlays:
                if overlay.kind != kind:
                    continue
                line = self.line_for_overlay(overlay)
                if line:
                    return [line]
        return []

    @staticmethod
    def evaluation_step_lines(overlays: list[SemanticOverlay]) -> list[str]:
        steps = [overlay for overlay in overlays if overlay.kind == "EvaluationStep"]
        steps.sort(key=lambda overlay: int(overlay.attrs.get("order") or 0))
        call_lines_by_result = {
            overlay.attrs.get("result"): overlay.attrs.get("solidity_like")
            for overlay in overlays
            if overlay.kind in {"AbiEncodedLowLevelCall", "LowLevelCall", "StaticCallOverlay", "DelegateCallOverlay"}
            and overlay.attrs.get("result")
            and overlay.attrs.get("solidity_like")
        }
        lines: list[str] = []
        for overlay in steps:
            line = call_lines_by_result.get(overlay.attrs.get("temp")) or overlay.attrs.get("solidity_like")
            if line:
                lines.append(line)
        return lines

    def line_for_overlay(self, overlay: SemanticOverlay) -> str | None:
        attrs = overlay.attrs
        if overlay.kind == "RequireOverlay":
            if attrs.get("elided_by_native_precompile"):
                return None
            return attrs.get("require_like")
        if overlay.kind == "EventEmit":
            return attrs.get("emit_like")
        if overlay.kind == "CustomErrorRevert":
            return attrs.get("revert_like") or (f"revert {attrs.get('error')};" if attrs.get("error") else None)
        if overlay.kind in {"PrecompileCall", "PrecompileOutputRead"}:
            return attrs.get("solidity_like")
        if overlay.kind == "RawReturnData":
            return attrs.get("solidity_like")
        if overlay.kind in {"MappingRead", "MappingWrite", "StateVariableRead", "StateVariableWrite"}:
            line = attrs.get("solidity_like")
            if line and not line.replace(" ", "").endswith("=;"):
                return line
            return None
        if overlay.kind in {"PathConditionedStorageRead", "PathConditionedStorageWrite"}:
            return self.path_conditioned_line(overlay)
        if overlay.kind == "StoragePointerSlotBinding":
            return attrs.get("solidity_like")
        if overlay.kind == "CalldataWordRead":
            return attrs.get("solidity_like")
        if overlay.kind in {"AddressHasCode", "AddressCodeSize"}:
            return attrs.get("solidity_like")
        if overlay.kind == "StructFieldRead":
            return attrs.get("solidity_like")
        if overlay.kind == "MappingSlot":
            target = attrs.get("target")
            expr = attrs.get("expression")
            if target and expr:
                return f"{target} = slot({expr});"
        return None

    @staticmethod
    def path_conditioned_line(overlay: SemanticOverlay) -> str | None:
        candidates = overlay.attrs.get("candidates") or []
        resolved = []
        seen = set()
        for candidate in candidates:
            line = candidate.get("solidity_like")
            if candidate.get("status") != "resolved" or not line or line in seen:
                continue
            resolved.append(line)
            seen.add(line)
        if len(resolved) == 1:
            return resolved[0]
        if len(resolved) > 1:
            return " /* path-conditioned */ ".join(resolved)
        return None

    @staticmethod
    def expression_normalization(overlays: list[SemanticOverlay]) -> str | None:
        for overlay in overlays:
            if overlay.kind == "ExpressionNormalization":
                return overlay.attrs.get("solidity_like")
        return None

    def branch_effect(self, stmt: SourceStatement) -> EffectNode | None:
        for effect in self.effects_by_ref.get(stmt.stmt_id, []):
            if effect.kind == "Branch":
                return effect
        return None

    def memory_write_line(self, stmt: SourceStatement) -> str | None:
        for effect in self.effects_by_ref.get(stmt.stmt_id, []):
            if effect.kind != "MemoryWrite":
                continue
            if effect.effect_id in self.struct_field_lines_by_effect:
                return self.struct_field_lines_by_effect[effect.effect_id]
            if effect.attrs.get("write_kind") in {"loop_phi"}:
                continue
            address = self.memory_address(effect.attrs)
            value = self.memory_value(effect.attrs.get("value"))
            if not address:
                continue
            return f"memory[{address}] = {value};"
        return None

    @staticmethod
    def memory_value(value: Any) -> str:
        text = str(value or "").strip()
        m = re.match(r"^sload\(([A-Za-z_$][A-Za-z0-9_$]*)\.slot\)$", text)
        if m:
            return m.group(1)
        return normalize_expr(value)

    @staticmethod
    def memory_address(attrs: dict[str, Any]) -> str | None:
        aliases = attrs.get("aliases") or []
        if aliases:
            alias = aliases[0]
            base = alias.get("base")
            offset = alias.get("offset")
            if base is not None and offset is not None:
                try:
                    offset_i = int(offset)
                except Exception:
                    offset_i = None
                if offset_i == 0:
                    return str(base)
                if offset_i is not None:
                    return f"{base} + {offset_i}"
            expr = alias.get("expression")
            if expr:
                return normalize_expr(expr)
        address = attrs.get("address")
        return normalize_expr(address) if address else None
