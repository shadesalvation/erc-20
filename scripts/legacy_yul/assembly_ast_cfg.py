#!/usr/bin/env python3
"""
Extract inline assembly from solc AST and build a local Yul control-flow graph.

Input is Solidity source only. The script invokes solc --standard-json, locates
InlineAssembly nodes in the compiler AST, and builds a CFG for each YulBlock.
The output is a text report intended for MemorySSA and semantic recovery; it
does not modify or compile recovered Solidity.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


Json = dict[str, Any]
_SOLC_HELP_CACHE: dict[str, str] = {}


@dataclass
class FunctionContext:
    contract: str | None = None
    name: str | None = None
    visibility: str | None = None
    parameters: list[str] = field(default_factory=list)

    def label(self) -> str:
        contract = self.contract or "<source-unit>"
        if self.name is None:
            return contract
        return f"{contract}.{self.name}({', '.join(self.parameters)})"


@dataclass
class AssemblyAstBlock:
    block_id: int
    source_path: Path
    source: str
    solidity_node: Json
    yul_ast: Json
    context: FunctionContext

    @property
    def src(self) -> str:
        return str(self.solidity_node.get("src", self.yul_ast.get("src", "")))

    @property
    def source_range(self) -> tuple[int, int]:
        return parse_src(self.src)

    @property
    def snippet(self) -> str:
        start, end = self.source_range
        return self.source.encode("utf-8")[start:end].decode("utf-8", errors="replace")


@dataclass
class CFGNode:
    node_id: int
    kind: str
    text: str
    src: str


@dataclass
class CFGEdge:
    source: int
    target: int
    label: str


@dataclass
class CFGFunctionCall:
    caller_node_id: int
    function_name: str
    arguments: tuple[str, ...]
    src: str


@dataclass
class YulScope:
    scope_id: int
    parent_id: int | None
    kind: str
    src: str
    bindings: dict[str, str] = field(default_factory=dict)


@dataclass
class YulScopeAnalysis:
    scopes: dict[int, YulScope]
    node_scopes: dict[str, int]
    declaration_bindings: dict[str, dict[str, str]]


@dataclass
class YulCFG:
    nodes: list[CFGNode]
    edges: list[CFGEdge]
    entry: int
    exit: int
    function_cfgs: dict[str, "YulCFG"] = field(default_factory=dict)
    function_calls: list[CFGFunctionCall] = field(default_factory=list)
    scope_analysis: YulScopeAnalysis | None = None


def discover_solc(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    env_value = os.environ.get("SOLC_BIN")
    if env_value:
        return env_value
    for candidate in (".venv/bin/solc",):
        if Path(candidate).is_file():
            return str(Path(candidate).resolve())
    return shutil.which("solc")


def solc_help(solc_bin: str) -> str:
    key = str(Path(solc_bin).resolve()) if Path(solc_bin).exists() else solc_bin
    if key not in _SOLC_HELP_CACHE:
        process = subprocess.run(
            [solc_bin, "--help"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        _SOLC_HELP_CACHE[key] = process.stdout + process.stderr
    return _SOLC_HELP_CACHE[key]


def solc_supports_option(solc_bin: str, option: str) -> bool:
    return re.search(rf"^\s+{re.escape(option)}(?:\s|$)", solc_help(solc_bin), re.M) is not None


def parse_src(src: str) -> tuple[int, int]:
    try:
        start_text, length_text, _file = src.split(":", 2)
        start = int(start_text)
        return start, start + int(length_text)
    except (TypeError, ValueError):
        return 0, 0


def line_column(source: str, offset: int) -> tuple[int, int]:
    line = source.count("\n", 0, offset) + 1
    line_start = source.rfind("\n", 0, offset)
    return line, offset - line_start


def compile_source_ast(source: Path, solc_bin: str) -> Json:
    source = source.resolve()
    candidate = Path(solc_bin)
    if not candidate.is_absolute() and candidate.is_file():
        solc_bin = str(candidate.resolve())
    include_paths = [
        Path(item).resolve()
        for item in os.environ.get("SSEIR_SOLC_INCLUDE_PATHS", "").split(os.pathsep)
        if item.strip()
    ]
    source_text = source.read_text(encoding="utf-8")
    source_key = source_unit_name(source, [source.parent, *include_paths], source_text)
    remappings = [
        item
        for item in os.environ.get("SSEIR_SOLC_REMAPPINGS", "").split(os.pathsep)
        if item.strip()
    ]
    compiler_input = {
        "language": "Solidity",
        "sources": {source_key: {"content": source_text}},
        "settings": {
            "outputSelection": {"*": {"": ["ast"]}},
        },
    }
    if remappings:
        compiler_input["settings"]["remappings"] = remappings
    command = [
        solc_bin,
        "--standard-json",
    ]
    if solc_supports_option(solc_bin, "--base-path"):
        command.extend(["--base-path", str(source.parent)])
    include_paths = [path for path in include_paths if path != source.parent]
    if include_paths and solc_supports_option(solc_bin, "--include-path"):
        for include_path in include_paths:
            command.extend(["--include-path", str(include_path)])
    elif include_paths and solc_supports_option(solc_bin, "--allow-paths"):
        allowed = ",".join(str(path) for path in [source.parent, *include_paths])
        command.extend(["--allow-paths", allowed])
    process = subprocess.run(
        command,
        cwd=str(source.parent),
        input=json.dumps(compiler_input),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if not process.stdout.strip():
        raise RuntimeError(
            "solc did not produce standard JSON output.\n"
            f"Command: {' '.join(command)}\n"
            f"stderr:\n{process.stderr}"
        )
    try:
        result = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "solc standard JSON output could not be decoded.\n"
            f"stderr:\n{process.stderr}\nstdout:\n{process.stdout[:1000]}"
        ) from exc

    errors = [item for item in result.get("errors", []) if item.get("severity") == "error"]
    if errors:
        messages = "\n".join(item.get("formattedMessage", str(item)) for item in errors)
        raise RuntimeError(f"solc AST generation failed:\n{messages}")

    ast = result.get("sources", {}).get(source_key, {}).get("ast")
    if not isinstance(ast, dict):
        available = ", ".join(result.get("sources", {}).keys())
        raise RuntimeError(f"solc did not return AST for {source_key}; available: {available}")
    return ast


def source_unit_name(source: Path, roots: list[Path], source_text: str = "") -> str:
    candidates: list[str] = [source.name]
    for root in roots:
        try:
            rel = source.relative_to(root.resolve())
        except Exception:
            continue
        if rel.parts:
            candidates.append(rel.as_posix())
    min_parent_depth = max_relative_import_parent_depth(source_text)
    usable = [
        item
        for item in candidates
        if max(0, len(Path(item).parts) - 1) >= min_parent_depth
    ]
    # Solidity resolves relative imports against the source unit name. Prefer
    # the shortest usable relative name so entry files stay simple, while
    # imported package files keep enough path context for imports such as
    # "../../utils/Context.sol".
    return min(usable or candidates, key=lambda item: (len(Path(item).parts), len(item)))


def max_relative_import_parent_depth(source_text: str) -> int:
    max_depth = 0
    for match in re.finditer(r"import\s+(?:(?:[^;\"']*?\s+from\s+)?[\"']([^\"']+)[\"']|[\"']([^\"']+)[\"'])\s*;", source_text, re.S):
        spec = match.group(1) or match.group(2) or ""
        if not spec.startswith("."):
            continue
        parts = Path(spec).parts
        depth = sum(1 for part in parts if part == "..")
        max_depth = max(max_depth, depth)
    return max_depth


def iter_child_nodes(value: Any) -> Iterable[Json]:
    if isinstance(value, dict):
        if isinstance(value.get("nodeType"), str):
            yield value
        for child in value.values():
            yield from iter_child_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_child_nodes(child)


def parameter_text(parameter: Json) -> str:
    type_text = parameter.get("typeDescriptions", {}).get("typeString")
    if not type_text:
        type_text = parameter.get("typeName", {}).get("name") or "unknown"
    name = parameter.get("name", "")
    return f"{type_text} {name}".strip()


def function_name(node: Json) -> str:
    kind = node.get("kind")
    name = node.get("name", "")
    if kind == "constructor":
        return "constructor"
    if kind == "fallback":
        return "fallback"
    if kind == "receive":
        return "receive"
    return name or "<anonymous>"


def extract_inline_assembly_blocks(ast: Json, source_path: Path) -> list[AssemblyAstBlock]:
    source = source_path.read_text(encoding="utf-8")
    blocks: list[AssemblyAstBlock] = []

    def visit(node: Any, context: FunctionContext) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item, context)
            return
        if not isinstance(node, dict):
            return

        node_type = node.get("nodeType")
        next_context = context
        if node_type == "ContractDefinition":
            next_context = FunctionContext(contract=node.get("name", "<anonymous>"))
        elif node_type in {"FunctionDefinition", "ModifierDefinition"}:
            parameters = [
                parameter_text(parameter)
                for parameter in node.get("parameters", {}).get("parameters", [])
            ]
            next_context = FunctionContext(
                contract=context.contract,
                name=function_name(node),
                visibility=node.get("visibility"),
                parameters=parameters,
            )
        elif node_type == "InlineAssembly":
            yul_ast = node.get("AST") or node.get("ast")
            if isinstance(yul_ast, dict):
                blocks.append(
                    AssemblyAstBlock(
                        block_id=len(blocks) + 1,
                        source_path=source_path,
                        source=source,
                        solidity_node=node,
                        yul_ast=yul_ast,
                        context=context,
                    )
                )
            return

        for child in node.values():
            visit(child, next_context)

    visit(ast, FunctionContext())
    return blocks


def identifier_name(node: Any) -> str:
    if not isinstance(node, dict):
        return "?"
    return str(node.get("name") or node.get("value") or node.get("nodeType") or "?")


def yul_expression(node: Any) -> str:
    if not isinstance(node, dict):
        return "?"
    node_type = node.get("nodeType")
    if node_type == "YulIdentifier":
        return str(node.get("name", "?"))
    if node_type == "YulLiteral":
        value = node.get("value")
        if value is not None:
            return str(value)
        return str(node.get("hexValue", "literal"))
    if node_type == "YulFunctionCall":
        name = yul_expression(node.get("functionName"))
        arguments = ", ".join(yul_expression(argument) for argument in node.get("arguments", []))
        return f"{name}({arguments})"
    if node_type == "YulMemberAccess":
        return f"{yul_expression(node.get('expression'))}.{node.get('memberName', '?')}"
    if node_type == "YulTypedName":
        return str(node.get("name", "?"))
    return str(node.get("name") or node_type or "?")


def yul_statement_text(node: Json) -> str:
    node_type = node.get("nodeType", "Unknown")
    if node_type == "YulVariableDeclaration":
        variables = ", ".join(yul_expression(item) for item in node.get("variables", []))
        value = node.get("value")
        return f"let {variables}" + (f" := {yul_expression(value)}" if value else "")
    if node_type == "YulAssignment":
        variables = ", ".join(yul_expression(item) for item in node.get("variableNames", []))
        return f"{variables} := {yul_expression(node.get('value'))}"
    if node_type == "YulExpressionStatement":
        return yul_expression(node.get("expression"))
    if node_type == "YulIf":
        return f"if {yul_expression(node.get('condition'))}"
    if node_type == "YulSwitch":
        return f"switch {yul_expression(node.get('expression'))}"
    if node_type == "YulCase":
        value = node.get("value")
        return "default" if value is None or value == "default" else f"case {yul_expression(value)}"
    if node_type == "YulForLoop":
        return "for"
    if node_type == "YulFunctionDefinition":
        return f"function {node.get('name', '?')}"
    if node_type in {"YulBreak", "YulContinue", "YulLeave"}:
        return node_type.removeprefix("Yul").lower()
    return node_type


TERMINATING_YUL_BUILTINS = {"revert", "return", "stop", "invalid", "selfdestruct"}


def terminating_yul_builtin(node: Json) -> str | None:
    """Return a terminating builtin name for a direct Yul expression statement."""
    if node.get("nodeType") != "YulExpressionStatement":
        return None
    expression = node.get("expression")
    if not isinstance(expression, dict) or expression.get("nodeType") != "YulFunctionCall":
        return None
    function = expression.get("functionName")
    if not isinstance(function, dict) or function.get("nodeType") != "YulIdentifier":
        return None
    name = function.get("name")
    return name if name in TERMINATING_YUL_BUILTINS else None


def yul_children(node: Json) -> list[Json]:
    node_type = node.get("nodeType")
    keys_by_type = {
        "YulBlock": ("statements",),
        "YulVariableDeclaration": ("variables", "value"),
        "YulAssignment": ("variableNames", "value"),
        "YulExpressionStatement": ("expression",),
        "YulFunctionCall": ("functionName", "arguments"),
        "YulIf": ("condition", "body"),
        "YulSwitch": ("expression", "cases"),
        "YulCase": ("value", "body"),
        "YulForLoop": ("pre", "condition", "body", "post"),
        "YulFunctionDefinition": ("parameters", "returnVariables", "body"),
        "YulMemberAccess": ("expression",),
    }
    children: list[Json] = []
    for key in keys_by_type.get(node_type, ()):
        value = node.get(key)
        if isinstance(value, dict):
            children.append(value)
        elif isinstance(value, list):
            children.extend(item for item in value if isinstance(item, dict))
    return children


def build_yul_scope_analysis(root: Json) -> YulScopeAnalysis:
    """Record lexical Yul scopes and stable bindings without rewriting text."""
    scopes: dict[int, YulScope] = {}
    node_scopes: dict[str, int] = {}
    declaration_bindings: dict[str, dict[str, str]] = {}
    next_scope_id = 0

    def new_scope(parent_id: int | None, kind: str, node: Json | None) -> int:
        nonlocal next_scope_id
        scope_id = next_scope_id
        next_scope_id += 1
        scopes[scope_id] = YulScope(scope_id, parent_id, kind, str((node or {}).get("src", "")))
        return scope_id

    def bind(scope_id: int, names: list[Json], src: str) -> None:
        bindings: dict[str, str] = {}
        for item in names:
            name = item.get("name")
            if not name:
                continue
            canonical = f"{name}__scope{scope_id}"
            scopes[scope_id].bindings[str(name)] = canonical
            bindings[str(name)] = canonical
        if bindings:
            declaration_bindings[src] = bindings

    def visit_block(block: Json | None, parent_id: int, kind: str) -> None:
        if not isinstance(block, dict):
            return
        scope_id = new_scope(parent_id, kind, block)
        for statement in block.get("statements", []):
            visit_statement(statement, scope_id)

    def visit_statement(statement: Json, scope_id: int) -> None:
        node_type = statement.get("nodeType")
        src = str(statement.get("src", ""))
        node_scopes[src] = scope_id
        if node_type == "YulVariableDeclaration":
            bind(scope_id, list(statement.get("variables", [])), src)
            return
        if node_type == "YulIf":
            visit_block(statement.get("body"), scope_id, "if")
            return
        if node_type == "YulSwitch":
            for case in statement.get("cases", []):
                node_scopes[str(case.get("src", ""))] = scope_id
                visit_block(case.get("body"), scope_id, "switch-case")
            return
        if node_type == "YulForLoop":
            loop_scope = new_scope(scope_id, "for", statement)
            node_scopes[src] = loop_scope
            pre = statement.get("pre")
            if isinstance(pre, dict):
                for nested in pre.get("statements", []):
                    visit_statement(nested, loop_scope)
            condition = statement.get("condition")
            if isinstance(condition, dict):
                node_scopes[str(condition.get("src", ""))] = loop_scope
            visit_block(statement.get("body"), loop_scope, "for-body")
            visit_block(statement.get("post"), loop_scope, "for-post")
            return
        if node_type == "YulFunctionDefinition":
            function_scope = new_scope(scope_id, "function", statement)
            bind(function_scope, list(statement.get("parameters", [])), src)
            bind(function_scope, list(statement.get("returnVariables", [])), src)
            visit_block(statement.get("body"), function_scope, "function-body")

    root_scope = new_scope(None, "assembly", root)
    for statement in root.get("statements", []):
        visit_statement(statement, root_scope)
    return YulScopeAnalysis(scopes, node_scopes, declaration_bindings)


def statement_direct_call(node: Json) -> tuple[str | None, tuple[str, ...]]:
    if node.get("nodeType") == "YulExpressionStatement":
        expression = node.get("expression")
    elif node.get("nodeType") in {"YulVariableDeclaration", "YulAssignment"}:
        expression = node.get("value")
    else:
        expression = None
    if not isinstance(expression, dict) or expression.get("nodeType") != "YulFunctionCall":
        return None, ()
    function = expression.get("functionName")
    if not isinstance(function, dict) or function.get("nodeType") != "YulIdentifier":
        return None, ()
    return str(function.get("name", "")), tuple(yul_expression(argument) for argument in expression.get("arguments", []))


def yul_function_definitions(root: Json) -> list[Json]:
    """Collect local Yul functions without treating their bodies as outer flow."""
    definitions: list[Json] = []

    def visit(node: Json) -> None:
        for child in yul_children(node):
            if child.get("nodeType") == "YulFunctionDefinition":
                definitions.append(child)
                continue
            visit(child)

    visit(root)
    return definitions


def format_yul_ast(root: Json, max_depth: int = 5) -> list[str]:
    lines: list[str] = []

    def visit(node: Json, depth: int) -> None:
        text = yul_statement_text(node)
        src = node.get("src", "?")
        lines.append(f"{'  ' * depth}- {node.get('nodeType', 'Unknown')}: {text} [{src}]")
        if depth >= max_depth:
            if yul_children(node):
                lines.append(f"{'  ' * (depth + 1)}- ...")
            return
        for child in yul_children(node):
            visit(child, depth + 1)

    visit(root, 0)
    return lines


class YulCFGBuilder:
    def __init__(self, is_yul_function: bool = False, local_function_names: set[str] | None = None) -> None:
        self.nodes: list[CFGNode] = []
        self.edges: list[CFGEdge] = []
        self._edge_keys: set[tuple[int, int, str]] = set()
        self.is_yul_function = is_yul_function
        self.local_function_names = set(local_function_names or ())
        self.function_calls: list[CFGFunctionCall] = []
        self.entry = self.add_node("entry", "entry", "")
        self.exit = self.add_node("exit", "exit", "")

    def add_node(self, kind: str, text: str, src: str) -> int:
        node_id = len(self.nodes)
        self.nodes.append(CFGNode(node_id, kind, text, src))
        return node_id

    def edge(self, source: int, target: int, label: str = "next") -> None:
        key = (source, target, label)
        if key not in self._edge_keys:
            self._edge_keys.add(key)
            self.edges.append(CFGEdge(source, target, label))

    def connect(self, predecessors: list[int], target: int, label: str = "next") -> None:
        for predecessor in predecessors:
            self.edge(predecessor, target, label)

    def build(self, root: Json) -> YulCFG:
        exits = self.build_block(root, [self.entry], "next", None)
        self.connect(exits, self.exit)
        return YulCFG(self.nodes, self.edges, self.entry, self.exit, function_calls=self.function_calls)

    def build_block(
        self,
        block: Json | None,
        predecessors: list[int],
        entry_label: str,
        loop_targets: tuple[int, int] | None,
    ) -> list[int]:
        if not isinstance(block, dict):
            return predecessors
        current = predecessors
        label = entry_label
        for statement in block.get("statements", []):
            current = self.build_statement(statement, current, label, loop_targets)
            label = "next"
        return current

    def build_statement(
        self,
        node: Json,
        predecessors: list[int],
        entry_label: str,
        loop_targets: tuple[int, int] | None,
    ) -> list[int]:
        node_type = node.get("nodeType")
        if node_type == "YulIf":
            condition = yul_expression(node.get("condition"))
            condition_id = self.add_node("condition", f"if {condition}", str(node.get("src", "")))
            self.connect(predecessors, condition_id, entry_label)
            merge_id = self.add_node("merge", "if merge", str(node.get("src", "")))
            true_exits = self.build_block(
                node.get("body"),
                [condition_id],
                f"true: {condition}",
                loop_targets,
            )
            self.connect(true_exits, merge_id)
            self.edge(condition_id, merge_id, f"false: !({condition})")
            return [merge_id]

        if node_type == "YulSwitch":
            expression = yul_expression(node.get("expression"))
            switch_id = self.add_node("switch", f"switch {expression}", str(node.get("src", "")))
            self.connect(predecessors, switch_id, entry_label)
            merge_id = self.add_node("merge", "switch merge", str(node.get("src", "")))
            cases = node.get("cases", [])
            case_values = [
                yul_expression(case.get("value"))
                for case in cases
                if isinstance(case, dict) and case.get("value") is not None and case.get("value") != "default"
            ]
            if not cases:
                self.edge(switch_id, merge_id, "no cases")
            for case in cases:
                value = case.get("value")
                if value is None or value == "default":
                    exclusions = " && ".join(f"!({expression} == {case_value})" for case_value in case_values)
                    label = f"default: {exclusions or 'true'}"
                else:
                    label = f"case: {expression} == {yul_expression(value)}"
                case_exits = self.build_block(case.get("body"), [switch_id], label, loop_targets)
                self.connect(case_exits, merge_id)
            return [merge_id]

        if node_type == "YulForLoop":
            pre_exits = self.build_block(node.get("pre"), predecessors, entry_label, loop_targets)
            condition = yul_expression(node.get("condition"))
            condition_id = self.add_node("loop-condition", f"for condition {condition}", str(node.get("src", "")))
            self.connect(pre_exits, condition_id)
            after_id = self.add_node("loop-merge", "for exit", str(node.get("src", "")))
            post_dispatch = self.add_node("loop-post", "for post", str(node.get("src", "")))
            body_exits = self.build_block(
                node.get("body"),
                [condition_id],
                f"true: {condition}",
                (after_id, post_dispatch),
            )
            self.connect(body_exits, post_dispatch)
            post_exits = self.build_block(node.get("post"), [post_dispatch], "next", (after_id, post_dispatch))
            self.connect(post_exits, condition_id, "loop back")
            self.edge(condition_id, after_id, f"loop exit: !({condition})")
            return [after_id]

        if node_type == "YulBreak":
            node_id = self.add_node("break", "break", str(node.get("src", "")))
            self.connect(predecessors, node_id, entry_label)
            if loop_targets:
                self.edge(node_id, loop_targets[0], "break")
            else:
                self.edge(node_id, self.exit, "invalid break")
            return []

        if node_type == "YulContinue":
            node_id = self.add_node("continue", "continue", str(node.get("src", "")))
            self.connect(predecessors, node_id, entry_label)
            if loop_targets:
                self.edge(node_id, loop_targets[1], "continue")
            else:
                self.edge(node_id, self.exit, "invalid continue")
            return []

        if node_type == "YulLeave":
            node_id = self.add_node("leave", "leave", str(node.get("src", "")))
            self.connect(predecessors, node_id, entry_label)
            label = "leave: function exit" if self.is_yul_function else "invalid leave"
            self.edge(node_id, self.exit, label)
            return []

        if node_type == "YulFunctionDefinition":
            # A definition is not executed at its textual position. Its body is
            # represented by a separate CFG; calls are handled interprocedurally.
            return predecessors

        terminator = terminating_yul_builtin(node)
        if terminator:
            node_id = self.add_node("terminal", yul_statement_text(node), str(node.get("src", "")))
            self.connect(predecessors, node_id, entry_label)
            self.edge(node_id, self.exit, f"terminate: {terminator}")
            return []

        kind = "function-definition" if node_type == "YulFunctionDefinition" else "statement"
        node_id = self.add_node(kind, yul_statement_text(node), str(node.get("src", "")))
        self.connect(predecessors, node_id, entry_label)
        call_name, arguments = statement_direct_call(node)
        if call_name in self.local_function_names:
            self.function_calls.append(CFGFunctionCall(node_id, call_name, arguments, str(node.get("src", ""))))
        return [node_id]


def build_yul_cfg(yul_ast: Json) -> YulCFG:
    definitions = {str(definition.get("name", "<anonymous>")): definition for definition in yul_function_definitions(yul_ast)}
    cfg = YulCFGBuilder(local_function_names=set(definitions)).build(yul_ast)
    cfg.function_cfgs = {
        name: build_yul_function_cfg(definition, set(definitions))
        for name, definition in definitions.items()
    }
    cfg.scope_analysis = build_yul_scope_analysis(yul_ast)
    return cfg


def build_yul_function_cfg(definition: Json, local_function_names: set[str] | None = None) -> YulCFG:
    body = definition.get("body")
    if not isinstance(body, dict):
        body = {"nodeType": "YulBlock", "statements": []}
    cfg = YulCFGBuilder(is_yul_function=True, local_function_names=local_function_names).build(body)
    scope_root = {"nodeType": "YulBlock", "src": str(body.get("src", "")), "statements": [definition]}
    cfg.scope_analysis = build_yul_scope_analysis(scope_root)
    return cfg


def format_cfg(cfg: YulCFG, source: str) -> list[str]:
    lines = ["Nodes:"]
    for node in cfg.nodes:
        start, _end = parse_src(node.src)
        position = ""
        if node.src:
            line, column = line_column(source, start)
            position = f" @ {node.src} (line {line}:{column})"
        scope = cfg.scope_analysis.node_scopes.get(node.src) if cfg.scope_analysis else None
        scope_text = f" scope=S{scope}" if scope is not None else ""
        lines.append(f"  N{node.node_id} [{node.kind}{scope_text}] {node.text}{position}")
    lines.append("Edges:")
    for edge in cfg.edges:
        lines.append(f"  N{edge.source} --[{edge.label}]--> N{edge.target}")
    if cfg.scope_analysis:
        lines.append("YulScopes:")
        for scope in cfg.scope_analysis.scopes.values():
            parent = f"S{scope.parent_id}" if scope.parent_id is not None else "none"
            bindings = ", ".join(f"{name}->{binding}" for name, binding in scope.bindings.items()) or "-"
            lines.append(f"  S{scope.scope_id} kind={scope.kind} parent={parent} bindings={bindings}")
    if cfg.function_calls:
        lines.append("YulLocalFunctionCalls:")
        for call in cfg.function_calls:
            arguments = ", ".join(call.arguments)
            lines.append(f"  N{call.caller_node_id} -> {call.function_name}({arguments}) @ {call.src}")
    for name, function_cfg in cfg.function_cfgs.items():
        lines.append(f"YulFunctionCFG {name}:")
        lines.extend(f"  {line}" for line in format_cfg(function_cfg, source))
    return lines


def dot_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def format_cfg_dot(cfg: YulCFG, source: str, graph_label: str) -> str:
    shapes = {
        "entry": "oval",
        "exit": "oval",
        "condition": "diamond",
        "switch": "diamond",
        "merge": "circle",
        "loop-condition": "diamond",
        "loop-merge": "circle",
        "terminal": "octagon",
        "leave": "octagon",
        "break": "octagon",
        "continue": "octagon",
    }
    lines = [
        "digraph yul_cfg {",
        "  rankdir=TB;",
        "  graph [fontname=\"monospace\", label=\"" + dot_escape(graph_label) + "\", labelloc=t];",
        "  node [fontname=\"monospace\", shape=box];",
        "  edge [fontname=\"monospace\"];",
    ]
    for node in cfg.nodes:
        start, _end = parse_src(node.src)
        location = ""
        if node.src:
            line, column = line_column(source, start)
            location = f"\\n{node.src} (line {line}:{column})"
        label = f"N{node.node_id} [{node.kind}]\\n{node.text}{location}"
        shape = shapes.get(node.kind, "box")
        lines.append(f"  N{node.node_id} [shape={shape}, label=\"{dot_escape(label)}\"];")
    for edge in cfg.edges:
        lines.append(
            f"  N{edge.source} -> N{edge.target} [label=\"{dot_escape(edge.label)}\"];"
        )
    lines.append("}")
    return "\n".join(lines) + "\n"


def write_dot_files(blocks: list[AssemblyAstBlock], dot_dir: Path) -> list[Path]:
    dot_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for block in blocks:
        cfg = build_yul_cfg(block.yul_ast)
        filename = f"{block.source_path.stem}.assembly_{block.block_id}.dot"
        output = dot_dir / filename
        graph_label = f"Assembly block {block.block_id}: {block.context.label()}"
        output.write_text(format_cfg_dot(cfg, block.source, graph_label), encoding="utf-8")
        outputs.append(output)
        for name, function_cfg in cfg.function_cfgs.items():
            safe_name = "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in name)
            function_output = dot_dir / f"{block.source_path.stem}.assembly_{block.block_id}.function_{safe_name}.dot"
            function_label = f"Assembly block {block.block_id}, Yul function {name}: {block.context.label()}"
            function_output.write_text(format_cfg_dot(function_cfg, block.source, function_label), encoding="utf-8")
            outputs.append(function_output)
    return outputs


def format_report(blocks: list[AssemblyAstBlock], ast_depth: int) -> str:
    lines = [
        "INFO:AssemblyAstCfg:Input Solidity source only",
        f"INFO:AssemblyAstCfg:InlineAssembly blocks {len(blocks)}",
        "",
    ]
    for block in blocks:
        start, end = block.source_range
        start_line, start_column = line_column(block.source, start)
        end_line, end_column = line_column(block.source, max(start, end - 1))
        context = block.context
        lines.extend([
            f"AssemblyBlock {block.block_id}",
            f"  Contract: {context.contract or '<source-unit>'}",
            f"  Function: {context.name or '<none>'}({', '.join(context.parameters)})",
            f"  Visibility: {context.visibility or 'unknown'}",
            f"  SoliditySourceRange: {block.src} (line {start_line}:{start_column} to {end_line}:{end_column})",
            f"  YulRoot: {block.yul_ast.get('nodeType')} [{block.yul_ast.get('src', '?')}]",
            "  SoliditySnippet:",
        ])
        lines.extend(f"    {line}" for line in block.snippet.strip().splitlines())
        lines.append("  YulAst:")
        lines.extend(f"    {line}" for line in format_yul_ast(block.yul_ast, ast_depth))
        lines.append("  LocalCFG:")
        cfg = build_yul_cfg(block.yul_ast)
        lines.extend(f"    {line}" for line in format_cfg(cfg, block.source))
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract InlineAssembly from solc AST and build a per-block Yul CFG."
    )
    parser.add_argument("source", help="Solidity source file to analyze.")
    parser.add_argument("-o", "--output", default="assembly_ast_cfg.txt", help="Text report path.")
    parser.add_argument("--solc-bin", help="solc executable; defaults to SOLC_BIN, .venv/bin/solc, or PATH.")
    parser.add_argument("--ast-depth", type=int, default=5, help="Maximum Yul AST outline depth.")
    parser.add_argument("--dot-dir", help="Optional directory for one Graphviz DOT CFG file per assembly block.")
    args = parser.parse_args()

    source = Path(args.source)
    if not source.is_file():
        print(f"Source file not found: {source}", file=sys.stderr)
        return 2
    solc_bin = discover_solc(args.solc_bin)
    if solc_bin is None:
        print("solc executable not found. Use --solc-bin or set SOLC_BIN.", file=sys.stderr)
        return 2

    try:
        ast = compile_source_ast(source, solc_bin)
        blocks = extract_inline_assembly_blocks(ast, source)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(format_report(blocks, max(0, args.ast_depth)), encoding="utf-8")
    dot_files = write_dot_files(blocks, Path(args.dot_dir)) if args.dot_dir else []
    print(f"Wrote assembly AST/CFG report: {output}")
    if dot_files:
        print(f"Wrote CFG DOT files: {len(dot_files)} in {args.dot_dir}")
    print(f"Assembly blocks found: {len(blocks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
