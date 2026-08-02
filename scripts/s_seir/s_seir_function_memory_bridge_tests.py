#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "legacy_yul", ROOT / "s_seir"):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from s_seir_memory_ssa import (
    SSeirMemorySSAView,
    classify_intervening_solidity_memory_safety,
    words_from_byte_slice,
)


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


class State:
    def __init__(self, *defs: Def, values=None, predicates=()):
        self.memory = {alias.key: d for d in defs for alias in (d.aliases or (Alias(d.address, d.address, 0, d.address),))}
        self.values = values or {}
        self.predicates = predicates
        self.loop_bases = {}
        self.trace = ()


class Backend:
    def __init__(self, state: State):
        self.state = state
        self.memory_definitions = state.memory
        self.value_definitions = {}

    def states_at(self, _node_id: int):
        return [self.state]


class Block:
    def __init__(self, source: str, start: int, end: int, block_id: int = 1):
        self.source = source
        self._range = (start, end)
        self.block_id = block_id

    @property
    def source_range(self):
        return self._range


def view(current: State, inherited: list[State] | None = None) -> SSeirMemorySSAView:
    return SSeirMemorySSAView(2, Backend(current), [], inherited_states=inherited or [])


def expect(case: str, got, expected) -> None:
    if got != expected:
        raise AssertionError(f"{case}: expected {expected!r}, got {got!r}")


def test_cross_block_keccak_words() -> None:
    inherited = State(
        Def("mem_1", 1, "0", "from"),
        Def("mem_2", 2, "32", "_balances.slot"),
    )
    result = view(State(), [inherited]).resolve_memory_byte_slice(1, "0", "64", "keccak256")
    words = words_from_byte_slice(result)
    expect("cross_block_complete", result["complete"], True)
    expect("cross_block_words", [w["value"] for w in words], ["from", "_balances.slot"])


def test_current_block_overrides_inherited_word() -> None:
    inherited = State(
        Def("mem_1", 1, "0", "from"),
        Def("mem_2", 2, "32", "_balances.slot"),
    )
    current = State(Def("mem_3", 1, "0", "to"))
    result = view(current, [inherited]).resolve_memory_byte_slice(2, "0", "64", "keccak256")
    words = words_from_byte_slice(result)
    expect("override_complete", result["complete"], True)
    expect("override_words", [w["value"] for w in words], ["to", "_balances.slot"])


def test_symbolic_pointer_survives_between_assembly_blocks() -> None:
    inherited = State(
        Def(
            "mem_1",
            1,
            "ptr",
            "msg.sender",
            aliases=(Alias("ptr", "ptr", 0, "ptr"),),
        ),
        Def(
            "mem_2",
            2,
            "ptr + 32",
            "_slotSeed",
            aliases=(Alias("ptr + 32", "ptr", 32, "ptr + 32"),),
        ),
    )
    result = view(State(), [inherited]).resolve_memory_byte_slice(1, "ptr", "64", "keccak256")
    words = words_from_byte_slice(result)
    expect("symbolic_bridge_complete", result["complete"], True)
    expect("symbolic_bridge_words", [w["value"] for w in words], ["msg.sender", "_slotSeed"])


def test_intervening_solidity_safety_policy() -> None:
    source = "assembly { mstore(0, from) }\nif (x) { y = z; }\nrequire(y >= z, \"ok\");\nassembly { sload(keccak256(0, 64)) }"
    prev_start = source.index("assembly")
    prev_end = source.index("}\n") + 1
    next_start = source.rindex("assembly")
    next_end = len(source)
    result = classify_intervening_solidity_memory_safety(Block(source, prev_start, prev_end), Block(source, next_start, next_end, 2))
    expect("safe_span", result["safe"], True)


def test_intervening_solidity_unsafe_policy() -> None:
    source = "assembly { mstore(0, from) }\nbytes memory b = abi.encode(from);\nassembly { sload(keccak256(0, 64)) }"
    prev_start = source.index("assembly")
    prev_end = source.index("}\n") + 1
    next_start = source.rindex("assembly")
    next_end = len(source)
    result = classify_intervening_solidity_memory_safety(Block(source, prev_start, prev_end), Block(source, next_start, next_end, 2))
    expect("unsafe_span", result["safe"], False)


def main() -> None:
    tests = [
        test_cross_block_keccak_words,
        test_current_block_overrides_inherited_word,
        test_symbolic_pointer_survives_between_assembly_blocks,
        test_intervening_solidity_safety_policy,
        test_intervening_solidity_unsafe_policy,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")


if __name__ == "__main__":
    main()
