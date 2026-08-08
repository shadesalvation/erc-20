from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .ast_utils import write_json, write_text
from .atomicity_validator import AtomicityValidator
from .expression_splitter import SolidityExpressionSplitter
from .readable import simplify
from .solc_runner import SolcRunner, error_count, first_bytecode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Atomize nested Solidity AST expressions and validate with solc.")
    parser.add_argument("source", type=Path, help="single Solidity source file")
    parser.add_argument("--output-dir", type=Path, default=Path("output"), help="directory for generated artifacts")
    parser.add_argument("--solc", default=None, help="solc binary path; defaults to SOLC_BINARY, PATH, then .solc/solc-linux-amd64-v0.8.36")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_pipeline(args.source, args.output_dir, args.solc)
    print_summary(result)
    return 0 if result["overall_passed"] else 1


def run_pipeline(source_path: Path, output_dir: Path, solc_binary: str | None = None) -> dict[str, Any]:
    source_path = source_path.resolve()
    output_dir = output_dir.resolve()
    source_name = source_path.name
    content = source_path.read_text(encoding="utf-8")
    solc = SolcRunner(solc_binary)
    compiler_version = solc.version()

    print("[1] Compiling Solidity source...")
    source_output = solc.compile_source(source_name, content)
    source_errors = error_count(source_output)
    original_ast = source_output.get("sources", {}).get(source_name, {}).get("ast")
    original_bytecode = first_bytecode(source_output)
    source_passed = source_errors == 0 and bool(original_ast) and bool(original_bytecode)
    print(f"    {'PASS' if source_passed else 'FAIL'}")

    write_json(output_dir / "original_ast.json", original_ast)
    write_text(output_dir / "original_bytecode.txt", original_bytecode)

    print("[2] Round-tripping original AST through SolidityAST importer...")
    roundtrip_output = solc.compile_ast(source_name, original_ast)
    roundtrip_errors = error_count(roundtrip_output)
    roundtrip_bytecode = first_bytecode(roundtrip_output)
    roundtrip_passed = roundtrip_errors == 0 and bool(roundtrip_bytecode)
    print(f"    {'PASS' if roundtrip_passed else 'FAIL'}")

    print("[3] Splitting nested expressions...")
    splitter = SolidityExpressionSplitter(original_ast)
    atomic_ast = splitter.split_source_unit()
    diagnostics = [diag.to_json() for diag in splitter.diagnostics]
    print(f"    generated temporaries: {splitter.generated_temporaries}")
    print(f"    generated atomic statements: {splitter.generated_atomic_statements}")
    print(f"    unsupported expressions: {len(diagnostics)}")

    write_json(output_dir / "atomic_ast.json", atomic_ast)
    write_json(output_dir / "atomic_ast_readable.json", simplify(atomic_ast))
    if diagnostics:
        write_json(output_dir / "unsupported_diagnostics.json", diagnostics)

    print("[4] Validating Atomic AST...")
    atomicity = AtomicityValidator(original_ast, atomic_ast).validate(
        splitter.generated_temporaries,
        splitter.generated_atomic_statements,
        len(diagnostics),
    )
    print(f"    atomicity: {'PASS' if atomicity['passed'] else 'FAIL'}")

    print("[5] Recompiling Atomic AST with solc...")
    atomic_output = solc.compile_ast(source_name, atomic_ast)
    atomic_errors = error_count(atomic_output)
    atomic_bytecode = first_bytecode(atomic_output)
    atomic_compile_passed = atomic_errors == 0
    bytecode_generated = bool(atomic_bytecode)
    print(f"    semantic analysis: {'PASS' if atomic_compile_passed else 'FAIL'}")
    print(f"    bytecode generation: {'PASS' if bytecode_generated else 'FAIL'}")

    reanalyzed_ast = atomic_output.get("sources", {}).get(source_name, {}).get("ast")
    write_json(output_dir / "reanalyzed_atomic_ast.json", reanalyzed_ast)
    write_text(output_dir / "atomic_bytecode.txt", atomic_bytecode)
    write_json(output_dir / "atomic_compile_output.json", atomic_output)

    report = {
        "compiler_version": compiler_version,
        "source_file": str(source_path),
        "output_dir": str(output_dir),
        "source_compilation": {
            "passed": source_passed,
            "error_count": source_errors,
            "bytecode_generated": bool(original_bytecode),
            "bytecode_size": len(original_bytecode) // 2 if original_bytecode else 0,
        },
        "original_ast_roundtrip": {
            "passed": roundtrip_passed,
            "error_count": roundtrip_errors,
            "bytecode_generated": bool(roundtrip_bytecode),
            "bytecode_size": len(roundtrip_bytecode) // 2 if roundtrip_bytecode else 0,
        },
        "atomicity": atomicity,
        "atomic_ast_compilation": {
            "passed": atomic_compile_passed,
            "error_count": atomic_errors,
            "bytecode_generated": bytecode_generated,
            "bytecode_size": len(atomic_bytecode) // 2 if atomic_bytecode else 0,
        },
        "unsupported": diagnostics,
    }
    report["overall_passed"] = (
        source_passed
        and roundtrip_passed
        and atomicity["passed"]
        and atomic_compile_passed
        and bytecode_generated
    )
    write_json(output_dir / "validation_report.json", report)
    return report


def print_summary(report: dict[str, Any]) -> None:
    output_dir = Path(report["output_dir"])
    print()
    print("Result:")
    print(f"    {output_dir / 'atomic_ast.json'}")
    print(f"    {output_dir / 'validation_report.json'}")
    print()
    print(f"overall: {'PASS' if report['overall_passed'] else 'FAIL'}")
    if not report["overall_passed"]:
        print(json.dumps(report, indent=2)[:4000])
