#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "legacy_yul", ROOT / "s_seir"):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from s_seir_model import EffectNode, FunctionUnit, VariableInfo
from s_seir_overlay_builder import SemanticOverlayBuilder
from s_seir_type_env import TypeEnv


def unit(params: list[VariableInfo], locals_: list[VariableInfo] | None = None, returns: list[VariableInfo] | None = None) -> FunctionUnit:
    return FunctionUnit(
        function_id="Test.f",
        contract="Test",
        function="f",
        signature="f()",
        ast_node={},
        parameters=params,
        returns=returns or [],
        locals=locals_ or [],
    )


def effect(kind: str, attrs: dict, effect_id: str = "eff") -> EffectNode:
    return EffectNode(effect_id, kind, ["asm_s_1"], attrs)


def overlays_for(fn: FunctionUnit, effects: list[EffectNode]):
    return SemanticOverlayBuilder().build(fn, TypeEnv(fn), [], effects)


def first(overlays, kind: str):
    items = [item for item in overlays if item.kind == kind]
    assert items, f"missing {kind}: {[item.kind for item in overlays]}"
    return items[0]


def test_parameter_array_length_read() -> None:
    fn = unit([VariableInfo("ordinals", "parameter", "uint8[]", "memory")])
    overlays = overlays_for(fn, [
        effect("MemoryRead", {"read_from": "ordinals", "value": "len"}),
        effect("ValueDef", {"targets": ["i"], "value": "shl(5, mload(ordinals))"}),
    ])
    length = first(overlays, "MemoryArrayLengthRead").attrs
    assert length["array"] == "ordinals"
    assert length["access"] == "ordinals.length"
    expr = first([o for o in overlays if o.kind == "ExpressionNormalization" and o.attrs.get("target") == "i"], "ExpressionNormalization").attrs
    assert expr["solidity_like"] == "i = (ordinals.length << 5);"


def test_parameter_array_element_read_constant_offset() -> None:
    fn = unit([VariableInfo("values", "parameter", "uint256[]", "memory")])
    overlays = overlays_for(fn, [
        effect("MemoryRead", {"read_from": "add(values, 0x20)", "value": "x"}),
        effect("ValueDef", {"targets": ["x"], "value": "mload(add(values, 0x20))"}),
    ])
    element = first(overlays, "MemoryArrayElementRead").attrs
    assert element["array"] == "values"
    assert element["index"] == "0"
    assert element["access"] == "values[0]"
    expr = first([o for o in overlays if o.kind == "ExpressionNormalization" and o.attrs.get("target") == "x"], "ExpressionNormalization").attrs
    assert expr["solidity_like"] == "x = values[0];"


def test_parameter_array_element_read_nested_bitset_expression() -> None:
    fn = unit([VariableInfo("ordinals", "parameter", "uint8[]", "memory")])
    overlays = overlays_for(fn, [
        effect("MemoryRead", {"read_from": "add(ordinals, i)", "value": "roles"}),
        effect("ValueDef", {"targets": ["roles"], "value": "or(shl(mload(add(ordinals, i)), 1), roles)"}),
    ])
    element = first(overlays, "MemoryArrayElementRead").attrs
    assert element["index"] == "((i - 32) / 32)"
    assert element["access"] == "ordinals[((i - 32) / 32)]"
    expr = first([o for o in overlays if o.kind == "ExpressionNormalization" and o.attrs.get("target") == "roles"], "ExpressionNormalization").attrs
    assert expr["solidity_like"] == "roles = ((1 << ordinals[((i - 32) / 32)]) | roles);"


def test_parameter_array_element_read_loop_plus_one_offset() -> None:
    fn = unit([VariableInfo("to", "parameter", "address[]", "memory")])
    overlays = overlays_for(fn, [
        effect("MemoryRead", {"read_from": "add(to, mul(add(i, 1), 0x20))", "value": "account"}),
        effect("ValueDef", {"targets": ["account"], "value": "mload(add(to, mul(add(i, 1), 0x20)))"}),
    ])
    element = first(overlays, "MemoryArrayElementRead").attrs
    assert element["index"] == "i"
    assert element["access"] == "to[i]"
    expr = first([o for o in overlays if o.kind == "ExpressionNormalization" and o.attrs.get("target") == "account"], "ExpressionNormalization").attrs
    assert expr["solidity_like"] == "account = to[i];"


def test_parameter_array_element_read_data_base_plus_index_stride() -> None:
    fn = unit([VariableInfo("users", "parameter", "address[]", "memory")])
    overlays = overlays_for(fn, [
        effect("MemoryRead", {"read_from": "add(users, add(0x20, mul(i, 0x20)))", "value": "account"}),
        effect("ValueDef", {"targets": ["account"], "value": "mload(add(users, add(0x20, mul(i, 0x20))))"}),
    ])
    element = first(overlays, "MemoryArrayElementRead").attrs
    assert element["index"] == "i"
    assert element["access"] == "users[i]"
    expr = first([o for o in overlays if o.kind == "ExpressionNormalization" and o.attrs.get("target") == "account"], "ExpressionNormalization").attrs
    assert expr["solidity_like"] == "account = users[i];"


def test_parameter_array_element_read_commuted_array_add() -> None:
    fn = unit([VariableInfo("users", "parameter", "address[]", "memory")])
    overlays = overlays_for(fn, [
        effect("MemoryRead", {"read_from": "add(mul(add(i, 1), 0x20), users)", "value": "account"}),
        effect("ValueDef", {"targets": ["account"], "value": "mload(add(mul(add(i, 1), 0x20), users))"}),
    ])
    element = first(overlays, "MemoryArrayElementRead").attrs
    assert element["index"] == "i"
    assert element["access"] == "users[i]"
    expr = first([o for o in overlays if o.kind == "ExpressionNormalization" and o.attrs.get("target") == "account"], "ExpressionNormalization").attrs
    assert expr["solidity_like"] == "account = users[i];"


def test_local_array_is_not_parameter_array() -> None:
    fn = unit([], [VariableInfo("localArray", "local", "uint256[]", "memory")])
    overlays = overlays_for(fn, [
        effect("MemoryRead", {"read_from": "localArray", "value": "len"}),
        effect("ValueDef", {"targets": ["len"], "value": "mload(localArray)"}),
    ])
    assert not [item for item in overlays if item.kind.startswith("MemoryArray")]
    expr = first([o for o in overlays if o.kind == "ExpressionNormalization" and o.attrs.get("target") == "len"], "ExpressionNormalization").attrs
    assert expr["solidity_like"] == "len = mload(localArray);"


def test_return_array_construction_cursor_pattern() -> None:
    fn = unit([], returns=[VariableInfo("ordinals", "return", "uint8[]", "memory")])
    overlays = overlays_for(fn, [
        effect("ValueDef", {"targets": ["ordinals"], "value": "mload(0x40)", "cfg_node_id": 1}, "eff_base"),
        effect("ValueDef", {"targets": ["ptr"], "value": "add(ordinals, 0x20)", "cfg_node_id": 2}, "eff_ptr"),
        effect("MemoryWrite", {"address": "ptr", "value": "o", "cfg_node_id": 3}, "eff_elem"),
        effect("MemoryWrite", {
            "address": "ordinals",
            "value": "shr(5, sub(ptr, add(ordinals, 0x20)))",
            "cfg_node_id": 4,
            "aliases": [{"base": "ordinals", "offset": 0}],
        }, "eff_len"),
        effect("MemoryWrite", {"address": "0x40", "value": "ptr", "cfg_node_id": 5}, "eff_free"),
    ])
    construction = first(overlays, "MemoryArrayConstruction").attrs
    assert construction["result"] == "ordinals"
    assert construction["array_type"] == "uint8[]"
    assert construction["element_type"] == "uint8"
    assert construction["element_write_pattern"] == "cursor_based"
    assert construction["length_expr_normalized"] == "((ptr - (ordinals + 0x20)) >> 5)"
    assert construction["free_memory_pointer_update"] == "ptr"


def test_memory_region_allocate_semantic_dedupe() -> None:
    builder = SemanticOverlayBuilder()
    first_overlay = builder.ov("MemoryRegionAllocate", ["eff_read", "eff_write_1"], ["asm_s_1", "asm_s_2"], {
        "base": "mload(0x40)",
        "new_free_pointer": "ptr",
        "start_node": 1,
        "end_node": 5,
        "stmt_refs": ["asm_s_1", "asm_s_2"],
        "path_states": ["entry"],
    })
    second_overlay = builder.ov("MemoryRegionAllocate", ["eff_read", "eff_write_2"], ["asm_s_1", "asm_s_2"], {
        "base": "mload(0x40)",
        "new_free_pointer": "ptr",
        "start_node": 1,
        "end_node": 5,
        "stmt_refs": ["asm_s_1", "asm_s_2"],
        "path_states": ["entry", "cond"],
    })
    deduped = SemanticOverlayBuilder.dedupe([first_overlay, second_overlay])
    assert len(deduped) == 1
    assert deduped[0].effects == ["eff_read", "eff_write_1", "eff_write_2"]
    assert deduped[0].attrs["path_states"] == ["entry", "cond"]
    assert deduped[0].attrs["merged_effects"]


def test_manual_memory_allocation_requires_free_pointer_advance() -> None:
    builder = SemanticOverlayBuilder()
    allocations = builder.manual_memory_allocations([
        effect("MemoryRead", {"read_from": "0x40", "value": "m", "cfg_node_id": 1}, "eff_read"),
        effect("ValueDef", {"targets": ["ptr"], "value": "add(m, 0x40)", "cfg_node_id": 2}, "eff_ptr"),
        effect("MemoryWrite", {"address": "0x40", "value": "ptr", "cfg_node_id": 3}, "eff_write"),
    ])
    assert len(allocations) == 1
    assert allocations[0]["new_free_pointer"] == "ptr"


def test_manual_memory_allocation_accepts_indirect_cursor_advance() -> None:
    builder = SemanticOverlayBuilder()
    allocations = builder.manual_memory_allocations([
        effect("MemoryRead", {"read_from": "0x40", "value": "m", "cfg_node_id": 1}, "eff_read"),
        effect("ValueDef", {"targets": ["logs"], "value": "add(m, 0x40)", "cfg_node_id": 2}, "eff_logs"),
        effect("ValueDef", {"targets": ["offset"], "value": "add(0x20, logs)", "cfg_node_id": 3}, "eff_offset"),
        effect("MemoryWrite", {"address": "0x40", "value": "add(offset, shl(5, n))", "cfg_node_id": 4}, "eff_write"),
    ])
    assert len(allocations) == 1
    assert allocations[0]["new_free_pointer"] == "(offset + (n << 5))"


def test_manual_memory_allocation_rejects_free_pointer_restore() -> None:
    builder = SemanticOverlayBuilder()
    allocations = builder.manual_memory_allocations([
        effect("MemoryRead", {"read_from": "0x40", "value": "m", "cfg_node_id": 1}, "eff_read"),
        effect("MemoryWrite", {"address": "0x40", "value": "m", "cfg_node_id": 3}, "eff_restore"),
    ])
    assert allocations == []


def test_manual_memory_allocation_rejects_zero_advance() -> None:
    builder = SemanticOverlayBuilder()
    allocations = builder.manual_memory_allocations([
        effect("MemoryRead", {"read_from": "0x40", "value": "m", "cfg_node_id": 1}, "eff_read"),
        effect("MemoryWrite", {"address": "0x40", "value": "add(m, 0)", "cfg_node_id": 3}, "eff_restore"),
    ])
    assert allocations == []


def test_manual_memory_allocation_rejects_unrelated_scratch_write() -> None:
    builder = SemanticOverlayBuilder()
    allocations = builder.manual_memory_allocations([
        effect("MemoryRead", {"read_from": "0x40", "value": "m", "cfg_node_id": 1}, "eff_read"),
        effect("ValueDef", {"targets": ["r"], "value": "ecrecover(hash, v, r, s)", "cfg_node_id": 2}, "eff_r"),
        effect("MemoryWrite", {"address": "0x40", "value": "r", "cfg_node_id": 3}, "eff_scratch"),
    ])
    assert allocations == []


if __name__ == "__main__":
    tests = [
        test_parameter_array_length_read,
        test_parameter_array_element_read_constant_offset,
        test_parameter_array_element_read_nested_bitset_expression,
        test_parameter_array_element_read_loop_plus_one_offset,
        test_parameter_array_element_read_data_base_plus_index_stride,
        test_parameter_array_element_read_commuted_array_add,
        test_local_array_is_not_parameter_array,
        test_return_array_construction_cursor_pattern,
        test_memory_region_allocate_semantic_dedupe,
        test_manual_memory_allocation_requires_free_pointer_advance,
        test_manual_memory_allocation_accepts_indirect_cursor_advance,
        test_manual_memory_allocation_rejects_free_pointer_restore,
        test_manual_memory_allocation_rejects_zero_advance,
        test_manual_memory_allocation_rejects_unrelated_scratch_write,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
