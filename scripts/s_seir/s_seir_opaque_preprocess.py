#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path as _SSEIRPath
import sys as _sseir_sys

_SSEIR_ROOT = _SSEIRPath(__file__).resolve().parents[1]
for _sseir_path in (_SSEIR_ROOT / "legacy_yul", _SSEIR_ROOT / "s_seir"):
    _sseir_text = str(_sseir_path)
    if _sseir_text not in _sseir_sys.path:
        _sseir_sys.path.insert(0, _sseir_text)

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assembly_ast_cfg import compile_source_ast, extract_inline_assembly_blocks, parse_src, yul_expression
from assembly_semantic_ir import parse_int_literal
from s_seir_yul_normalize import call_parts, normalize_expr


Json = dict[str, Any]


@dataclass
class OpaqueRewrite:
    start: int
    end: int
    replacement: str
    condition: str
    value: bool
    reason: str


@dataclass
class OpaquePreprocessResult:
    source: str
    rewrite_count: int
    report: str


def prune_opaque_yul_ifs(source_path: Path, solc_bin: str) -> OpaquePreprocessResult:
    source = source_path.read_text(encoding="utf-8")
    ast = compile_source_ast(source_path, solc_bin)
    rewrites: list[OpaqueRewrite] = []
    report = [
        "INFO:SSEIROpaquePreprocess:algorithm AST-located conservative Yul opaque-if pruning",
        "INFO:SSEIROpaquePreprocess:rules syntactic algebraic identities only",
    ]
    for block in extract_inline_assembly_blocks(ast, source_path):
        for node in iter_yul_nodes(block.yul_ast):
            if node.get("nodeType") != "YulIf":
                continue
            condition = yul_expression(node.get("condition"))
            folded = fold_opaque_condition(condition)
            if folded is None:
                continue
            value, reason = folded
            start, end = parse_src(str(node.get("src", "")))
            if start < 0 or end <= start:
                continue
            if value:
                body_start, body_end = parse_src(str((node.get("body") or {}).get("src", "")))
                replacement = yul_block_inner_source(source[body_start:body_end])
            else:
                replacement = ""
            rewrites.append(OpaqueRewrite(start, end, replacement, condition, value, reason))
            report.append(
                f"OpaqueIf: {block.context.label()} src={node.get('src')} "
                f"value={'true' if value else 'false'} reason={reason} condition={condition}"
            )

    accepted: list[OpaqueRewrite] = []
    for rewrite in sorted(rewrites, key=lambda item: (item.start, item.end)):
        if accepted and rewrite.start < accepted[-1].end:
            continue
        accepted.append(rewrite)

    rewritten = source
    for rewrite in reversed(accepted):
        rewritten = rewritten[:rewrite.start] + rewrite.replacement + rewritten[rewrite.end:]
    if not accepted:
        report.append("OpaqueIf: none")
    return OpaquePreprocessResult(rewritten, len(accepted), "\n".join(report))


def iter_yul_nodes(value: Any):
    if isinstance(value, dict):
        if isinstance(value.get("nodeType"), str):
            yield value
        for item in value.values():
            yield from iter_yul_nodes(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_yul_nodes(item)


def yul_block_inner_source(block_source: str) -> str:
    text = block_source.strip()
    if text.startswith("{") and text.endswith("}"):
        return text[1:-1].strip("\n")
    return block_source


def fold_opaque_condition(expr: Any) -> tuple[bool, str] | None:
    text = str(expr or "").strip()
    if not text:
        return None
    call, args = call_parts(text)
    if call == "iszero" and len(args) == 1:
        inner = fold_opaque_condition(args[0])
        if inner is not None:
            return (not inner[0], f"iszero({inner[1]})")
        if is_zero_identity(args[0]):
            return True, "zero_identity"
        return None
    if call == "eq" and len(args) == 2:
        if same_expr(args[0], args[1]):
            return True, "same_expression"
        if equivalent_expr(args[0], args[1]):
            return True, "algebraic_identity"
        left_zero = is_zero_identity(args[0])
        right_zero = is_zero_identity(args[1])
        if left_zero and is_zero_literal(args[1]):
            return True, "zero_identity_eq_zero"
        if right_zero and is_zero_literal(args[0]):
            return True, "zero_identity_eq_zero"
        return None
    if is_zero_identity(text):
        return False, "zero_identity_as_condition"
    return None


def equivalent_expr(left: Any, right: Any) -> bool:
    if same_expr(left, right):
        return True
    left_call, left_args = call_parts(str(left or "").strip())
    right_call, right_args = call_parts(str(right or "").strip())
    if left_call in {"add", "or", "xor"} and len(left_args) == 2 and same_expr(right, nonzero_arg_if_other_zero(left_args)):
        return True
    if right_call in {"add", "or", "xor"} and len(right_args) == 2 and same_expr(left, nonzero_arg_if_other_zero(right_args)):
        return True
    if left_call == "and" and len(left_args) == 2 and same_expr(left_args[0], left_args[1]) and same_expr(left_args[0], right):
        return True
    if right_call == "and" and len(right_args) == 2 and same_expr(right_args[0], right_args[1]) and same_expr(right_args[0], left):
        return True
    if left_call == right_call == "mul" and len(left_args) == len(right_args) == 2:
        return same_expr(left_args[0], right_args[1]) and same_expr(left_args[1], right_args[0])
    return False


def is_zero_identity(expr: Any) -> bool:
    call, args = call_parts(str(expr or "").strip())
    if call in {"sub", "xor"} and len(args) == 2 and same_expr(args[0], args[1]):
        return True
    if call in {"mul", "and"} and len(args) == 2:
        return any(is_zero_literal(arg) for arg in args)
    return False


def nonzero_arg_if_other_zero(args: list[str]) -> str | None:
    if len(args) != 2:
        return None
    if is_zero_literal(args[0]):
        return args[1]
    if is_zero_literal(args[1]):
        return args[0]
    return None


def same_expr(left: Any, right: Any) -> bool:
    return normalize_expr(left) == normalize_expr(right)


def is_zero_literal(value: Any) -> bool:
    parsed = parse_int_literal(str(value or "").strip())
    return parsed == 0
