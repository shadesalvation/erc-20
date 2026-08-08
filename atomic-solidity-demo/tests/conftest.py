from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from atomic_solidity.compiler import find_solc
from atomic_solidity.diagnostics import SolcNotFoundError
from atomic_solidity.lowerer import lower_file
from atomic_solidity.serializer import to_jsonable


ROOT = Path(__file__).resolve().parents[1]


def pytest_configure(config: pytest.Config) -> None:
    try:
        find_solc()
    except SolcNotFoundError:
        config.option.solc_missing = True
    else:
        config.option.solc_missing = False


@pytest.fixture(autouse=True)
def require_solc(request: pytest.FixtureRequest) -> None:
    if getattr(request.config.option, "solc_missing", False):
        pytest.skip("solc is not installed; install solc or set SOLC_BINARY")


def lower_example(name: str) -> dict[str, Any]:
    return to_jsonable(lower_file(ROOT / "examples" / name))


def lower_source(tmp_path: Path, source: str) -> dict[str, Any]:
    path = tmp_path / "Case.sol"
    path.write_text(source, encoding="utf-8")
    return to_jsonable(lower_file(path))


def operations(program: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for source_file in program["source_files"]:
        for contract in source_file["contracts"]:
            for function in contract["functions"]:
                for block in function["blocks"]:
                    result.extend(block["operations"])
                    if block["terminator"] is not None:
                        result.append(block["terminator"])
    return result


def function_ops(program: dict[str, Any], function_name: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for source_file in program["source_files"]:
        for contract in source_file["contracts"]:
            for function in contract["functions"]:
                if function["name"] != function_name:
                    continue
                for block in function["blocks"]:
                    result.extend(block["operations"])
                    if block["terminator"] is not None:
                        result.append(block["terminator"])
    return result
