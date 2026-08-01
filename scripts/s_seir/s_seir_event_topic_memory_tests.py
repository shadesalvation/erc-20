#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "legacy_yul", ROOT / "s_seir"):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from assembly_event_ir import EventDecl, EventParam, keccak256
from s_seir_model import EffectNode
from s_seir_overlay_builder import SemanticOverlayBuilder


class FakeTypeEnv:
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


def topic_byte_slice(source: str = "user") -> dict:
    return {
        "topic_index": 1,
        "topic": "shr(96, mload(0x0c))",
        "read_expr": "mload(0x0c)",
        "ptr": "0x0c",
        "memory_read": {
            "complete": False,
            "byte_slice": {
                "complete": True,
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
        test_unmatched_topic_memory_keeps_expression,
        test_event_topic0_uses_ethereum_keccak256,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
