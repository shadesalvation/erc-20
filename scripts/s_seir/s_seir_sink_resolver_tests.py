#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for item in (ROOT / "legacy_yul", ROOT / "s_seir"):
    text = str(item)
    if text not in sys.path:
        sys.path.insert(0, text)

from s_seir_model import EffectNode
from s_seir_sink_resolver import SinkResolver


def effect(kind: str, attrs: dict, effect_id: str = "eff_x") -> EffectNode:
    return EffectNode(effect_id, kind, ["asm_s_1"], attrs)


def byte_slice(paths: list[tuple[str, list[str]]]) -> dict:
    return {
        "byte_slice": {
            "complete": True,
            "size": 32,
            "path_slices": [
                {
                    "path": path,
                    "complete": True,
                    "slices": [
                        {
                            "query_offset": index * 32,
                            "size": 32,
                            "extraction": value,
                            "source_version": f"mem_{path}_{index}",
                            "source_node_id": index + 1,
                        }
                        for index, value in enumerate(values)
                    ],
                }
                for path, values in paths
            ],
        }
    }


def test_event_log_data_gets_path_sensitive_sink_resolution() -> None:
    e = effect("EventLog", {
        "data_ptr": "0",
        "data_size": "0x20",
        "cfg_node_id": 7,
        "data_memory": byte_slice([
            ("cond", ["amountA"]),
            ("!(cond)", ["amountB"]),
        ]),
    })
    resolution = SinkResolver().resolve_effect(e)
    assert resolution is not None
    assert resolution.sink_kind == "EventLog"
    assert resolution.path_sensitive is True
    values = [
        path.arg_resolutions["data"].normalized
        for path in resolution.path_resolutions
    ]
    assert values == ["MemorySlice(amountA)", "MemorySlice(amountB)"], values


def test_return_and_call_attach_sink_resolution() -> None:
    effects = [
        effect("Return", {
            "payload_ptr": "0",
            "payload_size": "0x20",
            "cfg_node_id": 3,
            "payload_memory": byte_slice([("entry", ["retValue"])]),
        }, "eff_return"),
        effect("Call", {
            "input_ptr": "0",
            "input_size": "0x24",
            "output_ptr": "0",
            "output_size": "0x20",
            "cfg_node_id": 5,
            "input_memory": byte_slice([("entry", ["selector", "arg0"])]),
        }, "eff_call"),
    ]
    SinkResolver().attach_all(effects)
    assert effects[0].attrs["sink_resolution"]["sink_kind"] == "Return"
    assert effects[0].attrs["sink_resolution"]["path_resolutions"][0]["arg_resolutions"]["payload"]["normalized"] == "MemorySlice(retValue)"
    assert effects[1].attrs["sink_resolution"]["sink_kind"] == "Call"
    assert effects[1].attrs["sink_resolution"]["path_resolutions"][0]["arg_resolutions"]["input"]["normalized"] == "MemorySlice(selector, arg0)"


def test_zero_length_revert_resolves_empty_payload_before_memory_slice() -> None:
    e = effect("Revert", {
        "payload_ptr": "0",
        "payload_size": "0",
        "cfg_node_id": 9,
        "path_states": ["cond"],
        "payload_memory": {},
    })
    resolution = SinkResolver().resolve_effect(e)
    assert resolution is not None
    assert resolution.sink_kind == "Revert"
    assert resolution.path_sensitive is True
    path = resolution.path_resolutions[0]
    assert path.status == "resolved"
    assert path.reason is None
    payload = path.arg_resolutions["payload"]
    assert payload.normalized == "empty"
    assert payload.memory_slice["query_kind"] == "EmptyMemorySlice"
    assert payload.memory_slice["slices"] == []
    assert "empty_payload" in payload.notes


def test_nonzero_revert_still_uses_memory_slice_resolution() -> None:
    e = effect("Revert", {
        "payload_ptr": "0x1c",
        "payload_size": "0x04",
        "cfg_node_id": 10,
        "payload_memory": byte_slice([("entry", ["selector"])]),
    })
    resolution = SinkResolver().resolve_effect(e)
    assert resolution is not None
    path = resolution.path_resolutions[0]
    assert path.status == "resolved"
    assert path.arg_resolutions["payload"].normalized == "MemorySlice(selector)"
    assert "empty_payload" not in path.arg_resolutions["payload"].notes


def test_nonzero_revert_incomplete_memory_remains_unresolved() -> None:
    e = effect("Revert", {
        "payload_ptr": "0",
        "payload_size": "0x20",
        "cfg_node_id": 11,
        "payload_memory": {"byte_slice": {
            "complete": True,
            "path_slices": [{
                "path": "entry",
                "complete": True,
                "slices": [],
            }],
        }},
    })
    resolution = SinkResolver().resolve_effect(e)
    assert resolution is not None
    path = resolution.path_resolutions[0]
    assert path.status == "unresolved"
    assert path.reason == "incomplete_memory_slice"


if __name__ == "__main__":
    tests = [
        test_event_log_data_gets_path_sensitive_sink_resolution,
        test_return_and_call_attach_sink_resolution,
        test_zero_length_revert_resolves_empty_payload_before_memory_slice,
        test_nonzero_revert_still_uses_memory_slice_resolution,
        test_nonzero_revert_incomplete_memory_remains_unresolved,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
