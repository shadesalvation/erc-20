#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for item in (ROOT / "legacy_yul", ROOT / "s_seir"):
    text = str(item)
    if text not in sys.path:
        sys.path.insert(0, text)

from s_seir_effect_lifter import EffectLifter
from s_seir_memory_byte_axis_tests import Alias, Def, State, ValueDef, ycall, yid, ylit
from s_seir_memory_ssa import SSeirMemorySSAView, words_from_byte_slice
from s_seir_model import EffectNode, FunctionUnit, SemanticOverlay, VariableInfo
from s_seir_overlay_builder import SemanticOverlayBuilder
from s_seir_semantic_normalizer import SemanticNormalizer
from s_seir_selector_registry import normalize_selector_value
from s_seir_sink_resolver import SinkResolver
from s_seir_type_env import TypeEnv as RealTypeEnv


class Backend:
    def __init__(self, state: State):
        self.state = state

    def states_at(self, _node_id: int):
        return [self.state]


@dataclass
class StateVar:
    name: str
    type_string: str = "uint256"


class TypeEnv:
    constants = {
        "_ROLE_SLOT_SEED": {
            "name": "_ROLE_SLOT_SEED",
            "type_string": "uint256",
            "value": "0x8b78c6d8",
        },
        "_OWNER_SLOT": {
            "name": "_OWNER_SLOT",
            "type_string": "uint256",
            "value": "not(_ROLE_SLOT_SEED)",
        },
    }

    def state_var_by_slot(self, slot: str):
        return {"_zpewsnjt.slot": StateVar("_zpewsnjt")}.get(str(slot).strip())

    def storage_ref_mapping_access(self, *_args):
        return None

    def is_mapping_type(self, type_string: str | None) -> bool:
        return str(type_string or "").startswith("mapping(")

    def constant(self, name: str | None):
        return self.constants.get(str(name or "").strip())

    def constant_value(self, name: str | None):
        item = self.constant(name)
        return item.get("value") if item else None


def effect(kind: str, attrs: dict, effect_id: str) -> EffectNode:
    return EffectNode(effect_id, kind, ["asm_s_1"], attrs)


def test_sload_slot_alias_traces_to_state_variable() -> None:
    effects = [
        effect("ValueDef", {
            "targets": ["storedHashSlot"],
            "value": "_zpewsnjt.slot",
            "target_versions": {"storedHashSlot": ["storedHashSlot__ssa1"]},
        }, "eff_value"),
        effect("StorageRead", {
            "slot": "storedHashSlot",
            "slot_versions": ["storedHashSlot__ssa1"],
            "value": "storedHash",
            "path_states": ["entry"],
        }, "eff_read"),
    ]
    overlays = SemanticOverlayBuilder().storage_overlays(TypeEnv(), effects)
    read = next(item for item in overlays if item.kind == "StateVariableRead")
    assert read.attrs["access"] == "_zpewsnjt", read.attrs
    assert read.attrs["target"] == "storedHash"


def test_symbolic_call_input_slice_is_known_bytes20_sender() -> None:
    state = State(
        Def("mem_1", 2, "ptr", "caller()", aliases=(Alias("ptr", "ptr", 0, "ptr"),)),
        values={
            "ptr": ValueDef("ptr__ssa1", ycall("mload", ylit("0x40"))),
            "input": ValueDef("input__ssa2", ycall("add", yid("ptr"), ylit("0x0c"))),
            "inputSize": ValueDef("inputSize__ssa3", ylit("0x14")),
        },
    )
    view = SSeirMemorySSAView(1, Backend(state), [])
    byte_slice = view.resolve_memory_byte_slice(7, "input", "inputSize", "staticcall_input")
    words = words_from_byte_slice(byte_slice)
    assert byte_slice["complete"] is True
    assert byte_slice["packed_semantics"] == ["bytes20(msg.sender)"], byte_slice
    assert words[0]["known_value"]["known"] is True


def test_staticcall_size_literal_is_resolved_before_precompile_lift() -> None:
    state = State(
        Def("mem_1", 2, "ptr", "caller()", aliases=(Alias("ptr", "ptr", 0, "ptr"),)),
        values={
            "input": ValueDef("input__ssa2", ycall("add", yid("ptr"), ylit("0x0c"))),
            "inputSize": ValueDef("inputSize__ssa3", ylit("0x14")),
        },
    )
    view = SSeirMemorySSAView(1, Backend(state), [])
    attrs = {"op": "staticcall", "args": ["gas()", "2", "input", "inputSize", "output", "0x20"], "cfg_node_id": 7}
    EffectLifter.attach_call_memory(attrs, view, 7, attrs["args"], "staticcall", [])
    assert attrs["input_size"] == "0x14", attrs
    assert attrs["input_memory"]["words"][0]["value"] == "bytes20(msg.sender)", attrs["input_memory"]
    overlays = SemanticOverlayBuilder().call_overlays([effect("StaticCall", attrs, "eff_call")])
    precompile = next(item for item in overlays if item.kind == "PrecompileCall")
    assert precompile.attrs["native_precompile"]["output_word_expression"].startswith("uint256(sha256_result")
    assert "sha256(abi.encodePacked(bytes20(msg.sender)))" in precompile.attrs["solidity_like"]


def test_path_conditioned_staticcall_is_lifted_to_precompile() -> None:
    attrs = {
        "op": "staticcall",
        "args": ["gas()", "2", "ptr", "20", "ptr", "32"],
        "gas": "gas()",
        "target": "2",
        "input_ptr": "ptr",
        "input_size": "20",
        "output_ptr": "ptr",
        "output_size": "32",
        "cfg_node_id": 7,
        "path_states": ["ready"],
        "sink_resolution": {
            "path_sensitive": True,
            "path_resolutions": [{
                "status": "resolved",
                "condition": "ready",
                "arg_resolutions": {
                    "input": {
                        "memory_slice": {
                            "complete": True,
                            "slices": [{
                                "query_offset": 0,
                                "size": 20,
                                "extraction": "high_bytes((msg.sender << 96), 20)",
                                "source_value": "shl(96, caller())",
                                "source_version": "mem_1",
                            }],
                        }
                    }
                },
            }],
        },
    }
    call = effect("StaticCall", attrs, "eff_call")
    read = effect("MemoryRead", {
        "read_from": "ptr",
        "value": "result",
        "cfg_node_id": 9,
        "path_states": ["ready && call_ok"],
        "memory_read": {"complete": False, "has_unknown": True, "words": [{"value": "unknown"}]},
    }, "eff_read")
    EffectLifter.attach_cross_statement_call_outputs([call, read])
    assert read.attrs["memory_read"]["words"][0]["value"] == "call_output_word(eff_call, 0)"

    builder = SemanticOverlayBuilder()
    calls = builder.call_overlays([call, read])
    precompile = next(item for item in calls if item.kind == "PathConditionedPrecompileCall")
    candidate = precompile.attrs["candidates"][0]
    assert candidate["precompile"] == "sha256", candidate
    assert "sha256(abi.encodePacked(bytes20(msg.sender)))" in candidate["solidity_like"], candidate
    outputs = builder.precompile_output_overlays([call, read], calls)
    output = next(item for item in outputs if item.kind == "PathConditionedPrecompileOutputRead")
    assert output.attrs["target"] == "result", output.attrs
    assert "uint256((sha256(abi.encodePacked(bytes20(msg.sender)))))" == output.attrs["candidates"][0]["value"], output.attrs


def test_dynamic_ecrecover_v_is_classified_but_not_unsafely_lifted() -> None:
    attrs = {
        "op": "staticcall", "gas": "gas()", "target": "1",
        "input_ptr": "0", "input_size": "128", "output_ptr": "32", "output_size": "32",
        "cfg_node_id": 7, "path_states": ["ready"],
        "sink_resolution": {
            "path_sensitive": True,
            "path_resolutions": [{
                "status": "resolved", "condition": "ready",
                "arg_resolutions": {"input": {"memory_slice": {
                    "complete": True,
                    "slices": [
                        {"query_offset": 0, "size": 32, "extraction": "digest", "source_value": "digest", "source_version": "m1"},
                        {"query_offset": 32, "size": 32, "extraction": "(v & 0xff)", "source_value": "and(v, 0xff)", "source_version": "m2"},
                        {"query_offset": 64, "size": 32, "extraction": "r", "source_value": "r", "source_version": "m3"},
                        {"query_offset": 96, "size": 32, "extraction": "s", "source_value": "s", "source_version": "m4"},
                    ],
                }}},
            }],
        },
    }
    overlay = next(item for item in SemanticOverlayBuilder().call_overlays([effect("StaticCall", attrs, "eff_ec")]) if item.kind == "PathConditionedPrecompileCall")
    assert overlay.attrs["precompile"] == "ecrecover", overlay.attrs
    assert overlay.attrs["native_solidity_projection"] is False, overlay.attrs
    assert "yulCall" in overlay.attrs["candidates"][0]["solidity_like"], overlay.attrs


def fixed_precompile_call(effect_id: str = "eff_call", node: int = 7, target: str = "1") -> EffectNode:
    return effect("StaticCall", {
        "op": "staticcall",
        "args": ["gas()", target, "0x00", "0x80", "0x20", "0x20"],
        "gas": "gas()",
        "target": target,
        "input_ptr": "0x00",
        "input_size": "0x80",
        "output_ptr": "0x20",
        "output_size": "0x20",
        "cfg_node_id": node,
        "path_states": ["entry"],
    }, effect_id)


def returndata_memory_resolver(_node: int, pointer: str, _reason: str) -> dict:
    value = "digest" if pointer == "0x00" else "unknown"
    return {
        "complete": value != "unknown",
        "has_unknown": value == "unknown",
        "words": [{"value": value, "address_key": pointer}],
    }


def test_returndatasize_pointer_has_call_output_and_memoryssa_candidates() -> None:
    call = fixed_precompile_call()
    read = effect("MemoryRead", {
        "read_from": "returndatasize()",
        "value": "recovered",
        "cfg_node_id": 9,
        "path_states": ["entry"],
        "memory_read": {"complete": False, "has_unknown": True},
    }, "eff_read")
    EffectLifter.attach_cross_statement_call_outputs([call, read], returndata_memory_resolver)
    candidates = read.attrs["returndatasize_pointer_candidates"]
    assert [item["returndata_size"] for item in candidates] == ["0x20", "0x00"], candidates
    assert candidates[0]["value"] == "call_output_word(eff_call, 0)", candidates
    assert candidates[1]["value"] == "digest", candidates
    SinkResolver().attach_all([call, read])
    assert {path["status"] for path in read.attrs["sink_resolution"]["path_resolutions"]} == {"resolved"}

    overlays = SemanticOverlayBuilder().returndatasize_precompile_output_overlays([read], [])
    assert len(overlays) == 1
    overlay = overlays[0]
    assert overlay.kind == "PathConditionedPrecompileOutputRead"
    assert overlay.attrs["pointer_kind"] == "returndatasize"
    assert [item["value"] for item in overlay.attrs["candidates"]] == [
        "call_output_word(eff_call, 0)", "digest"
    ]


def test_returndatasize_alias_is_traced_through_value_def() -> None:
    call = fixed_precompile_call()
    alias = effect("ValueDef", {
        "targets": ["rdsize"],
        "value": "returndatasize()",
        "cfg_node_id": 8,
        "path_states": ["entry"],
    }, "eff_alias")
    read = effect("MemoryRead", {
        "read_from": "rdsize",
        "value": "recovered",
        "cfg_node_id": 9,
        "path_states": ["entry"],
        "memory_read": {"complete": False, "has_unknown": True},
    }, "eff_read")
    EffectLifter.attach_cross_statement_call_outputs([call, alias, read], returndata_memory_resolver)
    assert len(read.attrs["returndatasize_pointer_candidates"]) == 2


def test_returndatasize_unknown_call_shape_stays_unresolved() -> None:
    call = fixed_precompile_call(target="token")
    read = effect("MemoryRead", {
        "read_from": "returndatasize()",
        "value": "recovered",
        "cfg_node_id": 9,
        "path_states": ["entry"],
        "memory_read": {"complete": False, "has_unknown": True},
    }, "eff_read")
    EffectLifter.attach_cross_statement_call_outputs([call, read], returndata_memory_resolver)
    assert "returndatasize_pointer_candidates" not in read.attrs


def test_returndatasize_uses_nearest_compatible_precompile_call() -> None:
    first = fixed_precompile_call("eff_first", 3, "2")
    nearest = fixed_precompile_call("eff_nearest", 7, "1")
    read = effect("MemoryRead", {
        "read_from": "returndatasize()",
        "value": "recovered",
        "cfg_node_id": 9,
        "path_states": ["entry"],
        "memory_read": {"complete": False, "has_unknown": True},
    }, "eff_read")
    EffectLifter.attach_cross_statement_call_outputs([first, nearest, read], returndata_memory_resolver)
    candidates = read.attrs["returndatasize_pointer_candidates"]
    assert {item["call_effect"] for item in candidates} == {"eff_nearest"}, candidates
    assert {item["precompile"] for item in candidates} == {"ecrecover"}, candidates


def test_high_bytes_literal_is_normalized_as_call_selector() -> None:
    literal = "0x0902f1ac00000000000000000000000000000000000000000000000000000000"
    extraction = f"high_bytes({literal}, 4)"
    assert normalize_selector_value(extraction) == "0x0902f1ac"
    assert normalize_selector_value("high_bytes((0x70a08231 << 224), 4)") == "0x70a08231"
    call = effect("StaticCall", {
        "op": "staticcall",
        "gas": "gas()",
        "target": "pair",
        "input_ptr": "ptr",
        "input_size": "0x04",
        "output_ptr": "0",
        "output_size": "0",
        "input_memory_partial": {
            "abi_hint": {"kind": "selector", "selector": extraction},
        },
        "path_states": ["entry"],
    }, "eff_call")
    builder = SemanticOverlayBuilder(selector_registry={
        "0x0902f1ac": [{
            "selector": "0x0902f1ac",
            "signature": "getReserves()",
            "name": "getReserves",
            "kind": "function",
        }],
    })
    overlay = builder.call_overlays([call])[0]
    assert overlay.attrs["selector"] == "0x0902f1ac", overlay.attrs
    assert overlay.attrs["selector_signature"] == "getReserves()", overlay.attrs
    assert "abi.encodeWithSelector" in overlay.attrs["solidity_like"], overlay.attrs


def test_nested_extcodesize_bool_is_lifted_to_address_has_code() -> None:
    class BoolEnv:
        @staticmethod
        def lookup(name: str):
            return StateVar(name, "bool") if name == "result" else None

        @staticmethod
        def is_bool(name: str) -> bool:
            return name == "result"

    value = effect("ValueDef", {
        "targets": ["result"],
        "value": "iszero(iszero(extcodesize(account)))",
        "path_states": ["entry"],
    }, "eff_value")
    overlays = SemanticOverlayBuilder().address_code_overlays(BoolEnv(), [value])
    assert len(overlays) == 1, overlays
    assert overlays[0].kind == "AddressHasCode"
    assert overlays[0].attrs["condition"] == "(account.code.length != 0)"
    assert overlays[0].attrs["check_kind"] == "has_code"
    assert overlays[0].attrs["iszero_depth"] == 2


def test_single_path_call_uses_sink_resolved_calldata() -> None:
    call = effect("StaticCall", {
        "op": "staticcall",
        "gas": "gas()",
        "target": "token",
        "input_ptr": "ptr",
        "input_size": "0x24",
        "output_ptr": "ptr",
        "output_size": "0x20",
        "path_states": ["entry"],
        "sink_resolution": {
            "sink_kind": "StaticCall",
            "path_sensitive": False,
            "path_resolutions": [{
                "condition": None,
                "status": "resolved",
                "arg_resolutions": {
                    "input": {
                        "normalized": "MemorySlice(high_bytes((0x70a08231 << 224), 4), account)",
                        "memory_slice": {
                            "slices": [
                                {"extraction": "high_bytes((0x70a08231 << 224), 4)"},
                                {"extraction": "account"},
                            ]
                        },
                    }
                },
            }],
        },
    }, "eff_call")
    builder = SemanticOverlayBuilder(selector_registry={
        "0x70a08231": [{
            "selector": "0x70a08231",
            "signature": "balanceOf(address)",
            "name": "balanceOf",
            "kind": "function",
        }],
    })
    overlay = builder.call_overlays([call])[0]
    assert overlay.kind == "StaticCallOverlay"
    assert overlay.attrs["selector"] == "0x70a08231"
    assert overlay.attrs["selector_signature"] == "balanceOf(address)"
    assert overlay.attrs["arguments"] == ["account"]
    assert overlay.attrs["decoded_input"] == "MemorySlice(high_bytes((0x70a08231 << 224), 4), account)"


def test_struct_dynamic_array_field_becomes_memory_array_alias() -> None:
    struct_read = SemanticOverlay("ov_struct", "StructFieldRead", ["eff_struct"], ["asm_s_1"], {
        "struct_object": "p",
        "value": "logs",
        "field": {"name": "logs", "type_string": "uint256[]"},
    })
    builder = SemanticOverlayBuilder()
    aliases = builder.struct_array_aliases([struct_read])
    read = builder.memory_array_read_from_pointer(TypeEnv(), "logs", aliases)
    assert read["overlay_kind"] == "MemoryArrayLengthRead", read
    assert read["access"] == "p.logs.length", read
    assert read["array_type"] == "uint256[]", read


def test_solidity_guard_prefix_reaches_assembly_revert() -> None:
    control = {
        "blocks": [
            {"block_id": "bb_sol_slither_n2", "terminator": {"kind": "Branch", "condition": "_xbkkpxhzey[from]"}},
            {"block_id": "bb_asm1_n0", "terminator": {"kind": "YulNode"}},
        ],
        "edges": [{"from": "bb_sol_slither_n2", "to": "bb_asm1_n0", "kind": "true"}],
    }
    prefixes = EffectLifter.assembly_entry_conditions(control)
    assert prefixes == {1: ["_xbkkpxhzey[from]"]}
    merged = EffectLifter.merge_function_path_prefixes(prefixes[1], "entry")
    assert merged == ["_xbkkpxhzey[from]"]
    overlays = SemanticOverlayBuilder().require_overlays(TypeEnv(), [
        effect("Revert", {"payload_ptr": "0", "payload_size": "0", "cfg_node_id": 2, "path_states": merged}, "eff_revert")
    ])
    assert overlays[0].attrs["condition"] == "!(_xbkkpxhzey[from])", overlays[0].attrs


def test_assembly_entry_uses_real_cfg_paths_after_early_return() -> None:
    control = {
        "blocks": [
            {"block_id": "entry", "terminator": {"kind": "Branch", "condition": "A"}},
            {"block_id": "nested", "terminator": {"kind": "Branch", "condition": "B"}},
            {"block_id": "returned", "terminator": {"kind": "Return"}},
            {"block_id": "join", "terminator": {"kind": "Fallthrough"}},
            {"block_id": "bb_asm1_n0", "terminator": {"kind": "YulNode"}},
        ],
        "edges": [
            {"from": "entry", "to": "nested", "kind": "true"},
            {"from": "entry", "to": "join", "kind": "false"},
            {"from": "nested", "to": "returned", "kind": "true"},
            {"from": "nested", "to": "join", "kind": "false"},
            {"from": "join", "to": "bb_asm1_n0", "kind": "fallthrough"},
        ],
        # This closure is intentionally unsuitable as a path expression.
        "control_dependency_closure": {
            "bb_asm1_n0": [{"predicate": "!(A)"}, {"predicate": "!(B)"}],
        },
        "assembly_boundaries": {1: {"entry_block": "bb_asm1_n0"}},
    }
    paths = EffectLifter.assembly_entry_conditions(control)
    assert set(paths[1]) == {"!(A)", "A && !(B)"}, paths


def test_function_paths_merge_with_local_yul_paths() -> None:
    merged = []
    for local in ("C", "!(C)"):
        merged.extend(EffectLifter.merge_function_path_prefixes(["!(A)", "A && !(B)"], local))
    assert set(merged) == {
        "!(A) && C",
        "A && !(B) && C",
        "!(A) && !(C)",
        "A && !(B) && !(C)",
    }


def test_yul_revert_structural_exit_edge_does_not_reach_later_assembly() -> None:
    control = {
        "blocks": [
            {"block_id": "entry", "terminator": {"kind": "Branch", "condition": "A"}},
            {"block_id": "revert", "terminator": {"kind": "Revert"}},
            {"block_id": "merge", "terminator": {"kind": "Fallthrough"}},
            {"block_id": "asm_exit", "terminator": {"kind": "Fallthrough"}},
            {"block_id": "bb_asm2_n0", "terminator": {"kind": "YulNode"}},
        ],
        "edges": [
            {"from": "entry", "to": "revert", "kind": "true: A"},
            {"from": "entry", "to": "merge", "kind": "false: !(A)"},
            {"from": "revert", "to": "asm_exit", "kind": "terminate: revert"},
            {"from": "merge", "to": "asm_exit", "kind": "next"},
            {"from": "asm_exit", "to": "bb_asm2_n0", "kind": "fallthrough"},
        ],
        "assembly_boundaries": {2: {"entry_block": "bb_asm2_n0"}},
    }
    assert EffectLifter.assembly_entry_conditions(control) == {2: ["!(A)"]}


def test_call_output_expands_returned_and_preserved_memory_paths() -> None:
    call = fixed_precompile_call(node=10)
    call.attrs["path_states"] = ["entry"]
    stale_slice = {
        "query_kind": "MemoryByteSliceResult",
        "pointer": "0x2c",
        "size": 52,
        "complete": True,
        "slices": [
            {
                "query_offset": 0, "size": 20, "source_value": "and(v, 0xff)",
                "source_offset": 12, "source_width": 32, "source_version": "mem_old",
                "source_node_id": 4, "source_kind": "mstore", "extraction": "low_bytes((v & 0xff), 20)",
            },
            {
                "query_offset": 20, "size": 32, "source_value": "allowance_seed_and_spender",
                "source_offset": 0, "source_width": 32, "source_version": "mem_new",
                "source_node_id": 14, "source_kind": "mstore", "extraction": "allowance_seed_and_spender",
            },
        ],
    }
    stale_slice["path_slices"] = [{
        "path": "!(iszero(eq(mload(returndatasize()), owner)))",
        "complete": True,
        "slices": [dict(item) for item in stale_slice["slices"]],
    }]
    sink = effect("MemoryHash", {
        "value": "allowanceSlot",
        "value_versions": {"allowanceSlot": ["allowanceSlot__ssa1"]},
        "ptr": "0x2c", "size": "0x34", "cfg_node_id": 20,
        "path_states": ["!(iszero(eq(mload(returndatasize()), owner)))"],
        "memory_read": {"complete": True, "has_unknown": False, "byte_slice": stale_slice},
    }, "eff_hash")
    EffectLifter.attach_cross_statement_call_outputs([call, sink])
    paths = sink.attrs["memory_read"]["byte_slice"]["path_slices"]
    assert len(paths) == 2, paths
    returned = next(item for item in paths if item["call_memory_outcome"] == "returndata_copied")
    preserved = next(item for item in paths if item["call_memory_outcome"] == "no_returndata_preserve_pre_call_memory")
    assert "returndatasize_after(eff_call) == 0x20" in returned["path"], returned
    assert returned["slices"][0]["source_kind"] == "call_output", returned
    assert returned["slices"][0]["source_value"] == "owner", returned
    assert returned["slices"][0]["extraction"] == "low_bytes(owner, 20)", returned
    assert "returndatasize_after(eff_call) == 0x00" in preserved["path"], preserved
    assert preserved["slices"][0]["source_kind"] == "mstore", preserved
    assert preserved["slices"][0]["source_value"] == "and(v, 0xff)", preserved
    SinkResolver().attach_all([sink])
    candidates = sink.attrs["sink_resolution"]["path_resolutions"]
    assert len(candidates) == 2, candidates
    normalized = {item["arg_resolutions"]["slot"]["normalized"] for item in candidates}
    assert any("owner" in item for item in normalized), normalized
    assert any("v & 0xff" in item for item in normalized), normalized
    write = effect("StorageWrite", {
        "slot": "allowanceSlot",
        "slot_versions": ["allowanceSlot__ssa1"],
        "value": "amount",
        "path_states": ["!(iszero(eq(mload(returndatasize()), owner)))"],
    }, "eff_write")
    overlays = SemanticOverlayBuilder().storage_overlays(TypeEnv(), [sink, write])
    storage = next(item for item in overlays if item.kind == "PathConditionedStorageWrite")
    storage_candidates = storage.attrs["candidates"]
    assert len(storage_candidates) == 2, storage_candidates
    assert any(
        "returndatasize_after(eff_call) == 0x20" in item["condition"]
        and "owner" in item["access"]
        for item in storage_candidates
    ), storage_candidates
    assert any(
        "returndatasize_after(eff_call) == 0x00" in item["condition"]
        and "v & 0xff" in item["access"]
        for item in storage_candidates
    ), storage_candidates


def test_unproven_call_output_does_not_replace_memoryssa() -> None:
    call = fixed_precompile_call(node=10)
    original = [{
        "query_offset": 0, "size": 32, "source_value": "old",
        "source_offset": 0, "source_width": 32, "source_version": "mem_old",
        "source_node_id": 4, "source_kind": "mstore", "extraction": "old",
    }]
    sink = effect("MemoryHash", {
        "ptr": "0x20", "size": "0x20", "cfg_node_id": 20, "path_states": ["entry"],
        "memory_read": {"complete": True, "has_unknown": False, "byte_slice": {
            "pointer": "0x20", "size": 32, "complete": True, "slices": list(original),
            "path_slices": [{"path": "entry", "complete": True, "slices": list(original)}],
        }},
    }, "eff_hash")
    EffectLifter.attach_cross_statement_call_outputs([call, sink])
    resolved = sink.attrs["memory_read"]["byte_slice"]["path_slices"][0]["slices"]
    assert resolved[0]["source_value"] == "old", resolved
    assert not sink.attrs["memory_read"]["byte_slice"].get("call_output_path_expansion")


def memory_read_single_word(value: str) -> dict:
    return {
        "complete": True,
        "has_unknown": False,
        "words": [{
            "offset": 0,
            "value": normalize_for_test(value),
        }],
        "byte_slice": {
            "complete": True,
            "size": 32,
            "slices": [{
                "query_offset": 0,
                "size": 32,
                "source_value": value,
                "extraction": normalize_for_test(value),
                "known_value": {"known": True},
            }],
            "packed_semantics": [normalize_for_test(value)],
        },
    }


def normalize_for_test(value: str) -> str:
    if value == "or(from_, _BALANCE_SLOT_SEED)":
        return "(from_ | _BALANCE_SLOT_SEED)"
    if value == "or(shl(96, from), _BALANCE_SLOT_SEED)":
        return "((from << 96) | _BALANCE_SLOT_SEED)"
    return value


def test_constant_expr_manual_slot_is_resolved() -> None:
    effects = [
        effect("StorageRead", {
            "slot": "not(_ROLE_SLOT_SEED)",
            "slot_versions": [],
            "value": "owner",
        }, "eff_read"),
    ]
    overlays = SemanticOverlayBuilder().storage_overlays(TypeEnv(), effects)
    read = next(item for item in overlays if item.kind == "StateVariableRead")
    assert read.attrs.get("unresolved_reason") is None, read.attrs
    assert read.attrs["slot_constant"] in {"_OWNER_SLOT", "not(_ROLE_SLOT_SEED)"}, read.attrs
    assert read.attrs["storage_model"] == "manual_constant_slot"


def test_constant_expr_slot_alias_is_resolved() -> None:
    effects = [
        effect("ValueDef", {
            "targets": ["ownerSlot"],
            "value": "not(_ROLE_SLOT_SEED)",
            "target_versions": {"ownerSlot": ["ownerSlot__ssa1"]},
        }, "eff_value"),
        effect("StorageWrite", {
            "slot": "ownerSlot",
            "slot_versions": ["ownerSlot__ssa1"],
            "value": "newOwner",
        }, "eff_write"),
    ]
    overlays = SemanticOverlayBuilder().storage_overlays(TypeEnv(), effects)
    write = next(item for item in overlays if item.kind == "StateVariableWrite")
    assert write.attrs.get("unresolved_reason") is None, write.attrs
    assert write.attrs["state_mutation"] is True


def test_single_word_packed_slot_direct_shift_seed_is_resolved() -> None:
    effects = [
        effect("ValueDef", {
            "targets": ["fromBalanceSlot"],
            "value": "keccak256(0x0c, 0x20)",
            "target_versions": {"fromBalanceSlot": ["fromBalanceSlot__ssa1"]},
        }, "eff_slot_def"),
        effect("MemoryHash", {
            "value": "fromBalanceSlot",
            "value_versions": {"fromBalanceSlot": ["fromBalanceSlot__ssa1"]},
            "ptr": "0x0c",
            "size": "0x20",
            "memory_read": memory_read_single_word("or(shl(96, from), _BALANCE_SLOT_SEED)"),
        }, "eff_hash"),
        effect("StorageRead", {
            "slot": "fromBalanceSlot",
            "slot_versions": ["fromBalanceSlot__ssa1"],
            "value": "fromBalance",
        }, "eff_read"),
    ]
    overlays = SemanticOverlayBuilder().storage_overlays(TypeEnv(), effects)
    read = next(item for item in overlays if item.kind == "StateVariableRead")
    assert "bytes20(from)" in read.attrs["access"], read.attrs
    assert "low_bytes(_BALANCE_SLOT_SEED, 12)" in read.attrs["access"], read.attrs


def test_single_word_packed_slot_shift_alias_is_resolved() -> None:
    effects = [
        effect("ValueDef", {
            "targets": ["from_"],
            "value": "shl(96, from)",
            "target_versions": {"from_": ["from___ssa1"]},
        }, "eff_from"),
        effect("ValueDef", {
            "targets": ["fromBalanceSlot"],
            "value": "keccak256(0x0c, 0x20)",
            "target_versions": {"fromBalanceSlot": ["fromBalanceSlot__ssa1"]},
        }, "eff_slot_def"),
        effect("MemoryHash", {
            "value": "fromBalanceSlot",
            "value_versions": {"fromBalanceSlot": ["fromBalanceSlot__ssa1"]},
            "ptr": "0x0c",
            "size": "0x20",
            "memory_read": memory_read_single_word("or(from_, _BALANCE_SLOT_SEED)"),
        }, "eff_hash"),
        effect("StorageWrite", {
            "slot": "fromBalanceSlot",
            "slot_versions": ["fromBalanceSlot__ssa1"],
            "value": "sub(fromBalance, amount)",
        }, "eff_write"),
    ]
    overlays = SemanticOverlayBuilder().storage_overlays(TypeEnv(), effects)
    write = next(item for item in overlays if item.kind == "StateVariableWrite")
    assert "bytes20(from)" in write.attrs["access"], write.attrs
    assert write.attrs["storage_model"] == "manual_packed_hash_slot", write.attrs


def dynamic_bytes_unit(
    object_type: str = "bytes",
    return_type: str = "uint256",
    extra_parameters: list[VariableInfo] | None = None,
) -> FunctionUnit:
    return FunctionUnit(
        "Test.hash(bytes)",
        "Test",
        "hash",
        "hash(bytes)",
        {},
        parameters=[VariableInfo("data", "parameter", object_type, "memory"), *(extra_parameters or [])],
        returns=[VariableInfo("result", "return", return_type)],
    )


def dynamic_bytes_hash_effects(ptr: str, size: str, target_type: str = "uint256") -> list[EffectNode]:
    return [
        effect("ValueDef", {
            "targets": ["result"],
            "value": f"keccak256({ptr}, {size})",
            "target_versions": {"result": ["result__ssa1"]},
            "path_states": ["entry"],
        }, "eff_value"),
        effect("MemoryHash", {
            "ptr": ptr,
            "size": size,
            "value": "result",
            "value_versions": {"result": ["result__ssa1"]},
            "memory_read": {"complete": False, "has_unknown": True},
            "path_states": ["entry"],
        }, "eff_hash"),
    ]


def test_dynamic_bytes_content_hash_is_lifted_and_finishes_sink() -> None:
    fn = dynamic_bytes_unit()
    env = RealTypeEnv(fn)
    effects = dynamic_bytes_hash_effects("add(data, 0x20)", "mload(data)")
    overlays = SemanticOverlayBuilder().build(fn, env, [], effects)
    lifted = next(item for item in overlays if item.kind == "BytesContentHash")
    assert lifted.effects == ["eff_hash"], lifted.effects
    assert lifted.attrs["expression"] == "keccak256(data)", lifted.attrs
    assert lifted.attrs["solidity_like"] == "result = uint256(keccak256(data));", lifted.attrs
    expression = next(item for item in overlays if item.kind == "ExpressionNormalization")
    assert expression.attrs["solidity_like"] == "result = uint256(keccak256(data));", expression.attrs
    roles, _effects, _overlays, facts = SemanticNormalizer().normalize(fn, env, [], effects, overlays)
    sink = next(item for item in facts if item.get("kind") == "SemanticSinkMemoryQuery")
    assert sink["final_status"] == "resolved", sink
    assert sink["resolved_by"] == "semantic_overlay_pattern", sink
    assert any(role.role == "bytes_data_ptr" and role.normalized == "data.data" for role in roles)
    assert any(role.role == "bytes_length_value" and role.normalized == "data.length" for role in roles)


def test_dynamic_bytes_content_hash_follows_unique_ssa_aliases() -> None:
    fn = dynamic_bytes_unit(return_type="bytes32")
    env = RealTypeEnv(fn)
    effects = [
        effect("ValueDef", {
            "targets": ["ptr"], "value": "add(data, 32)",
            "target_versions": {"ptr": ["ptr__ssa1"]}, "path_states": ["entry"],
        }, "eff_ptr"),
        effect("ValueDef", {
            "targets": ["length"], "value": "mload(data)",
            "target_versions": {"length": ["length__ssa1"]}, "path_states": ["entry"],
        }, "eff_length"),
        effect("ValueDef", {
            "targets": ["result"], "value": "keccak256(ptr, length)",
            "target_versions": {"result": ["result__ssa1"]}, "path_states": ["entry"],
        }, "eff_value"),
        effect("MemoryHash", {
            "ptr": "ptr", "size": "length", "value": "result",
            "ptr_versions": ["ptr__ssa1"], "size_versions": ["length__ssa1"],
            "value_versions": {"result": ["result__ssa1"]},
            "memory_read": {"complete": False, "has_unknown": True}, "path_states": ["entry"],
        }, "eff_hash"),
    ]
    overlays = SemanticOverlayBuilder().build(fn, env, [], effects)
    lifted = next(item for item in overlays if item.kind == "BytesContentHash")
    assert lifted.effects == ["eff_ptr", "eff_length", "eff_hash"], lifted.effects
    assert lifted.attrs["via_value_defs"] == ["eff_ptr", "eff_length"], lifted.attrs
    assert lifted.attrs["solidity_like"] == "result = keccak256(data);", lifted.attrs


def test_string_memory_content_hash_uses_explicit_bytes_conversion() -> None:
    fn = dynamic_bytes_unit(object_type="string memory", return_type="bytes32")
    env = RealTypeEnv(fn)
    effects = dynamic_bytes_hash_effects("add(0x20, data)", "mload(data)")
    overlays = SemanticOverlayBuilder().build(fn, env, [], effects)
    lifted = next(item for item in overlays if item.kind == "BytesContentHash")
    assert lifted.attrs["expression"] == "keccak256(bytes(data))", lifted.attrs
    assert lifted.attrs["solidity_like"] == "result = keccak256(bytes(data));", lifted.attrs


def test_dynamic_bytes_hash_rejects_mismatched_length_object() -> None:
    other = VariableInfo("other", "parameter", "bytes", "memory")
    fn = dynamic_bytes_unit(extra_parameters=[other])
    env = RealTypeEnv(fn)
    effects = dynamic_bytes_hash_effects("add(data, 0x20)", "mload(other)")
    overlays = SemanticOverlayBuilder().build(fn, env, [], effects)
    assert not any(item.kind == "BytesContentHash" for item in overlays), [item.kind for item in overlays]
    expression = next(item for item in overlays if item.kind == "ExpressionNormalization")
    assert expression.attrs["expression_normalized"] == "keccak256((data + 0x20), mload(other))", expression.attrs


if __name__ == "__main__":
    tests = [
        test_sload_slot_alias_traces_to_state_variable,
        test_symbolic_call_input_slice_is_known_bytes20_sender,
        test_staticcall_size_literal_is_resolved_before_precompile_lift,
        test_path_conditioned_staticcall_is_lifted_to_precompile,
        test_dynamic_ecrecover_v_is_classified_but_not_unsafely_lifted,
        test_returndatasize_pointer_has_call_output_and_memoryssa_candidates,
        test_returndatasize_alias_is_traced_through_value_def,
        test_returndatasize_unknown_call_shape_stays_unresolved,
        test_returndatasize_uses_nearest_compatible_precompile_call,
        test_high_bytes_literal_is_normalized_as_call_selector,
        test_nested_extcodesize_bool_is_lifted_to_address_has_code,
        test_single_path_call_uses_sink_resolved_calldata,
        test_struct_dynamic_array_field_becomes_memory_array_alias,
        test_solidity_guard_prefix_reaches_assembly_revert,
        test_assembly_entry_uses_real_cfg_paths_after_early_return,
        test_function_paths_merge_with_local_yul_paths,
        test_yul_revert_structural_exit_edge_does_not_reach_later_assembly,
        test_call_output_expands_returned_and_preserved_memory_paths,
        test_unproven_call_output_does_not_replace_memoryssa,
        test_constant_expr_manual_slot_is_resolved,
        test_constant_expr_slot_alias_is_resolved,
        test_single_word_packed_slot_direct_shift_seed_is_resolved,
        test_single_word_packed_slot_shift_alias_is_resolved,
        test_dynamic_bytes_content_hash_is_lifted_and_finishes_sink,
        test_dynamic_bytes_content_hash_follows_unique_ssa_aliases,
        test_string_memory_content_hash_uses_explicit_bytes_conversion,
        test_dynamic_bytes_hash_rejects_mismatched_length_object,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
