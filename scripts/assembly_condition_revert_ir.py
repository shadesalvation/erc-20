#!/usr/bin/env python3
"""
Recover require-like semantics from assembly revert(0, 0) blocks.

Input is Solidity source only. Slither helper outputs are generated internally
through the storage pipeline, but this module focuses only on condition/revert
semantics and does not classify opaque predicates.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from assembly_recovery_pipeline import discover_binary, generate_slither_inputs
from assembly_semantic_ir import strip_ssa
from assembly_storage_ir import build_storage_report, format_control, yul_expr_to_solidity

SIDE_EFFECT_KINDS = {
    "storage_write",
    "event_log",
    "external_call",
    "memory_copy",
}


def simple_identifier(text: str) -> bool:
    return bool(text) and all(ch.isalnum() or ch == "_" for ch in text) and not text[0].isdigit()


def is_bool_like_expr(expr: str) -> bool:
    expr = expr.strip()
    return (
        expr.startswith(("eq(", "lt(", "gt(", "iszero(", "staticcall(", "call(", "delegatecall(", "callcode("))
        or " == " in expr
        or " < " in expr
        or " > " in expr
    )


def yul_negate_condition(condition: str, address_names: set[str] | None = None) -> str:
    address_names = address_names or set()
    condition = strip_ssa(condition.strip()) or condition.strip()
    if condition.startswith("iszero(") and condition.endswith(")"):
        inner = condition[len("iszero("):-1].strip()
        if simple_identifier(inner):
            if inner in address_names:
                return f"{inner} != address(0)"
            return f"{inner} != 0"
        solidity_inner = yul_expr_to_solidity(inner)
        if is_bool_like_expr(inner):
            return solidity_inner
        return f"{solidity_inner} != 0"
    return f"!({yul_expr_to_solidity(condition)})"


def require_for_conditions(conditions: list[str], address_names: set[str] | None = None) -> str:
    if not conditions:
        return "require(false);"
    if len(conditions) == 1:
        return f"require({yul_negate_condition(conditions[0], address_names)});"
    joined = " && ".join(f"({yul_expr_to_solidity(condition)})" for condition in conditions)
    return f"require(!({joined}));"


def inline_storage_substitutions(block: dict[str, Any]) -> list[tuple[str, str]]:
    substitutions: list[tuple[str, str]] = []
    for item in block.get("storage_recovery", {}).get("storage_ir", []):
        if item.get("section") != "storage_read" or not item.get("inline"):
            continue
        slot = item.get("slot")
        access = item.get("access")
        if slot and access:
            substitutions.append((f"sload({slot})", access))
    return substitutions


def apply_condition_substitutions(condition: str, substitutions: list[tuple[str, str]]) -> str:
    for old, new in substitutions:
        condition = condition.replace(old, strip_ssa(new) or new)
    return condition


def is_revert_zero(op: dict[str, Any]) -> bool:
    if op.get("kind") != "revert":
        return False
    args = [str(arg).strip() for arg in op.get("semantic", {}).get("args", [])]
    return args == ["0", "0"]


def condition_block_has_other_effective_ops(block_ops: list[dict[str, Any]], condition: str, revert_index: int) -> bool:
    for op in block_ops:
        if op["index"] == revert_index:
            continue
        if condition not in op.get("control_path", []):
            continue
        if op["kind"] not in {"condition", "revert"}:
            return True
    return False


def mergeable_conditions(block_ops: list[dict[str, Any]], control_path: list[str], revert_index: int) -> list[str]:
    if not control_path:
        return []
    selected = [control_path[-1]]
    for condition in reversed(control_path[:-1]):
        if condition_block_has_other_effective_ops(block_ops, condition, revert_index):
            break
        selected.insert(0, condition)
    return selected


def recover_reverts_for_block(block: dict[str, Any]) -> dict[str, Any]:
    ops = block["source_yul_semantic_ir"]
    address_names = {param["name"] for param in block["context"].get("function_parameters", []) if param.get("type") == "address" and param.get("name")}
    substitutions = inline_storage_substitutions(block)
    conditions = [
        {
            "op_index": op["index"],
            "condition": op["semantic"].get("condition"),
            "control_path": op.get("control_path", []),
            "yul": op["text"],
        }
        for op in ops
        if op.get("kind") == "condition"
    ]

    reverts = []
    for op in ops:
        if not is_revert_zero(op):
            continue
        control_path = op.get("control_path", [])
        nearest = control_path[-1] if control_path else None
        merged = mergeable_conditions(ops, control_path, op["index"])
        require_conditions = [apply_condition_substitutions(condition, substitutions) for condition in merged]
        discarded_ops = [
            {
                "op_index": prior["index"],
                "kind": prior["kind"],
                "yul": prior["text"],
            }
            for prior in ops
            if prior["index"] < op["index"]
            and prior["kind"] in SIDE_EFFECT_KINDS
            and all(condition in prior.get("control_path", []) for condition in merged)
        ]
        reverts.append({
            "op_index": op["index"],
            "args": op["semantic"].get("args", []),
            "nearest_condition": nearest,
            "control_path": control_path,
            "merged_conditions": merged,
            "require_conditions": require_conditions,
            "require_like": require_for_conditions(require_conditions or ([apply_condition_substitutions(nearest, substitutions)] if nearest else []), address_names),
            "discarded_before_revert": discarded_ops,
            "yul": op["text"],
        })

    return {
        "conditions": conditions,
        "reverts": reverts,
    }


def build_condition_revert_report(source_path: Path, slithir_path: Path | None, variables_order_path: Path | None) -> dict[str, Any]:
    report = build_storage_report(source_path, slithir_path, variables_order_path)
    for block in report["assembly_blocks"]:
        block["condition_revert_recovery"] = recover_reverts_for_block(block)
    report["condition_revert_note"] = "Only revert(0, 0) is converted to require-like semantics. Opaque predicate analysis is intentionally skipped."
    return report


def require_replacements_by_op(block: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        item["op_index"]: item
        for item in block["condition_revert_recovery"]["reverts"]
    }


def skipped_condition_indices(block: dict[str, Any]) -> set[int]:
    merged_conditions = set()
    for item in block["condition_revert_recovery"]["reverts"]:
        merged_conditions.update(item["merged_conditions"])
    indices = set()
    for op in block["source_yul_semantic_ir"]:
        if op["kind"] == "condition" and op["semantic"].get("condition") in merged_conditions:
            indices.add(op["index"])
    return indices


def format_processed_require_view(block: dict[str, Any]) -> list[str]:
    replacements = require_replacements_by_op(block)
    skipped_conditions = skipped_condition_indices(block)
    lines = ["assembly /* revert-to-require semantic view */ {"]
    for op in block["source_yul_semantic_ir"]:
        if op["index"] in skipped_conditions:
            continue
        indent = "    " + "    " * len(op.get("control_path", []))
        if op["index"] in replacements:
            require_item = replacements[op["index"]]
            merged_count = len(require_item.get("merged_conditions", []))
            require_indent_level = max(0, len(op.get("control_path", [])) - merged_count)
            require_indent = "    " + "    " * require_indent_level
            lines.append(f"{require_indent}{strip_ssa(require_item['require_like'])} // yul: {op['text']}")
            continue
        solidity_like = strip_ssa(op["semantic"].get("solidity_like", op["text"]))
        lines.append(f"{indent}{solidity_like} // yul: {op['text']}")
    lines.append("}")
    return lines


def format_condition_revert_text(report: dict[str, Any]) -> str:
    lines = [
        f"INFO:AssemblyConditionRevertIR:Source {report['source_file']}",
        f"INFO:AssemblyConditionRevertIR:SlithIR-SSA {report['slithir_ssa'] or 'not provided'}",
        f"INFO:AssemblyConditionRevertIR:VariablesOrder {report['variables_order'] or 'not provided'}",
        f"INFO:AssemblyConditionRevertIR:Assembly blocks {len(report['assembly_blocks'])}",
        f"INFO:AssemblyConditionRevertIR:Note {report['condition_revert_note']}",
        "",
    ]

    current_contract: str | None = None
    for block in report["assembly_blocks"]:
        ctx = block["context"]
        pos = ctx["assembly_position"]
        if ctx["contract"] != current_contract:
            current_contract = ctx["contract"]
            lines.append(f"Contract {current_contract}")

        recovery = block["condition_revert_recovery"]
        lines.extend([
            f"\tFunction {ctx['contract']}.{ctx['function']}",
            f"\t\tVisibility: {ctx['function_visibility'] or 'unknown'}",
            f"\t\tReachableFromERC20: {', '.join(ctx['reachable_from']) or 'no'}",
            f"\t\tAssemblyBlock {block['block_id']}",
            f"\t\t\tSourceRange: line {pos['start_line']}:{pos['start_column']} to line {pos['end_line']}:{pos['end_column']}",
            "\t\tConditions:",
        ])
        if not recovery["conditions"]:
            lines.append("\t\t\tNo conditions found.")
        for condition in recovery["conditions"]:
            lines.append(f"\t\t\t[{condition['op_index']}] CONDITION {strip_ssa(condition['condition'])}")
            lines.append(f"\t\t\t\tControlPath: {strip_ssa(format_control(condition['control_path']))}")
            lines.append(f"\t\t\t\tYul: {condition['yul']}")

        lines.append("\t\tRevert To Require:")
        if not recovery["reverts"]:
            lines.append("\t\t\tNo revert(0, 0) found.")
        for revert in recovery["reverts"]:
            lines.append(f"\t\t\t[{revert['op_index']}] REVERT args({', '.join(revert['args'])})")
            lines.append(f"\t\t\t\tNearestCondition: {strip_ssa(revert['nearest_condition']) or 'none'}")
            lines.append(f"\t\t\t\tMergedConditions: {strip_ssa(' && '.join(revert['merged_conditions'])) if revert['merged_conditions'] else 'none'}")
            lines.append(f"\t\t\t\tRequireLike: {strip_ssa(revert['require_like'])}")
            lines.append(f"\t\t\t\tControlPath: {strip_ssa(format_control(revert['control_path']))}")
            if revert["discarded_before_revert"]:
                lines.append("\t\t\t\tDiscardedBeforeRevert:")
                for discarded in revert["discarded_before_revert"]:
                    lines.append(f"\t\t\t\t\t[{discarded['op_index']}] {discarded['kind']}: {discarded['yul']}")
            lines.append(f"\t\t\t\tYul: {revert['yul']}")

        lines.extend([
            "\t\tProcessed Assembly View After Revert Recovery:",
            "\t\t```solidity",
        ])
        for line in format_processed_require_view(block):
            lines.append(f"\t\t{line}")
        lines.extend(["\t\t```", ""])

    return "\n".join(lines)


def run_from_source(source: Path, output: Path, slither_bin: str, solc_bin: str, workdir: Path, keep_generated: bool) -> dict[str, Any]:
    if keep_generated:
        generated_dir = output.parent / f"{output.stem}.slither"
        slithir_path, variables_path = generate_slither_inputs(source, slither_bin, solc_bin, workdir, generated_dir)
        return build_condition_revert_report(source, slithir_path, variables_path)
    with tempfile.TemporaryDirectory(prefix="condition_revert_") as tmp:
        slithir_path, variables_path = generate_slither_inputs(source, slither_bin, solc_bin, workdir, Path(tmp))
        return build_condition_revert_report(source, slithir_path, variables_path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recover require-like semantics from assembly revert(0, 0)."
    )
    parser.add_argument("source", help="Solidity source file.")
    parser.add_argument("-o", "--output", default="assembly_condition_revert_ir.txt", help="Text report path.")
    parser.add_argument("--slither-bin", help="Slither executable. Default: .venv/bin/slither or PATH slither.")
    parser.add_argument("--solc-bin", help="solc executable. Default: .venv/bin/solc or PATH solc.")
    parser.add_argument("--workdir", default=".", help="Working directory for Slither. Default: current directory.")
    parser.add_argument("--keep-generated", action="store_true", help="Keep generated Slither printer outputs next to the report.")
    args = parser.parse_args()

    source = Path(args.source)
    if not source.is_file():
        print(f"Source file not found: {source}", file=sys.stderr)
        return 2

    slither_bin = discover_binary(args.slither_bin, "SLITHER_BIN", [".venv/bin/slither"], "slither")
    solc_bin = discover_binary(args.solc_bin, "SOLC_BIN", [".venv/bin/solc"], "solc")
    if slither_bin is None:
        print("Slither executable not found. Use --slither-bin or set SLITHER_BIN.", file=sys.stderr)
        return 2
    if solc_bin is None:
        print("solc executable not found. Use --solc-bin or set SOLC_BIN.", file=sys.stderr)
        return 2

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    report = run_from_source(source, output, slither_bin, solc_bin, Path(args.workdir).resolve(), args.keep_generated)
    output.write_text(format_condition_revert_text(report), encoding="utf-8")

    print(f"Wrote condition/revert IR report: {output}")
    print(f"Assembly blocks found: {len(report['assembly_blocks'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
