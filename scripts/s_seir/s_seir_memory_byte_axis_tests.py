#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "legacy_yul", ROOT / "s_seir"):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from s_seir_memory_ssa import SSeirMemorySSAView


class Def:
    def __init__(self, version: str, node_id: int, address: str, value: str, kind: str = "mstore"):
        self.version = version
        self.node_id = node_id
        self.address = address
        self.value = value
        self.kind = kind
        self.origin_src = f"{node_id}:0:0"
        self.aliases = ()


class State:
    predicates = ()

    def __init__(self, *defs: Def):
        self.memory = {d.version: d for d in defs}


class Backend:
    def __init__(self, state: State):
        self.state = state

    def states_at(self, _node_id: int):
        return [self.state]


def view(*defs: Def) -> SSeirMemorySSAView:
    return SSeirMemorySSAView(1, Backend(State(*defs)), [])


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

    for name, result in [
        ("handover_overlap", handover),
        ("selector_partial", selector),
        ("mstore8_overwrite", byte_overwrite),
    ]:
        print(f"PASS {name}: {result['packed_semantics']}")


if __name__ == "__main__":
    main()
