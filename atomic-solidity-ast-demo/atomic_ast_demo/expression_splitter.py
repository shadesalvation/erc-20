from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .ast_utils import IdGenerator, collect_declared_names, deep_clone, max_ast_id
from .diagnostics import Diagnostic
from .type_utils import elementary_type_name, is_supported_value_type, type_descriptions


SUPPORTED_BINARY = {
    "+",
    "-",
    "*",
    "/",
    "%",
    "**",
    "&",
    "|",
    "^",
    "<<",
    ">>",
    "<",
    ">",
    "<=",
    ">=",
    "==",
    "!=",
}
UNSUPPORTED_BINARY = {"&&", "||"}
SUPPORTED_UNARY = {"!", "~", "-", "+"}
SOLIDITY_KEYWORDS = {
    "after",
    "alias",
    "apply",
    "auto",
    "bool",
    "break",
    "case",
    "catch",
    "constant",
    "continue",
    "contract",
    "copyof",
    "default",
    "delete",
    "do",
    "else",
    "emit",
    "event",
    "external",
    "false",
    "final",
    "for",
    "function",
    "if",
    "immutable",
    "implements",
    "import",
    "in",
    "indexed",
    "inline",
    "internal",
    "is",
    "let",
    "mapping",
    "match",
    "memory",
    "modifier",
    "mutable",
    "new",
    "null",
    "of",
    "override",
    "partial",
    "pragma",
    "private",
    "public",
    "pure",
    "reference",
    "relocatable",
    "return",
    "returns",
    "sizeof",
    "static",
    "storage",
    "struct",
    "super",
    "supports",
    "switch",
    "this",
    "throw",
    "true",
    "try",
    "type",
    "typedef",
    "typeof",
    "unchecked",
    "using",
    "var",
    "view",
    "virtual",
    "while",
}


@dataclass
class SplitResult:
    expression: dict[str, Any]
    prefix: list[dict[str, Any]]
    ok: bool


class SolidityExpressionSplitter:
    def __init__(self, ast: dict[str, Any]) -> None:
        self.ast = deep_clone(ast)
        self.ids = IdGenerator(max_ast_id(ast))
        self.used_names = collect_declared_names(ast)
        self.diagnostics: list[Diagnostic] = []
        self.generated_temporaries = 0
        self.generated_atomic_statements = 0

    def split_source_unit(self) -> dict[str, Any]:
        self._split_node(self.ast)
        return self.ast

    def _split_node(self, node: Any) -> None:
        if isinstance(node, dict):
            if node.get("nodeType") == "Block":
                self._split_block(node)
                return
            for value in node.values():
                self._split_node(value)
        elif isinstance(node, list):
            for item in node:
                self._split_node(item)

    def _split_block(self, block: dict[str, Any]) -> None:
        new_statements: list[dict[str, Any]] = []
        for statement in block.get("statements", []):
            prefix, rewritten = self.split_statement(statement, block["id"])
            new_statements.extend(prefix)
            new_statements.append(rewritten)
        block["statements"] = new_statements

    def split_statement(self, statement: dict[str, Any], scope_id: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        stmt = deep_clone(statement)
        node_type = stmt.get("nodeType")

        if node_type == "VariableDeclarationStatement":
            initial = stmt.get("initialValue")
            if initial:
                result = self.lower_rvalue(initial, scope_id, force_outline=False)
                if result.ok:
                    stmt["initialValue"] = result.expression
                    return result.prefix, stmt
            return [], stmt

        if node_type == "Return":
            expr = stmt.get("expression")
            if expr:
                result = self.lower_rvalue(expr, scope_id, force_outline=True)
                if result.ok:
                    stmt["expression"] = result.expression
                    return result.prefix, stmt
            return [], stmt

        if node_type == "ExpressionStatement":
            expr = stmt.get("expression")
            if not expr:
                return [], stmt
            if expr.get("nodeType") == "Assignment":
                return self._split_assignment_statement(stmt, expr, scope_id)
            result = self.lower_rvalue(expr, scope_id, force_outline=False)
            if result.ok:
                stmt["expression"] = result.expression
                return result.prefix, stmt
            return [], stmt

        if node_type == "IfStatement":
            prefix: list[dict[str, Any]] = []
            condition = stmt.get("condition")
            if condition:
                result = self.lower_rvalue(condition, scope_id, force_outline=True)
                if result.ok:
                    stmt["condition"] = result.expression
                    prefix.extend(result.prefix)
            self._split_embedded_statement(stmt, "trueBody")
            self._split_embedded_statement(stmt, "falseBody")
            return prefix, stmt

        if node_type in {"ForStatement", "WhileStatement", "DoWhileStatement"}:
            self._split_embedded_statement(stmt, "body")
            return [], stmt

        if node_type == "UncheckedBlock":
            if "statements" in stmt:
                fake_block = {"id": scope_id, "nodeType": "Block", "statements": stmt["statements"]}
                self._split_block(fake_block)
                stmt["statements"] = fake_block["statements"]
            return [], stmt

        if node_type == "Block":
            self._split_block(stmt)
            return [], stmt

        return [], stmt

    def _split_embedded_statement(self, owner: dict[str, Any], key: str) -> None:
        child = owner.get(key)
        if not isinstance(child, dict):
            return
        if child.get("nodeType") == "Block":
            self._split_block(child)
            return

        scope_id = owner.get("id", 0)
        prefix, rewritten = self.split_statement(child, scope_id)
        if prefix:
            owner[key] = {
                "id": self.ids.new(),
                "nodeType": "Block",
                "src": child.get("src", owner.get("src", "-1:-1:-1")),
                "statements": [*prefix, rewritten],
            }
        else:
            owner[key] = rewritten

    def _split_assignment_statement(
        self,
        stmt: dict[str, Any],
        assignment: dict[str, Any],
        scope_id: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if assignment.get("operator") != "=":
            self._unsupported(assignment, "compound assignments are not atomized in this demo")
            return [], stmt
        lhs = assignment.get("leftHandSide")
        rhs = assignment.get("rightHandSide")
        if not isinstance(lhs, dict) or lhs.get("nodeType") != "Identifier":
            self._unsupported(assignment, "only Identifier assignment LValues are supported")
            return [], stmt
        if not isinstance(rhs, dict):
            return [], stmt
        result = self.lower_rvalue(rhs, scope_id, force_outline=True)
        if result.ok:
            assignment["rightHandSide"] = result.expression
            stmt["expression"] = assignment
            return result.prefix, stmt
        return [], stmt

    def lower_rvalue(self, expr: dict[str, Any], scope_id: int, force_outline: bool) -> SplitResult:
        node_type = expr.get("nodeType")
        if node_type in {"Identifier", "Literal"}:
            return SplitResult(deep_clone(expr), [], True)

        if node_type == "BinaryOperation":
            operator = expr.get("operator")
            if operator in UNSUPPORTED_BINARY:
                self._unsupported(expr, "short-circuit expression is preserved")
                return SplitResult(deep_clone(expr), [], False)
            if operator not in SUPPORTED_BINARY:
                self._unsupported(expr, f"unsupported binary operator: {operator}")
                return SplitResult(deep_clone(expr), [], False)

            left = self.lower_rvalue(expr["leftExpression"], scope_id, force_outline=True)
            right = self.lower_rvalue(expr["rightExpression"], scope_id, force_outline=True)
            if not left.ok or not right.ok:
                return SplitResult(deep_clone(expr), [], False)

            rewritten = deep_clone(expr)
            rewritten["leftExpression"] = left.expression
            rewritten["rightExpression"] = right.expression
            prefix = [*left.prefix, *right.prefix]
            if force_outline:
                statement, identifier = self.outline_expression(rewritten, scope_id)
                return SplitResult(identifier, [*prefix, statement], True)
            return SplitResult(rewritten, prefix, True)

        if node_type == "UnaryOperation":
            operator = expr.get("operator")
            if operator not in SUPPORTED_UNARY or expr.get("prefix") is False:
                self._unsupported(expr, f"unsupported unary operator: {operator}")
                return SplitResult(deep_clone(expr), [], False)
            operand = self.lower_rvalue(expr["subExpression"], scope_id, force_outline=True)
            if not operand.ok:
                return SplitResult(deep_clone(expr), [], False)

            rewritten = deep_clone(expr)
            rewritten["subExpression"] = operand.expression
            prefix = list(operand.prefix)
            if force_outline:
                statement, identifier = self.outline_expression(rewritten, scope_id)
                return SplitResult(identifier, [*prefix, statement], True)
            return SplitResult(rewritten, prefix, True)

        if node_type == "TupleExpression":
            components = expr.get("components") or []
            if expr.get("isInlineArray") is False and len(components) == 1 and isinstance(components[0], dict):
                return self.lower_rvalue(components[0], scope_id, force_outline=force_outline)
            self._unsupported(expr, "tuple or inline-array expression is preserved")
            return SplitResult(deep_clone(expr), [], False)

        if node_type == "Conditional":
            self._unsupported(expr, "Conditional expression is preserved")
            return SplitResult(deep_clone(expr), [], False)

        self._unsupported(expr, f"unsupported expression node: {node_type}")
        return SplitResult(deep_clone(expr), [], False)

    def outline_expression(
        self,
        expr: dict[str, Any],
        scope_id: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        type_desc = type_descriptions(expr)
        if not type_desc or not is_supported_value_type(type_desc["typeString"]):
            self._unsupported(expr, "cannot synthesize a reliable temporary variable type")
            raise ValueError("attempted to outline unsupported temporary type")

        temp_name = self._new_temporary_name()
        decl_id = self.ids.new()
        type_name_id = self.ids.new()
        statement_id = self.ids.new()
        src = expr.get("src", "-1:-1:-1")
        declaration = {
            "constant": False,
            "id": decl_id,
            "mutability": "mutable",
            "name": temp_name,
            "nameLocation": "-1:-1:-1",
            "nodeType": "VariableDeclaration",
            "scope": scope_id,
            "src": src,
            "stateVariable": False,
            "storageLocation": "default",
            "typeDescriptions": dict(type_desc),
            "typeName": elementary_type_name(type_desc, src, type_name_id),
            "visibility": "internal",
        }
        statement = {
            "assignments": [decl_id],
            "declarations": [declaration],
            "id": statement_id,
            "initialValue": expr,
            "nodeType": "VariableDeclarationStatement",
            "src": src,
        }
        identifier = {
            "id": self.ids.new(),
            "name": temp_name,
            "nodeType": "Identifier",
            "overloadedDeclarations": [],
            "referencedDeclaration": decl_id,
            "src": src,
            "typeDescriptions": dict(type_desc),
        }
        self.generated_temporaries += 1
        self.generated_atomic_statements += 1
        return statement, identifier

    def _new_temporary_name(self) -> str:
        index = 0
        while True:
            name = f"__atom{index}"
            index += 1
            if name not in self.used_names and name not in SOLIDITY_KEYWORDS:
                self.used_names.add(name)
                return name

    def _unsupported(self, node: dict[str, Any], message: str) -> None:
        self.diagnostics.append(
            Diagnostic(
                code="UNSUPPORTED",
                message=message,
                node_type=node.get("nodeType"),
                src=node.get("src"),
                operator=node.get("operator"),
            )
        )
