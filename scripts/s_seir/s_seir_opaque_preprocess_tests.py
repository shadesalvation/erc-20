#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
for item in (ROOT / "legacy_yul", ROOT / "s_seir"):
    text = str(item)
    if text not in sys.path:
        sys.path.insert(0, text)

from assembly_ast_cfg import discover_solc
from s_seir_opaque_preprocess import prune_opaque_yul_ifs


def run_source(body: str) -> str:
    solc = discover_solc(".venv/bin/solc")
    assert solc
    with tempfile.TemporaryDirectory(prefix="sseir_opaque_test_") as tmp:
        source = Path(tmp) / "T.sol"
        source.write_text(
            """
pragma solidity ^0.8.26;
contract T {
    function f(uint256 x, uint256 y) external pure returns (uint256 z) {
        assembly {
"""
            + body
            + """
        }
    }
}
""",
            encoding="utf-8",
        )
        return prune_opaque_yul_ifs(source, solc).source


def test_eq_add_zero_true_if_is_unwrapped() -> None:
    out = run_source("""
            if eq(x, add(x, 0)) {
                z := 1
            }
""")
    assert "if eq(x, add(x, 0))" not in out
    assert "z := 1" in out


def test_commutative_mul_true_if_is_unwrapped() -> None:
    out = run_source("""
            if eq(mul(x, y), mul(y, x)) {
                z := 2
            }
""")
    assert "if eq(mul(x, y), mul(y, x))" not in out
    assert "z := 2" in out


def test_iszero_sub_same_true_if_is_unwrapped() -> None:
    out = run_source("""
            if iszero(sub(x, x)) {
                z := 3
            }
""")
    assert "if iszero(sub(x, x))" not in out
    assert "z := 3" in out


def test_iszero_eq_add_zero_false_if_is_removed() -> None:
    out = run_source("""
            z := 4
            if iszero(eq(x, add(x, 0))) {
                z := 5
            }
""")
    assert "z := 4" in out
    assert "z := 5" not in out


def test_symbolic_condition_is_kept() -> None:
    out = run_source("""
            if lt(x, y) {
                z := 6
            }
""")
    assert "if lt(x, y)" in out
    assert "z := 6" in out


if __name__ == "__main__":
    tests = [
        test_eq_add_zero_true_if_is_unwrapped,
        test_commutative_mul_true_if_is_unwrapped,
        test_iszero_sub_same_true_if_is_unwrapped,
        test_iszero_eq_add_zero_false_if_is_removed,
        test_symbolic_condition_is_kept,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
