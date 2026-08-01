#!/usr/bin/env python3
"""
Recover Solidity event emits from Yul log0-log4 instructions.

The module uses Solidity source event declarations plus each assembly block's
MemoryTracker-backed log data resolution. It is a semantic view only: it does
not modify or execute the source.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assembly_context_report import find_contracts, mask_comments_and_strings, split_top_level_commas
from assembly_semantic_ir import build_report, parse_int_literal, strip_ssa
from assembly_storage_ir import yul_expr_to_solidity


try:
    from eth_hash.auto import keccak as _eth_keccak
except Exception:
    _eth_keccak = None

if _eth_keccak is None:
    try:
        from Crypto.Hash import keccak as _crypto_keccak
    except Exception:
        _crypto_keccak = None
else:
    _crypto_keccak = None


MASK_64 = (1 << 64) - 1
KECCAK_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
    0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
    0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
    0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
    0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
    0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]
KECCAK_R = [
    [0, 36, 3, 41, 18],
    [1, 44, 10, 45, 2],
    [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56],
    [27, 20, 39, 8, 14],
]


def rotl64(value: int, shift: int) -> int:
    shift %= 64
    if shift == 0:
        return value & MASK_64
    return ((value << shift) | (value >> (64 - shift))) & MASK_64


def keccak_f1600(state: list[int]) -> None:
    for rc in KECCAK_RC:
        c = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ rotl64(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                state[x + 5 * y] ^= d[x]

        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = rotl64(state[x + 5 * y], KECCAK_R[x][y])

        for x in range(5):
            for y in range(5):
                state[x + 5 * y] = (b[x + 5 * y] ^ ((~b[(x + 1) % 5 + 5 * y]) & b[(x + 2) % 5 + 5 * y])) & MASK_64
        state[0] ^= rc


def _keccak256_fallback(data: bytes) -> bytes:
    rate = 136
    state = [0] * 25
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate != rate - 1:
        padded.append(0)
    padded.append(0x80)

    for offset in range(0, len(padded), rate):
        block = padded[offset:offset + rate]
        for i in range(rate // 8):
            state[i] ^= int.from_bytes(block[i * 8:(i + 1) * 8], 'little')
        keccak_f1600(state)

    output = bytearray()
    while len(output) < 32:
        for i in range(rate // 8):
            output.extend(state[i].to_bytes(8, 'little'))
            if len(output) >= 32:
                break
        if len(output) < 32:
            keccak_f1600(state)
    return bytes(output[:32])


def keccak256(data: bytes) -> bytes:
    """Ethereum Keccak-256, not NIST SHA3-256.

    Prefer the audited library implementation when available. The local
    fallback is retained only for standalone script use in minimal
    environments.
    """
    if _eth_keccak is not None:
        return bytes(_eth_keccak(data))
    if _crypto_keccak is not None:
        h = _crypto_keccak.new(digest_bits=256)
        h.update(data)
        return h.digest()
    return _keccak256_fallback(data)


EVENT_PARAM_QUALIFIERS = {"indexed", "memory", "calldata", "storage", "payable"}
DYNAMIC_BASE_TYPES = {"string", "bytes"}


@dataclass
class EventParam:
    name: str
    type: str
    indexed: bool
    raw: str


@dataclass
class EventDecl:
    contract: str
    name: str
    params: list[EventParam]
    anonymous: bool
    signature: str
    topic0: str | None


def canonical_type(type_text: str) -> str:
    text = re.sub(r"\s+", "", type_text.strip())
    text = re.sub(r"\buint(?=\W|$)", "uint256", text)
    text = re.sub(r"\bint(?=\W|$)", "int256", text)
    if text == "byte":
        return "bytes1"
    return text


def parse_event_param(raw: str) -> EventParam:
    tokens = raw.strip().split()
    indexed = "indexed" in tokens
    name = ""
    type_tokens = tokens[:]
    if tokens and tokens[-1] not in EVENT_PARAM_QUALIFIERS and re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", tokens[-1]):
        name = tokens[-1]
        type_tokens = tokens[:-1]
    clean_type = " ".join(token for token in type_tokens if token not in EVENT_PARAM_QUALIFIERS)
    return EventParam(name=name, type=canonical_type(clean_type), indexed=indexed, raw=raw.strip())


def event_signature(name: str, params: list[EventParam]) -> str:
    return f"{name}({','.join(param.type for param in params)})"


def parse_events_from_source(source_path: Path) -> dict[str, list[EventDecl]]:
    source = source_path.read_text(encoding="utf-8")
    masked = mask_comments_and_strings(source)
    events_by_contract: dict[str, list[EventDecl]] = {}
    event_pattern = re.compile(
        r"\bevent\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\((.*?)\)\s*(anonymous)?\s*;",
        re.S,
    )

    for contract_name, _contract_start, _brace, body_start, body_end in find_contracts(masked):
        contract_events: list[EventDecl] = []
        for match in event_pattern.finditer(masked, body_start, body_end):
            name = match.group(1)
            params_text = source[match.start(2):match.end(2)]
            params = [
                parse_event_param(part)
                for part in split_top_level_commas(params_text)
                if part.strip()
            ]
            anonymous = bool(match.group(3))
            signature = event_signature(name, params)
            topic0 = None if anonymous else "0x" + keccak256(signature.encode("utf-8")).hex()
            contract_events.append(EventDecl(
                contract=contract_name,
                name=name,
                params=params,
                anonymous=anonymous,
                signature=signature,
                topic0=topic0,
            ))
        events_by_contract[contract_name] = contract_events

    return events_by_contract


def all_events_for_contract(events_by_contract: dict[str, list[EventDecl]], contract: str) -> list[EventDecl]:
    own = events_by_contract.get(contract, [])
    if own:
        return own
    # Fallback for simple parsing gaps or inherited declarations. Exact topic0
    # matching still keeps non-anonymous events precise.
    merged: list[EventDecl] = []
    seen: set[str] = set()
    for events in events_by_contract.values():
        for event in events:
            key = event.signature + (" anonymous" if event.anonymous else "")
            if key not in seen:
                seen.add(key)
                merged.append(event)
    return merged


def is_static_word_type(type_name: str) -> bool:
    if type_name in DYNAMIC_BASE_TYPES:
        return False
    if re.search(r"\[\]$", type_name):
        return False
    return True


def normalize_topic_value(expr: str | None) -> str | None:
    if expr is None:
        return None
    text = (strip_ssa(expr) or "").strip()
    value = parse_int_literal(text)
    if value is None:
        return text.lower()
    return "0x" + value.to_bytes(32, "big").hex()


def expr_to_solidity(expr: str | None) -> str:
    if expr is None:
        return "unknown"
    text = strip_ssa(expr) or str(expr)
    try:
        return yul_expr_to_solidity(text)
    except Exception:
        return text


def resolved_word_value(word: dict[str, Any]) -> tuple[str, list[str]]:
    notes: list[str] = []
    value = word.get("value")
    if value in {None, "unknown"}:
        fallback = word.get("exact_fallback")
        if fallback not in {None, "unknown"}:
            notes.append("data_word_has_symbolic_overwrite_candidate")
            return expr_to_solidity(str(fallback)), notes
        notes.append("data_word_unknown")
        return "unknown", notes
    if word.get("symbolic_candidates"):
        notes.append("data_word_has_symbolic_overwrite_candidate")
    return expr_to_solidity(str(value)), notes


def resolved_data_args(words: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    args: list[str] = []
    notes: list[str] = []
    for word in words:
        value, word_notes = resolved_word_value(word)
        args.append(value)
        notes.extend(word_notes)
    return args, notes


def log_data_range(log_op: dict[str, Any]) -> tuple[str | None, int | None]:
    data = log_op["semantic"].get("data") or {}
    ptr = data.get("ptr")
    length = parse_int_literal(str(data.get("length", "")).strip())
    return ptr, length


def memory_write_overlaps_data(op: dict[str, Any], ptr: str | None, length: int | None) -> bool:
    if ptr is None or length is None or op.get("kind") != "memory_write":
        return False
    write = op["semantic"].get("write", {})
    address = write.get("address", {})
    data_addr = re.sub(r"\s+", "", ptr)
    write_base = re.sub(r"\s+", "", str(address.get("base", "")))
    if write_base != data_addr:
        return False
    offset = address.get("offset")
    if offset is None:
        return True
    return 0 <= int(offset) < length


def post_log_memory_notes(block: dict[str, Any], log_op: dict[str, Any]) -> list[str]:
    ptr, length = log_data_range(log_op)
    notes: list[str] = []
    for op in block["source_yul_semantic_ir"]:
        if op["index"] <= log_op["index"]:
            continue
        if memory_write_overlaps_data(op, ptr, length):
            notes.append(f"memory_data_written_after_log_at_op_{op['index']}")
            break
        if op.get("kind") not in {"condition", "loop"}:
            break
    return notes


def unknown_event_name(topics: list[str], op_name: str) -> str:
    if not topics:
        return f"UnknownEvent_{op_name}"
    topic0 = (strip_ssa(topics[0]) or topics[0]).strip()
    if topic0.lower().startswith("0x"):
        topic0 = topic0[2:]
    cleaned = re.sub(r"[^0-9a-fA-F]", "", topic0)
    if not cleaned:
        return "UnknownEvent_unknownTopic"
    return f"UnknownEvent_{cleaned[:12]}"


def build_unknown_event(log_op: dict[str, Any]) -> dict[str, Any]:
    semantic = log_op["semantic"]
    topics = semantic.get("topics", [])
    topic_args = [expr_to_solidity(topic) for topic in topics[1:]] if topics else []
    data_args, notes = resolved_data_args(semantic.get("resolved_data", []))
    args = topic_args + data_args
    event_name = unknown_event_name(topics, semantic.get("op", "log"))
    if topics:
        notes.insert(0, "unknown_event_topic0")
        topic0 = normalize_topic_value(topics[0])
    else:
        notes.insert(0, "unknown_log_without_topic0")
        topic0 = None
    emit_args = ", ".join(args) if args else ""
    return {
        "op_index": log_op["index"],
        "event_name": event_name,
        "signature": "unknown",
        "topic0": topic0,
        "emit_like": f"emit {event_name}({emit_args});",
        "args": args,
        "confidence": "low",
        "notes": notes,
        "control_path": log_op.get("control_path", []),
        "yul": log_op["text"],
        "unknown": True,
        "raw_topics": topics,
        "raw_data": semantic.get("data"),
    }


def match_event(log_op: dict[str, Any], event: EventDecl) -> dict[str, Any] | None:
    semantic = log_op["semantic"]
    topics = semantic.get("topics", [])
    topic_count = semantic.get("topic_count")
    indexed = [param for param in event.params if param.indexed]
    non_indexed = [param for param in event.params if not param.indexed]
    notes: list[str] = []

    if event.anonymous:
        if topic_count != len(indexed):
            return None
        topic_values = topics
        confidence = "low"
        notes.append("anonymous_event_matched_by_arity_only")
    else:
        if topic_count != len(indexed) + 1:
            return None
        if not topics:
            return None
        if normalize_topic_value(topics[0]) != normalize_topic_value(event.topic0):
            return None
        topic_values = topics[1:]
        confidence = "high"

    if any(not is_static_word_type(param.type) for param in non_indexed):
        notes.append("dynamic_non_indexed_data_not_decoded")
        confidence = "low"

    data_length = parse_int_literal(str((semantic.get("data") or {}).get("length", "")).strip())
    expected_length = 32 * len(non_indexed)
    if data_length is not None and data_length != expected_length:
        notes.append(f"data_length_{data_length}_does_not_match_expected_{expected_length}")
        confidence = "low"

    resolved_words = semantic.get("resolved_data", [])
    if len(resolved_words) < len(non_indexed):
        notes.append("memory_data_words_incomplete")
        confidence = "low"

    args_by_name: list[str] = []
    data_index = 0
    topic_index = 0
    for param in event.params:
        if param.indexed:
            value = topic_values[topic_index] if topic_index < len(topic_values) else None
            args_by_name.append(expr_to_solidity(value))
            topic_index += 1
            continue
        if data_index < len(resolved_words):
            value, word_notes = resolved_word_value(resolved_words[data_index])
            notes.extend(word_notes)
            args_by_name.append(value)
        else:
            args_by_name.append("unknown")
        data_index += 1

    if any(arg == "unknown" for arg in args_by_name):
        confidence = "low" if confidence == "high" else confidence

    emit_like = f"emit {event.name}({', '.join(args_by_name)});"
    return {
        "op_index": log_op["index"],
        "event_name": event.name,
        "signature": event.signature,
        "topic0": event.topic0,
        "emit_like": emit_like,
        "args": args_by_name,
        "confidence": confidence,
        "notes": notes,
        "control_path": log_op.get("control_path", []),
        "yul": log_op["text"],
    }


def recover_events_for_block(block: dict[str, Any], events_by_contract: dict[str, list[EventDecl]]) -> dict[str, Any]:
    contract = block["context"]["contract"]
    candidates = all_events_for_contract(events_by_contract, contract)
    recovered: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []

    for op in block["source_yul_semantic_ir"]:
        if op["kind"] != "event_log":
            continue
        matches = [match for event in candidates if (match := match_event(op, event))]
        if len(matches) == 1:
            item = matches[0]
            item["notes"].extend(post_log_memory_notes(block, op))
            recovered.append(item)
        elif len(matches) > 1:
            best = sorted(matches, key=lambda item: 0 if item["confidence"] == "high" else 1)[0]
            best["confidence"] = "medium"
            best["notes"].append("multiple_event_candidates")
            best["notes"].extend(post_log_memory_notes(block, op))
            recovered.append(best)
        else:
            item = build_unknown_event(op)
            item["notes"].extend(post_log_memory_notes(block, op))
            recovered.append(item)

    return {
        "events": recovered,
        "unmatched_logs": unmatched,
        "event_definitions": [
            {
                "name": event.name,
                "signature": event.signature,
                "topic0": event.topic0,
                "anonymous": event.anonymous,
            }
            for event in candidates
        ],
    }


def attach_event_recovery(report: dict[str, Any], source_path: Path | None = None) -> None:
    source = source_path or Path(report["source_file"])
    events_by_contract = parse_events_from_source(source)
    for block in report["assembly_blocks"]:
        block["event_recovery"] = recover_events_for_block(block, events_by_contract)


def event_replacements_by_op(block: dict[str, Any], min_confidence: str = "low") -> dict[int, dict[str, Any]]:
    order = {"high": 3, "medium": 2, "low": 1}
    threshold = order[min_confidence]
    replacements: dict[int, dict[str, Any]] = {}
    for item in block.get("event_recovery", {}).get("events", []):
        if order.get(item.get("confidence", "low"), 0) >= threshold:
            replacements[item["op_index"]] = item
    return replacements


def format_event_text_ir(report: dict[str, Any]) -> str:
    lines = [
        f"INFO:AssemblyEventIR:Source {report['source_file']}",
        "INFO:AssemblyEventIR:Rule topic0 = keccak256(EventName(canonicalTypes)); log data is read from the assembly-block-local MemoryTracker",
        f"INFO:AssemblyEventIR:Assembly blocks {len(report['assembly_blocks'])}",
        "",
    ]
    current_contract: str | None = None
    for block in report["assembly_blocks"]:
        ctx = block["context"]
        if ctx["contract"] != current_contract:
            current_contract = ctx["contract"]
            lines.append(f"Contract {current_contract}")
        lines.append(f"\tFunction {ctx['contract']}.{ctx['function']}")
        lines.append(f"\t\tAssemblyBlock {block['block_id']}")
        recovery = block.get("event_recovery", {})
        if not recovery.get("events") and not recovery.get("unmatched_logs"):
            lines.append("\t\t\tNo log/event operations.")
        for item in recovery.get("events", []):
            note = f" notes={','.join(item['notes'])}" if item.get("notes") else ""
            lines.append(f"\t\t\t[{item['op_index']}] {item['emit_like']} signature={item['signature']} confidence={item['confidence']}{note}")
        for item in recovery.get("unmatched_logs", []):
            lines.append(f"\t\t\t[{item['op_index']}] unmatched {item['op']} topics={item['topics']} data={item['data']}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Recover event emits from inline assembly log instructions.")
    parser.add_argument("source", help="Solidity source file.")
    parser.add_argument("--slithir-ssa", help="Optional SlithIR-SSA text output.")
    parser.add_argument("-o", "--output", default="assembly_event_ir.txt", help="Text event IR report path.")
    args = parser.parse_args()

    source = Path(args.source)
    if not source.is_file():
        print(f"Source file not found: {source}", file=sys.stderr)
        return 2

    report = build_report(source, Path(args.slithir_ssa) if args.slithir_ssa else None)
    attach_event_recovery(report, source)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(format_event_text_ir(report), encoding="utf-8")
    print(f"Wrote assembly event IR report: {output}")
    print(f"Assembly blocks found: {len(report['assembly_blocks'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
