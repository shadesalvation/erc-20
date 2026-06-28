#!/usr/bin/env python3
"""Recover arithmetic and comparison expressions from Yul into Solidity-like text.

This module parses Yul call expressions into a small AST before rendering them.
It does not modify source. Unsupported EVM-specific operations remain explicit
``yul*`` helpers, so their semantics are not silently changed.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assembly_semantic_ir import build_report, parse_int_literal, split_assignment, strip_ssa


TOKEN_RE = re.compile(
    r"\s*(?:(?P<number>0x[0-9a-fA-F]+|\d+)|(?P<ident>[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*)|(?P<punct>[(),]))"
)

DIRECT_ARITHMETIC = {
    "add": "+",
    "sub": "-",
    "mul": "*",
    "div": "/",
    "mod": "%",
}
COMPARISONS = {"eq": "==", "lt": "<", "gt": ">", "slt": "<", "sgt": ">"}
DIVISION_OPS = {"div", "mod", "sdiv", "smod"}
BITWISE_BINARY = {
    "and": "&",
    "or": "|",
    "xor": "^",
}
ENVIRONMENT_OPS = {
    "address": "address(this)",
    "caller": "msg.sender",
    "callvalue": "msg.value",
    "origin": "tx.origin",
    "gasprice": "tx.gasprice",
    "coinbase": "block.coinbase",
    "timestamp": "block.timestamp",
    "number": "block.number",
    "difficulty": "block.prevrandao",
    "prevrandao": "block.prevrandao",
    "gaslimit": "block.gaslimit",
    "chainid": "block.chainid",
    "selfbalance": "address(this).balance",
    "basefee": "block.basefee",
    "gas": "gasleft()",
    "calldatasize": "msg.data.length",
}

HELPER_OPS = {
    "sdiv": "yulSdiv",
    "smod": "yulSmod",
    "exp": "yulExp",
    "sar": "yulSar",
    "signextend": "yulSignextend",
    "byte": "yulByte",
}


class ParseError(ValueError):
    pass


@dataclass(frozen=True)
class Literal:
    value: str


@dataclass(frozen=True)
class Identifier:
    name: str


@dataclass(frozen=True)
class Call:
    name: str
    args: tuple["Expr", ...]


Expr = Literal | Identifier | Call


class YulExpressionParser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.tokens = self._tokenize(text)
        self.index = 0

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        tokens: list[str] = []
        pos = 0
        while pos < len(text):
            match = TOKEN_RE.match(text, pos)
            if not match:
                raise ParseError(f"unsupported token near {text[pos:pos + 24]!r}")
            token = match.group("number") or match.group("ident") or match.group("punct")
            tokens.append(token)
            pos = match.end()
        return tokens

    def peek(self) -> str | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def consume(self, expected: str | None = None) -> str:
        token = self.peek()
        if token is None:
            raise ParseError("unexpected end of expression")
        if expected is not None and token != expected:
            raise ParseError(f"expected {expected!r}, got {token!r}")
        self.index += 1
        return token

    def parse(self) -> Expr:
        expr = self.parse_expression()
        if self.peek() is not None:
            raise ParseError(f"unexpected token {self.peek()!r}")
        return expr

    def parse_expression(self) -> Expr:
        token = self.consume()
        if token in {"(", ")", ","}:
            raise ParseError(f"unexpected token {token!r}")
        if parse_int_literal(token) is not None:
            return Literal(token)
        if self.peek() != "(":
            return Identifier(token)

        self.consume("(")
        args: list[Expr] = []
        if self.peek() != ")":
            while True:
                args.append(self.parse_expression())
                if self.peek() != ",":
                    break
                self.consume(",")
        self.consume(")")
        return Call(token, tuple(args))


def parse_yul_expression(text: str) -> Expr:
    return YulExpressionParser(text.strip()).parse()


def ast_text(expr: Expr) -> str:
    if isinstance(expr, Literal):
        return expr.value
    if isinstance(expr, Identifier):
        return expr.name
    return f"{expr.name}({', '.join(ast_text(arg) for arg in expr.args)})"


def expression_uses_supported_op(expr: Expr) -> bool:
    if isinstance(expr, (Literal, Identifier)):
        return False
    if expr.name in ENVIRONMENT_OPS and not expr.args:
        return True
    if expr.name in DIRECT_ARITHMETIC or expr.name in COMPARISONS or expr.name == "iszero":
        return True
    if expr.name in HELPER_OPS or expr.name in {"addmod", "mulmod"}:
        return True
    return any(expression_uses_supported_op(arg) for arg in expr.args)


def is_boolean_node(expr: Expr) -> bool:
    if not isinstance(expr, Call):
        return False
    if expr.name in COMPARISONS or expr.name == "iszero":
        return True
    if expr.name in {"and", "or"} and len(expr.args) == 2:
        return all(is_boolean_node(arg) for arg in expr.args)
    return False


def identifier_type(expr: Expr, type_env: dict[str, str]) -> str | None:
    if not isinstance(expr, Identifier):
        return None
    name = strip_ssa(expr.name) or expr.name
    return type_env.get(name)


def render_value(expr: Expr, type_env: dict[str, str] | None = None) -> str:
    type_env = type_env or {}
    if isinstance(expr, Literal):
        return expr.value
    if isinstance(expr, Identifier):
        return strip_ssa(expr.name) or expr.name

    args = [render_value(arg, type_env) for arg in expr.args]
    if expr.name in ENVIRONMENT_OPS and not args:
        return ENVIRONMENT_OPS[expr.name]
    if expr.name in DIRECT_ARITHMETIC and len(args) == 2:
        return f"({args[0]} {DIRECT_ARITHMETIC[expr.name]} {args[1]})"
    if expr.name in COMPARISONS and len(args) == 2:
        return f"({args[0]} {COMPARISONS[expr.name]} {args[1]})"
    if expr.name == "iszero" and len(args) == 1:
        return f"({args[0]} == 0)"
    if expr.name in BITWISE_BINARY and len(args) == 2:
        return f"({args[0]} {BITWISE_BINARY[expr.name]} {args[1]})"
    if expr.name == "not" and len(args) == 1:
        return f"(~{args[0]})"
    if expr.name == "shl" and len(args) == 2:
        return f"({args[1]} << {args[0]})"
    if expr.name == "shr" and len(args) == 2:
        return f"({args[1]} >> {args[0]})"
    if expr.name in {"addmod", "mulmod"} and len(args) == 3:
        return f"{expr.name}({', '.join(args)})"
    if expr.name in HELPER_OPS:
        return f"{HELPER_OPS[expr.name]}({', '.join(args)})"
    return f"{expr.name}({', '.join(args)})"


def render_condition(expr: Expr, type_env: dict[str, str] | None = None) -> str:
    type_env = type_env or {}
    if not isinstance(expr, Call):
        value = render_value(expr, type_env)
        if identifier_type(expr, type_env) == "bool":
            return value
        if identifier_type(expr, type_env) == "address":
            return f"({value} != address(0))"
        return f"({value} != 0)"

    if expr.name in COMPARISONS and len(expr.args) == 2:
        left = render_value(expr.args[0], type_env)
        right = render_value(expr.args[1], type_env)
        if expr.name in {"slt", "sgt"}:
            return f"(int256({left}) {COMPARISONS[expr.name]} int256({right}))"
        return f"({left} {COMPARISONS[expr.name]} {right})"
    if expr.name == "iszero" and len(expr.args) == 1:
        inner = expr.args[0]
        if is_boolean_node(inner):
            return f"(!{render_condition(inner, type_env)})"
        value = render_value(inner, type_env)
        if identifier_type(inner, type_env) == "address":
            return f"({value} == address(0))"
        return f"({value} == 0)"
    if expr.name in {"and", "or"} and len(expr.args) == 2 and all(is_boolean_node(arg) for arg in expr.args):
        operator = "&&" if expr.name == "and" else "||"
        return f"({render_condition(expr.args[0], type_env)} {operator} {render_condition(expr.args[1], type_env)})"
    return f"({render_value(expr, type_env)} != 0)"


def division_guards(expr: Expr, type_env: dict[str, str] | None = None) -> list[str]:
    type_env = type_env or {}
    guards: list[str] = []

    def visit(node: Expr) -> None:
        if not isinstance(node, Call):
            return
        for arg in node.args:
            visit(arg)
        if node.name not in DIVISION_OPS or len(node.args) != 2:
            return
        denominator = render_value(node.args[1], type_env)
        guard = f"require({denominator} != 0);"
        if guard not in guards:
            guards.append(guard)

    visit(expr)
    return guards


def division_guards_for_expression(text: str, type_env: dict[str, str] | None = None) -> list[str]:
    try:
        return division_guards(parse_yul_expression(text), type_env)
    except ParseError:
        return []


@dataclass
class RenderedExpression:
    text: str
    ast: str
    context: str
    guards: list[str]
    helpers: list[str]


def helper_names(expr: Expr) -> list[str]:
    names: list[str] = []

    def visit(node: Expr) -> None:
        if not isinstance(node, Call):
            return
        if node.name in HELPER_OPS:
            helper = HELPER_OPS[node.name]
            if helper not in names:
                names.append(helper)
        if node.name in COMPARISONS:
            helper = f"yul{node.name.title()}"
            if helper not in names:
                names.append(helper)
        if node.name == "iszero" and "yulIszero" not in names:
            names.append("yulIszero")
        for arg in node.args:
            visit(arg)

    visit(expr)
    return names


def render_yul_expression(text: str, context: str = "value", type_env: dict[str, str] | None = None) -> RenderedExpression:
    expr = parse_yul_expression(text)
    rendered = render_condition(expr, type_env) if context == "condition" else render_value(expr, type_env)
    helpers = helper_names(expr)
    if context == "condition":
        helpers = [helper for helper in helpers if helper not in {"yulEq", "yulLt", "yulGt", "yulSlt", "yulSgt", "yulIszero"}]
    return RenderedExpression(
        text=rendered,
        ast=ast_text(expr),
        context=context,
        guards=division_guards(expr, type_env),
        helpers=helpers,
    )


def function_type_env(block: dict[str, Any]) -> dict[str, str]:
    return {
        param["name"]: param["type"]
        for param in block["context"].get("function_parameters", [])
        if param.get("name")
    }


def expression_for_op(op: dict[str, Any]) -> tuple[str, str, str | None] | None:
    if op["kind"] == "condition":
        return "condition", op["semantic"].get("condition", ""), None
    if op["kind"] == "loop":
        return "condition", op["semantic"].get("condition", ""), None
    statement = op.get("ssa_text", op["text"])
    target, expr = split_assignment(statement)
    if op["kind"] in {"storage_write", "memory_write"}:
        value = op.get("semantic", {}).get("value")
        if value:
            return "value", str(value), None
    if target is None or not expr:
        return None
    return "value", expr, target


def recover_arithmetic_for_block(block: dict[str, Any]) -> dict[str, Any]:
    type_env = function_type_env(block)
    entries: list[dict[str, Any]] = []
    for op in block["source_yul_semantic_ir"]:
        source = expression_for_op(op)
        if source is None:
            continue
        context, expression, target = source
        try:
            expr = parse_yul_expression(expression)
        except ParseError as exc:
            continue
        if not expression_uses_supported_op(expr):
            continue
        rendered = render_yul_expression(expression, context, type_env)
        if context == "condition":
            solidity_like = f"if {rendered.text}" if op["kind"] == "condition" else f"for {rendered.text}"
        elif target is not None:
            display_target = strip_ssa(target) or target or "_"
            solidity_like = f"{display_target} = {rendered.text};"
        elif op["kind"] == "memory_write":
            address = op.get("semantic", {}).get("write", {}).get("address", {})
            base = strip_ssa(address.get("base")) or address.get("base", "unknown")
            offset = address.get("offset")
            offset_text = str(offset) if offset is not None else address.get("offset_expr", "unknown")
            solidity_like = f"memory[{base} + {offset_text}] = {rendered.text};"
        else:
            solidity_like = None
        entries.append({
            "op_index": op["index"],
            "kind": op["kind"],
            "context": context,
            "target": strip_ssa(target) if target else None,
            "expression": strip_ssa(expression) or expression,
            "ast": strip_ssa(rendered.ast) or rendered.ast,
            "solidity_like": solidity_like,
            "division_guards": rendered.guards,
            "helpers": rendered.helpers,
            "control_path": op.get("control_path", []),
            "yul": op["text"],
        })
    return {"entries": entries}


def attach_arithmetic_recovery(report: dict[str, Any]) -> None:
    for block in report["assembly_blocks"]:
        block["arithmetic_compare_recovery"] = recover_arithmetic_for_block(block)


def arithmetic_replacements_by_op(block: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        item["op_index"]: item
        for item in block.get("arithmetic_compare_recovery", {}).get("entries", [])
    }


def format_arithmetic_text_ir(report: dict[str, Any]) -> str:
    lines = [
        f"INFO:AssemblyArithmeticCompareIR:Source {report['source_file']}",
        "INFO:AssemblyArithmeticCompareIR:Expressions are parsed as ASTs before Solidity-like rendering",
        "INFO:AssemblyArithmeticCompareIR:Dynamic div/mod operands add require(denominator != 0)",
        f"INFO:AssemblyArithmeticCompareIR:Assembly blocks {len(report['assembly_blocks'])}",
        "",
    ]
    current_contract: str | None = None
    for block in report["assembly_blocks"]:
        ctx = block["context"]
        if ctx["contract"] != current_contract:
            current_contract = ctx["contract"]
            lines.append(f"Contract {current_contract}")
        lines.append(f"\tFunction {ctx['contract']}.{ctx['function']}")
        entries = block.get("arithmetic_compare_recovery", {}).get("entries", [])
        if not entries:
            lines.append("\t\tNo arithmetic/comparison recovery entries.")
        for item in entries:
            lines.append(f"\t\t[{item['op_index']}] {item['kind'].upper()} context={item['context']}")
            lines.append(f"\t\t\tAST: {item['ast']}")
            lines.append(f"\t\t\tSolidityLike: {item['solidity_like']}")
            if item["division_guards"]:
                lines.append(f"\t\t\tDivisionGuards: {' '.join(item['division_guards'])}")
            if item["helpers"]:
                lines.append(f"\t\t\tHelpers: {', '.join(item['helpers'])}")
            lines.append(f"\t\t\tYul: {item['yul']}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Recover Yul arithmetic and comparisons into Solidity-like IR.")
    parser.add_argument("source", help="Solidity source file.")
    parser.add_argument("--slithir-ssa", help="Optional SlithIR-SSA text output.")
    parser.add_argument("-o", "--output", default="assembly_arithmetic_compare_ir.txt", help="Text report path.")
    args = parser.parse_args()

    source = Path(args.source)
    if not source.is_file():
        print(f"Source file not found: {source}", file=sys.stderr)
        return 2
    report = build_report(source, Path(args.slithir_ssa) if args.slithir_ssa else None)
    attach_arithmetic_recovery(report)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(format_arithmetic_text_ir(report), encoding="utf-8")
    print(f"Wrote arithmetic/comparison IR report: {output}")
    print(f"Assembly blocks found: {len(report['assembly_blocks'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
