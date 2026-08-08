from __future__ import annotations

from typing import Any


IMPORTANT_KEYS = {
    "nodeType",
    "id",
    "name",
    "operator",
    "typeName",
    "leftExpression",
    "rightExpression",
    "subExpression",
    "expression",
    "condition",
    "initialValue",
    "statements",
    "declarations",
    "assignments",
    "leftHandSide",
    "rightHandSide",
    "trueBody",
    "falseBody",
    "body",
    "nodes",
    "parameters",
    "returnParameters",
    "functionReturnParameters",
    "src",
}


def simplify(node: Any) -> Any:
    if isinstance(node, list):
        return [simplify(item) for item in node]
    if not isinstance(node, dict):
        return node
    result: dict[str, Any] = {}
    for key, value in node.items():
        if key in IMPORTANT_KEYS:
            result[key] = simplify(value)
        elif key == "typeDescriptions":
            result[key] = value
    return result

