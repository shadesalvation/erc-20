from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atomic_solidity.compiler import compile_standard_json
from atomic_solidity.diagnostics import Diagnostic


@dataclass
class LoadedSolidityAST:
    source_name: str
    ast: dict[str, Any]
    contracts: dict[str, Any]
    diagnostics: list[Diagnostic]
    compiler_version: str


def load_typed_ast(source_path: Path) -> LoadedSolidityAST:
    output, diagnostics, compiler_version = compile_standard_json(source_path)
    source_name = source_path.as_posix()
    source_output = output.get("sources", {}).get(source_name)
    if not source_output or "ast" not in source_output:
        raise ValueError(f"solc output did not contain sources[{source_name!r}].ast")
    contracts = output.get("contracts", {}).get(source_name, {})
    return LoadedSolidityAST(
        source_name=source_name,
        ast=source_output["ast"],
        contracts=contracts,
        diagnostics=diagnostics,
        compiler_version=compiler_version,
    )
