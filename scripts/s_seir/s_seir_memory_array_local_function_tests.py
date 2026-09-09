#!/usr/bin/env python3
"""Focused regression for SSA array-read patterns and local Yul functions."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import json

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "legacy_yul", ROOT / "s_seir"):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from s_seir_pipeline import build_sseir
from s_seir_semantic_fact_adapter import build_function_level_semantic_fact_ir_payload


def sample_source() -> Path:
    return ROOT.parent / "人工构造样例" / "07_内联Assembly_memorysafe_Yul操作" / "contracts" / "AssemblyERC20.sol"


def matrix_source() -> Path:
    return ROOT / "s_seir" / "fixtures" / "memory_array_matrix.sol"


def test_array_pattern_and_local_function_projection() -> None:
    source = sample_source()
    solc = ROOT.parent / ".venv" / "bin" / "solc"
    slither = ROOT.parent / ".venv" / "bin" / "slither"
    with tempfile.TemporaryDirectory(prefix="sseir_array_local_test_") as directory:
        functions = build_sseir(source, str(solc), str(slither), Path(directory))
    fn = next(item for item in functions if item.function_id == "AssemblyERC20.sumSkipping(uint256[])")
    overlays = fn.semantic_overlays

    array_read = next(item for item in overlays if item.kind == "MemoryArrayElementRead" and item.attrs.get("target") == "v")
    assert array_read.attrs["access"] == "values[i]", array_read.attrs
    assert array_read.attrs["bounds_proof"]["predicate"] == "(i < len)", array_read.attrs
    assert array_read.attrs["pointer_value_effects"], array_read.attrs
    assert [item["operation"] for item in array_read.attrs["pattern_evidence"]["steps"]] == ["add", "mul", "add", "mload"], array_read.attrs

    definition = next(item for item in overlays if item.kind == "YulLocalFunctionDefinition")
    assert definition.attrs["name"] == "addOrLeave", definition.attrs
    assert definition.attrs["body"][0]["condition"] == "(a > (~b))", definition.attrs
    assert "gt(" not in str(definition.attrs["semantic_cfg"]), definition.attrs
    assert "add(" not in str(definition.attrs["body"]), definition.attrs
    calls = [item for item in overlays if item.kind == "YulLocalFunctionCall"]
    assert [item.attrs["arguments"] for item in calls] == [["total", "v"], ["total", "(v * 2)"]], calls
    switch = next(item for item in overlays if item.kind == "Predicate" and item.attrs.get("semantic_model") == "cfg_switch_discriminant")
    assert switch.attrs["expression"] == "(v & 1)", switch.attrs
    assert switch.attrs["switch_edges"] == [
        {"kind": "case: 0", "case_value": "0", "guard": "((v & 1) == 0)"},
        {"kind": "default", "guard": "!(((v & 1) == 0))"},
    ], switch.attrs

    sfir = build_function_level_semantic_fact_ir_payload(functions)
    result = next(item for item in sfir["functions"] if item["function_id"] == fn.function_id)
    nodes = result["semantic_nodes"]
    assert any(item.get("lvalue") == "v" and item.get("rvalue") == "values[i]" for item in nodes), nodes
    assert not any("mload(" in str(item.get("rvalue") or "") for item in nodes), nodes
    assert not any(item.get("lvalue") == "ptr" for item in nodes), nodes
    assert len([item for item in nodes if item["kind"] == "InternalCall" and item.get("semantic", {}).get("operation") == "yul_local_function_call"]) == 2, nodes
    assert not result.get("diagnostics"), result["diagnostics"]
    switch_block = next(item for item in result["fact_cfg"]["blocks"] if item["block_id"].endswith("bb_asm3_n15"))
    assert switch_block["terminator"]["condition"] == "(v & 1)", switch_block
    switch_edges = [item for item in result["fact_cfg"]["edges"] if item["from"].endswith("bb_asm3_n15")]
    assert [(item["kind"], item.get("guard")) for item in switch_edges] == [
        ("case: 0", "((v & 1) == 0)"),
        ("default", "!(((v & 1) == 0))"),
    ], switch_edges
    assert not any(token in json.dumps(result["fact_cfg"]) for token in ("and(", "eq(", "gt(", "lt(")), result["fact_cfg"]

    selector_fn = next(item for item in functions if item.function_id == "AssemblyERC20.selectorFromCalldata()")
    selector_overlay = next(item for item in selector_fn.semantic_overlays if item.kind == "CalldataSelectorRead")
    assert selector_overlay.attrs["access"] == "msg.sig", selector_overlay.attrs
    selector_result = next(item for item in sfir["functions"] if item["function_id"] == selector_fn.function_id)
    selector_nodes = selector_result["semantic_nodes"]
    assert any(item.get("lvalue") == "selector" and item.get("rvalue") == "msg.sig" for item in selector_nodes), selector_nodes
    assert len([item for item in selector_nodes if item.get("lvalue") == "selector"]) == 1, selector_nodes
    assert not any("calldataload" in str(item.get("rvalue") or "") for item in selector_nodes), selector_nodes

    context_result = next(item for item in sfir["functions"] if item["function_id"] == "AssemblyERC20.rawContext()")
    context_size = next(item for item in context_result["semantic_nodes"] if item.get("lvalue") == "size")
    assert context_size["rvalue"] == "msg.data.length", context_size
    assert context_size["reads"] == [], context_size
    assert not context_result.get("diagnostics"), context_result["diagnostics"]

    move_result = next(item for item in sfir["functions"] if item["function_id"] == "AssemblyERC20._assemblyMove(address, address, uint256)")
    move_require = next(item for item in move_result["semantic_nodes"] if item["kind"] == "Require")
    assert move_require["rvalue"] == 'require(bool,string)(TMP_32, "zero")', move_require
    assert move_require["reads"] == ["TMP_32"], move_require
    assert move_require["semantic"]["arguments"] == ["TMP_32", '"zero"'], move_require
    assert not move_result.get("diagnostics"), move_result["diagnostics"]


def test_multidimensional_memory_array_projection() -> None:
    solc = ROOT.parent / ".venv" / "bin" / "solc"
    slither = ROOT.parent / ".venv" / "bin" / "slither"
    with tempfile.TemporaryDirectory(prefix="sseir_matrix_test_") as directory:
        functions = build_sseir(matrix_source(), str(solc), str(slither), Path(directory))
    fn = next(item for item in functions if item.function_id == "MemoryArrayMatrix.sumMatrix(uint256[][])")
    overlays = fn.semantic_overlays
    row = next(item for item in overlays if item.kind == "MemoryArrayElementRead" and item.attrs.get("target") == "row")
    value = next(item for item in overlays if item.kind == "MemoryArrayElementRead" and item.attrs.get("target") == "value")
    row_length = next(item for item in overlays if item.kind == "MemoryArrayLengthRead" and item.attrs.get("target") == "rowLength")
    assert row.attrs["access"] == "matrix[i]", row.attrs
    assert row.attrs["element_type"] == "uint256[]", row.attrs
    assert row_length.attrs["access"] == "matrix[i].length", row_length.attrs
    assert value.attrs["access"] == "matrix[i][j]", value.attrs
    assert value.attrs["bounds_proof"]["predicate"] == "(j < rowLength)", value.attrs

    sfir = build_function_level_semantic_fact_ir_payload(functions)
    result = next(item for item in sfir["functions"] if item["function_id"] == fn.function_id)
    nodes = result["semantic_nodes"]
    assert any(item.get("lvalue") == "row" and item.get("rvalue") == "matrix[i]" for item in nodes), nodes
    assert any(item.get("lvalue") == "rowLength" and item.get("rvalue") == "matrix[i].length" for item in nodes), nodes
    assert any(item.get("lvalue") == "value" and item.get("rvalue") == "matrix[i][j]" for item in nodes), nodes
    assert len([item for item in nodes if item.get("lvalue") == "row"]) == 1, nodes
    assert len([item for item in nodes if item.get("lvalue") == "rowLength"]) == 1, nodes
    assert len([item for item in nodes if item.get("lvalue") == "value"]) == 1, nodes
    assert not any("mload(" in str(item.get("rvalue") or "") for item in nodes), nodes
    assert not result.get("diagnostics"), result["diagnostics"]


if __name__ == "__main__":
    test_array_pattern_and_local_function_projection()
    test_multidimensional_memory_array_projection()
    print("PASS array pattern and local Yul function projection")
