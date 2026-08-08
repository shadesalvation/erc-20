from __future__ import annotations

from pathlib import Path
from typing import Any

from atomic_ast_demo.ast_utils import walk
from atomic_ast_demo.cli import run_pipeline


ROOT = Path(__file__).resolve().parents[1]


def names(ast: dict[str, Any]) -> set[str]:
    return {
        node["name"]
        for node in walk(ast)
        if node.get("nodeType") == "VariableDeclaration" and node.get("name")
    }


def binary_ops(ast: dict[str, Any]) -> list[str]:
    return [
        node["operator"]
        for node in walk(ast)
        if node.get("nodeType") == "BinaryOperation"
    ]


def run_example(tmp_path: Path, filename: str) -> dict[str, Any]:
    return run_pipeline(ROOT / "examples" / filename, tmp_path / filename.removesuffix(".sol"))


def test_binary_nesting_generates_one_temporary_and_compiles(tmp_path: Path) -> None:
    report = run_example(tmp_path, "Demo1.sol")
    assert report["overall_passed"]
    assert report["atomicity"]["generated_temporaries"] == 1
    assert report["atomicity"]["atomic_nested_operation_count"] == 0
    ast = (tmp_path / "Demo1" / "atomic_ast.json").read_text()
    assert "__atom0" in ast


def test_multi_level_binary_generates_three_temporaries(tmp_path: Path) -> None:
    report = run_example(tmp_path, "Demo2.sol")
    assert report["overall_passed"]
    assert report["atomicity"]["generated_temporaries"] == 3
    assert report["atomic_ast_compilation"]["bytecode_generated"]


def test_unary_and_binary_are_atomized(tmp_path: Path) -> None:
    report = run_example(tmp_path, "Demo3.sol")
    assert report["overall_passed"]
    assert report["atomicity"]["generated_temporaries"] == 3


def test_if_condition_prefixes_before_if(tmp_path: Path) -> None:
    report = run_example(tmp_path, "Demo4.sol")
    assert report["overall_passed"]
    assert report["atomicity"]["generated_temporaries"] == 2


def test_assignment_rhs_is_replaced_by_temporary(tmp_path: Path) -> None:
    report = run_example(tmp_path, "Demo5.sol")
    assert report["overall_passed"]
    assert report["atomicity"]["generated_temporaries"] == 2


def test_short_circuit_is_preserved_and_reported(tmp_path: Path) -> None:
    report = run_example(tmp_path, "UnsupportedDemo.sol")
    assert report["overall_passed"]
    assert report["atomicity"]["generated_temporaries"] == 0
    assert report["atomicity"]["unsupported_expressions"] == 1
    atomic_ast = report_path_json(tmp_path / "UnsupportedDemo" / "atomic_ast.json")
    assert "&&" in binary_ops(atomic_ast)
    assert "__atom0" not in names(atomic_ast)


def report_path_json(path: Path) -> dict[str, Any]:
    import json

    return json.loads(path.read_text(encoding="utf-8"))

