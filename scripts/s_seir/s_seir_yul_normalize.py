#!/usr/bin/env python3
from __future__ import annotations

import re
from typing import Any

from assembly_arithmetic_compare_ir import (
    ParseError,
    division_guards_for_expression,
    render_yul_expression,
)
from assembly_semantic_ir import parse_int_literal, strip_ssa


def split_args(text: str) -> list[str]:
    out: list[str] = []
    cur: list[str] = []
    depth = 0
    for ch in text:
        if ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        cur.append(ch)
    if cur:
        out.append("".join(cur).strip())
    return out


def call_parts(expr: str) -> tuple[str | None, list[str]]:
    text = str(expr or "").strip()
    m = re.match(r"^([A-Za-z_$][A-Za-z0-9_$]*)\((.*)\)$", text)
    if not m:
        return None, []
    return m.group(1), split_args(m.group(2))


def int_text(value: str) -> int | None:
    return parse_int_literal(str(value).strip())


def normalize_expr(expr: Any, context: str = "value") -> str:
    """Normalize a Yul expression using the legacy parser-based renderer.

    S-SEIR keeps its own graph shape, but expression semantics should follow the
    original recovery module instead of a parallel lightweight string renderer.
    """

    text = strip_ssa(str(expr or "").strip()) or str(expr or "").strip()
    if not text:
        return text
    try:
        return render_yul_expression(text, context=context).text
    except Exception:
        return text


def invert_condition(expr: Any, type_env: Any | None = None) -> str:
    text = str(expr or "").strip()
    name, args = call_parts(text)
    if name == "iszero" and len(args) == 1:
        inner = args[0].strip()
        v = type_env.lookup(inner) if type_env is not None and hasattr(type_env, "lookup") else None
        if v and "address" in str(v.type_string):
            return f"{inner} != address(0)"
        inner_name, _inner_args = call_parts(inner)
        if inner_name in {"staticcall", "call", "delegatecall", "callcode"}:
            return f"({normalize_expr(inner)} != 0)"
        return normalize_expr(inner, context="condition")
    if name == "lt" and len(args) == 2:
        return f"({normalize_expr(args[0])} >= {normalize_expr(args[1])})"
    if name == "gt" and len(args) == 2:
        return f"({normalize_expr(args[0])} <= {normalize_expr(args[1])})"
    if name == "eq" and len(args) == 2:
        return f"({normalize_expr(args[0])} != {normalize_expr(args[1])})"
    return f"!({normalize_expr(text, context='condition')})"


def words_from_memory_query(query: dict[str, Any] | None, prefer_known_branch: bool = True) -> list[dict[str, Any]]:
    words = []
    for word in (query or {}).get("words", []) or []:
        item = dict(word)
        value = item.get("value")
        if prefer_known_branch and (value in {None, "unknown"}):
            known = [
                c
                for c in item.get("branch_candidates", []) or []
                if c.get("value") not in {None, "unknown"}
            ]
            if known:
                vals = []
                for cand in known:
                    if cand.get("value") not in vals:
                        vals.append(cand.get("value"))
                if len(vals) == 1:
                    item["value"] = vals[0]
                    item["discarded_unknown_branch"] = True
                    item["known_branch_candidates"] = known
                else:
                    item["value"] = vals
                    item["discarded_unknown_branch"] = True
                    item["known_branch_candidates"] = known
        words.append(item)
    return words


def abi_packed_word(value: str, size: str | None = None) -> str:
    n = int_text(size or "")
    if n == 20:
        name, args = call_parts(str(value))
        if name == "shl" and len(args) == 2 and int_text(args[0]) == 96:
            return f"abi.encodePacked(bytes20({normalize_expr(args[1])}))"
        return f"abi.encodePacked(bytes20({normalize_expr(value)}))"
    return f"abi.encodePacked({normalize_expr(value)})"


def division_guards(expr: Any) -> list[str]:
    try:
        return division_guards_for_expression(str(expr or ""))
    except Exception:
        return []
