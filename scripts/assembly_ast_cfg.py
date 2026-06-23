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
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


Json = dict[str, Any]


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
        return self.source[start:end]


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
class YulCFG:
    nodes: list[CFGNode]
    edges: list[CFGEdge]
    entry: int
    exit: int


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
    compiler_input = {
        "language": "Solidity",
        "sources": {source.name: {"content": source.read_text(encoding="utf-8")}},
        "settings": {
            "outputSelection": {"*": {"": ["ast"]}},
        },
    }
    command = [
        solc_bin,
        "--standard-json",
        "--base-path",
        str(source.parent),
        "--include-path",
        str(source.parent),
    ]
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

    ast = result.get("sources", {}).get(source.name, {}).get("ast")
    if not isinstance(ast, dict):
        available = ", ".join(result.get("sources", {}).keys())
        raise RuntimeError(f"solc did not return AST for {source.name}; available: {available}")
    return ast


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
        return "default" if value is None else f"case {yul_expression(value)}"
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
    def __init__(self) -> None:
        self.nodes: list[CFGNode] = []
        self.edges: list[CFGEdge] = []
        self._edge_keys: set[tuple[int, int, str]] = set()
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
        return YulCFG(self.nodes, self.edges, self.entry, self.exit)

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
            if not cases:
                self.edge(switch_id, merge_id, "no cases")
            for case in cases:
                value = case.get("value")
                label = "default" if value is None else f"case: {yul_expression(value)}"
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
            post_exits = self.build_block(node.get("post"), [post_dispatch], "next", loop_targets)
            self.connect(post_exits, condition_id, "loop back")
            self.edge(condition_id, after_id, f"false: !({condition})")
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
            self.edge(node_id, self.exit, "leave")
            return []

        terminator = terminating_yul_builtin(node)
        if terminator:
            node_id = self.add_node("terminal", yul_statement_text(node), str(node.get("src", "")))
            self.connect(predecessors, node_id, entry_label)
            self.edge(node_id, self.exit, f"terminate: {terminator}")
            return []

        kind = "function-definition" if node_type == "YulFunctionDefinition" else "statement"
        node_id = self.add_node(kind, yul_statement_text(node), str(node.get("src", "")))
        self.connect(predecessors, node_id, entry_label)
        return [node_id]


def build_yul_cfg(yul_ast: Json) -> YulCFG:
    return YulCFGBuilder().build(yul_ast)


def format_cfg(cfg: YulCFG, source: str) -> list[str]:
    lines = ["Nodes:"]
    for node in cfg.nodes:
        start, _end = parse_src(node.src)
        position = ""
        if node.src:
            line, column = line_column(source, start)
            position = f" @ {node.src} (line {line}:{column})"
        lines.append(f"  N{node.node_id} [{node.kind}] {node.text}{position}")
    lines.append("Edges:")
    for edge in cfg.edges:
        lines.append(f"  N{edge.source} --[{edge.label}]--> N{edge.target}")
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
