from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Diagnostic:
    code: str
    message: str
    severity: str = "warning"
    node_type: str | None = None
    ast_id: int | None = None
    src: str | None = None
    source_file: str | None = None
    details: dict[str, Any] | None = None


class AtomicSolidityError(RuntimeError):
    """Base exception for user-facing demo errors."""


class SolcNotFoundError(AtomicSolidityError):
    """Raised when solc cannot be located."""


class SolcCompilationError(AtomicSolidityError):
    """Raised when solc returns at least one error severity diagnostic."""
