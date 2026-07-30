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


@dataclass
class FakeScopedMemorySSA:
    assembly_block_id: int


class FakeTypeEnv:
    def state_var_by_slot(self, _slot: str):
        return None

    def storage_ref_mapping_access(self, *_args):
        return None

    def is_mapping_type(self, type_string: str | None) -> bool:
        return str(type_string or "").startswith("mapping(")


@dataclass
class FakeStateVariable:
    name: str
    type_string: str


class FakeMappingTypeEnv(FakeTypeEnv):
    def __init__(self, slots: dict[str, FakeStateVariable]):
        self.slots = slots

    def state_var_by_slot(self, slot: str):
        return self.slots.get(str(slot).strip())


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


def test_inline_hash_slot_key_is_assembly_block_scoped() -> None:
    first = EffectLifter.inline_hash_slot_key("keccak256(0, 64)", "asm_s_5", FakeScopedMemorySSA(1), 6)
    second = EffectLifter.inline_hash_slot_key("keccak256(0, 64)", "asm_s_12", FakeScopedMemorySSA(3), 6)
    assert first != second
    assert "asm1_asm_s_5" in first
    assert "asm3_asm_s_12" in second


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


def test_require_condition_keeps_comparison_operands_as_values() -> None:
    builder = SemanticOverlayBuilder()
    condition = builder.require_condition(["gt(amount, currentAllowance)"], FakeTypeEnv())
    assert condition == "(amount <= currentAllowance)", condition


def test_require_condition_replaces_state_read_without_booleanizing_operand() -> None:
    builder = SemanticOverlayBuilder()
    type_env = FakeMappingTypeEnv({"k.slot": FakeStateVariable("k", "uint256")})
    condition = builder.require_condition(["gt(amount, sload(k.slot))"], type_env)
    assert condition == "(amount <= k)", condition


def test_keccak_value_def_uses_resolved_full_word_memory_hash() -> None:
    builder = SemanticOverlayBuilder()
    effects = [
        effect("ValueDef", {
            "targets": ["ar"],
            "value": "keccak256(0, 32)",
        }, "eff_value"),
        effect("MemoryHash", {
            "value": "ar",
            "memory_read": {"byte_slice": {
                "complete": True,
                "size": 32,
                "slices": [
                    {"query_offset": 0, "size": 32, "extraction": "msg.sender"},
                ],
            }},
        }, "eff_hash"),
    ]
    overlays = builder.expression_overlays(FakeTypeEnv(), effects)
    expr = next(item for item in overlays if item.kind == "ExpressionNormalization")
    assert expr.attrs["solidity_like"] == "ar = keccak256(abi.encode(msg.sender));", expr.attrs
    assert expr.attrs["memory_hash"]["encoding"] == "abi.encode"


def test_keccak_value_def_uses_packed_for_partial_memory_slices() -> None:
    builder = SemanticOverlayBuilder()
    effects = [
        effect("ValueDef", {
            "targets": ["slot"],
            "value": "keccak256(0x0c, 0x20)",
        }, "eff_value"),
        effect("MemoryHash", {
            "value": "slot",
            "memory_read": {"byte_slice": {
                "complete": True,
                "size": 32,
                "slices": [
                    {"query_offset": 0, "size": 20, "extraction": "bytes20(user)"},
                    {"query_offset": 20, "size": 12, "extraction": "low_bytes(SEED, 12)"},
                ],
            }},
        }, "eff_hash"),
    ]
    overlays = builder.expression_overlays(FakeTypeEnv(), effects)
    expr = next(item for item in overlays if item.kind == "ExpressionNormalization")
    assert expr.attrs["solidity_like"] == "slot = keccak256(abi.encodePacked(bytes20(user), low_bytes(SEED, 12)));", expr.attrs
    assert expr.attrs["memory_hash"]["encoding"] == "abi.encodePacked"


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
        "storage[keccak256(abi.encode(0x1111, a.slot))] = 1;",
        "storage[keccak256(abi.encode(0x2222, b.slot))] = 2;",
    ], writes
    normal = [item for item in overlays if item.kind == "StateVariableWrite"]
    assert all(item.attrs.get("sink_resolution") for item in normal)


def test_byte_axis_standard_mapping_read_recovers_mapping_access() -> None:
    builder = SemanticOverlayBuilder()
    type_env = FakeMappingTypeEnv({"l.slot": FakeStateVariable("l", "mapping(address => uint256)")})
    effects = [
        effect("MemoryHash", {
            "value": "keccak256(0, 64)",
            "value_versions": {"keccak256(0, 64)": ["keccak256(0, 64)__inline_n4"]},
            "memory_read": {"byte_slice": {
                "complete": True,
                "size": 64,
                "slices": [
                    {"query_offset": 0, "size": 32, "extraction": "acc"},
                    {"query_offset": 32, "size": 32, "extraction": "l.slot"},
                ],
            }},
            "inline_storage_slot": True,
            "inline_slot_key": "keccak256(0, 64)__inline_n4",
        }, "eff_hash"),
        effect("StorageRead", {
            "slot": "keccak256(0, 64)",
            "slot_versions": ["keccak256(0, 64)__inline_n4"],
            "value": "aa",
        }, "eff_read"),
    ]
    overlays = builder.storage_overlays(type_env, effects)
    reads = [item for item in overlays if item.kind == "MappingRead"]
    assert len(reads) == 1, [item.kind for item in overlays]
    attrs = reads[0].attrs
    assert attrs["access"] == "l[acc]"
    assert attrs["state_variable"] == "l"
    assert attrs["solidity_like"] == "aa = l[acc];"
    assert attrs["slot_derivation"]["encoding"] == "abi.encode"


def test_byte_axis_standard_mapping_write_recovers_mapping_access() -> None:
    builder = SemanticOverlayBuilder()
    type_env = FakeMappingTypeEnv({"balances.slot": FakeStateVariable("balances", "mapping(address => uint256)")})
    effects = [
        effect("MemoryHash", {
            "value": "slot",
            "value_versions": {"slot": ["slot__ssa1"]},
            "memory_read": {"byte_slice": {
                "complete": True,
                "size": 64,
                "slices": [
                    {"query_offset": 0, "size": 32, "extraction": "user"},
                    {"query_offset": 32, "size": 32, "extraction": "balances.slot"},
                ],
            }},
        }, "eff_hash"),
        effect("StorageWrite", {
            "slot": "slot",
            "slot_versions": ["slot__ssa1"],
            "value": "amount",
        }, "eff_write"),
    ]
    overlays = builder.storage_overlays(type_env, effects)
    writes = [item for item in overlays if item.kind == "MappingWrite"]
    assert len(writes) == 1, [item.kind for item in overlays]
    attrs = writes[0].attrs
    assert attrs["access"] == "balances[user]"
    assert attrs["solidity_like"] == "balances[user] = amount;"
    assert attrs["slot_derivation"]["kind"] == "mapping_slot"


def test_path_sensitive_byte_axis_mapping_candidates_recover_mapping_accesses() -> None:
    builder = SemanticOverlayBuilder()
    type_env = FakeMappingTypeEnv({"p.slot": FakeStateVariable("p", "mapping(address => bool)")})
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
    overlays = builder.storage_overlays(type_env, effects)
    writes = [item for item in overlays if item.kind == "PathConditionedStorageWrite"]
    assert len(writes) == 1, [item.kind for item in overlays]
    candidates = writes[0].attrs["candidates"]
    assert [item["overlay_kind"] for item in candidates] == ["MappingWrite", "MappingWrite"]
    assert [item["solidity_like"] for item in candidates] == [
        "p[a] = 1;",
        "p[b] = 1;",
    ]


def test_byte_axis_nested_mapping_read_recovers_all_dimensions() -> None:
    builder = SemanticOverlayBuilder()
    type_env = FakeMappingTypeEnv({"m.slot": FakeStateVariable("m", "mapping(address => mapping(address => uint256))")})
    effects = [
        effect("MemoryHash", {
            "value": "ac",
            "value_versions": {"ac": ["ac__ssa1"]},
            "memory_read": {"byte_slice": {
                "complete": True,
                "size": 64,
                "slices": [
                    {"query_offset": 0, "size": 32, "extraction": "owner_"},
                    {"query_offset": 32, "size": 32, "extraction": "m.slot"},
                ],
            }},
        }, "eff_hash_inner"),
        effect("MemoryHash", {
            "value": "keccak256(0, 64)",
            "value_versions": {"keccak256(0, 64)": ["keccak256(0, 64)__inline_n7"]},
            "memory_read": {"byte_slice": {
                "complete": True,
                "size": 64,
                "slices": [
                    {"query_offset": 0, "size": 32, "extraction": "spender"},
                    {"query_offset": 32, "size": 32, "extraction": "ac"},
                ],
            }},
            "inline_storage_slot": True,
            "inline_slot_key": "keccak256(0, 64)__inline_n7",
        }, "eff_hash_outer"),
        effect("StorageRead", {
            "slot": "keccak256(0, 64)",
            "slot_versions": ["keccak256(0, 64)__inline_n7"],
            "value": "ab",
        }, "eff_read"),
    ]
    overlays = builder.storage_overlays(type_env, effects)
    reads = [item for item in overlays if item.kind == "MappingRead"]
    assert len(reads) == 1, [item.kind for item in overlays]
    attrs = reads[0].attrs
    assert attrs["access"] == "m[owner_][spender]"
    assert attrs["state_variable"] == "m"
    assert attrs["solidity_like"] == "ab = m[owner_][spender];"


def test_byte_axis_nested_mapping_write_recovers_all_dimensions() -> None:
    builder = SemanticOverlayBuilder()
    type_env = FakeMappingTypeEnv({"allowances.slot": FakeStateVariable("allowances", "mapping(address => mapping(address => uint256))")})
    effects = [
        effect("MemoryHash", {
            "value": "base",
            "value_versions": {"base": ["base__ssa1"]},
            "memory_read": {"byte_slice": {
                "complete": True,
                "size": 64,
                "slices": [
                    {"query_offset": 0, "size": 32, "extraction": "owner"},
                    {"query_offset": 32, "size": 32, "extraction": "allowances.slot"},
                ],
            }},
        }, "eff_hash_inner"),
        effect("MemoryHash", {
            "value": "slot",
            "value_versions": {"slot": ["slot__ssa1"]},
            "memory_read": {"byte_slice": {
                "complete": True,
                "size": 64,
                "slices": [
                    {"query_offset": 0, "size": 32, "extraction": "spender"},
                    {"query_offset": 32, "size": 32, "extraction": "base"},
                ],
            }},
        }, "eff_hash_outer"),
        effect("StorageWrite", {
            "slot": "slot",
            "slot_versions": ["slot__ssa1"],
            "value": "amount",
        }, "eff_write"),
    ]
    overlays = builder.storage_overlays(type_env, effects)
    writes = [item for item in overlays if item.kind == "MappingWrite"]
    assert len(writes) == 1, [item.kind for item in overlays]
    assert writes[0].attrs["access"] == "allowances[owner][spender]"
    assert writes[0].attrs["solidity_like"] == "allowances[owner][spender] = amount;"


def test_byte_axis_three_dimensional_mapping_read_recovers_all_dimensions() -> None:
    builder = SemanticOverlayBuilder()
    type_env = FakeMappingTypeEnv({"root.slot": FakeStateVariable("root", "mapping(uint256 => mapping(address => mapping(bytes32 => uint256)))")})
    effects = [
        effect("MemoryHash", {
            "value": "s1",
            "value_versions": {"s1": ["s1__ssa1"]},
            "memory_read": {"byte_slice": {
                "complete": True,
                "size": 64,
                "slices": [
                    {"query_offset": 0, "size": 32, "extraction": "id"},
                    {"query_offset": 32, "size": 32, "extraction": "root.slot"},
                ],
            }},
        }, "eff_hash_1"),
        effect("MemoryHash", {
            "value": "s2",
            "value_versions": {"s2": ["s2__ssa1"]},
            "memory_read": {"byte_slice": {
                "complete": True,
                "size": 64,
                "slices": [
                    {"query_offset": 0, "size": 32, "extraction": "user"},
                    {"query_offset": 32, "size": 32, "extraction": "s1"},
                ],
            }},
        }, "eff_hash_2"),
        effect("MemoryHash", {
            "value": "slot",
            "value_versions": {"slot": ["slot__ssa1"]},
            "memory_read": {"byte_slice": {
                "complete": True,
                "size": 64,
                "slices": [
                    {"query_offset": 0, "size": 32, "extraction": "tag"},
                    {"query_offset": 32, "size": 32, "extraction": "s2"},
                ],
            }},
        }, "eff_hash_3"),
        effect("StorageRead", {
            "slot": "slot",
            "slot_versions": ["slot__ssa1"],
            "value": "value",
        }, "eff_read"),
    ]
    overlays = builder.storage_overlays(type_env, effects)
    reads = [item for item in overlays if item.kind == "MappingRead"]
    assert len(reads) == 1, [item.kind for item in overlays]
    assert reads[0].attrs["access"] == "root[id][user][tag]"
    assert reads[0].attrs["solidity_like"] == "value = root[id][user][tag];"


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
        "storage[keccak256(abi.encode(a, p.slot))] = 1;",
        "storage[keccak256(abi.encode(b, p.slot))] = 1;",
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
    assert normal[0].attrs["solidity_like"] == "storage[keccak256(abi.encode(a, p.slot))] = 1;"
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
        test_inline_hash_slot_key_is_assembly_block_scoped,
        test_condition_sload_uses_manual_slot_derivation,
        test_require_condition_keeps_comparison_operands_as_values,
        test_require_condition_replaces_state_read_without_booleanizing_operand,
        test_keccak_value_def_uses_resolved_full_word_memory_hash,
        test_keccak_value_def_uses_packed_for_partial_memory_slices,
        test_repeated_inline_keccak_slots_keep_distinct_memory_snapshots,
        test_byte_axis_standard_mapping_read_recovers_mapping_access,
        test_byte_axis_standard_mapping_write_recovers_mapping_access,
        test_path_sensitive_byte_axis_mapping_candidates_recover_mapping_accesses,
        test_byte_axis_nested_mapping_read_recovers_all_dimensions,
        test_byte_axis_nested_mapping_write_recovers_all_dimensions,
        test_byte_axis_three_dimensional_mapping_read_recovers_all_dimensions,
        test_path_sensitive_inline_keccak_storage_write_gets_candidates,
        test_path_sensitive_inline_keccak_same_access_collapses,
        test_revert_selector_from_byte_slice,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
