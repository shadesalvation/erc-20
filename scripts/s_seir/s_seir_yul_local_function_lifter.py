#!/usr/bin/env python3
"""Lift Yul-local function definitions and their calls as first-class semantics."""
from __future__ import annotations

from typing import Any

from assembly_ast_cfg import build_yul_function_cfg, yul_expression, yul_function_definitions
from s_seir_id import IdAllocator
from s_seir_model import EffectNode, FunctionUnit, SemanticOverlay
from s_seir_yul_normalize import call_parts, normalize_expr


class YulLocalFunctionLifter:
    """Extract local Yul functions without inlining their control flow.

    A definition owns an independent semantic CFG.  Calls are represented as
    typed local calls at their original caller CFG node, which preserves the
    required interprocedural boundary and avoids pretending that a callee's
    conditional body executes linearly in the caller.
    """

    def __init__(self) -> None:
        self.ids = IdAllocator()

    def lift(self, unit: FunctionUnit, effects: list[EffectNode], overlays: list[SemanticOverlay]) -> list[SemanticOverlay]:
        out = list(overlays)
        definitions: dict[str, tuple[dict[str, Any], int, SemanticOverlay]] = {}
        refs_by_src = self._refs_by_src(unit)
        for block in unit.assembly_blocks:
            for definition in yul_function_definitions(block.yul_ast):
                name = str(definition.get("name") or "").strip()
                if not name or name in definitions:
                    continue
                refs = self._definition_refs(definition, refs_by_src)
                body = self._block_semantics(definition.get("body"))
                cfg = build_yul_function_cfg(definition, {name})
                attrs = {
                    "name": name,
                    "parameters": [self._parameter(item) for item in definition.get("parameters") or []],
                    "returns": [self._parameter(item) for item in definition.get("returnVariables") or []],
                    "body": body,
                    "semantic_cfg": self._cfg(cfg),
                    "solidity_like": self._solidity_like(name, definition, body),
                    "semantic_model": "yul_local_function_cfg",
                    "assembly_block": block.block_id,
                }
                overlay = SemanticOverlay(self.ids.new("ov_yul_local_def"), "YulLocalFunctionDefinition", [], refs, attrs)
                out.append(overlay)
                definitions[name] = (definition, block.block_id, overlay)

        for effect in effects:
            if effect.kind != "ValueDef":
                continue
            targets = [str(value) for value in effect.attrs.get("targets") or [] if value]
            if len(targets) != 1:
                continue
            name, args = call_parts(str(effect.attrs.get("value") or ""))
            if not name or name not in definitions:
                continue
            _definition, block_id, definition_overlay = definitions[name]
            out.append(SemanticOverlay(self.ids.new("ov_yul_local_call"), "YulLocalFunctionCall", [effect.effect_id], list(effect.stmt_refs), {
                "target": targets[0],
                "function": name,
                "arguments": [normalize_expr(arg) for arg in args],
                "definition_overlay": definition_overlay.overlay_id,
                "assembly_block": block_id,
                "source_expression": effect.attrs.get("value"),
                "source_value_effect": effect.effect_id,
                "semantic_model": "yul_local_function_call",
            }))
        return out

    @staticmethod
    def _refs_by_src(unit: FunctionUnit) -> dict[str, str]:
        return {
            str(statement.src): str(statement.stmt_id)
            for statement in unit.source_statements
            if getattr(statement, "lang", None) == "yul" and getattr(statement, "src", None)
        }

    def _definition_refs(self, definition: dict[str, Any], refs_by_src: dict[str, str]) -> list[str]:
        refs: list[str] = []

        def visit(node: Any) -> None:
            if not isinstance(node, dict):
                return
            ref = refs_by_src.get(str(node.get("src") or ""))
            if ref and ref not in refs:
                refs.append(ref)
            for value in node.values():
                if isinstance(value, dict):
                    visit(value)
                elif isinstance(value, list):
                    for item in value:
                        visit(item)

        visit(definition.get("body"))
        return refs

    def _block_semantics(self, block: Any) -> list[dict[str, Any]]:
        if not isinstance(block, dict):
            return []
        out: list[dict[str, Any]] = []
        for statement in block.get("statements") or []:
            item = self._statement_semantics(statement)
            if item:
                out.append(item)
        return out

    def _statement_semantics(self, statement: Any) -> dict[str, Any] | None:
        if not isinstance(statement, dict):
            return None
        kind = str(statement.get("nodeType") or "")
        if kind in {"YulVariableDeclaration", "YulAssignment"}:
            targets = statement.get("variables") if kind == "YulVariableDeclaration" else statement.get("variableNames")
            names = [str(item.get("name")) for item in targets or [] if isinstance(item, dict) and item.get("name")]
            value = yul_expression(statement.get("value"))
            return {"kind": "ValueAssign", "targets": names, "value": normalize_expr(value)}
        if kind == "YulIf":
            condition = yul_expression(statement.get("condition"))
            return {"kind": "If", "condition": normalize_expr(condition, context="condition"), "body": self._block_semantics(statement.get("body"))}
        if kind == "YulSwitch":
            expression = yul_expression(statement.get("expression"))
            cases = []
            for case in statement.get("cases") or []:
                value = case.get("value")
                cases.append({
                    "value": "default" if value is None or value == "default" else normalize_expr(yul_expression(value)),
                    "body": self._block_semantics(case.get("body")),
                })
            return {"kind": "Switch", "expression": normalize_expr(expression), "cases": cases}
        if kind == "YulLeave":
            return {"kind": "ReturnFromLocalFunction"}
        if kind in {"YulBreak", "YulContinue"}:
            return {"kind": "ControlTransfer", "operation": kind.removeprefix("Yul").lower()}
        if kind == "YulForLoop":
            condition = yul_expression(statement.get("condition"))
            return {
                "kind": "For",
                "pre": self._block_semantics(statement.get("pre")),
                "condition": normalize_expr(condition, context="condition"),
                "post": self._block_semantics(statement.get("post")),
                "body": self._block_semantics(statement.get("body")),
            }
        if kind == "YulExpressionStatement":
            expression = yul_expression(statement.get("expression"))
            return {"kind": "Expression", "expression": normalize_expr(expression)}
        return None

    @staticmethod
    def _parameter(item: Any) -> dict[str, str]:
        name = str((item or {}).get("name") or "")
        # Yul identifiers are EVM words; uint256 is the faithful high-level
        # default until type evidence proves a narrower interpretation.
        return {"name": name, "type": "uint256"}

    def _solidity_like(self, name: str, definition: dict[str, Any], body: list[dict[str, Any]]) -> str:
        params = ", ".join(f"uint256 {item['name']}" for item in [self._parameter(value) for value in definition.get("parameters") or []])
        returns = ", ".join(f"uint256 {item['name']}" for item in [self._parameter(value) for value in definition.get("returnVariables") or []])
        lines = [f"function {name}({params}) internal pure returns ({returns}) {{"]
        lines.extend(self._render_body(body, "  "))
        lines.append("}")
        return "\n".join(lines)

    def _render_body(self, body: list[dict[str, Any]], indent: str) -> list[str]:
        lines: list[str] = []
        for item in body:
            kind = item.get("kind")
            if kind == "ValueAssign":
                for target in item.get("targets") or []:
                    lines.append(f"{indent}{target} = {item.get('value')};")
            elif kind == "If":
                lines.append(f"{indent}if {item.get('condition')} {{")
                lines.extend(self._render_body(item.get("body") or [], indent + "  "))
                lines.append(f"{indent}}}")
            elif kind == "ReturnFromLocalFunction":
                lines.append(f"{indent}return;")
            elif kind == "Expression":
                lines.append(f"{indent}{item.get('expression')};")
            else:
                lines.append(f"{indent}/* {kind}: retained in semantic body */")
        return lines

    @staticmethod
    def _cfg(cfg: Any) -> dict[str, Any]:
        return {
            "blocks": [
                YulLocalFunctionLifter._cfg_block(node)
                for node in getattr(cfg, "nodes", [])
            ],
            "edges": [
                {"from": edge.source, "to": edge.target, "kind": YulLocalFunctionLifter._cfg_edge_kind(edge.label)}
                for edge in getattr(cfg, "edges", [])
            ],
        }

    @staticmethod
    def _cfg_block(node: Any) -> dict[str, Any]:
        out = {"node_id": node.node_id, "kind": node.kind}
        text = str(getattr(node, "text", ""))
        if getattr(node, "kind", "") in {"condition", "loop-condition"} and text.startswith("if "):
            out["condition"] = normalize_expr(text.removeprefix("if "), context="condition")
        return out

    @staticmethod
    def _cfg_edge_kind(label: Any) -> str:
        text = str(label or "")
        if text.startswith("true:"):
            return "true"
        if text.startswith("false:"):
            return "false"
        if text.startswith("leave:"):
            return "return"
        return text
