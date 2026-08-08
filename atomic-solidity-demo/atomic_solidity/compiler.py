from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from atomic_solidity.diagnostics import (
    Diagnostic,
    SolcCompilationError,
    SolcNotFoundError,
)


def find_solc() -> str:
    configured = os.environ.get("SOLC_BINARY")
    if configured:
        if Path(configured).is_file() and os.access(configured, os.X_OK):
            return configured
        raise SolcNotFoundError(
            f"SOLC_BINARY is set to {configured!r}, but it is not an executable file."
        )
    solc = shutil.which("solc")
    if solc:
        return solc
    raise SolcNotFoundError(
        "solc was not found on PATH. Install solc, or set SOLC_BINARY to a solc executable."
    )


def solc_version(solc: str | None = None) -> str:
    binary = solc or find_solc()
    completed = subprocess.run(
        [binary, "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return "unknown"
    for line in completed.stdout.splitlines():
        if "Version:" in line:
            return line.split("Version:", 1)[1].strip()
    return completed.stdout.strip() or "unknown"


def build_standard_json(source_path: Path, source_name: str) -> dict[str, Any]:
    return {
        "language": "Solidity",
        "sources": {
            source_name: {
                "content": source_path.read_text(encoding="utf-8"),
            }
        },
        "settings": {
            "outputSelection": {
                "*": {
                    "": ["ast"],
                    "*": ["storageLayout"],
                }
            }
        },
    }


def compile_standard_json(source_path: Path) -> tuple[dict[str, Any], list[Diagnostic], str]:
    solc = find_solc()
    source_name = source_path.as_posix()
    input_json = build_standard_json(source_path, source_name)
    completed = subprocess.run(
        [solc, "--standard-json"],
        input=json.dumps(input_json),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and not completed.stdout:
        raise SolcCompilationError(completed.stderr.strip() or "solc failed without output")
    try:
        output = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SolcCompilationError(f"solc returned invalid JSON: {exc}") from exc

    diagnostics: list[Diagnostic] = []
    fatal_messages: list[str] = []
    for item in output.get("errors", []):
        severity = item.get("severity", "warning")
        message = item.get("formattedMessage") or item.get("message") or "solc diagnostic"
        diagnostic = Diagnostic(
            code=f"SOLC_{severity.upper()}",
            message=message,
            severity=severity,
            source_file=source_name,
            details={
                "component": item.get("component"),
                "errorCode": item.get("errorCode"),
                "type": item.get("type"),
            },
        )
        diagnostics.append(diagnostic)
        if severity == "error":
            fatal_messages.append(message)
    if fatal_messages:
        raise SolcCompilationError("\n".join(fatal_messages))
    return output, diagnostics, solc_version(solc)
