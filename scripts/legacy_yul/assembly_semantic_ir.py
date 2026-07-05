#!/usr/bin/env python3
"""
Extract inline assembly blocks and build a first-pass semantic IR.

The script does not modify Solidity source and does not execute contracts. It
uses the assembly context extractor as the source-of-truth for block locations,
then optionally adapts SlithIR-SSA text into a normalized helper view.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from assembly_context_report import analyze_file, find_matching, split_top_level_commas


MEMORY_WORD_BYTES = 32
ASSEMBLY_OPS = {
    "mload",
    "mstore",
    "keccak256",
    "sload",
    "sstore",
    "log0",
    "log1",
    "log2",
    "log3",
    "log4",
    "call",
    "staticcall",
    "delegatecall",
    "callcode",
    "revert",
    "calldatacopy",
    "codecopy",
    "returndatacopy",
}


@dataclass
class YulStatement:
    index: int
    kind: str
    text: str
    control_path: list[str]
    semantic: dict[str, Any]


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def strip_assembly_wrapper(snippet: str) -> str:
    match = re.search(r"\bassembly\b", snippet)
    if not match:
        return snippet
    brace = snippet.find("{", match.end())
    if brace == -1:
        return snippet
    end = find_matching(snippet, brace, "{", "}")
    if end == -1:
        return snippet[brace + 1:]
    return snippet[brace + 1:end]


def parse_int_literal(text: str) -> int | None:
    text = text.strip()
    if re.fullmatch(r"0x[0-9a-fA-F]+", text):
        return int(text, 16)
    if re.fullmatch(r"\d+", text):
        return int(text, 10)
    return None


def split_assignment(statement: str) -> tuple[str | None, str]:
    statement = statement.strip()
    if statement.startswith("let "):
        rest = statement[4:].strip()
        if ":=" in rest:
            left, right = rest.split(":=", 1)
            return left.strip(), right.strip()
        return rest.strip(), ""
    if ":=" in statement:
        left, right = statement.split(":=", 1)
        return left.strip(), right.strip()
    return None, statement


SSA_SUFFIX_RE = re.compile(r"\b([A-Za-z_$][A-Za-z0-9_$]*)__ssa\d+\b")
IDENT_RE = re.compile(r"\b[A-Za-z_$][A-Za-z0-9_$]*\b")


def strip_ssa(text: str | None) -> str | None:
    if text is None:
        return None
    return SSA_SUFFIX_RE.sub(r"\1", str(text))


class SSAEnv:
    def __init__(self) -> None:
        self.versions: dict[str, int] = {}
        self.current: dict[str, str] = {}

    def new_version(self, name: str) -> str:
        version = self.versions.get(name, 0) + 1
        self.versions[name] = version
        ssa_name = f"{name}__ssa{version}"
        self.current[name] = ssa_name
        return ssa_name

    def substitute_expr(self, expr: str) -> str:
        def repl(match: re.Match[str]) -> str:
            token = match.group(0)
            return self.current.get(token, token)
        return IDENT_RE.sub(repl, expr)

    def transform_statement(self, statement: str) -> str:
        if statement.startswith("if "):
            return "if " + self.substitute_expr(statement[3:].strip())
        if statement.startswith("for "):
            return "for " + self.substitute_expr(statement[4:].strip())

        target, expr = split_assignment(statement)
        if target is None:
            return self.substitute_expr(statement)

        expr_ssa = self.substitute_expr(expr)
        target_ssa = self.new_version(target)
        if statement.startswith("let "):
            return f"let {target_ssa} := {expr_ssa}" if expr else f"let {target_ssa}"
        return f"{target_ssa} := {expr_ssa}"

    def transform_control_path(self, control_path: list[str]) -> list[str]:
        return [self.substitute_expr(condition) for condition in control_path]


def parse_call(expr: str) -> tuple[str, list[str]] | None:
    expr = expr.strip()
    match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)$", expr, re.S)
    if not match:
        return None
    name = match.group(1)
    args = split_top_level_commas(match.group(2))
    return name, args


def parse_memory_address(expr: str) -> dict[str, Any]:
    expr = expr.strip()
    call = parse_call(expr)
    if call and call[0] == "add" and len(call[1]) == 2:
        left, right = call[1]
        right_int = parse_int_literal(right)
        left_int = parse_int_literal(left)
        if right_int is not None:
            return {"base": left.strip(), "offset": right_int, "offset_expr": str(right_int), "expr": expr}
        if left_int is not None:
            return {"base": right.strip(), "offset": left_int, "offset_expr": str(left_int), "expr": expr}
        return {"base": left.strip(), "offset": None, "offset_expr": right.strip(), "expr": expr}
    return {"base": expr, "offset": 0, "offset_expr": "0", "expr": expr}


def offset_key(address: dict[str, Any]) -> str:
    return str(address["offset"]) if address.get("offset") is not None else address["offset_expr"]


def ranges_overlap(start_a: int, size_a: int, start_b: int, size_b: int) -> bool:
    return start_a < start_b + size_b and start_b < start_a + size_a


def yul_body_to_statements(body: str) -> list[tuple[str, list[str]]]:
    statements: list[tuple[str, list[str]]] = []

    def skip_ws(pos: int, end: int) -> int:
        while pos < end and body[pos].isspace():
            pos += 1
        return pos

    def parse_inline_statements(text: str, control_path: list[str]) -> None:
        for raw in text.splitlines():
            statement = normalize_space(raw)
            if statement and statement not in {"}", "{"}:
                statements.append((statement, control_path[:]))

    def parse_block(start: int, end: int, control_path: list[str]) -> None:
        i = start
        while i < end:
            i = skip_ws(i, end)
            if i >= end:
                break

            if body.startswith("for", i) and (i + 3 == end or not body[i + 3].isalnum()):
                cursor = skip_ws(i + 3, end)
                if cursor >= end or body[cursor] != "{":
                    line_end = body.find("\n", i, end)
                    if line_end == -1:
                        line_end = end
                    statement = normalize_space(body[i:line_end])
                    if statement:
                        statements.append((statement, control_path[:]))
                    i = line_end + 1
                    continue

                init_end = find_matching(body, cursor, "{", "}")
                if init_end == -1 or init_end >= end:
                    break
                init_text = body[cursor + 1:init_end]
                parse_inline_statements(init_text, control_path)

                cond_start = skip_ws(init_end + 1, end)
                post_start = body.find("{", cond_start, end)
                if post_start == -1:
                    break
                condition = normalize_space(body[cond_start:post_start])
                post_end = find_matching(body, post_start, "{", "}")
                if post_end == -1 or post_end >= end:
                    break
                post_text = body[post_start + 1:post_end]

                body_start = skip_ws(post_end + 1, end)
                if body_start >= end or body[body_start] != "{":
                    break
                body_end = find_matching(body, body_start, "{", "}")
                if body_end == -1 or body_end > end:
                    body_end = end - 1

                loop_condition = f"for {condition}" if condition else "for"
                statements.append((loop_condition, control_path[:]))
                parse_block(body_start + 1, body_end, control_path + [loop_condition])
                parse_inline_statements(post_text, control_path + [loop_condition])
                i = body_end + 1
                continue

            if body.startswith("if", i) and (i + 2 == end or not body[i + 2].isalnum()):
                cond_start = i + 2
                brace = body.find("{", cond_start, end)
                if brace == -1:
                    line_end = body.find("\n", i, end)
                    if line_end == -1:
                        line_end = end
                    statement = normalize_space(body[i:line_end])
                    if statement:
                        statements.append((statement, control_path[:]))
                    i = line_end + 1
                    continue

                condition = normalize_space(body[cond_start:brace])
                block_end = find_matching(body, brace, "{", "}")
                if block_end == -1 or block_end > end:
                    block_end = end - 1
                if condition:
                    statements.append((f"if {condition}", control_path[:]))
                parse_block(brace + 1, block_end, control_path + [condition])
                i = block_end + 1
                continue

            line_end = body.find("\n", i, end)
            next_brace = body.find("{", i, end)
            if line_end == -1:
                line_end = end
            if next_brace != -1 and next_brace < line_end:
                line_end = next_brace
            statement = normalize_space(body[i:line_end])
            if statement and statement not in {"}", "{"}:
                statements.append((statement, control_path[:]))
            i = line_end + 1

    parse_block(0, len(body), [])
    return statements


class CFGPathTracker:
    """Structured Yul CFG state traversal equivalent to IF/END_IF edges."""

    def __init__(self) -> None:
        self._next_branch_id = 1
        self.frames: list[dict[str, Any]] = []
        self.active_states: list[tuple[tuple[int, bool], ...]] = [()]

    def reconcile(self, target_conditions: list[str]) -> None:
        common = 0
        while common < len(self.frames) and common < len(target_conditions):
            if self.frames[common]["condition"] != target_conditions[common]:
                break
            common += 1

        while len(self.frames) > common:
            frame = self.frames.pop()
            self.active_states = frame["true_states"] + frame["false_states"]

        while len(self.frames) < len(target_conditions):
            condition = target_conditions[len(self.frames)]
            branch_id = self._next_branch_id
            self._next_branch_id += 1
            parent_states = self.active_states
            true_states = [state + ((branch_id, True),) for state in parent_states]
            false_states = [state + ((branch_id, False),) for state in parent_states]
            self.frames.append({
                "condition": condition,
                "branch_id": branch_id,
                "true_states": true_states,
                "false_states": false_states,
            })
            self.active_states = true_states

    @staticmethod
    def format_state(state: tuple[tuple[int, bool], ...]) -> str:
        return "/".join(f"b{branch}:{'T' if value else 'F'}" for branch, value in state) or "entry"

    def formatted_active_states(self) -> list[str]:
        return [self.format_state(state) for state in self.active_states]


class MemoryTracker:
    """Path-sensitive virtual memory for one inline assembly block.

    A write carries the SSA control path active when it occurred. A later read
    can use that write exactly only when the write path dominates the read path.
    Writes from optional prior branches become explicit merge candidates.
    """

    def __init__(self, scope: str = "assembly_block") -> None:
        self.scope = scope
        self.memory: dict[str, list[dict[str, Any]]] = {}
        self.versions: dict[tuple[str, str], int] = {}

    @staticmethod
    def _path_text(control_path: list[str] | None) -> str:
        return " -> ".join(strip_ssa(part) or part for part in (control_path or [])) or "root"

    @staticmethod
    def _visible(entry_path: list[str], read_path: list[str]) -> bool:
        return len(entry_path) <= len(read_path) and entry_path == read_path[:len(entry_path)]

    def _new_mssa(self, address: dict[str, Any]) -> str:
        base = re.sub(r"[^A-Za-z0-9_]", "_", str(address["base"]))
        offset = re.sub(r"[^A-Za-z0-9_]", "_", offset_key(address))
        key = (str(address["base"]), offset_key(address))
        version = self.versions.get(key, 0) + 1
        self.versions[key] = version
        return f"mem_{base}_{offset}__mssa{version}"

    def _append(self, address: dict[str, Any], entry: dict[str, Any]) -> None:
        entry["memory_ssa"] = self._new_mssa(address)
        self.memory.setdefault(str(address["base"]), []).append(entry)

    def write(self, address_expr: str, value: str, size: int = MEMORY_WORD_BYTES, control_path: list[str] | None = None, path_states: list[tuple[tuple[int, bool], ...]] | None = None) -> dict[str, Any]:
        address = parse_memory_address(address_expr)
        base = str(address["base"])
        entry = {
            "kind": "word",
            "offset": address.get("offset"),
            "offset_expr": address.get("offset_expr"),
            "size": size,
            "value": value.strip(),
            "source": "mstore",
            "control_path": list(control_path or []),
            "cfg_path_states": list(path_states or [()]),
        }
        self._append(address, entry)
        return {
            "address": address,
            "value": value.strip(),
            "memory_ssa": entry["memory_ssa"],
            "control_path": entry["control_path"],
            "tracker_scope": self.scope,
            "memory_after": self.snapshot_for_base(base),
        }

    def copy(self, op: str, dst_expr: str, src_expr: str, size_expr: str, control_path: list[str] | None = None, path_states: list[tuple[tuple[int, bool], ...]] | None = None) -> dict[str, Any]:
        address = parse_memory_address(dst_expr)
        base = str(address["base"])
        size = parse_int_literal(size_expr)
        src = src_expr.strip()
        if op == "calldatacopy":
            source = f"calldata[{src}:{src}+{size_expr}]"
        elif op == "codecopy":
            source = f"code[{src}:{src}+{size_expr}]"
        else:
            source = f"returndata[{src}:{src}+{size_expr}]"
        entry = {
            "kind": "range",
            "offset": address.get("offset"),
            "offset_expr": address.get("offset_expr"),
            "size": size,
            "size_expr": size_expr.strip(),
            "value": source,
            "source": op,
            "source_offset": src,
            "control_path": list(control_path or []),
            "cfg_path_states": list(path_states or [()]),
        }
        self._append(address, entry)
        return {
            "address": address,
            "source": source,
            "size": size,
            "size_expr": size_expr.strip(),
            "memory_ssa": entry["memory_ssa"],
            "control_path": entry["control_path"],
            "tracker_scope": self.scope,
            "memory_after": self.snapshot_for_base(base),
        }

    def read(self, address_expr: str, length_expr: str | None = None, control_path: list[str] | None = None, path_states: list[tuple[tuple[int, bool], ...]] | None = None) -> dict[str, Any]:
        address = parse_memory_address(address_expr)
        base = str(address["base"])
        start = address.get("offset")
        start_expr = address.get("offset_expr")
        length = parse_int_literal(length_expr or "") if length_expr is not None else None
        path = list(control_path or [])
        resolved_words = []
        if start is not None and length is not None and length > 0:
            word_count = (length + MEMORY_WORD_BYTES - 1) // MEMORY_WORD_BYTES
            for idx in range(word_count):
                word_offset = start + idx * MEMORY_WORD_BYTES
                resolved_words.append(self.resolve_word(base, word_offset, path, path_states))
        elif start is not None:
            resolved_words.append(self.resolve_word(base, start, path, path_states))
        else:
            resolved_words.append({
                "offset": None,
                "offset_expr": start_expr,
                "value": self.resolve_symbolic(base, start_expr, path),
                "control_path": path,
            })
        return {
            "address": address,
            "length": length,
            "control_path": path,
            "tracker_scope": self.scope,
            "resolved_words": resolved_words,
        }

    def _candidate(self, entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "value": entry["value"],
            "memory_ssa": entry.get("memory_ssa"),
            "control_path": entry.get("control_path", []),
            "path": self._path_text(entry.get("control_path", [])),
            "source": entry.get("source"),
        }

    def _unknown_word(self, offset: int, candidates: list[dict[str, Any]], fallback: dict[str, Any] | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "offset": offset,
            "offset_expr": str(offset),
            "value": "unknown",
            "branch_candidates": candidates,
        }
        if fallback is not None:
            result["exact_fallback"] = fallback["value"]
            result["memory_ssa"] = fallback.get("memory_ssa")
            result["control_path"] = fallback.get("control_path", [])
        return result

    def resolve_word(self, base: str, offset: int, control_path: list[str] | None = None, path_states: list[tuple[tuple[int, bool], ...]] | None = None) -> dict[str, Any]:
        if path_states:
            return self.resolve_word_cfg(base, offset, path_states)
        path = list(control_path or [])
        candidates: list[dict[str, Any]] = []
        for entry in reversed(self.memory.get(base, [])):
            entry_path = entry.get("control_path", [])
            visible = self._visible(entry_path, path)
            entry_offset = entry.get("offset")
            if entry_offset is None:
                candidates.append(self._candidate(entry))
                continue
            matches = entry["kind"] == "word" and entry_offset == offset
            if entry["kind"] == "range":
                size = entry.get("size")
                matches = size is not None and ranges_overlap(entry_offset, size, offset, MEMORY_WORD_BYTES)
            if not matches:
                continue
            if not visible:
                candidates.append(self._candidate(entry))
                continue
            if candidates:
                return self._unknown_word(offset, candidates, entry)
            if entry["kind"] == "range":
                value = self.slice_range_source(entry, offset - entry_offset)
            else:
                value = entry["value"]
            return {
                "offset": offset,
                "offset_expr": str(offset),
                "value": value,
                "memory_ssa": entry.get("memory_ssa"),
                "control_path": entry_path,
            }
        if candidates:
            return self._unknown_word(offset, candidates)
        return {"offset": offset, "offset_expr": str(offset), "value": None}

    @staticmethod
    def _cfg_prefix(write_state: tuple[tuple[int, bool], ...], read_state: tuple[tuple[int, bool], ...]) -> bool:
        return len(write_state) <= len(read_state) and write_state == read_state[:len(write_state)]

    def _resolve_word_cfg_state(self, base: str, offset: int, state: tuple[tuple[int, bool], ...]) -> dict[str, Any]:
        for entry in reversed(self.memory.get(base, [])):
            states = entry.get("cfg_path_states", [()])
            if not any(self._cfg_prefix(tuple(write_state), state) for write_state in states):
                continue
            entry_offset = entry.get("offset")
            if entry_offset is None:
                return self._unknown_word(offset, [self._candidate(entry)])
            matches = entry["kind"] == "word" and entry_offset == offset
            if entry["kind"] == "range":
                size = entry.get("size")
                matches = size is not None and ranges_overlap(entry_offset, size, offset, MEMORY_WORD_BYTES)
            if not matches:
                continue
            value = self.slice_range_source(entry, offset - entry_offset) if entry["kind"] == "range" else entry["value"]
            return {
                "offset": offset,
                "offset_expr": str(offset),
                "value": value,
                "memory_ssa": entry.get("memory_ssa"),
                "control_path": entry.get("control_path", []),
                "cfg_path_state": CFGPathTracker.format_state(state),
            }
        return {"offset": offset, "offset_expr": str(offset), "value": None, "cfg_path_state": CFGPathTracker.format_state(state)}

    def resolve_word_cfg(self, base: str, offset: int, path_states: list[tuple[tuple[int, bool], ...]]) -> dict[str, Any]:
        per_path = [self._resolve_word_cfg_state(base, offset, tuple(state)) for state in path_states]
        values = [item.get("value") for item in per_path]
        known_values = {value for value in values if value not in {None, "unknown"}}
        mssa_inputs = [item.get("memory_ssa") for item in per_path if item.get("memory_ssa")]
        if len(known_values) == 1 and all(value not in {None, "unknown"} for value in values):
            result = dict(per_path[0])
            if len(set(mssa_inputs)) > 1:
                result["memory_phi"] = f"phi({', '.join(dict.fromkeys(mssa_inputs))})"
            result["cfg_path_states"] = [item["cfg_path_state"] for item in per_path]
            return result
        candidates = [
            {
                "value": item.get("value"),
                "memory_ssa": item.get("memory_ssa"),
                "path": item.get("cfg_path_state"),
                "source": "cfg_path",
            }
            for item in per_path
        ]
        result = self._unknown_word(offset, candidates)
        result["cfg_path_states"] = [item["cfg_path_state"] for item in per_path]
        return result

    def resolve_symbolic(self, base: str, offset_expr: str | None, control_path: list[str] | None = None) -> str | None:
        path = list(control_path or [])
        candidates = []
        for entry in reversed(self.memory.get(base, [])):
            if entry.get("offset") is not None or entry.get("offset_expr") != offset_expr:
                continue
            if self._visible(entry.get("control_path", []), path):
                return entry["value"] if not candidates else None
            candidates.append(entry)
        return None

    def slice_range_source(self, entry: dict[str, Any], relative_offset: int) -> str:
        source_offset = entry.get("source_offset", "0")
        if parse_int_literal(source_offset) is not None:
            start = str(parse_int_literal(source_offset) + relative_offset)
        elif relative_offset == 0:
            start = source_offset
        else:
            start = f"{source_offset}+{relative_offset}"
        source_name = entry["value"].split("[", 1)[0]
        return f"{source_name}[{start}:{start}+32]"

    def entry_text(self, entry: dict[str, Any]) -> str:
        offset = entry.get("offset")
        offset_text = str(offset) if offset is not None else entry.get("offset_expr", "unknown")
        size = entry.get("size") if entry.get("size") is not None else entry.get("size_expr", "unknown")
        return f"{offset_text}/size {size}: {entry['value']} [{entry.get('memory_ssa', 'unknown')} path={self._path_text(entry.get('control_path', []))}]"

    def snapshot_for_base(self, base: str) -> list[dict[str, Any]]:
        return [
            {
                "offset": entry.get("offset"),
                "offset_expr": entry.get("offset_expr"),
                "size": entry.get("size"),
                "size_expr": entry.get("size_expr"),
                "value": entry.get("value"),
                "source": entry.get("source"),
                "memory_ssa": entry.get("memory_ssa"),
                "control_path": entry.get("control_path", []),
                "cfg_path_states": [CFGPathTracker.format_state(tuple(state)) for state in entry.get("cfg_path_states", [()])],
            }
            for entry in self.memory.get(base, [])
        ]

def semantic_for_statement(statement: str, memory: MemoryTracker, control_path: list[str] | None = None, path_states: list[tuple[tuple[int, bool], ...]] | None = None) -> tuple[str, dict[str, Any]]:
    target, expr = split_assignment(statement)
    call = parse_call(expr)

    if statement.startswith("if "):
        condition = statement[3:].strip()
        return "condition", {"condition": condition, "solidity_like": f"if ({condition})"}

    if statement.startswith("for "):
        condition = statement[4:].strip()
        return "loop", {"condition": condition, "solidity_like": f"for ({condition})"}

    if not call:
        return "assignment" if target else "unknown", {
            "target": target,
            "expr": expr,
            "solidity_like": f"{target} = {expr};" if target and expr else statement,
        }

    op, args = call
    if op == "mload" and len(args) == 1:
        read = memory.read(args[0], control_path=control_path, path_states=path_states)
        solidity_like = f"{target} = memory[{args[0]}];" if target else f"memory[{args[0]}]"
        return "memory_read", {
            "op": op,
            "target": target,
            "ptr": args[0],
            "read": read,
            "solidity_like": solidity_like,
        }

    if op == "mstore" and len(args) == 2:
        write = memory.write(args[0], args[1], control_path=control_path, path_states=path_states)
        address = write["address"]
        offset_text = address["offset"] if address.get("offset") is not None else address.get("offset_expr")
        solidity_like = f"memory[{address['base']} + {offset_text}] = {args[1]};"
        return "memory_write", {
            "op": op,
            "ptr": args[0],
            "value": args[1],
            "write": write,
            "solidity_like": solidity_like,
        }

    if op == "keccak256" and len(args) == 2:
        read = memory.read(args[0], args[1], control_path=control_path, path_states=path_states)
        words = [word["value"] for word in read["resolved_words"]]
        words_text = ", ".join(value if value is not None else "unknown" for value in words)
        solidity_like = f"{target} = keccak256({words_text});" if target else f"keccak256({words_text})"
        return "memory_hash", {
            "op": op,
            "target": target,
            "ptr": args[0],
            "length": args[1],
            "read": read,
            "resolved_inputs": read["resolved_words"],
            "solidity_like": solidity_like,
        }

    if op == "sload" and len(args) == 1:
        return "storage_read", {
            "op": op,
            "target": target,
            "slot": args[0],
            "solidity_like": f"{target} = sload({args[0]});" if target else f"sload({args[0]})",
        }

    if op == "sstore" and len(args) == 2:
        return "storage_write", {
            "op": op,
            "slot": args[0],
            "value": args[1],
            "unchecked_arithmetic_candidate": any(fn in args[1] for fn in ("add(", "sub(", "mul(")),
            "solidity_like": f"sstore({args[0]}, {args[1]});",
        }

    if op.startswith("log") and op[3:].isdigit():
        topic_count = int(op[3:])
        data_args = args[:2]
        topic_args = args[2:]
        read = memory.read(data_args[0], data_args[1], control_path=control_path, path_states=path_states) if len(data_args) == 2 else None
        return "event_log", {
            "op": op,
            "topic_count": topic_count,
            "data": {"ptr": data_args[0], "length": data_args[1]} if len(data_args) == 2 else None,
            "topics": topic_args,
            "resolved_data": read["resolved_words"] if read else [],
            "solidity_like": f"{op}(data={data_args}, topics={topic_args});",
        }

    if op in {"calldatacopy", "codecopy", "returndatacopy"} and len(args) == 3:
        copied = memory.copy(op, args[0], args[1], args[2], control_path=control_path, path_states=path_states)
        address = copied["address"]
        offset_text = address["offset"] if address.get("offset") is not None else address.get("offset_expr")
        return "memory_copy", {
            "op": op,
            "dst": args[0],
            "src": args[1],
            "size": args[2],
            "copy": copied,
            "solidity_like": f"memory[{address['base']} + {offset_text} : + {args[2]}] = {copied['source']};",
        }

    if op in {"call", "staticcall", "delegatecall", "callcode"}:
        return "external_call", {
            "op": op,
            "args": args,
            "target": args[1] if len(args) > 1 else None,
            "solidity_like": f"{target + ' = ' if target else ''}{op}({', '.join(args)});",
        }

    if op == "revert":
        return "revert", {
            "op": op,
            "args": args,
            "solidity_like": f"revert({', '.join(args)});",
        }

    return "assignment" if target else "call", {
        "op": op,
        "target": target,
        "args": args,
        "solidity_like": f"{target} = {op}({', '.join(args)});" if target else f"{op}({', '.join(args)});",
    }


def build_source_semantic_ir(assembly_snippet: str, memory_scope: str = "assembly_block") -> tuple[list[dict[str, Any]], list[str]]:
    body = strip_assembly_wrapper(assembly_snippet)
    raw_statements = yul_body_to_statements(body)
    memory = MemoryTracker(memory_scope)
    ssa = SSAEnv()
    cfg_paths = CFGPathTracker()
    ops: list[dict[str, Any]] = []
    replacement_lines: list[str] = []

    for index, (statement, control_path) in enumerate(raw_statements):
        ssa_control_path = ssa.transform_control_path(control_path)
        cfg_paths.reconcile(ssa_control_path)
        ssa_statement = ssa.transform_statement(statement)
        kind, semantic = semantic_for_statement(ssa_statement, memory, ssa_control_path, cfg_paths.active_states)
        op = YulStatement(
            index=index,
            kind=kind,
            text=statement,
            control_path=ssa_control_path,
            semantic=semantic,
        )
        op_dict = asdict(op)
        op_dict["ssa_text"] = ssa_statement
        op_dict["raw_control_path"] = control_path
        op_dict["cfg_path_states"] = cfg_paths.formatted_active_states()
        ops.append(op_dict)

        indent = "  " * len(control_path)
        if ssa_control_path:
            op_dict["controlled_by"] = ssa_control_path[-1]
        replacement = strip_ssa(semantic.get('solidity_like', ssa_statement))
        replacement_lines.append(f"{indent}{replacement} // yul: {statement}")

    return ops, replacement_lines


def classify_slithir_expression(expression: str, irs: list[str]) -> str:
    expr = expression.strip()
    for op in sorted(ASSEMBLY_OPS, key=len, reverse=True):
        if re.search(rf"\b{op}\s*(?:\(|\(uint|$)", expr):
            if op == "mload":
                return "memory_read"
            if op == "mstore":
                return "memory_write"
            if op == "keccak256":
                return "memory_hash"
            if op == "sload":
                return "storage_read"
            if op == "sstore":
                return "storage_write"
            if op.startswith("log"):
                return "event_log"
            if op in {"call", "staticcall", "delegatecall", "callcode"}:
                return "external_call"
            if op == "revert":
                return "revert"
            if op in {"calldatacopy", "codecopy", "returndatacopy"}:
                return "memory_copy"
    if any("CONDITION " in ir for ir in irs):
        return "condition"
    if any("INTERNAL_CALL" in ir for ir in irs):
        return "internal_call"
    if any("Emit " in ir for ir in irs):
        return "event_emit"
    if any("RETURN " in ir for ir in irs):
        return "return"
    return "other"


def parse_slithir_ssa(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    functions: dict[str, dict[str, Any]] = {}
    current_function: dict[str, Any] | None = None
    current_expr: dict[str, Any] | None = None
    in_irs = False

    function_pattern = re.compile(r"^\s*Function\s+([A-Za-z_$][A-Za-z0-9_$]*)\.([A-Za-z_$][A-Za-z0-9_$]*|constructor|fallback|receive)\((.*)\)")

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        function_match = function_pattern.match(line)
        if function_match:
            if current_expr and current_function is not None:
                current_expr["kind"] = classify_slithir_expression(current_expr["expression"], current_expr["irs"])
                current_function["entries"].append(current_expr)
            contract, name, params = function_match.groups()
            key = f"{contract}.{name}"
            current_function = {
                "contract": contract,
                "function": name,
                "signature": f"{name}({params})",
                "entries": [],
            }
            functions[key] = current_function
            current_expr = None
            in_irs = False
            continue

        if current_function is None:
            continue

        stripped = line.strip()
        if stripped.startswith("Expression: "):
            if current_expr:
                current_expr["kind"] = classify_slithir_expression(current_expr["expression"], current_expr["irs"])
                current_function["entries"].append(current_expr)
            current_expr = {"expression": stripped[len("Expression: "):], "irs": [], "kind": None}
            in_irs = False
            continue

        if stripped == "IRs:":
            if current_expr is None:
                current_expr = {"expression": "", "irs": [], "kind": None}
            in_irs = True
            continue

        if in_irs and current_expr is not None and stripped:
            current_expr["irs"].append(stripped)

    if current_expr and current_function is not None:
        current_expr["kind"] = classify_slithir_expression(current_expr["expression"], current_expr["irs"])
        current_function["entries"].append(current_expr)

    return {
        "source": str(path),
        "functions": functions,
    }


def select_relevant_slithir_entries(function_view: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not function_view:
        return []
    relevant = []
    for index, entry in enumerate(function_view["entries"]):
        if entry["kind"] in {
            "memory_read",
            "memory_write",
            "memory_hash",
            "storage_read",
            "storage_write",
            "event_log",
            "external_call",
            "revert",
            "condition",
        }:
            relevant.append({
                "index": index,
                "kind": entry["kind"],
                "expression": entry["expression"],
                "irs": entry["irs"],
            })
    return relevant


def build_report(source_path: Path, slithir_path: Path | None) -> dict[str, Any]:
    blocks = analyze_file(source_path)
    slithir = parse_slithir_ssa(slithir_path) if slithir_path else None
    slithir_functions = slithir["functions"] if slithir else {}

    report_blocks = []
    for block_index, block in enumerate(blocks):
        source_ops, replacement_lines = build_source_semantic_ir(block.assembly_snippet, f"{block.contract}.{block.function}#{block_index}")
        slithir_key = f"{block.contract}.{block.function}"
        slithir_view = slithir_functions.get(slithir_key)
        report_blocks.append({
            "block_id": f"{block.contract}.{block.function}#{block_index}",
            "context": {
                "source_file": block.source_file,
                "contract": block.contract,
                "function": block.function,
                "function_parameters": block.function_parameters,
                "function_visibility": block.function_visibility,
                "function_is_erc20_standard_entry": block.function_is_erc20_standard_entry,
                "function_erc20_signature": block.function_erc20_signature,
                "assembly_position": asdict(block.assembly_position),
                "reachable_from_erc20_entrypoints": block.reachable_from_erc20_entrypoints,
                "reachable_from": block.reachable_from,
            },
            "assembly_source": block.assembly_snippet,
            "source_yul_semantic_ir": source_ops,
            "semantic_replacement_view": replacement_lines,
            "slithir_ssa_adapter": {
                "matched_function": slithir_key if slithir_view else None,
                "relevant_entries": select_relevant_slithir_entries(slithir_view),
            },
        })

    return {
        "source_file": str(source_path),
        "slithir_ssa": str(slithir_path) if slithir_path else None,
        "ir_note": "This IR is a semantic replacement view only. It is not compiled and does not modify the source. Memory is tracked per function-context assembly block with path-sensitive SSA writes.",
        "assembly_blocks": report_blocks,
    }


def param_signature(params: list[dict[str, str]]) -> str:
    return ",".join(param["type"] for param in params)


def param_display(params: list[dict[str, str]]) -> str:
    return ", ".join(
        f"{param['type']} {param['name']}".strip()
        for param in params
    )


def memory_words_text(words: list[dict[str, Any]]) -> str:
    if not words:
        return "[]"
    parts = []
    for word in words:
        value = word.get("value")
        offset = word.get("offset")
        offset_text = str(offset) if offset is not None else word.get("offset_expr", "unknown")
        value_text = strip_ssa(value) if value is not None else "unknown"
        if word.get("symbolic_candidates"):
            value_text += " candidates=" + repr([strip_ssa(candidate) for candidate in word["symbolic_candidates"]])
        if word.get("branch_candidates"):
            candidates = [
                {
                    "value": strip_ssa(candidate.get("value")) or candidate.get("value"),
                    "path": candidate.get("path", "root"),
                    "mssa": candidate.get("memory_ssa"),
                }
                for candidate in word["branch_candidates"]
            ]
            value_text += " branch_candidates=" + repr(candidates)
        if word.get("memory_ssa"):
            value_text += f" mssa={word['memory_ssa']}"
        size = word.get("size") or word.get("size_expr")
        if size is not None:
            parts.append(f"{offset_text}/size {size}: {value_text}")
        else:
            parts.append(f"{offset_text}: {value_text}")
    return "[" + ", ".join(parts) + "]"


def semantic_ir_text(op: dict[str, Any]) -> list[str]:
    semantic = op["semantic"]
    kind = op["kind"]
    prefix = f"[{op['index']}] {kind.upper()}"
    lines: list[str] = []

    if kind == "memory_read":
        lines.append(f"{prefix}: {semantic.get('target') or '_'} := MLOAD {semantic['ptr']}")
        lines.append(f"\t\t\tResolved: {memory_words_text(semantic['read']['resolved_words'])}")
    elif kind == "memory_write":
        address = semantic["write"]["address"]
        offset_text = str(address.get("offset")) if address.get("offset") is not None else address.get("offset_expr", "unknown")
        lines.append(f"{prefix}: MSTORE mem[{address['base']} + {offset_text}] := {semantic['value']}")
        lines.append(f"\t\t\tMemoryAfter: {memory_words_text(semantic['write']['memory_after'])}")
    elif kind == "memory_hash":
        lines.append(f"{prefix}: {semantic.get('target') or '_'} := KECCAK256 {semantic['ptr']} len {semantic['length']}")
        lines.append(f"\t\t\tResolvedInputs: {memory_words_text(semantic['resolved_inputs'])}")
    elif kind == "memory_copy":
        copied = semantic["copy"]
        address = copied["address"]
        offset_text = str(address.get("offset")) if address.get("offset") is not None else address.get("offset_expr", "unknown")
        lines.append(f"{prefix}: {semantic['op'].upper()} mem[{address['base']} + {offset_text}] size {semantic['size']} := {copied['source']}")
        lines.append(f"\t\t\tMemoryAfter: {memory_words_text(copied['memory_after'])}")
    elif kind == "storage_read":
        lines.append(f"{prefix}: {semantic.get('target') or '_'} := SLOAD {semantic['slot']}")
    elif kind == "storage_write":
        lines.append(f"{prefix}: SSTORE {semantic['slot']} := {semantic['value']}")
        if semantic.get("unchecked_arithmetic_candidate"):
            lines.append("\t\t\tFlag: unchecked_arithmetic_candidate")
    elif kind == "event_log":
        data = semantic.get("data") or {}
        lines.append(f"{prefix}: {semantic['op'].upper()} data({data.get('ptr')}, {data.get('length')}) topics {semantic['topics']}")
        lines.append(f"\t\t\tResolvedData: {memory_words_text(semantic.get('resolved_data', []))}")
    elif kind == "external_call":
        lines.append(f"{prefix}: {semantic['op'].upper()} args({', '.join(semantic['args'])})")
        if semantic.get("target") is not None:
            lines.append(f"\t\t\tTarget: {semantic['target']}")
    elif kind == "revert":
        lines.append(f"{prefix}: REVERT args({', '.join(semantic['args'])})")
    elif kind == "condition":
        lines.append(f"{prefix}: CONDITION {semantic['condition']}")
    elif kind == "loop":
        lines.append(f"{prefix}: LOOP {semantic['condition']}")
    elif kind == "assignment":
        target = semantic.get("target") or "_"
        expr = semantic.get("expr") or f"{semantic.get('op')}({', '.join(semantic.get('args', []))})"
        lines.append(f"{prefix}: {target} := {expr}")
    else:
        lines.append(f"{prefix}: {op['text']}")

    if op.get("control_path"):
        lines.append(f"\t\t\tControlPath: {' -> '.join(op['control_path'])}")
    lines.append(f"\t\t\tSolidityLike: {semantic.get('solidity_like', op['text'])}")
    lines.append(f"\t\t\tYul: {op['text']}")
    return lines


def format_text_ir(report: dict[str, Any]) -> str:
    lines = [
        f"INFO:AssemblySemanticIR:Source {report['source_file']}",
        f"INFO:AssemblySemanticIR:SlithIR-SSA {report['slithir_ssa'] or 'not provided'}",
        f"INFO:AssemblySemanticIR:Assembly blocks {len(report['assembly_blocks'])}",
        f"INFO:AssemblySemanticIR:Note {report['ir_note']}",
        "",
    ]

    current_contract: str | None = None
    for block in report["assembly_blocks"]:
        ctx = block["context"]
        pos = ctx["assembly_position"]
        params_sig = param_signature(ctx["function_parameters"])
        params_human = param_display(ctx["function_parameters"])

        if ctx["contract"] != current_contract:
            current_contract = ctx["contract"]
            lines.append(f"Contract {current_contract}")

        lines.extend([
            f"\tFunction {ctx['contract']}.{ctx['function']}({params_sig})",
            f"\t\tParameters: {params_human or 'none'}",
            f"\t\tVisibility: {ctx['function_visibility'] or 'unknown'}",
            f"\t\tERC20Entry: {ctx['function_erc20_signature'] or 'no'}",
            f"\t\tReachableFromERC20: {', '.join(ctx['reachable_from']) or 'no'}",
            f"\t\tAssemblyBlock {block['block_id']}",
            f"\t\t\tSourceRange: line {pos['start_line']}:{pos['start_column']} to line {pos['end_line']}:{pos['end_column']}",
            f"\t\t\tOffsetRange: {pos['start_offset']}:{pos['end_offset']}",
            "\t\t\tMemoryTrackerScope: assembly_block_isolated + branch_sensitive_memory_ssa",
            "\t\tSemantic IRs:",
        ])

        for op in block["source_yul_semantic_ir"]:
            lines.extend(f"\t\t\t{line}" for line in semantic_ir_text(op))

        lines.append("\t\tSemantic Replacement View:")
        for replacement_line in block["semantic_replacement_view"]:
            lines.append(f"\t\t\t{replacement_line}")

        lines.append("\t\tSlithIR-SSA Adapter:")
        matched = block["slithir_ssa_adapter"]["matched_function"]
        lines.append(f"\t\t\tMatchedFunction: {matched or 'no'}")
        entries = block["slithir_ssa_adapter"]["relevant_entries"]
        if not entries:
            lines.append("\t\t\tNo relevant SlithIR-SSA entries.")
        for entry in entries:
            lines.append(f"\t\t\t[{entry['index']}] {entry['kind']}: {entry['expression']}")
            lines.append("\t\t\t\tIRs:")
            for ir in entry["irs"]:
                lines.append(f"\t\t\t\t\t{ir}")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract assembly blocks and build a semantic IR with optional SlithIR-SSA adaptation."
    )
    parser.add_argument("source", help="Solidity source file.")
    parser.add_argument("--slithir-ssa", help="Optional SlithIR-SSA text output.")
    parser.add_argument("-o", "--output", default="assembly_semantic_ir.txt", help="Text IR report path.")
    args = parser.parse_args()

    source_path = Path(args.source)
    if not source_path.is_file():
        print(f"Source file not found: {source_path}", file=sys.stderr)
        return 2

    slithir_path = Path(args.slithir_ssa) if args.slithir_ssa else None
    if slithir_path and not slithir_path.is_file():
        print(f"SlithIR-SSA file not found: {slithir_path}", file=sys.stderr)
        return 2

    report = build_report(source_path, slithir_path)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(format_text_ir(report), encoding="utf-8")

    print(f"Wrote text IR report: {output_path}")
    print(f"Assembly blocks found: {len(report['assembly_blocks'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
