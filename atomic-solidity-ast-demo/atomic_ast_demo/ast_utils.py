from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Iterable


Json = dict[str, Any]


def deep_clone(node: Any) -> Any:
    return copy.deepcopy(node)


def walk(node: Any) -> Iterable[Json]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk(item)


def max_ast_id(ast: Json) -> int:
    ids = [node["id"] for node in walk(ast) if isinstance(node.get("id"), int)]
    return max(ids, default=0)


def collect_declared_names(ast: Json) -> set[str]:
    names: set[str] = set()
    for node in walk(ast):
        if node.get("nodeType") == "VariableDeclaration" and node.get("name"):
            names.add(node["name"])
    return names


class IdGenerator:
    def __init__(self, start_after: int) -> None:
        self._next = start_after + 1

    def new(self) -> int:
        value = self._next
        self._next += 1
        return value


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=False), encoding="utf-8")


def write_text(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")


def node_label(node: Json) -> str:
    if node.get("nodeType") == "BinaryOperation":
        return f"BinaryOperation {node.get('operator')}"
    if node.get("nodeType") == "UnaryOperation":
        return f"UnaryOperation {node.get('operator')}"
    return str(node.get("nodeType"))

