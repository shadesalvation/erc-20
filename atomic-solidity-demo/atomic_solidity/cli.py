from __future__ import annotations

import argparse
from pathlib import Path

from atomic_solidity.diagnostics import AtomicSolidityError
from atomic_solidity.lowerer import lower_file
from atomic_solidity.serializer import write_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lower Solidity typed AST into Atomic IR JSON.")
    parser.add_argument("source", type=Path, help="Solidity source file")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="Directory for <source>.atomic.json",
    )
    args = parser.parse_args(argv)
    source = args.source
    if not source.is_file():
        parser.error(f"source file does not exist: {source}")
    try:
        program = lower_file(source)
    except AtomicSolidityError as exc:
        parser.exit(2, f"error: {exc}\n")
    output_path = args.output_dir / f"{source.stem}.atomic.json"
    write_json(output_path, program)
    print(output_path.as_posix())
    return 0
