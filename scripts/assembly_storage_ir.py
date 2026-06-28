#!/usr/bin/env python3
"""
Recover storage slot computations, storage reads, and storage writes from inline assembly.

All keccak256 slot recovery consumes CFG path-sensitive MemorySSA facts through
assembly_cfg_memory_adapter. It then applies static Solidity storage rules and
optional Slither variables_order metadata.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from assembly_cfg_memory_adapter import build_cfg_memory_report
from assembly_arithmetic_compare_ir import ParseError, render_yul_expression
from assembly_semantic_ir import (
    memory_words_text,
    parse_call,
    parse_int_literal,
    param_display,
    param_signature,
    strip_ssa,
)


ARITHMETIC_INFIX = {
    "add": "+",
    "sub": "-",
    "mul": "*",
    "div": "/",
    "mod": "%",
    "and": "&",
    "or": "|",
    "xor": "^",
    "shl": "<<",
    "shr": ">>",
    "lt": "<",
    "gt": ">",
    "eq": "==",
}


class StateLayout:
    def __init__(self) -> None:
        self.by_contract: dict[str, list[dict[str, Any]]] = {}
        self.by_contract_slot: dict[tuple[str, int], dict[str, Any]] = {}
        self.by_contract_name: dict[tuple[str, str], dict[str, Any]] = {}

    def add(self, contract: str, full_name: str, type_name: str, slot: int, offset: int) -> None:
        name = full_name.split(".")[-1]
        item = {
            "contract": contract,
            "full_name": full_name,
            "name": name,
            "type": type_name,
            "slot": slot,
            "offset": offset,
            "is_mapping": type_name.strip().startswith("mapping"),
        }
        self.by_contract.setdefault(contract, []).append(item)
        self.by_contract_slot[(contract, slot)] = item
        self.by_contract_name[(contract, name)] = item
        self.by_contract_name[(contract, full_name)] = item

    def resolve_ref(self, contract: str, ref: str | None) -> dict[str, Any] | None:
        if ref is None:
            return None
        text = ref.strip()
        value = parse_int_literal(text)
        if value is not None:
            return self.by_contract_slot.get((contract, value))
        if text.endswith(".slot"):
            name = text[:-5]
            if "." in name:
                name = name.split(".")[-1]
            return self.by_contract_name.get((contract, name))
        if "." in text:
            maybe_name = text.split(".")[-1]
            found = self.by_contract_name.get((contract, maybe_name))
            if found:
                return found
        return self.by_contract_name.get((contract, text))

    def format_state_ref(self, item: dict[str, Any] | None) -> str | None:
        if not item:
            return None
        return item["name"]


class EmptyLayout(StateLayout):
    pass


def parse_variables_order(path: Path | None) -> StateLayout:
    layout = StateLayout()
    if path is None:
        return layout
    text = path.read_text(encoding="utf-8")
    current_contract: str | None = None
    contract_header = re.compile(r"^([A-Za-z_$][A-Za-z0-9_$]*)\s*:$")
    table_row = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([0-9]+)\s*\|\s*([0-9]+)\s*\|\s*([^|]+?)\s*\|$")

    for raw_line in text.splitlines():
        line = raw_line.strip()
        header_match = contract_header.match(line)
        if header_match:
            current_contract = header_match.group(1)
            continue
        row_match = table_row.match(line)
        if not row_match or current_contract is None:
            continue
        full_name, type_name, slot_text, offset_text, state = row_match.groups()
        if full_name == "Name" or state.strip() != "Storage":
            continue
        layout.add(current_contract, full_name.strip(), type_name.strip(), int(slot_text), int(offset_text))

    return layout


def yul_expr_to_solidity(expr: str) -> str:
    expr = re.sub(r"__ssa\d+", "", expr.strip())
    try:
        return render_yul_expression(expr).text
    except ParseError:
        pass
    call = parse_call(expr)
    if not call:
        return strip_ssa(expr) or expr
    op, args = call
    if op in ARITHMETIC_INFIX and len(args) == 2:
        return f"{yul_expr_to_solidity(args[0])} {ARITHMETIC_INFIX[op]} {yul_expr_to_solidity(args[1])}"
    if op == "iszero" and len(args) == 1:
        return f"!({yul_expr_to_solidity(args[0])})"
    if op == "not" and len(args) == 1:
        return f"~{yul_expr_to_solidity(args[0])}"
    return f"{op}({', '.join(yul_expr_to_solidity(arg) for arg in args)})"


def input_value(words: list[dict[str, Any]], index: int) -> str | None:
    if index >= len(words):
        return None
    value = words[index].get("value")
    if value is None:
        return None
    return str(value)


def extract_sload_slots(statement: str) -> list[str]:
    slots: list[str] = []
    pattern = "sload("
    i = 0
    while True:
        start = statement.find(pattern, i)
        if start == -1:
            break
        arg_start = start + len(pattern)
        depth = 1
        j = arg_start
        while j < len(statement) and depth > 0:
            if statement[j] == "(":
                depth += 1
            elif statement[j] == ")":
                depth -= 1
            j += 1
        if depth == 0:
            slots.append(statement[arg_start:j - 1].strip())
            i = j
        else:
            break
    return slots


def make_storage_read(contract: str, op: dict[str, Any], slot: str, target: str | None, layout: StateLayout, slot_symbols: dict[str, dict[str, Any]]) -> dict[str, Any]:
    slot_calc = slot_symbols.get(slot or "")
    direct_state = layout.resolve_ref(contract, slot)
    if slot_calc:
        access = slot_calc["expression"]
        state_variable = slot_calc.get("state_variable")
        read_kind = slot_calc["kind"].replace("_slot", "_read")
    elif direct_state:
        access = direct_state["name"]
        state_variable = direct_state["name"]
        read_kind = "state_variable_read"
    else:
        access = f"sload({slot})"
        state_variable = None
        read_kind = "raw_storage_read"
    return {
        "op_index": op["index"],
        "target": target,
        "kind": read_kind,
        "slot": slot,
        "state_variable": state_variable,
        "access": access,
        "solidity_like": f"{target} = {access};" if target else f"read {access};",
        "yul": op["text"],
        "control_path": op.get("control_path", []),
        "inline": target is None,
    }


def slot_ref_text(slot_info: dict[str, Any] | None, raw: str) -> str:
    if not slot_info:
        return raw
    return f"{slot_info['name']}.slot"


def recover_storage_for_block(block: dict[str, Any], layout: StateLayout) -> dict[str, Any]:
    contract = block["context"]["contract"]
    hash_candidates: dict[str, dict[str, Any]] = {}
    slot_symbols: dict[str, dict[str, Any]] = {}
    activated_slots: set[str] = set()
    slot_computations: list[dict[str, Any]] = []
    storage_reads: list[dict[str, Any]] = []
    storage_writes: list[dict[str, Any]] = []
    storage_ir: list[dict[str, Any]] = []

    # First pass: collect hash computations that could become storage slots.
    # They are only candidates here. A candidate is emitted later only if a
    # storage operation actually consumes it as a slot.
    for op in block["source_yul_semantic_ir"]:
        if op["kind"] != "memory_hash":
            continue
        semantic = op["semantic"]
        target = semantic.get("target")
        if not target:
            continue
        inputs = semantic.get("resolved_inputs", [])
        key = input_value(inputs, 0)
        base = input_value(inputs, 1)
        base_state = layout.resolve_ref(contract, base)
        parent = hash_candidates.get(base or "")
        expression = None
        state_var = None
        slot_kind = None
        parent_target = None

        if base_state:
            state_var = base_state["name"]
            if base_state["is_mapping"] and key is not None:
                expression = f"{state_var}[{yul_expr_to_solidity(key or 'unknown')}]"
                slot_kind = "mapping_slot"
            else:
                expression = f"keccak256({yul_expr_to_solidity(key or 'unknown')}, {base_state['name']}.slot)"
                slot_kind = "hash_with_state_base"
        elif parent and key is not None:
            state_var = parent.get("state_variable")
            expression = f"{parent['expression']}[{yul_expr_to_solidity(key or 'unknown')}]"
            slot_kind = "nested_mapping_slot"
            parent_target = base

        if slot_kind is None:
            continue

        hash_candidates[target] = {
            "op_index": op["index"],
            "target": target,
            "kind": slot_kind,
            "key": key,
            "base": base,
            "parent_target": parent_target,
            "state_variable": state_var,
            "expression": expression,
            "resolved_inputs": inputs,
            "yul": op["text"],
            "control_path": op.get("control_path", []),
        }

    def activate_slot(slot: str | None) -> None:
        if not slot or slot in activated_slots:
            return
        calc = hash_candidates.get(slot)
        if not calc:
            return
        parent_target = calc.get("parent_target")
        if parent_target:
            activate_slot(parent_target)
        activated_slots.add(slot)
        slot_symbols[slot] = calc
        slot_computations.append(calc)
        storage_ir.append({"section": "slot_computation", **calc})

    for op in block["source_yul_semantic_ir"]:
        kind = op["kind"]
        semantic = op["semantic"]

        if kind != "storage_read":
            for inline_slot in extract_sload_slots(op.get("ssa_text", op["text"])):
                activate_slot(inline_slot)
                inline_read = make_storage_read(contract, op, inline_slot, None, layout, slot_symbols)
                inline_read["kind"] = "inline_" + inline_read["kind"]
                storage_reads.append(inline_read)
                storage_ir.append({"section": "storage_read", **inline_read})

        if kind == "storage_read":
            target = semantic.get("target")
            slot = semantic.get("slot")
            activate_slot(slot)
            read = make_storage_read(contract, op, slot, target, layout, slot_symbols)
            storage_reads.append(read)
            storage_ir.append({"section": "storage_read", **read})
            continue

        if kind == "storage_write":
            slot = semantic.get("slot")
            value = semantic.get("value")
            activate_slot(slot)
            slot_calc = slot_symbols.get(slot or "")
            direct_state = layout.resolve_ref(contract, slot)
            if slot_calc:
                access = slot_calc["expression"]
                state_variable = slot_calc.get("state_variable")
                write_kind = slot_calc["kind"].replace("_slot", "_write")
            elif direct_state:
                access = direct_state["name"]
                state_variable = direct_state["name"]
                write_kind = "state_variable_write"
            else:
                access = f"storage[{slot}]"
                state_variable = None
                write_kind = "raw_storage_write"

            value_sol = yul_expr_to_solidity(value or "unknown")
            unchecked = semantic.get("unchecked_arithmetic_candidate", False)
            solidity_like = f"{access} = {value_sol};"
            if unchecked:
                solidity_like = f"unchecked {{ {solidity_like} }}"
            write = {
                "op_index": op["index"],
                "kind": write_kind,
                "slot": slot,
                "value": value,
                "value_solidity": value_sol,
                "state_variable": state_variable,
                "access": access,
                "unchecked_arithmetic_candidate": unchecked,
                "solidity_like": solidity_like,
                "yul": op["text"],
                "control_path": op.get("control_path", []),
            }
            storage_writes.append(write)
            storage_ir.append({"section": "storage_write", **write})
            continue

    storage_ir.sort(key=lambda item: (item["op_index"], {"slot_computation": 0, "storage_read": 1, "storage_write": 2}.get(item["section"], 9)))
    slot_computations.sort(key=lambda item: item["op_index"])
    storage_reads.sort(key=lambda item: item["op_index"])
    storage_writes.sort(key=lambda item: item["op_index"])

    return {
        "slot_computations": slot_computations,
        "storage_reads": storage_reads,
        "storage_writes": storage_writes,
        "storage_ir": storage_ir,
    }


def build_storage_report(source_path: Path, slithir_path: Path | None, variables_order_path: Path | None, solc_bin: str | None = None) -> dict[str, Any]:
    base_report = build_cfg_memory_report(source_path, slithir_path, solc_bin)
    layout = parse_variables_order(variables_order_path)
    for block in base_report["assembly_blocks"]:
        block["storage_recovery"] = recover_storage_for_block(block, layout)
    base_report["variables_order"] = str(variables_order_path) if variables_order_path else None
    base_report["storage_note"] = "Storage recovery uses static keccak256(key, baseSlot), sload, and sstore rules over each assembly-block-local CFG MemorySSA result."
    return base_report


def format_control(control_path: list[str]) -> str:
    return " -> ".join(control_path) if control_path else "none"


def format_storage_text_ir(report: dict[str, Any]) -> str:
    lines = [
        f"INFO:AssemblyStorageIR:Source {report['source_file']}",
        f"INFO:AssemblyStorageIR:SlithIR-SSA {report['slithir_ssa'] or 'not provided'}",
        f"INFO:AssemblyStorageIR:VariablesOrder {report['variables_order'] or 'not provided'}",
        f"INFO:AssemblyStorageIR:Assembly blocks {len(report['assembly_blocks'])}",
        f"INFO:AssemblyStorageIR:Note {report['storage_note']}",
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
            f"\tFunction {ctx['contract']}.{ctx['function']}({param_signature(ctx['function_parameters'])})",
            f"\t\tParameters: {param_display(ctx['function_parameters']) or 'none'}",
            f"\t\tVisibility: {ctx['function_visibility'] or 'unknown'}",
            f"\t\tReachableFromERC20: {', '.join(ctx['reachable_from']) or 'no'}",
            f"\t\tAssemblyBlock {block['block_id']}",
            f"\t\t\tSourceRange: line {pos['start_line']}:{pos['start_column']} to line {pos['end_line']}:{pos['end_column']}",
            "\t\t\tMemoryTrackerScope: assembly_block_isolated + branch_sensitive_memory_ssa",
            "\t\tStorage Slot Computations:",
        ])

        recovery = block["storage_recovery"]
        if not recovery["slot_computations"]:
            lines.append("\t\t\tNo slot computations recovered.")
        for calc in recovery["slot_computations"]:
            lines.append(f"\t\t\t[{calc['op_index']}] {calc['kind'].upper()}: {strip_ssa(calc['target']) or '_'} := {strip_ssa(calc['expression'])}")
            lines.append(f"\t\t\t\tKey: {strip_ssa(calc['key']) or 'unknown'}")
            lines.append(f"\t\t\t\tBase: {strip_ssa(calc['base']) or 'unknown'}")
            lines.append(f"\t\t\t\tStateVariable: {calc['state_variable'] or 'unknown'}")
            lines.append(f"\t\t\t\tResolvedInputs: {memory_words_text(calc['resolved_inputs'])}")
            lines.append(f"\t\t\t\tControlPath: {format_control(calc['control_path'])}")
            lines.append(f"\t\t\t\tYul: {calc['yul']}")

        lines.append("\t\tStorage Reads:")
        if not recovery["storage_reads"]:
            lines.append("\t\t\tNo storage reads recovered.")
        for read in recovery["storage_reads"]:
            lines.append(f"\t\t\t[{read['op_index']}] {read['kind'].upper()}: {strip_ssa(read['target']) or '_'} := {strip_ssa(read['access'])}")
            lines.append(f"\t\t\t\tSlot: {strip_ssa(read['slot'])}")
            lines.append(f"\t\t\t\tStateVariable: {read['state_variable'] or 'unknown'}")
            lines.append(f"\t\t\t\tSolidityLike: {strip_ssa(read['solidity_like'])}")
            lines.append(f"\t\t\t\tControlPath: {format_control(read['control_path'])}")
            lines.append(f"\t\t\t\tYul: {read['yul']}")

        lines.append("\t\tStorage Writes:")
        if not recovery["storage_writes"]:
            lines.append("\t\t\tNo storage writes recovered.")
        for write in recovery["storage_writes"]:
            lines.append(f"\t\t\t[{write['op_index']}] {write['kind'].upper()}: {strip_ssa(write['access'])} := {strip_ssa(write['value_solidity'])}")
            lines.append(f"\t\t\t\tSlot: {strip_ssa(write['slot'])}")
            lines.append(f"\t\t\t\tStateVariable: {write['state_variable'] or 'unknown'}")
            if write["unchecked_arithmetic_candidate"]:
                lines.append("\t\t\t\tFlag: unchecked_arithmetic_candidate")
            lines.append(f"\t\t\t\tSolidityLike: {strip_ssa(write['solidity_like'])}")
            lines.append(f"\t\t\t\tControlPath: {format_control(write['control_path'])}")
            lines.append(f"\t\t\t\tYul: {write['yul']}")

        lines.append("\t\tStorage Semantic Replacement View:")
        if not recovery["storage_ir"]:
            lines.append("\t\t\tNo storage semantic replacement available.")
        for item in recovery["storage_ir"]:
            if item["section"] == "slot_computation":
                lines.append(f"\t\t\t{strip_ssa(item['target']) or '_'} = slot({strip_ssa(item['expression'])}); // yul: {item['yul']}")
            elif item["section"] == "storage_read":
                lines.append(f"\t\t\t{strip_ssa(item['solidity_like'])} // yul: {item['yul']}")
            elif item["section"] == "storage_write":
                lines.append(f"\t\t\t{strip_ssa(item['solidity_like'])} // yul: {item['yul']}")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recover assembly storage slot computations, storage reads, and storage writes."
    )
    parser.add_argument("source", help="Solidity source file.")
    parser.add_argument("--slithir-ssa", help="Optional SlithIR-SSA text output.")
    parser.add_argument("--variables-order", help="Optional Slither variables_order.txt output.")
    parser.add_argument("-o", "--output", default="assembly_storage_ir.txt", help="Text storage IR report path.")
    args = parser.parse_args()

    source_path = Path(args.source)
    if not source_path.is_file():
        print(f"Source file not found: {source_path}", file=sys.stderr)
        return 2

    slithir_path = Path(args.slithir_ssa) if args.slithir_ssa else None
    if slithir_path and not slithir_path.is_file():
        print(f"SlithIR-SSA file not found: {slithir_path}", file=sys.stderr)
        return 2

    variables_order_path = Path(args.variables_order) if args.variables_order else None
    if variables_order_path and not variables_order_path.is_file():
        print(f"variables_order file not found: {variables_order_path}", file=sys.stderr)
        return 2

    report = build_storage_report(source_path, slithir_path, variables_order_path)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(format_storage_text_ir(report), encoding="utf-8")

    print(f"Wrote text storage IR report: {output_path}")
    print(f"Assembly blocks found: {len(report['assembly_blocks'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
