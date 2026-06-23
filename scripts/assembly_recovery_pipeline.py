#!/usr/bin/env python3
"""
End-to-end inline assembly recovery pipeline.

Input: Solidity source only.
Internally:
  1. Runs Slither printers for slithir-ssa and variable-order.
  2. Reuses memory recovery and storage recovery modules.
  3. Prints each assembly block after memory/storage/revert/event/arithmetic semantic replacement.

The output is a txt view for analysis only. It is not compiled and does not
modify the Solidity source file.
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

from assembly_semantic_ir import strip_ssa
from assembly_storage_ir import build_storage_report, format_control, format_storage_text_ir


def discover_binary(explicit: str | None, env_name: str, local_candidates: list[str], path_name: str) -> str | None:
    if explicit:
        return explicit
    env_value = os.environ.get(env_name)
    if env_value:
        return env_value
    for candidate in local_candidates:
        if Path(candidate).is_file():
            return candidate
    return shutil.which(path_name)


def run_slither_printer(source: Path, printer: str, slither_bin: str, solc_bin: str | None, workdir: Path) -> str:
    cmd = [slither_bin, str(source), "--print", printer]
    if solc_bin:
        cmd.extend(["--solc", solc_bin])
    proc = subprocess.run(
        cmd,
        cwd=str(workdir),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Slither printer failed: {printer}\n"
            f"Command: {' '.join(cmd)}\n"
            f"Output:\n{proc.stdout}"
        )
    return proc.stdout


def generate_slither_inputs(source: Path, slither_bin: str, solc_bin: str | None, workdir: Path, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    slithir_text = run_slither_printer(source, "slithir-ssa", slither_bin, solc_bin, workdir)
    variables_text = run_slither_printer(source, "variable-order", slither_bin, solc_bin, workdir)

    slithir_path = out_dir / "slithir_ssa.generated.txt"
    variables_path = out_dir / "variables_order.generated.txt"
    slithir_path.write_text(slithir_text, encoding="utf-8")
    variables_path.write_text(variables_text, encoding="utf-8")
    return slithir_path, variables_path


def storage_replacements_by_op(block: dict[str, Any]) -> dict[int, list[str]]:
    by_op: dict[int, list[str]] = {}
    for item in block["storage_recovery"]["storage_ir"]:
        op_index = item["op_index"]
        if item["section"] == "slot_computation":
            line = f"{strip_ssa(item['target']) or '_'} = slot({strip_ssa(item['expression'])});"
        elif item["section"] == "storage_read" and not item.get("inline"):
            line = strip_ssa(item["solidity_like"])
        elif item["section"] == "storage_write":
            line = strip_ssa(item["solidity_like"])
        else:
            continue
        by_op.setdefault(op_index, []).append(line)
    return by_op


def inline_read_substitutions_by_op(block: dict[str, Any]) -> dict[int, list[tuple[str, str]]]:
    by_op: dict[int, list[tuple[str, str]]] = {}
    for item in block["storage_recovery"]["storage_ir"]:
        if item["section"] != "storage_read" or not item.get("inline"):
            continue
        slot = item.get("slot")
        access = item.get("access")
        if slot and access:
            by_op.setdefault(item["op_index"], []).append((f"sload({slot})", access))
    return by_op


def apply_inline_read_substitutions(line: str, substitutions: list[tuple[str, str]]) -> str:
    for old, new in substitutions:
        line = line.replace(old, new)
    return line


def require_replacements_by_op(block: dict[str, Any]) -> dict[int, dict[str, Any]]:
    recovery = block.get("condition_revert_recovery", {})
    return {
        item["op_index"]: item
        for item in recovery.get("reverts", [])
    }


def event_replacements_by_op(block: dict[str, Any]) -> dict[int, dict[str, Any]]:
    from assembly_event_ir import event_replacements_by_op as collect_event_replacements

    return collect_event_replacements(block, min_confidence="low")


def arithmetic_replacements_by_op(block: dict[str, Any]) -> dict[int, dict[str, Any]]:
    from assembly_arithmetic_compare_ir import arithmetic_replacements_by_op as collect_arithmetic_replacements

    return collect_arithmetic_replacements(block)


def skipped_condition_indices(block: dict[str, Any]) -> set[int]:
    recovery = block.get("condition_revert_recovery", {})
    merged_conditions = set()
    for item in recovery.get("reverts", []):
        merged_conditions.update(item.get("merged_conditions", []))
    indices = set()
    for op in block["source_yul_semantic_ir"]:
        if op["kind"] == "condition" and op["semantic"].get("condition") in merged_conditions:
            indices.add(op["index"])
    return indices


def build_processed_block_view(block: dict[str, Any]) -> list[str]:
    storage_replacements = storage_replacements_by_op(block)
    require_replacements = require_replacements_by_op(block)
    event_replacements = event_replacements_by_op(block)
    arithmetic_replacements = arithmetic_replacements_by_op(block)
    skip_conditions = skipped_condition_indices(block)
    inline_substitutions = inline_read_substitutions_by_op(block)
    lines = ["assembly /* recovered semantic view */ {"]
    for op in block["source_yul_semantic_ir"]:
        if op["index"] in skip_conditions:
            continue
        indent = "    " + "    " * len(op.get("control_path", []))
        if op["index"] in require_replacements:
            require_item = require_replacements[op["index"]]
            merged_count = len(require_item.get("merged_conditions", []))
            require_indent_level = max(0, len(op.get("control_path", [])) - merged_count)
            require_indent = "    " + "    " * require_indent_level
            for guard in require_item.get("division_guards", []):
                lines.append(f"{require_indent}{guard} // inserted for Solidity division semantics")
            lines.append(f"{require_indent}{require_item['require_like']} // yul: {op['text']}")
            continue
        arithmetic_item = arithmetic_replacements.get(op["index"])
        if arithmetic_item:
            for guard in arithmetic_item.get("division_guards", []):
                lines.append(f"{indent}{guard} // inserted for Solidity division semantics")
        if op["index"] in event_replacements:
            event_item = event_replacements[op["index"]]
            note = f" /* confidence={event_item['confidence']} */" if event_item.get("confidence") != "high" else ""
            lines.append(f"{indent}{event_item['emit_like']}{note} // yul: {op['text']}")
            continue
        chosen = storage_replacements.get(op["index"])
        if chosen:
            for replacement in chosen:
                replacement = apply_inline_read_substitutions(replacement, inline_substitutions.get(op["index"], []))
                lines.append(f"{indent}{replacement} // yul: {op['text']}")
            continue
        if arithmetic_item and arithmetic_item.get("solidity_like"):
            solidity_like = strip_ssa(arithmetic_item["solidity_like"])
        else:
            solidity_like = strip_ssa(op["semantic"].get("solidity_like", op["text"]))
        solidity_like = apply_inline_read_substitutions(solidity_like, inline_substitutions.get(op["index"], []))
        lines.append(f"{indent}{solidity_like} // yul: {op['text']}")
    lines.append("}")
    return lines


def attach_arithmetic_recovery(report: dict[str, Any]) -> None:
    from assembly_arithmetic_compare_ir import attach_arithmetic_recovery as attach

    attach(report)


def attach_condition_revert_recovery(report: dict[str, Any]) -> None:
    from assembly_condition_revert_ir import recover_reverts_for_block

    for block in report["assembly_blocks"]:
        block["condition_revert_recovery"] = recover_reverts_for_block(block)


def attach_event_recovery(report: dict[str, Any], source: Path) -> None:
    from assembly_event_ir import attach_event_recovery as attach

    attach(report, source)


def format_pipeline_report(report: dict[str, Any], slithir_path: Path, variables_path: Path, keep_generated: bool) -> str:
    lines = [
        f"INFO:AssemblyRecoveryPipeline:Source {report['source_file']}",
        "INFO:AssemblyRecoveryPipeline:Input Solidity source only",
        f"INFO:AssemblyRecoveryPipeline:GeneratedSlithIRSSA {slithir_path if keep_generated else 'internal temporary file'}",
        f"INFO:AssemblyRecoveryPipeline:GeneratedVariablesOrder {variables_path if keep_generated else 'internal temporary file'}",
        f"INFO:AssemblyRecoveryPipeline:Assembly blocks {len(report['assembly_blocks'])}",
        "INFO:AssemblyRecoveryPipeline:Note output is a semantic view; source is not modified and output is not compiled",
        "",
    ]

    current_contract: str | None = None
    for block in report["assembly_blocks"]:
        ctx = block["context"]
        pos = ctx["assembly_position"]
        if ctx["contract"] != current_contract:
            current_contract = ctx["contract"]
            lines.append(f"Contract {current_contract}")

        lines.extend([
            f"\tFunction {ctx['contract']}.{ctx['function']}",
            f"\t\tVisibility: {ctx['function_visibility'] or 'unknown'}",
            f"\t\tReachableFromERC20: {', '.join(ctx['reachable_from']) or 'no'}",
            f"\t\tAssemblyBlock {block['block_id']}",
            f"\t\t\tSourceRange: line {pos['start_line']}:{pos['start_column']} to line {pos['end_line']}:{pos['end_column']}",
            "\t\tOriginal Assembly:",
            "\t\t```solidity",
        ])
        for line in block["assembly_source"].splitlines():
            lines.append(f"\t\t{line}")
        lines.extend([
            "\t\t```",
            "\t\tProcessed Assembly View After Memory + Storage + Revert + Event + Arithmetic/Comparison Recovery:",
            "\t\t```solidity",
        ])
        for line in build_processed_block_view(block):
            lines.append(f"\t\t{line}")
        lines.extend([
            "\t\t```",
            "\t\tRecovered Revert Summary:",
        ])
        revert_recovery = block.get("condition_revert_recovery", {})
        if not revert_recovery.get("reverts"):
            lines.append("\t\t\tNo revert(0, 0) recovery entries.")
        for item in revert_recovery.get("reverts", []):
            lines.append(f"\t\t\t[{item['op_index']}] {item['require_like']} control={format_control(item['control_path'])}")
        lines.append("\t\tRecovered Event Summary:")
        event_recovery = block.get("event_recovery", {})
        if not event_recovery.get("events") and not event_recovery.get("unmatched_logs"):
            lines.append("\t\t\tNo log/event operations.")
        for item in event_recovery.get("events", []):
            note = f" notes={','.join(item['notes'])}" if item.get("notes") else ""
            lines.append(f"\t\t\t[{item['op_index']}] {item['emit_like']} signature={item['signature']} confidence={item['confidence']}{note}")
        for item in event_recovery.get("unmatched_logs", []):
            lines.append(f"\t\t\t[{item['op_index']}] unmatched {item['op']} topics={item['topics']} data={item['data']}")
        lines.append("\t\tRecovered Arithmetic/Comparison Summary:")
        arithmetic_recovery = block.get("arithmetic_compare_recovery", {})
        if not arithmetic_recovery.get("entries"):
            lines.append("\t\t\tNo arithmetic/comparison recovery entries.")
        for item in arithmetic_recovery.get("entries", []):
            guards = f" guards={' '.join(item['division_guards'])}" if item.get("division_guards") else ""
            helpers = f" helpers={','.join(item['helpers'])}" if item.get("helpers") else ""
            display = item.get("solidity_like") or item["expression"]
            lines.append(f"\t\t\t[{item['op_index']}] {display}{guards}{helpers}")
        lines.append("\t\tRecovered Storage Summary:")
        recovery = block["storage_recovery"]
        if not recovery["storage_ir"]:
            lines.append("\t\t\tNo storage recovery entries.")
        for item in recovery["storage_ir"]:
            if item["section"] == "slot_computation":
                lines.append(f"\t\t\t[{item['op_index']}] SLOT {strip_ssa(item['target']) or '_'} := {strip_ssa(item['expression'])}")
            elif item["section"] == "storage_read":
                lines.append(f"\t\t\t[{item['op_index']}] READ {strip_ssa(item['solidity_like'])} control={format_control(item['control_path'])}")
            elif item["section"] == "storage_write":
                flag = " unchecked" if item.get("unchecked_arithmetic_candidate") else ""
                summary_line = strip_ssa(item["solidity_like"])
                lines.append(f"\t\t\t[{item['op_index']}] WRITE{flag}: {summary_line} control={format_control(item['control_path'])}")
        lines.append("")

    from assembly_arithmetic_compare_ir import format_arithmetic_text_ir

    lines.extend([
        "----- Full Storage IR -----",
        format_storage_text_ir(report),
        "----- Full Arithmetic/Comparison IR -----",
        format_arithmetic_text_ir(report),
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the full assembly memory/storage recovery pipeline from Solidity source only."
    )
    parser.add_argument("source", help="Solidity source file.")
    parser.add_argument("-o", "--output", default="assembly_recovery_pipeline.txt", help="Pipeline text report path.")
    parser.add_argument("--slither-bin", help="Slither executable. Default: .venv/bin/slither or PATH slither.")
    parser.add_argument("--solc-bin", help="solc executable. Default: .venv/bin/solc or PATH solc.")
    parser.add_argument("--workdir", default=".", help="Working directory for Slither. Default: current directory.")
    parser.add_argument("--keep-generated", action="store_true", help="Keep generated Slither printer outputs next to the report.")
    args = parser.parse_args()

    source = Path(args.source)
    if not source.is_file():
        print(f"Source file not found: {source}", file=sys.stderr)
        return 2

    workdir = Path(args.workdir).resolve()
    output = Path(args.output)
    slither_bin = discover_binary(args.slither_bin, "SLITHER_BIN", [".venv/bin/slither"], "slither")
    solc_bin = discover_binary(args.solc_bin, "SOLC_BIN", [".venv/bin/solc"], "solc")
    if slither_bin is None:
        print("Slither executable not found. Use --slither-bin or set SLITHER_BIN.", file=sys.stderr)
        return 2
    if solc_bin is None:
        print("solc executable not found. Use --solc-bin or set SOLC_BIN.", file=sys.stderr)
        return 2

    output.parent.mkdir(parents=True, exist_ok=True)

    if args.keep_generated:
        generated_dir = output.parent / f"{output.stem}.slither"
        slithir_path, variables_path = generate_slither_inputs(source, slither_bin, solc_bin, workdir, generated_dir)
        report = build_storage_report(source, slithir_path, variables_path)
        attach_arithmetic_recovery(report)
        attach_condition_revert_recovery(report)
        attach_event_recovery(report, source)
        output.write_text(format_pipeline_report(report, slithir_path, variables_path, True), encoding="utf-8")
    else:
        with tempfile.TemporaryDirectory(prefix="assembly_pipeline_") as tmp:
            tmp_dir = Path(tmp)
            slithir_path, variables_path = generate_slither_inputs(source, slither_bin, solc_bin, workdir, tmp_dir)
            report = build_storage_report(source, slithir_path, variables_path)
            attach_arithmetic_recovery(report)
            attach_condition_revert_recovery(report)
            attach_event_recovery(report, source)
            output.write_text(format_pipeline_report(report, slithir_path, variables_path, False), encoding="utf-8")

    print(f"Wrote assembly recovery pipeline report: {output}")
    print(f"Assembly blocks found: {len(report['assembly_blocks'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
