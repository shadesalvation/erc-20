#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "legacy_yul", ROOT / "s_seir"):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from assembly_event_ir import EventDecl, EventParam, keccak256
from s_seir_model import EffectNode
from s_seir_overlay_builder import SemanticOverlayBuilder


class FakeTypeEnv:
    def __init__(self, variables: dict[str, str] | None = None):
        self.variables = variables or {}

    def lookup(self, name: str):
        type_string = self.variables.get(name)
        return SimpleNamespace(type_string=type_string) if type_string else None

    def state_var_by_slot(self, _slot: str):
        return None

    def storage_ref_mapping_access(self, *_args):
        return None


def event_log(topics: list[str], topic_memory_reads: list[dict] | None = None) -> EffectNode:
    return EffectNode("eff_event", "EventLog", ["asm_s_1"], {
        "topics": topics,
        "data_size": "0",
        "topic_memory_reads": topic_memory_reads or [],
    })


def roles_event() -> EventDecl:
    return EventDecl(
        contract="OwnableRoles",
        name="RolesUpdated",
        params=[
            EventParam(name="user", type="address", indexed=True, raw="address indexed user"),
            EventParam(name="roles", type="uint256", indexed=True, raw="uint256 indexed roles"),
        ],
        anonymous=False,
        signature="RolesUpdated(address,uint256)",
        topic0="0xdead",
    )


def topic_byte_slice(source: str = "user", *, complete: bool = True) -> dict:
    return {
        "topic_index": 1,
        "topic": "shr(96, mload(0x0c))",
        "read_expr": "mload(0x0c)",
        "ptr": "0x0c",
        "memory_read": {
            "complete": False,
            "byte_slice": {
                "complete": complete,
                "size": 32,
                "slices": [
                    {
                        "query_offset": 0,
                        "size": 20,
                        "source_value": source,
                        "source_offset": 12,
                        "source_width": 32,
                        "extraction": f"low_bytes({source}, 20)",
                    },
                    {
                        "query_offset": 20,
                        "size": 12,
                        "source_value": "_ROLE_SLOT_SEED",
                        "source_offset": 20,
                        "source_width": 32,
                        "extraction": "low_bytes(_ROLE_SLOT_SEED, 12)",
                    },
                ],
                "packed_semantics": [f"low_bytes({source}, 20)", "low_bytes(_ROLE_SLOT_SEED, 12)"],
            },
        },
    }


def test_indexed_address_topic_from_packed_memory() -> None:
    builder = SemanticOverlayBuilder()
    effect = event_log(["0xdead", "shr(96, mload(0x0c))", "roles"], [topic_byte_slice("user")])
    args, notes, _state_reads, memory_reads = builder.event_args(
        roles_event(),
        effect,
        FakeTypeEnv(),
        {},
        effect.attrs["topics"],
        [],
    )
    assert args == ["user", "roles"], args
    assert "event_topic_memory_read_resolved" in notes
    assert memory_reads and memory_reads[0]["resolved"] == "user"


def test_indexed_address_topic_from_caller_memory() -> None:
    builder = SemanticOverlayBuilder()
    effect = event_log(["0xdead", "shr(96, mload(0x0c))", "roles"], [topic_byte_slice("caller()")])
    args, _notes, _state_reads, memory_reads = builder.event_args(
        roles_event(),
        effect,
        FakeTypeEnv(),
        {},
        effect.attrs["topics"],
        [],
    )
    assert args[0] == "msg.sender", args
    assert memory_reads[0]["resolved"] == "msg.sender"


def test_indexed_address_only_requires_consumed_high_twenty_bytes() -> None:
    builder = SemanticOverlayBuilder()
    read = topic_byte_slice("spender", complete=False)
    read["memory_read"]["byte_slice"]["slices"] = read["memory_read"]["byte_slice"]["slices"][:1]
    read["memory_read"]["byte_slice"]["path_slices"] = [{
        "path": "entry",
        "complete": False,
        "slices": list(read["memory_read"]["byte_slice"]["slices"]),
    }]
    effect = event_log(["0xdead", "shr(96, mload(0x0c))", "roles"], [read])
    args, notes, _state_reads, _memory_reads = builder.event_args(
        roles_event(), effect, FakeTypeEnv(), {}, effect.attrs["topics"], [],
    )
    assert args == ["spender", "roles"], args
    assert "event_topic_memory_read_resolved" in notes


def test_path_condition_selects_matching_address_slice() -> None:
    builder = SemanticOverlayBuilder()
    read = topic_byte_slice("unused")
    byte_slice = read["memory_read"]["byte_slice"]
    byte_slice["slices"] = []
    byte_slice["path_slices"] = [
        {"path": "flag", "complete": False, "slices": topic_byte_slice("alice", complete=False)["memory_read"]["byte_slice"]["slices"][:1]},
        {"path": "!(flag)", "complete": False, "slices": topic_byte_slice("bob", complete=False)["memory_read"]["byte_slice"]["slices"][:1]},
    ]
    effect = event_log(["0xdead", "shr(96, mload(0x0c))", "roles"], [read])
    args, _notes, _state_reads, _memory_reads = builder.event_args(
        roles_event(), effect, FakeTypeEnv(), {}, effect.attrs["topics"], [], path_condition="flag",
    )
    assert args == ["alice", "roles"], args


def test_address_topic_projection_collapses_equivalent_ssa_defs() -> None:
    builder = SemanticOverlayBuilder()
    effect = event_log(["0xdead", "shr(96, packedOwner)", "roles"])
    defs = {
        "packedOwner": [
            EffectNode("eff_def_1", "ValueDef", ["asm_s_0"], {"targets": ["packedOwner"], "value": "shl(96, owner)"}),
            EffectNode("eff_def_2", "ValueDef", ["asm_s_0"], {"targets": ["packedOwner"], "value": "shl(96, owner)"}),
        ]
    }
    args, notes, _state_reads, _memory_reads = builder.event_args(
        roles_event(), effect, FakeTypeEnv({"owner": "address"}), defs, effect.attrs["topics"], [],
    )
    assert args == ["owner", "roles"], args
    assert "event_topic_address_projection_resolved" in notes


def test_unmatched_topic_memory_keeps_expression() -> None:
    builder = SemanticOverlayBuilder()
    effect = event_log(["0xdead", "shr(96, mload(0x0c))", "roles"], [])
    args, notes, _state_reads, memory_reads = builder.event_args(
        roles_event(),
        effect,
        FakeTypeEnv(),
        {},
        effect.attrs["topics"],
        [],
    )
    assert args == ["(mload(0x0c) >> 96)", "roles"], args
    assert "event_topic_memory_read_resolved" not in notes
    assert memory_reads == []


def test_path_conditioned_event_reuses_topic_memory_recovery() -> None:
    builder = SemanticOverlayBuilder()
    effect = event_log(
        ["0xdead", "shr(96, mload(0x0c))", "updated"],
        [topic_byte_slice("user")],
    )
    effect.attrs["sink_resolution"] = {
        "path_sensitive": True,
        "path_resolutions": [
            {
                "condition": "iszero(on)",
                "status": "resolved",
                "arg_resolutions": {
                    "data": {
                        "normalized": "empty",
                        "memory_slice": {"slices": []},
                    }
                },
            },
            {
                "condition": "!(iszero(on))",
                "status": "resolved",
                "arg_resolutions": {
                    "data": {
                        "normalized": "empty",
                        "memory_slice": {"slices": []},
                    }
                },
            },
        ],
    }
    overlay = builder.path_conditioned_event_overlay(
        effect,
        roles_event(),
        effect.attrs["topics"],
        FakeTypeEnv(),
        {},
    )
    assert overlay is not None
    assert overlay.kind == "PathConditionedEventEmit"
    assert [candidate["args"] for candidate in overlay.attrs["candidates"]] == [
        ["user", "updated"],
        ["user", "updated"],
    ]
    assert overlay.attrs["argument_memory_reads"][0]["resolved"] == "user"


def test_event_topic0_uses_ethereum_keccak256() -> None:
    assert "0x" + keccak256(b"Transfer(address,address,uint256)").hex() == (
        "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    )
    assert "0x" + keccak256(b"Approval(address,address,uint256)").hex() == (
        "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
    )


if __name__ == "__main__":
    tests = [
        test_indexed_address_topic_from_packed_memory,
        test_indexed_address_topic_from_caller_memory,
        test_indexed_address_only_requires_consumed_high_twenty_bytes,
        test_path_condition_selects_matching_address_slice,
        test_address_topic_projection_collapses_equivalent_ssa_defs,
        test_unmatched_topic_memory_keeps_expression,
        test_path_conditioned_event_reuses_topic_memory_recovery,
        test_event_topic0_uses_ethereum_keccak256,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
