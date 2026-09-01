from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .model import ExpressionNode


_TOKEN = re.compile(
    r"\s*(?:(0x[0-9a-fA-F]+|\d+)|([A-Za-z_$][A-Za-z0-9_.$:]*)|"
    r"(&&|\|\||==|!=|<=|>=|<<|>>|\+|-|\*|/|%|&|\||\^|!|~|<|>|\(|\)|\[|\]|,))"
)

_BINARY = {
    "||": (1, "Or"),
    "&&": (2, "And"),
    "|": (3, "BitOr"),
    "^": (4, "Xor"),
    "&": (5, "BitAnd"),
    "==": (6, "Eq"),
    "!=": (6, "Ne"),
    "<": (7, "Lt"),
    ">": (7, "Gt"),
    "<=": (7, "Le"),
    ">=": (7, "Ge"),
    "<<": (8, "Shl"),
    ">>": (8, "Shr"),
    "+": (9, "Add"),
    "-": (9, "Sub"),
    "*": (10, "Mul"),
    "/": (10, "Div"),
    "%": (10, "Mod"),
}

_YUL_CALLS = {
    "add": "Add", "sub": "Sub", "mul": "Mul", "div": "Div", "sdiv": "SDiv",
    "mod": "Mod", "smod": "SMod", "lt": "Lt", "slt": "SLt", "gt": "Gt",
    "sgt": "SGt", "eq": "Eq", "iszero": "IsZero", "and": "BitAnd", "or": "BitOr",
    "xor": "Xor", "not": "BitNot", "shl": "Shl", "shr": "Shr", "sar": "Sar",
    "keccak256": "Keccak", "sha3": "Keccak",
}


@dataclass
class _Parsed:
    kind: str
    operands: list["_Parsed"]
    value: Any = None
    name: str | None = None
    operator: str | None = None
    callee: str | None = None
    raw: str | None = None


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.tokens = self._tokens(text)
        self.index = 0

    @staticmethod
    def _tokens(text: str) -> list[str]:
        out: list[str] = []
        position = 0
        while position < len(text):
            match = _TOKEN.match(text, position)
            if not match:
                raise ValueError(f"unsupported token near {text[position:position + 24]!r}")
            out.append(next(value for value in match.groups() if value is not None))
            position = match.end()
        return out

    def peek(self) -> str | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take(self) -> str:
        token = self.peek()
        if token is None:
            raise ValueError("unexpected end of expression")
        self.index += 1
        return token

    def parse(self) -> _Parsed:
        value = self.expression(0)
        if self.peek() is not None:
            raise ValueError(f"unexpected token {self.peek()!r}")
        return value

    def expression(self, minimum: int) -> _Parsed:
        left = self.prefix()
        while self.peek() in _BINARY and _BINARY[self.peek()][0] >= minimum:
            token = self.take()
            precedence, kind = _BINARY[token]
            right = self.expression(precedence + 1)
            left = _Parsed(kind, [left, right], operator=token)
        return left

    def prefix(self) -> _Parsed:
        token = self.take()
        if token in {"!", "~", "-", "+"}:
            kinds = {"!": "Not", "~": "BitNot", "-": "Neg", "+": "Pos"}
            return _Parsed(kinds[token], [self.expression(11)], operator=token)
        if token == "(":
            value = self.expression(0)
            if self.take() != ")":
                raise ValueError("missing closing parenthesis")
            return self.postfix(value)
        if re.fullmatch(r"0x[0-9a-fA-F]+|\d+", token):
            value = int(token, 16) if token.lower().startswith("0x") else int(token)
            return self.postfix(_Parsed("Constant", [], value=value, raw=token))
        if token in {"true", "false"}:
            return self.postfix(_Parsed("Constant", [], value=token == "true", raw=token))
        return self.postfix(_Parsed("Variable", [], name=token, raw=token))

    def postfix(self, value: _Parsed) -> _Parsed:
        while True:
            token = self.peek()
            if token == "(":
                self.take()
                args: list[_Parsed] = []
                if self.peek() != ")":
                    while True:
                        args.append(self.expression(0))
                        if self.peek() != ",":
                            break
                        self.take()
                if self.take() != ")":
                    raise ValueError("missing call parenthesis")
                callee = value.name if value.kind == "Variable" else value.raw
                kind = _YUL_CALLS.get(str(callee), "Call")
                value = _Parsed(kind, args, callee=str(callee), raw=self.text)
            elif token == "[":
                self.take()
                index = self.expression(0)
                if self.take() != "]":
                    raise ValueError("missing index bracket")
                value = _Parsed("Index", [value, index], raw=self.text)
            else:
                break
        return value


class ExpressionArena:
    """Intern expressions so repeated subexpressions form a function-local DAG."""

    def __init__(self) -> None:
        self.nodes: dict[str, ExpressionNode] = {}
        self._key_to_id: dict[str, str] = {}
        self._counter = 0

    def build(self, value: Any, origin_facts: list[str] | None = None, type_hint: str | None = None) -> str | None:
        if value is None or value == "":
            return None
        if isinstance(value, list):
            parsed = _Parsed("Tuple", [self._parse(str(item)) for item in value], raw=str(value))
        elif isinstance(value, bool):
            parsed = _Parsed("Constant", [], value=value, raw=str(value).lower())
        elif isinstance(value, int):
            parsed = _Parsed("Constant", [], value=value, raw=str(value))
        else:
            parsed = self._parse(str(value).strip())
        return self._intern(parsed, origin_facts or [], type_hint)

    def build_with_bindings(
        self,
        value: Any,
        bindings: dict[str, Any],
        origin_facts: list[str] | None = None,
        type_hint: str | None = None,
    ) -> str | None:
        if value is None or value == "":
            return None
        if isinstance(value, list):
            parsed = _Parsed("Tuple", [self._parse(str(item)) for item in value], raw=str(value))
        elif isinstance(value, bool):
            parsed = _Parsed("Constant", [], value=value, raw=str(value).lower())
        elif isinstance(value, int):
            parsed = _Parsed("Constant", [], value=value, raw=str(value))
        else:
            parsed = self._parse(str(value).strip())
        parsed_bindings = {name: self._parse(str(expression)) for name, expression in bindings.items()}
        resolved = self._substitute(parsed, parsed_bindings, set())
        return self._intern(resolved, origin_facts or [], type_hint)

    @classmethod
    def _substitute(cls, parsed: _Parsed, bindings: dict[str, _Parsed], active: set[str]) -> _Parsed:
        if parsed.kind == "Variable" and parsed.name in bindings and parsed.name not in active:
            return cls._substitute(bindings[parsed.name], bindings, active | {parsed.name})
        operands = [cls._substitute(item, bindings, active) for item in parsed.operands]
        changed = operands != parsed.operands
        return _Parsed(
            parsed.kind,
            operands,
            value=parsed.value,
            name=parsed.name,
            operator=parsed.operator,
            callee=parsed.callee,
            raw=None if changed and parsed.kind not in {"OpaqueExpression", "Constant", "Variable"} else parsed.raw,
        )

    @staticmethod
    def _parse(text: str) -> _Parsed:
        try:
            return _Parser(text).parse()
        except (ValueError, StopIteration):
            return _Parsed("OpaqueExpression", [], raw=text)

    def _intern(self, parsed: _Parsed, origins: list[str], type_hint: str | None) -> str:
        operands = [self._intern(item, origins, None) for item in parsed.operands]
        key = json.dumps({
            "kind": parsed.kind,
            "operands": operands,
            "value": parsed.value,
            "name": parsed.name,
            "operator": parsed.operator,
            "callee": parsed.callee,
            "raw": parsed.raw if parsed.kind == "OpaqueExpression" else None,
            "type_hint": type_hint,
        }, sort_keys=True, ensure_ascii=False)
        existing = self._key_to_id.get(key)
        if existing:
            node = self.nodes[existing]
            node.origin_facts = list(dict.fromkeys(node.origin_facts + origins))
            return existing
        self._counter += 1
        expr_id = f"expr_{self._counter}"
        self.nodes[expr_id] = ExpressionNode(
            expr_id=expr_id,
            kind=parsed.kind,
            operands=operands,
            value=parsed.value,
            name=parsed.name,
            operator=parsed.operator,
            callee=parsed.callee,
            type_hint=type_hint,
            raw=parsed.raw,
            origin_facts=list(dict.fromkeys(origins)),
        )
        self._key_to_id[key] = expr_id
        return expr_id
