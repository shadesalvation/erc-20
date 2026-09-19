#!/usr/bin/env python3
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from assembly_ast_cfg import yul_expression
from assembly_memory_ssa import direct_call
from s_seir_yul_normalize import normalize_expr


@dataclass
class EvaluationStep:
    order: int
    temp: str
    expression: str
    expression_normalized: str
    call: str | None
    raw_args: list[str]
    evaluated_args: list[str]
    argument_nodes: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "order": self.order,
            "temp": self.temp,
            "expression": self.expression,
            "expression_normalized": self.expression_normalized,
            "call": self.call,
            "raw_args": self.raw_args,
            "evaluated_args": self.evaluated_args,
            "argument_nodes": self.argument_nodes,
        }


class YulEvaluationOrder:
    """Materialize Yul expression evaluation order from the AST.

    Solidity's Yul/EVM dialect evaluates function-call arguments from right to
    left. This helper follows the AST rather than splitting strings, and emits a
    temporary for every function-call expression in the exact order it executes.
    """

    def __init__(self, stmt_ref: str | None):
        self.prefix = self.safe_prefix(stmt_ref or "stmt")
        self.counter = 0
        self.steps: list[EvaluationStep] = []

    def materialize(self, expr: dict[str, Any] | None, context: str = "condition") -> dict[str, Any]:
        result = self.eval_expr(expr, context=context)
        return {
            "evaluation_model": "yul_ast_right_to_left_function_call_arguments",
            "final": result,
            "final_normalized": normalize_expr(result, context=context),
            "steps": [step.to_dict() for step in self.steps],
        }

    def eval_expr(self, node: Any, context: str = "value") -> str:
        call, arguments = direct_call(node)
        if not call:
            return yul_expression(node)

        evaluated_args = ["" for _ in arguments]
        for index in range(len(arguments) - 1, -1, -1):
            evaluated_args[index] = self.eval_expr(arguments[index], context="value")

        raw_args = [yul_expression(argument) for argument in arguments]
        expression = f"{call}({', '.join(evaluated_args)})"
        self.counter += 1
        temp = f"__sseir_eval_{self.prefix}_{self.counter}"
        normalized = normalize_expr(expression, context=context)
        self.steps.append(EvaluationStep(
            order=self.counter,
            temp=temp,
            expression=expression,
            expression_normalized=normalized,
            call=call,
            raw_args=raw_args,
            evaluated_args=evaluated_args,
            # Preserve AST leaf kind/identity instead of requiring consumers
            # to reverse-parse raw_args or normalized expression strings.
            argument_nodes=[{
                "node_type": arg.get("nodeType"),
                **({"name": arg.get("name")} if arg.get("nodeType") == "YulIdentifier" else {}),
                **({"value": arg.get("value"), "literal_kind": arg.get("kind")} if arg.get("nodeType") == "YulLiteral" else {}),
                **({"result_temp": evaluated_args[index]} if arg.get("nodeType") == "YulFunctionCall" else {}),
            } for index, arg in enumerate(arguments)],
        ))
        return temp

    @staticmethod
    def safe_prefix(value: str) -> str:
        text = re.sub(r"\W+", "_", str(value))
        text = text.strip("_") or "stmt"
        if text[0].isdigit():
            text = f"s_{text}"
        return text
