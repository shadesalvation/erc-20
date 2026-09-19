#!/usr/bin/env python3
"""Complete Yul branch conditions into canonical high-level predicates.

The recovery pipeline already obtains AST-accurate Yul evaluation order,
MemorySSA reaching values, and completed operation overlays.  This pass is the
single completion point for their *control* meaning.  It deliberately does not
create another value analysis: unresolved low-level leaves remain upstream
evidence and are represented downstream only by an opaque predicate.
"""
from __future__ import annotations

import re
from typing import Any

from s_seir_id import IdAllocator
from s_seir_model import EffectNode, SemanticOverlay
from s_seir_overlay_builder import SemanticOverlayBuilder
from s_seir_yul_normalize import call_parts, int_text


Json = dict[str, Any]
_COMPARISONS = {"eq": "==", "lt": "<", "gt": ">", "slt": "<", "sgt": ">"}
_NEGATED_COMPARISONS = {"==": "!=", "<": ">=", ">": "<=", "<=": ">", ">=": "<", "!=": "=="}
_LOW_LEVEL_CALLS = {
    "sload", "sstore", "mload", "mstore", "keccak256", "calldataload",
    "calldatacopy", "staticcall", "call", "delegatecall", "callcode",
    "returndatasize", "extcodesize", "log0", "log1", "log2", "log3", "log4",
}


class PredicateLifter:
    """Build completed predicate overlays and disconnect generic condition traces.

    All matching is by the Branch effect identity and its CFG anchor, never by
    source position.  The branch effect is internal evidence only; SFIR sees
    the resulting Predicate overlay and semantic CFG provenance.
    """

    def __init__(self) -> None:
        self.ids = IdAllocator()
        self._builder = SemanticOverlayBuilder()

    def lift(
        self,
        type_env: Any,
        effects: list[EffectNode],
        overlays: list[SemanticOverlay],
        control: Json | None = None,
    ) -> list[SemanticOverlay]:
        branches = [effect for effect in effects if effect.kind == "Branch" and effect.attrs.get("language") == "yul"]
        boolean_values = self._boolean_values(type_env, effects)
        loop_conditions = self._loop_conditions(control or {})
        switch_conditions = self._switch_conditions(control or {}, type_env)
        if not branches and not loop_conditions and not switch_conditions:
            return overlays

        predicates: list[SemanticOverlay] = []
        rewrites: dict[str, str] = {}
        branch_ids: set[str] = set()
        for branch in branches:
            raw = str(branch.attrs.get("condition") or "").strip()
            if not raw:
                continue
            branch_ids.add(branch.effect_id)
            predicate_id = self.ids.new("pred")
            semantic_values = self._branch_state_values(branch, effects, overlays)
            expression, status = self._predicate_expression(raw, type_env, semantic_values, predicate_id, boolean_values)
            rewrites[raw] = expression
            predicates.append(SemanticOverlay(
                predicate_id,
                "Predicate",
                [branch.effect_id],
                list(branch.stmt_refs),
                {
                    "predicate_id": predicate_id,
                    "expression": expression,
                    "status": status,
                    "context": "condition",
                    "semantic_model": "cfg_reaching_definition_predicate",
                    "evaluation_model": "yul_ast_right_to_left_function_call_arguments",
                    "dependencies": self._rough_reads(expression),
                    "typed_predicate": self._typed_evaluation(branch, predicate_id),
                },
            ))

        # Yul for-loops expose their predicate on the CFG true edge rather
        # than as a YulIf AST node.  This is still CFG-derived control
        # semantics, and it carries a direct completed CFG anchor.
        for raw, block_id, refs in loop_conditions:
            predicate_id = self.ids.new("pred")
            expression, status = self._predicate_expression(raw, type_env, {}, predicate_id, boolean_values)
            rewrites[raw] = expression
            predicates.append(SemanticOverlay(
                predicate_id,
                "Predicate",
                [],
                refs,
                {
                    "predicate_id": predicate_id,
                    "expression": expression,
                    "status": status,
                    "context": "condition",
                    "semantic_model": "cfg_loop_predicate",
                    "semantic_anchor_cfg_node": block_id,
                    "semantic_evidence_cfg_nodes": [block_id],
                    "dependencies": self._rough_reads(expression),
                },
            ))

        # A Yul switch is a multi-way value comparison, not a YulIf Branch
        # effect.  Its terminator plus case/default CFG edges provide complete
        # control evidence, including the complement guard of default.
        for switch in switch_conditions:
            predicate_id = self.ids.new("pred")
            predicates.append(SemanticOverlay(
                predicate_id,
                "Predicate",
                [],
                list(switch["stmt_refs"]),
                {
                    "predicate_id": predicate_id,
                    "expression": switch["expression"],
                    "status": "resolved",
                    "context": "switch",
                    "semantic_model": "cfg_switch_discriminant",
                    "semantic_anchor_cfg_node": switch["block_id"],
                    "semantic_evidence_cfg_nodes": [switch["block_id"]],
                    "dependencies": self._rough_reads(switch["expression"]),
                    "switch_edges": switch["edges"],
                },
            ))

        # A completed predicate is the canonical downstream representation of
        # a branch.  Generic condition normalization and the AST evaluation
        # trace are still useful upstream evidence, but cannot duplicate it in
        # SFIR.  Match only through parent Branch identities.
        effect_by_id = {effect.effect_id: effect for effect in effects}
        completed: list[SemanticOverlay] = []
        for overlay in overlays:
            attrs = overlay.attrs
            if overlay.kind == "ExpressionNormalization" and attrs.get("context") == "condition":
                if any(effect_id in branch_ids for effect_id in overlay.effects):
                    continue
            if overlay.kind == "EvaluationStep":
                parent = ""
                for effect_id in overlay.effects:
                    effect = effect_by_id.get(effect_id)
                    if effect:
                        parent = str(effect.attrs.get("parent_effect") or "")
                        if parent:
                            break
                if parent in branch_ids:
                    continue
            raw_require_guard = str(attrs.get("nearest_condition") or "").strip() if overlay.kind == "RequireOverlay" else ""
            self._rewrite_overlay_conditions(attrs, rewrites, type_env, boolean_values)
            if overlay.kind == "RequireOverlay":
                if raw_require_guard in rewrites:
                    # RequireOverlay denotes the continuing edge, whereas a
                    # recovered empty revert is anchored on the failing Yul
                    # branch.  Make that polarity explicit structurally.
                    attrs["condition"] = self._negate(rewrites[raw_require_guard])
            completed.append(overlay)
        completed.extend(predicates)
        return completed

    @staticmethod
    def _typed_evaluation(branch: EffectNode, predicate_id: str) -> Json:
        """Project the existing AST evaluation DAG; never parse expression text.

        This small evidence subset is deliberately separate from rendering.
        Each identifier stays an AST operand locator until SFIR FactSSA supplies
        its exact definition. Unsupported calls keep an explicit failure record.
        """
        evaluation = branch.attrs.get("condition_evaluation") or {}
        model = "yul_ast_right_to_left_function_call_arguments"
        evidence = {"format": "yul-typed-predicate/v1", "predicate_ref": predicate_id,
                    "evaluation_model": evaluation.get("evaluation_model")}
        if evaluation.get("evaluation_model") != model:
            return {**evidence, "resolution_status": "unsupported", "reason": "missing AST evaluation evidence"}
        values = {}
        try:
            for step in evaluation.get("steps") or []:
                args, operands = step.get("argument_nodes"), []
                if not isinstance(args, list) or len(args) != len(step.get("evaluated_args") or []):
                    raise ValueError("missing AST argument identities")
                for index, arg in enumerate(args):
                    identity = {"predicate_ref": predicate_id, "step_temp": step.get("temp"),
                                "argument_index": index, "ast_node": arg}
                    if arg.get("node_type") == "YulIdentifier" and arg.get("name"):
                        operands.append({"op": "read", "type": "uint256", "identity": identity})
                    elif arg.get("node_type") == "YulLiteral" and arg.get("literal_kind") == "number":
                        raw = str(arg.get("value"))
                        number = int(raw, 16) if raw.startswith("0x") else int(raw)
                        if not 0 <= number < 2**256:
                            raise ValueError("invalid Yul word literal")
                        operands.append({"op": "literal", "type": "uint256", "literal": str(number), "identity": identity})
                    elif arg.get("node_type") == "YulFunctionCall" and arg.get("result_temp") in values:
                        operands.append(values[arg["result_temp"]])
                    else:
                        raise ValueError("unresolved AST operand")
                call = step.get("call")
                if call in {"eq", "lt", "gt", "slt", "sgt"} and len(operands) == 2 and all(x["type"] == "uint256" for x in operands):
                    value = {"op": call, "type": "bool", "operands": operands}
                elif call == "iszero" and len(operands) == 1:
                    value = {"op": call, "type": "bool", "operands": operands}
                elif call in {"and", "or"} and len(operands) == 2 and all(x["type"] == "bool" for x in operands):
                    value = {"op": call, "type": "bool", "operands": operands}
                else:
                    raise ValueError("unsupported AST predicate call: " + str(call))
                if not step.get("temp") or step["temp"] in values:
                    raise ValueError("duplicate/missing evaluation temporary")
                values[step["temp"]] = {**value, "evaluation_ref": {"predicate_ref": predicate_id, "step_temp": step["temp"]}}
            expression = values.get(evaluation.get("final"))
            if not expression or expression["type"] != "bool":
                raise ValueError("no supported final typed Bool evaluation")
            return {**evidence, "resolution_status": "resolved", "expression": expression}
        except (ValueError, TypeError) as exc:
            return {**evidence, "resolution_status": "unsupported", "reason": str(exc)}

    def _predicate_expression(
        self,
        raw: str,
        type_env: Any,
        semantic_values: dict[str, str],
        predicate_id: str,
        boolean_values: set[str],
    ) -> tuple[str, str]:
        try:
            rendered = self._render_boolean(raw, type_env, semantic_values, boolean_values)
        except Exception:
            rendered = None
        if not rendered or self._contains_low_level_call(rendered):
            return f"opaquePredicate({predicate_id})", "unresolved"
        return rendered, "resolved"

    def _render_boolean(self, expr: str, type_env: Any, semantic_values: dict[str, str], boolean_values: set[str]) -> str | None:
        name, args = call_parts(expr)
        if name in _COMPARISONS and len(args) == 2:
            return f"({self._render_value(args[0], type_env, semantic_values)} {_COMPARISONS[name]} {self._render_value(args[1], type_env, semantic_values)})"
        if not name and str(expr).strip() in boolean_values:
            return str(expr).strip()
        if name == "iszero" and len(args) == 1:
            inner = args[0]
            if self._is_boolean_expr(inner, boolean_values):
                return self._negate(self._render_boolean(inner, type_env, semantic_values, boolean_values) or "false")
            if self._is_address_value(inner, type_env):
                return f"({self._render_value(inner, type_env, semantic_values)} == address(0))"
            return f"({self._render_value(inner, type_env, semantic_values)} == 0)"
        if name in {"and", "or"} and len(args) == 2 and all(self._is_boolean_expr(arg, boolean_values) for arg in args):
            # Yul evaluates both operands eagerly.  Any observable operation
            # inside them has already been lifted as a separate high-level
            # operation; only then is &&/|| a safe predicate representation.
            operator = "&&" if name == "and" else "||"
            return f"({self._render_boolean(args[0], type_env, semantic_values, boolean_values)} {operator} {self._render_boolean(args[1], type_env, semantic_values, boolean_values)})"
        value = self._render_value(expr, type_env, semantic_values)
        return f"({value} != 0)" if value else None

    def _render_value(self, expr: str, type_env: Any, semantic_values: dict[str, str]) -> str:
        text = str(expr or "").strip()
        if text in semantic_values:
            return semantic_values[text]
        name, args = call_parts(text)
        if name == "returndatasize" and not args:
            return "returnDataSize()"
        if name == "calldatasize" and not args:
            return "msg.data.length"
        if name == "callvalue" and not args:
            return "msg.value"
        if name == "not" and len(args) == 1 and int_text(args[0]) == 0:
            return "type(uint256).max"
        direct_state = self._builder.direct_state_read_from_sload_expr(type_env, text)
        if direct_state:
            return str(direct_state["solidity_like"])
        if name:
            rendered_args = [self._render_value(arg, type_env, semantic_values) for arg in args]
            return self._builder.render_normalized_call(name, rendered_args, text)
        return text

    @classmethod
    def _is_boolean_expr(cls, expr: str, boolean_values: set[str]) -> bool:
        name, args = call_parts(expr)
        if name in _COMPARISONS and len(args) == 2:
            return True
        if name == "iszero" and len(args) == 1:
            return True
        if not name:
            return str(expr).strip() in boolean_values
        return name in {"and", "or"} and len(args) == 2 and all(cls._is_boolean_expr(arg, boolean_values) for arg in args)

    @staticmethod
    def _is_address_value(expr: str, type_env: Any) -> bool:
        info = getattr(type_env, "lookup", lambda _name: None)(str(expr).strip())
        return "address" in str(getattr(info, "type_string", "") or "")

    @staticmethod
    def _negate(expression: str) -> str:
        text = str(expression).strip()
        inner = text[1:-1].strip() if text.startswith("(") and text.endswith(")") else text
        if "&&" not in inner and "||" not in inner:
            for operator in ("!=", "<=", ">=", "==", "<", ">"):
                marker = f" {operator} "
                if marker in inner and inner.count(marker) == 1:
                    left, right = inner.split(marker, 1)
                    return f"({left} {_NEGATED_COMPARISONS[operator]} {right})"
        if text.startswith("!(") and text.endswith(")"):
            return text[2:-1]
        return f"!({text})"

    @classmethod
    def _contains_low_level_call(cls, expression: str) -> bool:
        return any(re.search(rf"\b{re.escape(name)}\s*\(", expression) for name in _LOW_LEVEL_CALLS)

    def _rewrite_overlay_conditions(
        self,
        attrs: Json,
        rewrites: dict[str, str],
        type_env: Any,
        boolean_values: set[str],
    ) -> None:
        for key in ("condition", "nearest_condition"):
            if attrs.get(key):
                attrs[key] = self._rewrite_condition(str(attrs[key]), rewrites, type_env, boolean_values)
        for key in ("require_conditions", "semantic_require_conditions", "evaluated_require_conditions", "merged_conditions"):
            if isinstance(attrs.get(key), list):
                attrs[key] = [self._rewrite_condition(str(item), rewrites, type_env, boolean_values) for item in attrs[key] if item]
        for candidate in attrs.get("candidates") or []:
            if isinstance(candidate, dict) and candidate.get("condition"):
                candidate["condition"] = self._rewrite_condition(str(candidate["condition"]), rewrites, type_env, boolean_values)

    def _rewrite_condition(
        self,
        condition: str,
        rewrites: dict[str, str],
        type_env: Any,
        boolean_values: set[str],
    ) -> str:
        text = condition.strip()
        if self._is_high_condition(text):
            return text
        if text in rewrites:
            return rewrites[text]
        if text.startswith("!(") and text.endswith(")"):
            inner = text[2:-1].strip()
            if inner in rewrites:
                return self._negate(rewrites[inner])
        parts = [part.strip() for part in text.split(" && ") if part.strip() and part.strip() != "entry"]
        if len(parts) > 1:
            return " && ".join(self._rewrite_condition(part, rewrites, type_env, boolean_values) for part in parts)
        rendered, _status = self._predicate_expression(text, type_env, {}, "unresolved", boolean_values)
        return rendered

    @staticmethod
    def _branch_state_values(
        branch: EffectNode,
        effects: list[EffectNode],
        overlays: list[SemanticOverlay],
    ) -> dict[str, str]:
        """Resolve condition reads through the exact Branch evaluation DAG.

        A nested StorageRead is associated with its parent Branch by the
        evaluation-step identity emitted from the Yul AST.  Its completed
        state overlay must cite that same read effect.  This is a CFG/def-use
        relation, not a textual or source-order lookup.
        """
        values: dict[str, str] = {}
        for effect in effects:
            step = effect.attrs.get("evaluation_step") or {}
            if effect.kind != "StorageRead" or str(step.get("parent_effect") or "") != branch.effect_id:
                continue
            access = None
            for overlay in overlays:
                if effect.effect_id not in overlay.effects:
                    continue
                if overlay.kind in {"StateVariableRead", "MappingRead"}:
                    access = str(overlay.attrs.get("access") or "")
                    if access:
                        break
            if not access:
                continue
            expression = str(step.get("expression") or "").strip()
            temp = str(step.get("temp") or effect.attrs.get("value") or "").strip()
            if expression:
                values[expression] = access
            if temp:
                values[temp] = access
        return values

    @staticmethod
    def _is_high_condition(text: str) -> bool:
        if not any(operator in text for operator in ("==", "!=", "<=", ">=", "<", ">", "&&", "||")):
            return False
        return not any(re.search(rf"\b{name}\s*\(", text) for name in {"iszero", "eq", "lt", "gt", "slt", "sgt", "and", "or", "sload", "mload", "returndatasize"})

    @staticmethod
    def _boolean_values(type_env: Any, effects: list[EffectNode]) -> set[str]:
        values: set[str] = set()
        for effect in effects:
            if effect.kind in {"Call", "StaticCall", "DelegateCall", "CallCode"} and effect.attrs.get("result"):
                values.add(str(effect.attrs["result"]))
        for name, value in (getattr(type_env, "variables", {}) or {}).items():
            if "bool" in str(getattr(value, "type_string", "") or ""):
                values.add(str(name))
        return values

    @staticmethod
    def _loop_conditions(control: Json) -> list[tuple[str, str, list[str]]]:
        outgoing: dict[str, list[str]] = {}
        for edge in control.get("edges") or []:
            if isinstance(edge, dict) and edge.get("from") and str(edge.get("kind") or "").startswith("true:"):
                outgoing.setdefault(str(edge["from"]), []).append(str(edge["kind"]).split(":", 1)[1].strip())
        out: list[tuple[str, str, list[str]]] = []
        for block in control.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            if str((block.get("terminator") or {}).get("node_kind") or "") != "loop-condition":
                continue
            conditions = list(dict.fromkeys(outgoing.get(str(block.get("block_id") or ""), [])))
            if len(conditions) == 1 and conditions[0]:
                out.append((conditions[0], str(block["block_id"]), list(block.get("stmts") or [])))
        return out

    def _switch_conditions(self, control: Json, type_env: Any) -> list[Json]:
        """Lift every structurally verified Yul switch edge.

        The CFG producer writes each case as ``case: <discriminant> ==
        <value>``.  Matching the left side to the terminator's discriminant is
        the evidence that lets this pass turn the edge into a semantic case;
        it never infers cases from source order or block position.
        """
        outgoing: dict[str, list[str]] = {}
        for edge in control.get("edges") or []:
            if isinstance(edge, dict) and edge.get("from") and edge.get("kind"):
                outgoing.setdefault(str(edge["from"]), []).append(str(edge["kind"]))
        out: list[Json] = []
        for block in control.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            terminator = block.get("terminator") or {}
            if str(terminator.get("node_kind") or "") != "switch":
                continue
            raw = str(terminator.get("condition") or "").strip()
            if raw.startswith("switch "):
                raw = raw.removeprefix("switch ").strip()
            if not raw:
                continue
            expression = self._render_value(raw, type_env, {})
            if not expression or self._contains_low_level_call(expression):
                continue
            edges: list[Json] = []
            case_guards: list[str] = []
            for edge_kind in outgoing.get(str(block.get("block_id") or ""), []):
                if edge_kind.startswith("case:"):
                    case_value = self._switch_case(edge_kind.removeprefix("case:").strip(), raw)
                    if case_value is None:
                        continue
                    value = self._render_value(case_value, type_env, {})
                    if not value or self._contains_low_level_call(value):
                        continue
                    guard = f"({expression} == {value})"
                    case_guards.append(guard)
                    edges.append({
                        "kind": f"case: {value}",
                        "case_value": value,
                        "guard": guard,
                    })
                elif edge_kind.startswith("default:") or edge_kind == "default":
                    edges.append({"kind": "default", "default": True})
            if not case_guards:
                continue
            default_guard = " && ".join(f"!({guard})" for guard in case_guards)
            for edge in edges:
                if edge.pop("default", False):
                    edge["guard"] = default_guard
            out.append({
                "block_id": str(block["block_id"]),
                "stmt_refs": list(block.get("stmts") or []),
                "expression": expression,
                "edges": edges,
            })
        return out

    @staticmethod
    def _switch_case(case_expression: str, discriminant: str) -> str | None:
        """Return the RHS of a top-level case equality for this switch."""
        text = str(case_expression).strip()
        depth = 0
        for index in range(len(text) - 1):
            char = text[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == "=" and text[index + 1] == "=" and depth == 0:
                left, right = text[:index].strip(), text[index + 2:].strip()
                if PredicateLifter._compact(left) == PredicateLifter._compact(discriminant) and right:
                    return right
                return None
        return None

    @staticmethod
    def _compact(value: Any) -> str:
        return re.sub(r"\s+", "", str(value or ""))

    @staticmethod
    def _rough_reads(expression: str) -> list[str]:
        names = re.findall(r"\b[A-Za-z_$][A-Za-z0-9_$.]*\b", expression)
        ignored = {"true", "false", "address", "uint256", "type", "max", "msg", "returnDataSize", "opaquePredicate"}
        return list(dict.fromkeys(name for name in names if name not in ignored and not name.isdigit()))
