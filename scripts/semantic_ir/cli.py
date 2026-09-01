#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
SSEIR = SCRIPTS / "s_seir"
for path in (SCRIPTS, SSEIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from semantic_ir import build_semantic_ir_program, write_semantic_ir_json, write_semantic_ir_text
from s_seir_pipeline import build_sseir
from semantic_fact import build_function_level_semantic_fact_payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Build mutable Semantic IR from a Solidity source file.")
    parser.add_argument("source", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=Path("outputs/semantic_ir.json"))
    parser.add_argument("--text-output", type=Path, help="Optional human-readable Semantic IR output.")
    parser.add_argument("--solc-bin")
    parser.add_argument("--slither-bin")
    parser.add_argument("--workdir", type=Path, default=Path("."))
    parser.add_argument("--no-branch-preprocess", action="store_true")
    parser.add_argument(
        "--include-analysis-indexes",
        action="store_true",
        help="Include optional Def-Use/SSA lookup indexes in Semantic IR output.",
    )
    args = parser.parse_args()
    functions = build_sseir(
        args.source,
        solc_bin=args.solc_bin,
        slither_bin=args.slither_bin,
        workdir=args.workdir.resolve(),
        branch_preprocess=not args.no_branch_preprocess,
    )
    facts = build_function_level_semantic_fact_payload(functions, source=str(args.source))
    program = build_semantic_ir_program(functions, facts, source=str(args.source))
    write_semantic_ir_json(
        args.output,
        program,
        include_analysis_indexes=args.include_analysis_indexes,
    )
    print(f"Wrote {args.output}")
    if args.text_output:
        write_semantic_ir_text(
            args.text_output,
            program,
            include_analysis_indexes=args.include_analysis_indexes,
        )
        print(f"Wrote {args.text_output}")


if __name__ == "__main__":
    main()
