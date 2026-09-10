#!/usr/bin/env python3
"""End-to-end regression for completed S-SEIR overlays entering SFIR."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
for item in (ROOT / "legacy_yul", ROOT / "s_seir"):
    text = str(item)
    if text not in sys.path:
        sys.path.insert(0, text)

from s_seir_pipeline import build_sseir
from s_seir_semantic_fact_adapter import build_function_level_semantic_fact_ir_payload
from s_seir_semantic_fact_render import render_fact_cfg_dot, render_fact_cfg_text, render_sfir_c_like


FIXTURE = ROOT.parent / "tests" / "fixtures" / "sseir_high_semantics_surface.sol"
OPAQUE_FIXTURE = ROOT.parent / "tests" / "fixtures" / "address_alias_memory.sol"


def test_completed_overlay_surface_reaches_final_sfir() -> None:
    with tempfile.TemporaryDirectory(prefix="sseir_overlay_surface_") as directory:
        functions = build_sseir(FIXTURE, workdir=Path(directory), branch_preprocess=False)
    overlays = {
        item.kind
        for function in functions
        for item in function.semantic_overlays
    }
    assert {
        "AbiCallDataConstruction", "AbiEncodedLowLevelCall", "AddressZeroCheck",
        "BytesContentHash", "PathConditionedStaticCallOverlay",
        "StructFieldRead", "StructFieldWrite", "RawReturnData",
    }.issubset(overlays), overlays

    payload = build_function_level_semantic_fact_ir_payload(functions, source=str(FIXTURE))
    yul_nodes = [
        node for function in payload["functions"]
        for node in function["semantic_nodes"]
        if node.get("source_lang") == "yul"
    ]
    assert {"AbiEncode", "LowLevelCall", "BranchCondition", "HashCompute", "ValueAssign", "ValueCompute", "ExternalCall", "Return"}.issubset(
        {node["kind"] for node in yul_nodes}
    )
    assert any(node["kind"] == "ExternalCall" and node.get("lvalue") == "ok" for node in yul_nodes)

    for function in payload["functions"]:
        node_ids = {node["semantic_id"] for node in function["semantic_nodes"]}
        fact_cfg_ids = {
            semantic_id for block in function["fact_cfg"]["blocks"]
            for semantic_id in block.get("semantic_ids") or []
        }
        assert node_ids.issubset(fact_cfg_ids), function["diagnostics"]

    c_like = render_sfir_c_like(payload)
    cfg_text = render_fact_cfg_text(payload)
    dot = "\n".join(render_fact_cfg_dot(function) for function in payload["functions"])
    for rendered in (c_like, cfg_text, dot):
        assert "digest = keccak256(data);" in rendered
        assert "abi.encodeWithSelector(0x70a08231, account)" in rendered
        assert "externalStaticCall(token, __sfir_abi_payload_" in rendered
        assert "if (account != address(0))" in rendered
        assert "pair.left = digest;" in rendered
        assert "ok = externalStaticCall address(this).balanceOf(account);" in rendered
        assert "returnRawAbiWord(value);" in rendered
        assert "mload(" not in rendered and "mstore(" not in rendered and "effects" not in rendered


def test_residual_yul_memory_access_is_opaque_at_the_sfir_boundary() -> None:
    with tempfile.TemporaryDirectory(prefix="sseir_opaque_surface_") as directory:
        functions = build_sseir(OPAQUE_FIXTURE, workdir=Path(directory), branch_preprocess=False)
    payload = build_function_level_semantic_fact_ir_payload(functions, source=str(OPAQUE_FIXTURE))
    yul_nodes = [
        node for function in payload["functions"]
        for node in function["semantic_nodes"]
        if node.get("source_lang") == "yul"
    ]
    opaque = [node for node in yul_nodes if node["kind"] == "UnmodeledSSeirOverlay"]
    assert opaque
    assert all(node.get("rvalue") == "opaqueYulValue()" for node in opaque if node.get("lvalue"))
    assert all((node.get("semantic") or {}).get("unmodeled_reason") == "residual_low_level_yul_expression" for node in opaque)
    for function in payload["functions"]:
        fact_cfg_ids = {
            semantic_id for block in function["fact_cfg"]["blocks"]
            for semantic_id in block.get("semantic_ids") or []
        }
        assert {node["semantic_id"] for node in function["semantic_nodes"]}.issubset(fact_cfg_ids)

    c_like = render_sfir_c_like(payload)
    cfg_text = render_fact_cfg_text(payload)
    dot = "\n".join(render_fact_cfg_dot(function) for function in payload["functions"])
    public = json.dumps(payload, ensure_ascii=False)
    for rendered in (public, c_like, cfg_text, dot):
        assert "mload(" not in rendered and "mstore(" not in rendered and "effect_" not in rendered
    assert "opaqueYulValue()" in c_like and "opaqueYulValue()" in cfg_text and "opaqueYulValue()" in dot


if __name__ == "__main__":
    test_completed_overlay_surface_reaches_final_sfir()
    test_residual_yul_memory_access_is_opaque_at_the_sfir_boundary()
    print("sseir overlay surface tests passed")
