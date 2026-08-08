from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class Diagnostic:
    code: str
    message: str
    node_type: str | None = None
    src: str | None = None
    operator: str | None = None

    def to_json(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

