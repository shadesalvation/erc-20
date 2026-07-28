#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for item in (ROOT / "legacy_yul", ROOT / "s_seir"):
    text = str(item)
    if text not in sys.path:
        sys.path.insert(0, text)

from s_seir_effect_lifter import EffectLifter
from s_seir_model import EffectNode
from s_seir_overlay_builder import SemanticOverlayBuilder


@dataclass
class FakeValueDef:
    version: str


@dataclass
class FakeState:
    predicates: list[str]
    values: dict[str, FakeValueDef]


class FakeMemorySSA:
    def __init__(self, states: list[FakeState]):
        self._states = states

    def states_at(self, _nid: int) -> list[FakeState]:
        return self._states


class FakeTypeEnv:
    def state_var_by_slot(self, _slot: str):
        return None

    def storage_ref_mapping_access(self, *_args):
        return None


def effect(kind: str, attrs: dict, effect_id: str = "eff_x") -> EffectNode:
    return EffectNode(effect_id, kind, ["asm_s_1"], attrs)


def test_condition_sload_lifted_to_storage_read() -> None:
    lifter = EffectLifter()
    res = FakeMemorySSA([FakeState(["cond"], {"handoverSlot": FakeValueDef("handoverSlot__ssa1")})])
    evaluation = {
        "steps": [{
            "order": 1,
            "temp": "__tmp_1",
            "expression": "sload(handoverSlot)",
            "call": "sload",
            "raw_args": ["handoverSlot"],
            "evaluated_args": ["handoverSlot"],
        }]
    }
    effects = lifter.evaluation_effects("asm_s_1", res, 7, evaluation, "eff_branch", [])
    reads = [item for item in effects if item.kind == "StorageRead"]
    assert len(reads) == 1, f"expected one nested StorageRead, got {len(reads)}"
    read = reads[0]
    assert read.attrs["slot"] == "handoverSlot"
    assert read.attrs["slot_versions"] == ["handoverSlot__ssa1"]
    assert read.attrs["value"] == "__tmp_1"
    assert read.attrs["nested_in_condition"] is True


def test_condition_sload_uses_manual_slot_derivation() -> None:
    builder = SemanticOverlayBuilder()
    byte_slice = {
        "complete": True,
        "size": 32,
        "slices": [
            {"query_offset": 0, "size": 20, "extraction": "bytes20(pendingOwner)"},
            {"query_offset": 20, "size": 12, "extraction": "low_bytes(_HANDOVER_SLOT_SEED, 12)"},
        ],
        "packed_semantics": ["bytes20(pendingOwner)", "low_bytes(_HANDOVER_SLOT_SEED, 12)"],
    }
    effects = [
        effect("MemoryHash", {
            "value": "handoverSlot",
            "value_versions": {"handoverSlot": ["handoverSlot__ssa1"]},
            "memory_read": {"byte_slice": byte_slice},
        }, "eff_hash"),
        effect("StorageRead", {
            "slot": "handoverSlot",
            "slot_versions": ["handoverSlot__ssa1"],
            "value": "__tmp_1",
        }, "eff_read"),
    ]
    overlays = builder.storage_overlays(FakeTypeEnv(), effects)
    reads = [item for item in overlays if item.kind == "StateVariableRead"]
    assert len(reads) == 1, f"expected StateVariableRead, got {[item.kind for item in overlays]}"
    attrs = reads[0].attrs
    assert attrs["target"] == "__tmp_1"
    assert attrs["storage_model"] == "manual_packed_hash_slot"
    assert attrs["slot_derivation"]["kind"] == "manual_packed_hash_slot"
    assert attrs["slot_derivation"]["packed_inputs"] == ["bytes20(pendingOwner)", "low_bytes(_HANDOVER_SLOT_SEED, 12)"]


def test_repeated_inline_keccak_slots_keep_distinct_memory_snapshots() -> None:
    builder = SemanticOverlayBuilder()
    effects = [
        effect("MemoryHash", {
            "value": "keccak256(0, 64)",
            "value_versions": {"keccak256(0, 64)": ["keccak256(0, 64)__inline_n3"]},
            "memory_read": {"byte_slice": {
                "complete": True,
                "size": 64,
                "slices": [
                    {"query_offset": 0, "size": 32, "extraction": "0x1111"},
                    {"query_offset": 32, "size": 32, "extraction": "a.slot"},
                ],
            }},
            "inline_storage_slot": True,
            "inline_slot_key": "keccak256(0, 64)__inline_n3",
        }, "eff_hash_1"),
        effect("StorageWrite", {
            "slot": "keccak256(0, 64)",
            "slot_versions": ["keccak256(0, 64)__inline_n3"],
            "value": "1",
        }, "eff_write_1"),
        effect("MemoryHash", {
            "value": "keccak256(0, 64)",
            "value_versions": {"keccak256(0, 64)": ["keccak256(0, 64)__inline_n6"]},
            "memory_read": {"byte_slice": {
                "complete": True,
                "size": 64,
                "slices": [
                    {"query_offset": 0, "size": 32, "extraction": "0x2222"},
                    {"query_offset": 32, "size": 32, "extraction": "b.slot"},
                ],
            }},
            "inline_storage_slot": True,
            "inline_slot_key": "keccak256(0, 64)__inline_n6",
        }, "eff_hash_2"),
        effect("StorageWrite", {
            "slot": "keccak256(0, 64)",
            "slot_versions": ["keccak256(0, 64)__inline_n6"],
            "value": "2",
        }, "eff_write_2"),
    ]
    overlays = builder.storage_overlays(FakeTypeEnv(), effects)
    writes = [item.attrs["solidity_like"] for item in overlays if item.kind == "StateVariableWrite"]
    assert writes == [
        "storage[keccak256(abi.encodePacked(0x1111, a.slot))] = 1;",
        "storage[keccak256(abi.encodePacked(0x2222, b.slot))] = 2;",
    ], writes
    normal = [item for item in overlays if item.kind == "StateVariableWrite"]
    assert all(item.attrs.get("sink_resolution") for item in normal)


def test_path_sensitive_inline_keccak_storage_write_gets_candidates() -> None:
    builder = SemanticOverlayBuilder()
    effects = [
        effect("MemoryHash", {
            "value": "keccak256(0, 64)",
            "value_versions": {"keccak256(0, 64)": ["keccak256(0, 64)__inline_n8"]},
            "memory_read": {"byte_slice": {
                "complete": True,
                "size": 64,
                "path_slices": [
                    {
                        "path": "cond",
                        "complete": True,
                        "slices": [
                            {"query_offset": 0, "size": 32, "extraction": "a"},
                            {"query_offset": 32, "size": 32, "extraction": "p.slot"},
                        ],
                    },
                    {
                        "path": "!(cond)",
                        "complete": True,
                        "slices": [
                            {"query_offset": 0, "size": 32, "extraction": "b"},
                            {"query_offset": 32, "size": 32, "extraction": "p.slot"},
                        ],
                    },
                ],
            }},
            "inline_storage_slot": True,
            "inline_slot_key": "keccak256(0, 64)__inline_n8",
        }, "eff_hash"),
        effect("StorageWrite", {
            "slot": "keccak256(0, 64)",
            "slot_versions": ["keccak256(0, 64)__inline_n8"],
            "value": "1",
        }, "eff_write"),
    ]
    overlays = builder.storage_overlays(FakeTypeEnv(), effects)
    writes = [item for item in overlays if item.kind == "PathConditionedStorageWrite"]
    assert len(writes) == 1, [item.kind for item in overlays]
    candidates = writes[0].attrs["candidates"]
    assert [item["condition"] for item in candidates] == ["cond", "!(cond)"]
    assert [item["solidity_like"] for item in candidates] == [
        "storage[keccak256(abi.encodePacked(a, p.slot))] = 1;",
        "storage[keccak256(abi.encodePacked(b, p.slot))] = 1;",
    ]
    assert writes[0].attrs["sink_resolution"]["path_sensitive"] is True


def test_path_sensitive_inline_keccak_same_access_collapses() -> None:
    builder = SemanticOverlayBuilder()
    effects = [
        effect("MemoryHash", {
            "value": "keccak256(0, 64)",
            "value_versions": {"keccak256(0, 64)": ["keccak256(0, 64)__inline_n8"]},
            "memory_read": {"byte_slice": {
                "complete": True,
                "size": 64,
                "path_slices": [
                    {
                        "path": "cond",
                        "complete": True,
                        "slices": [
                            {"query_offset": 0, "size": 32, "extraction": "a"},
                            {"query_offset": 32, "size": 32, "extraction": "p.slot"},
                        ],
                    },
                    {
                        "path": "!(cond)",
                        "complete": True,
                        "slices": [
                            {"query_offset": 0, "size": 32, "extraction": "a"},
                            {"query_offset": 32, "size": 32, "extraction": "p.slot"},
                        ],
                    },
                ],
            }},
            "inline_storage_slot": True,
            "inline_slot_key": "keccak256(0, 64)__inline_n8",
        }, "eff_hash"),
        effect("StorageWrite", {
            "slot": "keccak256(0, 64)",
            "slot_versions": ["keccak256(0, 64)__inline_n8"],
            "value": "1",
        }, "eff_write"),
    ]
    overlays = builder.storage_overlays(FakeTypeEnv(), effects)
    normal = [item for item in overlays if item.kind == "StateVariableWrite"]
    path = [item for item in overlays if item.kind == "PathConditionedStorageWrite"]
    assert len(normal) == 1 and not path, [item.kind for item in overlays]
    assert normal[0].attrs["solidity_like"] == "storage[keccak256(abi.encodePacked(a, p.slot))] = 1;"
    assert "path_sensitive_sink_collapsed_same_access" in normal[0].attrs["notes"]


def test_revert_selector_from_byte_slice() -> None:
    builder = SemanticOverlayBuilder(selector_registry={
        "0x6f5e8818": [{
            "selector": "0x6f5e8818",
            "signature": "NoHandoverRequest()",
            "name": "NoHandoverRequest",
            "kind": "error",
            "contract": "Ownable",
            "source": "test",
        }]
    })
    revert = effect("Revert", {
        "payload_ptr": "0x1c",
        "payload_size": "0x04",
        "payload_memory": {
            "byte_slice": {
                "complete": True,
                "size": 4,
                "slices": [{"query_offset": 0, "size": 4, "extraction": "low_bytes(0x6f5e8818, 4)"}],
                "packed_semantics": ["low_bytes(0x6f5e8818, 4)"],
            }
        },
        "payload_memory_partial": {
            "abi_hint": {
                "kind": "selector",
                "selector": "bytes(_HANDOVER_SLOT_SEED, offset=16, size=4)",
            }
        },
    }, "eff_revert")
    overlays = builder.custom_error_selector_overlays([revert])
    assert len(overlays) == 1, f"expected CustomErrorRevert, got {len(overlays)}"
    attrs = overlays[0].attrs
    assert attrs["selector"] == "0x6f5e8818"
    assert attrs["error"] == "NoHandoverRequest()"
    assert attrs["revert_like"] == "revert NoHandoverRequest();"


if __name__ == "__main__":
    tests = [
        test_condition_sload_lifted_to_storage_read,
        test_condition_sload_uses_manual_slot_derivation,
        test_repeated_inline_keccak_slots_keep_distinct_memory_snapshots,
        test_path_sensitive_inline_keccak_storage_write_gets_candidates,
        test_path_sensitive_inline_keccak_same_access_collapses,
        test_revert_selector_from_byte_slice,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
