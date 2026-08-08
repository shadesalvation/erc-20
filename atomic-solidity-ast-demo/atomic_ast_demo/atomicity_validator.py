from __future__ import annotations

from typing import Any

from .ast_utils import walk
from .expression_splitter import SUPPORTED_BINARY, SUPPORTED_UNARY, UNSUPPORTED_BINARY


def is_supported_operation(node: dict[str, Any]) -> bool:
    if node.get("nodeType") == "BinaryOperation":
        return node.get("operator") in SUPPORTED_BINARY
    if node.get("nodeType") == "UnaryOperation":
        return node.get("operator") in SUPPORTED_UNARY and node.get("prefix") is not False
    return False


def is_unsupported_boundary(node: dict[str, Any]) -> bool:
    if node.get("nodeType") == "BinaryOperation" and node.get("operator") in UNSUPPORTED_BINARY:
        return True
    return node.get("nodeType") == "Conditional"


def count_nested_supported_operations(ast: dict[str, Any]) -> int:
    total = 0
    for node in walk(ast):
        if is_supported_operation(node):
            for child in operation_operands(node):
                if contains_supported_operation(child):
                    total += 1
    return total


def operation_operands(node: dict[str, Any]) -> list[Any]:
    if node.get("nodeType") == "BinaryOperation":
        return [node.get("leftExpression"), node.get("rightExpression")]
    if node.get("nodeType") == "UnaryOperation":
        return [node.get("subExpression")]
    return []


def contains_supported_operation(node: Any) -> bool:
    if isinstance(node, dict):
        if is_unsupported_boundary(node):
            return False
        if is_supported_operation(node):
            return True
        return any(contains_supported_operation(value) for value in node.values())
    if isinstance(node, list):
        return any(contains_supported_operation(item) for item in node)
    return False


class AtomicityValidator:
    def __init__(self, original_ast: dict[str, Any], atomic_ast: dict[str, Any]) -> None:
        self.original_ast = original_ast
        self.atomic_ast = atomic_ast

    def validate(self, generated_temporaries: int, generated_atomic_statements: int, unsupported_count: int) -> dict[str, Any]:
        original_nested = count_nested_supported_operations(self.original_ast)
        atomic_nested = count_nested_supported_operations(self.atomic_ast)
        return {
            "passed": atomic_nested == 0,
            "original_nested_operation_count": original_nested,
            "atomic_nested_operation_count": atomic_nested,
            "generated_temporaries": generated_temporaries,
            "generated_atomic_statements": generated_atomic_statements,
            "unsupported_expressions": unsupported_count,
        }

