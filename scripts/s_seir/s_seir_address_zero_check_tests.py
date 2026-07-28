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


def unit_for_addresses(*names: str) -> FunctionUnit:
    return FunctionUnit(
        "Test.f(address)",
        "Test",
        "f",
        "f(address)",
        {},
        parameters=[VariableInfo(name, "parameter", "address") for name in names],
    )


def branch(condition: str) -> EffectNode:
    return EffectNode("eff_branch", "Branch", ["asm_s_1"], {"condition": condition})


def value_def(effect_id: str, target: str, value: str) -> EffectNode:
    return EffectNode(effect_id, "ValueDef", [f"{effect_id}_stmt"], {
        "targets": [target],
        "value": value,
        "target_versions": {target: [f"{target}__ssa1"]},
    })


def run_case(name: str, unit: FunctionUnit, effects: list[EffectNode], expected_check: str, expected_var: str) -> dict:
    builder = SemanticOverlayBuilder()
    overlays = builder.address_zero_check_overlays(TypeEnv(unit), effects)
    if len(overlays) != 1:
        raise AssertionError(f"{name}: expected one AddressZeroCheck, got {len(overlays)}")
    overlay = overlays[0]
    attrs = overlay.attrs
    if overlay.kind != "AddressZeroCheck":
        raise AssertionError(f"{name}: wrong overlay kind {overlay.kind}")
    if attrs.get("check") != expected_check:
        raise AssertionError(f"{name}: expected check {expected_check}, got {attrs.get('check')}")
    if attrs.get("variable") != expected_var:
        raise AssertionError(f"{name}: expected variable {expected_var}, got {attrs.get('variable')}")
    return {"case": name, "overlay": overlay.to_dict()}


def main() -> None:
    mask = "0xffffffffffffffffffffffffffffffffffffffff"
    cases = [
        (
            "iszero_shl_address",
            unit_for_addresses("newOwner"),
            [branch("iszero(shl(96, newOwner))")],
            "is_zero",
            "newOwner",
        ),
        (
            "eq_address_zero",
            unit_for_addresses("recipient"),
            [branch("eq(recipient, 0)")],
            "is_zero",
            "recipient",
        ),
        (
            "double_iszero_shl_address",
            unit_for_addresses("target"),
            [branch("iszero(iszero(shl(96, target)))")],
            "is_nonzero",
            "target",
        ),
        (
            "ssa_clean_address",
            unit_for_addresses("account"),
            [value_def("eff_def", "clean", "shr(96, shl(96, account))"), branch("iszero(clean)")],
            "is_zero",
            "account",
        ),
        (
            "masked_address_zero",
            unit_for_addresses("spender"),
            [branch(f"eq(and(spender, {mask}), 0)")],
            "is_zero",
            "spender",
        ),
    ]
    results = [run_case(*case) for case in cases]
    for result in results:
        attrs = result["overlay"]["attrs"]
        print(f"PASS {result['case']}: {attrs['source_expression']} -> {attrs['condition']} [{attrs['source_pattern']}]")


if __name__ == "__main__":
    main()
