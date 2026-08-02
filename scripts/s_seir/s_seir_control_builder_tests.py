#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for item in (ROOT / "legacy_yul", ROOT / "s_seir"):
    text = str(item)
    if text not in sys.path:
        sys.path.insert(0, text)

from s_seir_control_builder import ControlBuilder
from s_seir_model import FunctionUnit


def fake_function(name: str, full_name: str, start: int) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        full_name=full_name,
        source_mapping=SimpleNamespace(start=start, length=50),
    )


def test_stack_too_deep_retries_with_via_ir() -> None:
    with tempfile.TemporaryDirectory(prefix="sseir_control_test_") as tmp:
        source = Path(tmp) / "Token.sol"
        source.write_text("contract Token {}", encoding="utf-8")
        builder = ControlBuilder(source, "solc")
        loaded = SimpleNamespace(contracts=[])
        with (
            patch("slither.slither.Slither", side_effect=[RuntimeError("Stack too deep"), loaded]) as slither,
            patch("s_seir_control_builder.solc_supports_option", return_value=True),
        ):
            assert builder._load_slither() is loaded
        assert slither.call_count == 2
        assert slither.call_args_list[0].kwargs == {"solc": "solc"}
        assert slither.call_args_list[1].kwargs == {
            "solc": "solc",
            "solc_args": "--via-ir --optimize",
        }
        assert builder._slither_compile_mode == "via_ir_retry"
        assert builder._slither_primary_failure == "stack_too_deep"
        assert builder._slither_error is None


def test_overloaded_function_uses_canonical_signature() -> None:
    one_arg = fake_function("f", "f(uint256)", 200)
    two_args = fake_function("f", "f(uint256,address)", 300)
    contract = SimpleNamespace(name="Token", functions_and_modifiers_declared=[one_arg, two_args])
    builder = ControlBuilder()
    builder._slither_cache = SimpleNamespace(contracts=[contract])
    unit = FunctionUnit("Token.f", "Token", "f", "f(uint256, address)", {"src": "100:50:0"})
    assert builder._find_slither_function(unit) is two_args
    assert builder._last_function_match_method == "canonical_signature"


def test_source_range_precedes_signature_fallback() -> None:
    target = fake_function("f", "f(uint256)", 100)
    other = fake_function("f", "f(uint256,address)", 300)
    contract = SimpleNamespace(name="Token", functions_and_modifiers_declared=[target, other])
    builder = ControlBuilder()
    builder._slither_cache = SimpleNamespace(contracts=[contract])
    unit = FunctionUnit("Token.f", "Token", "f", "f(uint256, address)", {"src": "100:50:0"})
    assert builder._find_slither_function(unit) is target
    assert builder._last_function_match_method == "source_range"


if __name__ == "__main__":
    tests = [
        test_stack_too_deep_retries_with_via_ir,
        test_overloaded_function_uses_canonical_signature,
        test_source_range_precedes_signature_fallback,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
