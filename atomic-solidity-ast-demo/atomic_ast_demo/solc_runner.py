from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


class SolcError(RuntimeError):
    pass


class SolcRunner:
    def __init__(self, binary: str | None = None) -> None:
        self.binary = binary or self._default_binary()

    @staticmethod
    def _default_binary() -> str:
        env_binary = os.environ.get("SOLC_BINARY")
        if env_binary:
            return env_binary
        path_binary = shutil.which("solc")
        if path_binary:
            return path_binary
        local = Path(__file__).resolve().parents[1] / ".solc" / "solc-linux-amd64-v0.8.36"
        if local.exists():
            return str(local)
        return "solc"

    def version(self) -> str:
        proc = subprocess.run(
            [self.binary, "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode != 0:
            raise SolcError(proc.stderr.strip() or f"failed to run {self.binary}")
        return proc.stdout.strip()

    def standard_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        proc = subprocess.run(
            [self.binary, "--standard-json"],
            input=json.dumps(payload),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode != 0:
            raise SolcError(proc.stderr.strip() or proc.stdout.strip())
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise SolcError(f"solc returned non-JSON output: {proc.stdout[:500]}") from exc

    def compile_source(self, source_name: str, content: str) -> dict[str, Any]:
        payload = {
            "language": "Solidity",
            "sources": {source_name: {"content": content}},
            "settings": {
                "outputSelection": {
                    "*": {
                        "*": ["abi", "evm.bytecode.object", "evm.deployedBytecode.object"],
                        "": ["ast"],
                    }
                }
            },
        }
        return self.standard_json(payload)

    def compile_ast(self, source_name: str, ast: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "language": "SolidityAST",
            "sources": {source_name: {"ast": ast}},
            "settings": {
                "experimental": True,
                "outputSelection": {
                    "*": {
                        "*": ["abi", "evm.bytecode.object", "evm.deployedBytecode.object"],
                        "": ["ast"],
                    }
                },
            },
        }
        return self.standard_json(payload)


def error_count(output: dict[str, Any]) -> int:
    return sum(1 for item in output.get("errors", []) if item.get("severity") == "error")


def first_bytecode(output: dict[str, Any]) -> str:
    for source_contracts in output.get("contracts", {}).values():
        for contract in source_contracts.values():
            bytecode = contract.get("evm", {}).get("bytecode", {}).get("object", "")
            if bytecode:
                return bytecode
    return ""

