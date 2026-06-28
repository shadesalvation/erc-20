#!/usr/bin/env python3
"""Extract and preserve Yul CALL-family operations as Solidity-like low-level calls.

The module is intentionally standalone. It does not alter source or the main
pipeline. Each assembly block gets an isolated call-data memory tracker so
parameters are read exactly at the point a call is made.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from assembly_arithmetic_compare_ir import Call, Expr, ParseError, parse_yul_expression, render_yul_expression
from assembly_memory_ssa import normalize_expression
from assembly_semantic_ir import build_report, parse_int_literal, parse_memory_address, split_assignment, strip_ssa


CALL_OPS = {"call", "staticcall", "delegatecall", "callcode"}
PRECOMPILES = {
    1: "ecrecover",
    2: "sha256",
    3: "ripemd160",
    4: "identity",
    5: "modexp",
    6: "bn256Add",
    7: "bn256ScalarMul",
    8: "bn256Pairing",
    9: "blake2f",
    10: "kzgPointEvaluation",
}

# These precompiles have Solidity global functions whose return values match a
# single 32-byte EVM output word. The remaining precompiles use byte-level
# protocols with no Solidity intrinsic, so their low-level call stays explicit.
NATIVE_STATIC_PRECOMPILES = {"ecrecover", "sha256", "ripemd160", "identity"}


@dataclass
class MemoryWrite:
    base: str
    offset: int | None
    offset_expr: str
    size: int | None
    value: str
    source: str
    op_index: int


class CallDataTracker:
    """A byte-range overlay for ABI call inputs within one assembly block."""

    def __init__(self) -> None:
        self.writes: dict[str, list[MemoryWrite]] = {}

    def record(self, op: dict[str, Any]) -> None:
        semantic = op.get("semantic", {})
        if op.get("kind") == "memory_write":
            address = semantic.get("write", {}).get("address", {})
            self._append(MemoryWrite(
                base=str(address.get("base", "unknown")),
                offset=address.get("offset"),
                offset_expr=str(address.get("offset_expr", "unknown")),
                size=32,
                value=str(semantic.get("value", "unknown")),
                source="mstore",
                op_index=op["index"],
            ))
        elif op.get("kind") == "memory_copy":
            copied = semantic.get("copy", {})
            address = copied.get("address", {})
            self._append(MemoryWrite(
                base=str(address.get("base", "unknown")),
                offset=address.get("offset"),
                offset_expr=str(address.get("offset_expr", "unknown")),
                size=copied.get("size"),
                value=str(copied.get("source", "unknown")),
                source=str(semantic.get("op", "memory_copy")),
                op_index=op["index"],
            ))

    def _append(self, write: MemoryWrite) -> None:
        self.writes.setdefault(write.base, []).append(write)

    def _find_covering(self, base: str, start: int, size: int) -> MemoryWrite | None:
        for write in reversed(self.writes.get(base, [])):
            if write.offset is None or write.size is None:
                continue
            if write.offset <= start and start + size <= write.offset + write.size:
                return write
        return None

    def _find_exact_word(self, base: str, offset: int) -> MemoryWrite | None:
        for write in reversed(self.writes.get(base, [])):
            if write.offset == offset and write.size == 32:
                return write
        return None

    def snapshot(self, ptr: str, size: str) -> dict[str, Any]:
        address = parse_memory_address(ptr)
        base = str(address["base"])
        start = address.get("offset")
        length = parse_int_literal(size)
        raw_range = f"memory[{strip_ssa(ptr) or ptr} : {strip_ssa(ptr) or ptr} + {strip_ssa(size) or size}]"
        result: dict[str, Any] = {
            "ptr": ptr,
            "size": size,
            "base": base,
            "start": start,
            "length": length,
            "raw_range": raw_range,
            "selector": None,
            "arguments": [],
            "complete_static_abi": False,
            "source_writes": [],
        }
        if start is None or length is None or length < 0:
            return result

        if length >= 4:
            selector_write = self._find_covering(base, start, 4)
            if selector_write and selector_write.offset == start:
                selector = selector_from_word(selector_write.value)
                result["selector"] = selector
                result["source_writes"].append(selector_write.op_index)

        if length < 4 or (length - 4) % 32 != 0:
            return result
        word_count = (length - 4) // 32
        arguments: list[str | None] = []
        for index in range(word_count):
            word_offset = start + 4 + index * 32
            write = self._find_exact_word(base, word_offset)
            if write:
                arguments.append(strip_ssa(write.value) or write.value)
                result["source_writes"].append(write.op_index)
            else:
                arguments.append(None)
        result["arguments"] = arguments
        result["complete_static_abi"] = all(argument is not None for argument in arguments)
        result["source_writes"] = sorted(set(result["source_writes"]))
        return result


def memory_ssa_snapshot(block: dict[str, Any], op: dict[str, Any], ptr: str, size: str) -> dict[str, Any] | None:
    memory_ssa = block.get("_memory_ssa_result")
    node_id = op.get("cfg_node_id")
    if memory_ssa is None or node_id is None:
        return None

    address = parse_memory_address(strip_ssa(ptr) or ptr)
    start = address.get("offset")
    length = parse_int_literal(strip_ssa(size) or size)
    if start is None or length is None or length < 0:
        return None

    base = str(address.get("base", ""))
    keys = []
    for offset in range(0, length, 32):
        key = base if offset == 0 else f"add({base}, {offset})"
        keys.append(normalize_expression(key))

    states = memory_ssa.states_at(node_id)
    if any(any(key.startswith("<unknown") for key in state.memory) for state in states):
        return None
    words = []
    for index, key in enumerate(keys):
        definitions = [state.memory.get(key) for state in states]
        values = {definition.value if definition else "unknown" for definition in definitions}
        if len(values) != 1 or "unknown" in values:
            return None
        value = next(iter(values))
        versions = [definition.version for definition in definitions if definition]
        words.append({
            "value": value,
            "size": min(32, length - index * 32),
            "memory_ssa": versions[0] if versions else None,
        })

    pointer = strip_ssa(ptr) or ptr
    size_text = strip_ssa(size) or size
    return {
        "ptr": ptr,
        "size": size,
        "base": base,
        "start": start,
        "length": length,
        "raw_range": f"memory[{pointer} : {pointer} + {size_text}]",
        "selector": None,
        "arguments": [],
        "complete_static_abi": False,
        "source_writes": [],
        "words": words,
        "complete_memory_ssa": True,
    }


def render_memory_word(value: str, size: int) -> str:
    try:
        expr = parse_yul_expression(value)
    except ParseError:
        return strip_ssa(value) or value

    if isinstance(expr, Call) and expr.name == "shl" and len(expr.args) == 2:
        shift = literal_value(expr.args[0])
        if shift is not None and shift % 8 == 0 and 0 < size <= 32:
            inner = render_yul_expression(expr_to_text(expr.args[1])).text
            if shift == (32 - size) * 8:
                return f"bytes{size}({inner})"
    return render_yul_expression(value).text


def selector_from_word(value: str) -> str | None:
    try:
        expr = parse_yul_expression(value)
    except ParseError:
        return None
    if isinstance(expr, Call) and expr.name == "shl" and len(expr.args) == 2:
        shift = literal_value(expr.args[0])
        selector = literal_value(expr.args[1])
        if shift == 224 and selector is not None and 0 <= selector < 2**32:
            return f"0x{selector:08x}"
    literal = literal_value(expr)
    if literal is None:
        return None
    return "0x" + literal.to_bytes(32, "big")[:4].hex()


def literal_value(expr: Expr) -> int | None:
    value = getattr(expr, "value", None)
    return parse_int_literal(value) if isinstance(value, str) else None


def walk_calls(expr: Expr, parent: Call | None = None) -> Iterator[tuple[Call, Call | None]]:
    if not isinstance(expr, Call):
        return
    if expr.name in CALL_OPS:
        yield expr, parent
    for arg in expr.args:
        yield from walk_calls(arg, expr)


def expression_from_op(op: dict[str, Any]) -> tuple[str | None, str, str] | None:
    if op.get("kind") in {"condition", "loop"}:
        return None, str(op["semantic"].get("condition", "")), op["kind"]
    target, expression = split_assignment(op.get("ssa_text", op["text"]))
    if not expression:
        return None
    return target, expression, "assignment"


def call_arguments(call: Call) -> dict[str, str] | None:
    args = [expr_to_text(arg) for arg in call.args]
    if call.name in {"call", "callcode"}:
        if len(args) != 7:
            return None
        gas, target, value, input_ptr, input_size, output_ptr, output_size = args
        return {
            "gas": gas,
            "target": target,
            "value": value,
            "input_ptr": input_ptr,
            "input_size": input_size,
            "output_ptr": output_ptr,
            "output_size": output_size,
        }
    if call.name in {"staticcall", "delegatecall"}:
        if len(args) != 6:
            return None
        gas, target, input_ptr, input_size, output_ptr, output_size = args
        return {
            "gas": gas,
            "target": target,
            "value": "0",
            "input_ptr": input_ptr,
            "input_size": input_size,
            "output_ptr": output_ptr,
            "output_size": output_size,
        }
    return None


def expr_to_text(expr: Expr) -> str:
    if isinstance(expr, Call):
        raw = f"{expr.name}({', '.join(expr_to_text(arg) for arg in expr.args)})"
        try:
            return render_yul_expression(raw).text
        except ParseError:
            return raw
    value = getattr(expr, "value", None)
    if isinstance(value, str):
        return strip_ssa(value) or value
    name = getattr(expr, "name", "unknown")
    return strip_ssa(name) or name


def format_target(target: str) -> tuple[str, str | None]:
    value = parse_int_literal(target)
    if value is None:
        return f"address(uint160({strip_ssa(target) or target}))", None
    address_value = value & ((1 << 160) - 1)
    precompile = PRECOMPILES.get(address_value)
    return f"address(0x{address_value:040x})", precompile


def payload_text(snapshot: dict[str, Any]) -> str:
    selector = snapshot.get("selector")
    arguments = snapshot.get("arguments", [])
    if selector and snapshot.get("complete_static_abi"):
        args = ", ".join(argument for argument in arguments if argument is not None)
        suffix = f", {args}" if args else ""
        return f"abi.encodeWithSelector(bytes4({selector}){suffix})"
    if snapshot.get("complete_memory_ssa"):
        values = [
            render_memory_word(word["value"], word["size"])
            for word in snapshot.get("words", [])
        ]
        return f"abi.encodePacked({', '.join(values)})"
    return "abi.encodePacked(yulMemoryWordUnknown())"


def native_precompile_call(
    precompile: str | None,
    kind: str,
    args: dict[str, str],
    snapshot: dict[str, Any],
    op_index: int,
) -> dict[str, Any] | None:
    """Lift a deterministic one-word STATICCALL to a Solidity intrinsic.

    CALL-family operations expose a success bit and write bytes into memory.
    Native Solidity intrinsics expose neither, so this is limited to complete
    MemorySSA input snapshots and an exactly 32-byte output. The pipeline can
    then replace a following mload(outputPtr) using output_word_expression.
    """

    if precompile not in NATIVE_STATIC_PRECOMPILES or kind != "staticcall":
        return None
    if not snapshot.get("complete_memory_ssa"):
        return None
    if parse_int_literal(strip_ssa(args["output_size"]) or args["output_size"]) != 32:
        return None

    payload = payload_text(snapshot)
    suffix = f"_{op_index}"
    words = snapshot.get("words", [])

    if precompile == "sha256":
        result = f"sha256_result{suffix}"
        return {
            "solidity_like": f"bytes32 {result} = sha256({payload});",
            "output_word_expression": f"uint256({result})",
            "result_name": result,
            "elides_success_check": True,
        }

    if precompile == "ripemd160":
        result = f"ripemd160_result{suffix}"
        return {
            "solidity_like": f"bytes32 {result} = bytes32(ripemd160({payload}));",
            "output_word_expression": f"uint256({result})",
            "result_name": result,
            "elides_success_check": True,
        }

    if precompile == "identity":
        if parse_int_literal(strip_ssa(args["input_size"]) or args["input_size"]) != 32 or len(words) != 1:
            return None
        result = f"identity_result{suffix}"
        value = render_yul_expression(words[0]["value"]).text
        return {
            "solidity_like": f"uint256 {result} = {value};",
            "output_word_expression": result,
            "result_name": result,
            "elides_success_check": True,
        }

    # Invalid signatures become address(0), matching the precompile output.
    # Native ecrecover accepts uint8 v, so do not silently truncate a dynamic
    # 256-bit precompile input whose range cannot be proven.
    if len(words) != 4 or parse_int_literal(strip_ssa(args["input_size"]) or args["input_size"]) != 128:
        return None
    v = parse_int_literal(strip_ssa(words[1]["value"]) or words[1]["value"])
    if v not in {27, 28}:
        return None
    rendered = [render_yul_expression(word["value"]).text for word in words]
    result = f"ecrecover_result{suffix}"
    return {
        "solidity_like": (
            f"address {result} = ecrecover(bytes32({rendered[0]}), uint8({rendered[1]}), "
            f"bytes32({rendered[2]}), bytes32({rendered[3]}));"
        ),
        "output_word_expression": f"uint256(uint160({result}))",
        "result_name": result,
        "elides_success_check": True,
    }


def call_solidity_like(
    kind: str,
    args: dict[str, str],
    snapshot: dict[str, Any],
    success: str | None,
    op_index: int,
) -> tuple[str, str | None, dict[str, Any] | None]:
    target, precompile = format_target(args["target"])
    native = native_precompile_call(precompile, kind, args, snapshot, op_index)
    if native:
        return native["solidity_like"], precompile, native
    payload = payload_text(snapshot)
    success_name = strip_ssa(success) if success else None
    binding = f"(bool {success_name}, bytes memory returndata) = " if success_name else "(bool success, bytes memory returndata) = "
    gas = strip_ssa(args["gas"]) or args["gas"]
    if kind == "call":
        value = strip_ssa(args["value"]) or args["value"]
        return f"{binding}{target}.call{{gas: {gas}, value: {value}}}({payload});", precompile, None
    if kind == "staticcall":
        return f"{binding}{target}.staticcall{{gas: {gas}}}({payload});", precompile, None
    if kind == "delegatecall":
        return f"{binding}{target}.delegatecall{{gas: {gas}}}({payload});", precompile, None
    return f"{binding}yulCallcode({target}, {gas}, {strip_ssa(args['value']) or args['value']}, {payload});", precompile, None


def recover_external_calls_for_block(block: dict[str, Any]) -> dict[str, Any]:
    tracker = CallDataTracker()
    calls: list[dict[str, Any]] = []
    for op in block["source_yul_semantic_ir"]:
        tracker.record(op)
        extracted = expression_from_op(op)
        if extracted is None:
            continue
        assignment_target, expression, usage = extracted
        try:
            root = parse_yul_expression(expression)
        except ParseError:
            continue
        for call, parent in walk_calls(root):
            args = call_arguments(call)
            if args is None:
                continue
            snapshot = memory_ssa_snapshot(block, op, args["input_ptr"], args["input_size"]) or tracker.snapshot(args["input_ptr"], args["input_size"])
            direct_result = assignment_target if parent is None and isinstance(root, Call) and root.name == call.name else None
            condition_success = f"{call.name}_success_{op['index']}" if usage in {"condition", "loop"} else None
            success_result = direct_result or condition_success
            solidity_like, precompile, native_precompile = call_solidity_like(
                call.name, args, snapshot, success_result, op["index"]
            )
            calls.append({
                "op_index": op["index"],
                "call_kind": call.name,
                "target": strip_ssa(args["target"]) or args["target"],
                "target_solidity": format_target(args["target"])[0],
                "precompile": precompile,
                "gas": strip_ssa(args["gas"]) or args["gas"],
                "value": strip_ssa(args["value"]) or args["value"],
                "input": snapshot,
                "output_range": f"memory[{strip_ssa(args['output_ptr']) or args['output_ptr']} : {strip_ssa(args['output_ptr']) or args['output_ptr']} + {strip_ssa(args['output_size']) or args['output_size']}]",
                "success_result": strip_ssa(success_result) if success_result else None,
                "success_usage": "condition" if usage in {"condition", "loop"} else "assigned" if direct_result else "nested_expression",
                "parent_expression": expr_to_text(parent) if parent else None,
                "solidity_like": solidity_like,
                "native_precompile": native_precompile,
                "selector": snapshot.get("selector"),
                "arguments": snapshot.get("arguments", []),
                "complete_static_abi": snapshot.get("complete_static_abi", False),
                "memory_source_ops": snapshot.get("source_writes", []),
                "yul": op["text"],
            })
    return {"calls": calls}


def attach_external_call_recovery(report: dict[str, Any]) -> None:
    for block in report["assembly_blocks"]:
        block["external_call_recovery"] = recover_external_calls_for_block(block)


def format_external_call_text_ir(report: dict[str, Any]) -> str:
    lines = [
        f"INFO:AssemblyExternalCallIR:Source {report['source_file']}",
        "INFO:AssemblyExternalCallIR:Each call input is reconstructed from an assembly-block-local memory snapshot",
        f"INFO:AssemblyExternalCallIR:Assembly blocks {len(report['assembly_blocks'])}",
        "",
    ]
    current_contract: str | None = None
    for block in report["assembly_blocks"]:
        ctx = block["context"]
        if ctx["contract"] != current_contract:
            current_contract = ctx["contract"]
            lines.append(f"Contract {current_contract}")
        lines.append(f"\tFunction {ctx['contract']}.{ctx['function']}")
        calls = block.get("external_call_recovery", {}).get("calls", [])
        if not calls:
            lines.append("\t\tNo CALL-family operations.")
        for item in calls:
            lines.append(f"\t\t[{item['op_index']}] {item['call_kind'].upper()} target={item['target']} gas={item['gas']} value={item['value']}")
            if item["precompile"]:
                lines.append(f"\t\t\tPrecompile: {item['precompile']}")
            lines.append(f"\t\t\tInput: {item['input']['raw_range']}")
            lines.append(f"\t\t\tSelector: {item['selector'] or 'unknown'}")
            lines.append(f"\t\t\tArguments: {item['arguments'] or 'none'}")
            lines.append(f"\t\t\tOutput: {item['output_range']}")
            lines.append(f"\t\t\tSuccess: {item['success_result'] or 'inline'} usage={item['success_usage']}")
            lines.append(f"\t\t\tSolidityLike: {item['solidity_like']}")
            lines.append(f"\t\t\tYul: {item['yul']}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract Yul CALL-family operations into a Solidity-like text IR.")
    parser.add_argument("source", help="Solidity source file.")
    parser.add_argument("--slithir-ssa", help="Optional SlithIR-SSA text output.")
    parser.add_argument("-o", "--output", default="assembly_external_call_ir.txt", help="Text report path.")
    args = parser.parse_args()
    source = Path(args.source)
    if not source.is_file():
        print(f"Source file not found: {source}", file=sys.stderr)
        return 2
    report = build_report(source, Path(args.slithir_ssa) if args.slithir_ssa else None)
    attach_external_call_recovery(report)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(format_external_call_text_ir(report), encoding="utf-8")
    print(f"Wrote external call IR report: {output}")
    print(f"Assembly blocks found: {len(report['assembly_blocks'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
