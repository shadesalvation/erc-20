#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "legacy_yul", ROOT / "s_seir"):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from s_seir_memory_ssa import SSeirMemorySSAView, words_from_byte_slice


class Alias:
    def __init__(self, key: str, base: str, offset: int, expression: str, source: str = "test"):
        self.key = key
        self.base = base
        self.offset = offset
        self.expression = expression
        self.source = source


class Def:
    def __init__(self, version: str, node_id: int, address: str, value: str, kind: str = "mstore", aliases=()):
        self.version = version
        self.node_id = node_id
        self.address = address
        self.value = value
        self.kind = kind
        self.origin_src = f"{node_id}:0:0"
        self.aliases = aliases


class ValueDef:
    def __init__(self, version: str, expression: dict):
        self.version = version
        self.expression = expression


class State:
    predicates = ()

    def __init__(self, *defs: Def, values=None):
        self.memory = {d.version: d for d in defs}
        self.values = values or {}


class Backend:
    def __init__(self, state: State):
        self.state = state

    def states_at(self, _node_id: int):
        return [self.state]


def view(*defs: Def) -> SSeirMemorySSAView:
    return SSeirMemorySSAView(1, Backend(State(*defs)), [])


def view_with_values(defs: list[Def], values: dict) -> SSeirMemorySSAView:
    return SSeirMemorySSAView(1, Backend(State(*defs, values=values)), [])


def yid(name: str) -> dict:
    return {"nodeType": "YulIdentifier", "name": name}


def ylit(value: str) -> dict:
    return {"nodeType": "YulLiteral", "value": value}


def ycall(name: str, *args: dict) -> dict:
    return {"nodeType": "YulFunctionCall", "functionName": yid(name), "arguments": list(args)}


def expect(case: str, got, expected) -> None:
    if got != expected:
        raise AssertionError(f"{case}: expected {expected!r}, got {got!r}")


def main() -> None:
    handover = view(
        Def("mem_1", 1, "0x0c", "_HANDOVER_SLOT_SEED"),
        Def("mem_2", 2, "0x00", "caller()"),
    ).resolve_memory_byte_slice(3, "0x0c", "0x20", "keccak256")
    expect("handover_complete", handover["complete"], True)
    expect("handover_semantics", handover["packed_semantics"], ["bytes20(msg.sender)", "low_bytes(_HANDOVER_SLOT_SEED, 12)"])

    selector = view(
        Def("mem_1", 1, "0x00", "0x82b42900"),
    ).resolve_memory_byte_slice(2, "0x1c", "0x04", "revert_payload")
    expect("selector_complete", selector["complete"], True)
    expect("selector_semantics", selector["packed_semantics"], ["low_bytes(0x82b42900, 4)"])

    byte_overwrite = view(
        Def("mem_1", 1, "0x00", "wordValue"),
        Def("mem_2", 2, "0x1f", "byteValue", "mstore8"),
    ).resolve_memory_byte_slice(3, "0x00", "0x20", "return")
    expect("mstore8_complete", byte_overwrite["complete"], True)
    expect("mstore8_semantics", byte_overwrite["packed_semantics"], ["high_bytes(wordValue, 31)", "low_bytes(byteValue, 1)"])

    symbolic_input = view_with_values(
        [
            Def("mem_1", 2, "ptr", "caller()", aliases=(Alias("ptr", "ptr", 0, "ptr"),)),
        ],
        {
            "ptr": ValueDef("ptr__ssa1", ycall("mload", ylit("0x40"))),
            "input": ValueDef("input__ssa2", ycall("add", yid("ptr"), ylit("0x0c"))),
            "inputSize": ValueDef("inputSize__ssa3", ylit("0x14")),
        },
    ).resolve_memory_byte_slice(6, "input", "inputSize", "staticcall_input")
    symbolic_words = words_from_byte_slice(symbolic_input)
    expect("symbolic_input_complete", symbolic_input["complete"], True)
    expect("symbolic_input_word", symbolic_words[0]["value"], "bytes20(msg.sender)")
    expect("symbolic_input_known", symbolic_words[0]["known_value"]["known"], True)

    for name, result in [
        ("handover_overlap", handover),
        ("selector_partial", selector),
        ("mstore8_overwrite", byte_overwrite),
        ("symbolic_input_split", symbolic_input),
    ]:
        print(f"PASS {name}: {result['packed_semantics']}")


if __name__ == "__main__":
    main()
