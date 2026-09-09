#!/usr/bin/env python3
"""Recover typed memory-array reads from a Yul def-use pattern.

The generic MemorySSA evidence deliberately retains raw ``mload`` operations.
This pass consumes that evidence only after the overlay builder has completed
direct array/length recovery.  Its public result is a single typed array read;
the constituent pointer arithmetic remains provenance, not another semantic
operation for SFIR.
"""
from __future__ import annotations

import re
from typing import Any

from assembly_semantic_ir import parse_int_literal
from s_seir_id import IdAllocator
from s_seir_model import EffectNode, FunctionUnit, SemanticOverlay
from s_seir_yul_normalize import call_parts, normalize_expr


class MemoryArrayPatternLifter:
    """Match ``mload(base + 32 + index * 32)`` through SSA pointer aliases.

    A match is accepted only when a typed memory-array base, a word-stride
    address, and a true-edge bounds predicate are all proved.  Pointer aliases
    are resolved through a unique dominating ValueDef; no source-order lookup
    is used.
    """

    def __init__(self) -> None:
        self.ids = IdAllocator()

    def lift(
        self,
        unit: FunctionUnit,
        type_env: Any,
        memory_results: dict[int, Any],
        effects: list[EffectNode],
        overlays: list[SemanticOverlay],
        control: dict[str, Any],
    ) -> list[SemanticOverlay]:
        out = list(overlays)
        defs = self._value_defs(effects)
        dominators = self._dominators(control)
        lengths = [item for item in out if item.kind == "MemoryArrayLengthRead"]
        completed = {
            str(item.attrs.get("target") or "")
            for item in out
            if item.kind == "MemoryArrayElementRead" and item.attrs.get("bounds_proof")
        }
        aliases = self._array_aliases(unit, type_env, out)
        block_by_ref = {
            str(stmt.stmt_id): self._assembly_block_id(getattr(stmt, "block_id", None))
            for stmt in unit.source_statements
            if getattr(stmt, "lang", None) == "yul"
        }

        # Each successful element read whose element type is itself an array
        # becomes a typed pointer alias.  Iterate to a fixed point so matrix
        # accesses are recovered from def-use dependencies, not source order.
        progress = True
        while progress:
            progress = False
            for read in effects:
                if read.kind != "MemoryRead":
                    continue
                target = str(read.attrs.get("value") or "").strip()
                pointer = str(read.attrs.get("read_from") or "").strip()
                node = self._int(read.attrs.get("cfg_node_id"))
                if not target or not pointer or node is None:
                    continue
                value_def = self._unique_value_def(target, read, defs)
                if value_def is None:
                    continue
                block_id = next((block_by_ref.get(str(ref)) for ref in read.stmt_refs if block_by_ref.get(str(ref)) is not None), None)
                if block_id is None:
                    continue
                source_block = self._cfg_block(block_id, node)
                # A direct mload(alias) is the dynamic-array length word.  It
                # establishes the next dimension's bounds proof.
                if pointer in aliases and self._is_mload_of(value_def, pointer):
                    if self._add_alias_length(target, pointer, aliases, value_def, read, lengths, out):
                        progress = True
                    continue
                if target in completed:
                    continue
                expanded, steps = self._expand_pointer(
                    pointer, node, block_id, source_block, defs, dominators, set()
                )
                pattern = self._match_address(expanded, aliases, source_block, dominators)
                if pattern is None:
                    continue
                bounds = self._bounds_proof(pattern, read, lengths, control, block_id, node)
                if bounds is None:
                    continue
                evidence = self._atomic_pattern_steps(expanded, steps, target, source_block)
                pointer_effects = [str(step.get("value_def")) for step in steps if step.get("value_def")]
                access = f"{pattern['access']}[{pattern['index']}]"
                attrs = {
                    "overlay_kind": "MemoryArrayElementRead",
                    "target": target,
                    "array": pattern["access"],
                    "array_symbol": pattern["symbol"],
                    "array_type": pattern["array_type"],
                    "data_location": "memory",
                    "element_type": pattern["element_type"],
                    "index": pattern["index"],
                    "access": access,
                    "layout": "solidity_memory_dynamic_array_elements_after_length_word",
                    "source_expression": f"mload({pointer})",
                    "source_value_effect": value_def.effect_id,
                    "pointer_value_effects": pointer_effects,
                    "pointer_cfg_nodes": [str(step.get("cfg_node")) for step in steps if step.get("value_def") and step.get("cfg_node")],
                    "pattern_model": "memory_array_read_def_use_word_stride",
                    "bounds_proof": bounds,
                    "pattern_evidence": {"sink": read.effect_id, "steps": evidence},
                }
                overlay = SemanticOverlay(
                    self.ids.new("ov_mem_array"), "MemoryArrayElementRead",
                    [value_def.effect_id, read.effect_id], list(dict.fromkeys([*value_def.stmt_refs, *read.stmt_refs])), attrs,
                )
                # The overlay builder's direct layout match is a candidate:
                # raw ``mload`` does not by itself have Solidity index
                # semantics.  This CFG-backed result is the one canonical
                # element-read representation for the target.
                out[:] = [
                    item for item in out
                    if not (item.kind == "MemoryArrayElementRead" and str(item.attrs.get("target") or "") == target)
                ]
                out.append(overlay)
                completed.add(target)
                if self._is_array_type(pattern["element_type"]):
                    aliases[target] = {
                        "access": access,
                        "array_type": pattern["element_type"],
                        "element_type": self._element_type(pattern["element_type"]),
                        "anchor": source_block,
                    }
                progress = True
        # An unproved layout candidate must not reach SFIR as ``array[i]``:
        # Yul ``mload`` has no implicit out-of-range revert.  Its generic
        # expression overlay remains as the faithful unresolved result.
        return [
            item for item in out
            if item.kind != "MemoryArrayElementRead" or item.attrs.get("bounds_proof")
        ]

    @staticmethod
    def _value_defs(effects: list[EffectNode]) -> dict[str, list[EffectNode]]:
        out: dict[str, list[EffectNode]] = {}
        for effect in effects:
            if effect.kind != "ValueDef":
                continue
            for target in effect.attrs.get("targets") or []:
                out.setdefault(str(target), []).append(effect)
        return out

    def _unique_value_def(self, target: str, read: EffectNode, defs: dict[str, list[EffectNode]]) -> EffectNode | None:
        candidates = [item for item in defs.get(target, []) if set(item.stmt_refs).intersection(read.stmt_refs)]
        if len(candidates) == 1:
            return candidates[0]
        # The ValueDef and MemoryRead are normally emitted from one AST node.
        # If a frontend did not preserve the stmt reference, ambiguity remains
        # explicit rather than resolved by effect/source order.
        return None

    def _expand_pointer(
        self,
        expression: str,
        sink_node: int,
        assembly_block: int,
        sink_block: str,
        defs: dict[str, list[EffectNode]],
        dominators: dict[str, set[str]],
        seen: set[str],
    ) -> tuple[str, list[dict[str, Any]]]:
        text = str(expression).strip()
        if self._identifier(text) and text not in seen:
            candidates = []
            for definition in defs.get(text, []):
                node = self._int(definition.attrs.get("cfg_node_id"))
                if node is None:
                    continue
                block = self._cfg_block(assembly_block, node)
                value = str(definition.attrs.get("value") or "")
                name, _args = call_parts(value)
                if name not in {"add", "sub"}:
                    continue
                if self._dominates(dominators, block, sink_block):
                    candidates.append((definition, block, value))
            if len(candidates) == 1:
                definition, block, value = candidates[0]
                expanded, steps = self._expand_pointer(
                    value, sink_node, assembly_block, sink_block, defs, dominators, seen | {text}
                )
                name, args = call_parts(value)
                steps.insert(0, {
                    "operation": name,
                    "result": text,
                    "args": list(args),
                    "cfg_node": block,
                    "value_def": definition.effect_id,
                })
                return expanded, steps
            return text, []
        name, args = call_parts(text)
        if name in {"add", "sub"} and len(args) == 2:
            left, left_steps = self._expand_pointer(args[0], sink_node, assembly_block, sink_block, defs, dominators, seen)
            right, right_steps = self._expand_pointer(args[1], sink_node, assembly_block, sink_block, defs, dominators, seen)
            return f"{name}({left}, {right})", [*left_steps, *right_steps]
        return text, []

    def _array_aliases(
        self,
        unit: FunctionUnit,
        type_env: Any,
        overlays: list[SemanticOverlay],
    ) -> dict[str, dict[str, Any]]:
        """Return typed array bases, including recovered array-valued reads.

        Only a declared memory-array parameter may seed the graph directly.
        Subsequent dimensions are seeded by a completed array-element overlay,
        so every alias has a typed predecessor rather than merely resembling a
        pointer expression.
        """
        out: dict[str, dict[str, Any]] = {}
        for variable in unit.parameters:
            name = str(getattr(variable, "name", "") or "")
            type_string = self._strip_location(getattr(variable, "type_string", ""))
            if name and getattr(type_env, "is_memory_array_parameter", lambda _name: False)(name):
                out[name] = {
                    "access": name,
                    "array_type": type_string,
                    "element_type": self._element_type(type_string),
                    "anchor": None,
                }
        for overlay in overlays:
            if overlay.kind != "MemoryArrayElementRead":
                continue
            attrs = overlay.attrs
            target = str(attrs.get("target") or "")
            element_type = self._strip_location(attrs.get("element_type"))
            access = str(attrs.get("access") or "")
            if not target or not access or not self._is_array_type(element_type):
                continue
            out[target] = {
                "access": access,
                "array_type": element_type,
                "element_type": self._element_type(element_type),
                "anchor": str(attrs.get("semantic_anchor_cfg_node") or "") or None,
            }
        return out

    @staticmethod
    def _strip_location(type_string: Any) -> str:
        return re.sub(r"\\s+(memory|calldata|storage)\\b", "", str(type_string or "")).strip()

    def _is_array_type(self, type_string: Any) -> bool:
        return self._strip_location(type_string).endswith("[]")

    def _element_type(self, type_string: Any) -> str:
        text = self._strip_location(type_string)
        return text[:-2].strip() if text.endswith("[]") else text

    @staticmethod
    def _is_mload_of(value_def: EffectNode, pointer: str) -> bool:
        name, args = call_parts(str(value_def.attrs.get("value") or ""))
        return name == "mload" and len(args) == 1 and str(args[0]).strip() == pointer

    def _add_alias_length(
        self,
        target: str,
        pointer: str,
        aliases: dict[str, dict[str, Any]],
        value_def: EffectNode,
        read: EffectNode,
        lengths: list[SemanticOverlay],
        out: list[SemanticOverlay],
    ) -> bool:
        if any(str(item.attrs.get("target") or "") == target for item in lengths):
            return False
        alias = aliases[pointer]
        attrs = {
            "overlay_kind": "MemoryArrayLengthRead",
            "target": target,
            "array": alias["access"],
            "array_symbol": pointer,
            "array_type": alias["array_type"],
            "data_location": "memory",
            "element_type": alias["element_type"],
            "access": f"{alias['access']}.length",
            "layout": "solidity_memory_dynamic_array_length_at_base",
            "source_expression": f"mload({pointer})",
            "source_value_effect": value_def.effect_id,
            "pattern_model": "memory_array_length_def_use_alias",
        }
        overlay = SemanticOverlay(
            self.ids.new("ov_mem_array_len"), "MemoryArrayLengthRead",
            [value_def.effect_id, read.effect_id], list(dict.fromkeys([*value_def.stmt_refs, *read.stmt_refs])), attrs,
        )
        out.append(overlay)
        lengths.append(overlay)
        return True

    def _match_address(
        self,
        expression: str,
        aliases: dict[str, dict[str, Any]],
        sink_block: str,
        dominators: dict[str, set[str]],
    ) -> dict[str, str] | None:
        terms: list[str] = []
        self._flatten_add(expression, terms)
        bases = [
            term for term in terms
            if term in aliases
            and (aliases[term].get("anchor") is None or self._dominates(dominators, str(aliases[term]["anchor"]), sink_block))
        ]
        if len(bases) != 1:
            return None
        base = bases[0]
        constants = 0
        strides: list[str] = []
        for term in terms:
            if term == base:
                continue
            integer = parse_int_literal(term)
            if integer is not None:
                constants += integer
                continue
            index = self._word_stride_index(term)
            if index is None:
                return None
            strides.append(index)
        if constants != 32 or len(strides) != 1:
            return None
        alias = aliases[base]
        return {
            "symbol": base,
            "access": str(alias["access"]),
            "array_type": str(alias["array_type"]),
            "element_type": str(alias["element_type"]),
            "index": normalize_expr(strides[0]),
        }

    def _atomic_pattern_steps(
        self,
        expression: str,
        pointer_steps: list[dict[str, Any]],
        target: str,
        sink_block: str,
    ) -> list[dict[str, Any]]:
        """Render the matched def-use graph as atomic, dependency-ordered evidence.

        These rows are deliberately evidence only.  They make the successful
        match auditable without reintroducing pointer arithmetic into SFIR.
        """
        out = list(pointer_steps)
        aliases: dict[str, str] = {}
        for step in pointer_steps:
            result = str(step.get("result") or "")
            args = step.get("args") or []
            operation = str(step.get("operation") or "")
            if result and operation and args:
                aliases[self._compact(f"{operation}({', '.join(str(arg) for arg in args)})")] = result
        counter = 0

        def visit(value: str) -> str:
            nonlocal counter
            compact = self._compact(value)
            if compact in aliases:
                return aliases[compact]
            name, args = call_parts(value)
            if name not in {"add", "sub", "mul"} or len(args) != 2:
                return value
            left, right = visit(args[0]), visit(args[1])
            counter += 1
            result = f"__array_pattern_{counter}"
            out.append({
                "operation": name,
                "result": result,
                "args": [left, right],
                "cfg_node": sink_block,
            })
            return result

        address = visit(expression)
        out.append({"operation": "mload", "result": target, "args": [address], "cfg_node": sink_block})
        return out

    def _flatten_add(self, expression: str, out: list[str]) -> None:
        name, args = call_parts(str(expression).strip())
        if name == "add" and len(args) == 2:
            self._flatten_add(args[0], out)
            self._flatten_add(args[1], out)
            return
        out.append(str(expression).strip())

    @staticmethod
    def _word_stride_index(expression: str) -> str | None:
        name, args = call_parts(str(expression).strip())
        if name != "mul" or len(args) != 2:
            return None
        if parse_int_literal(args[0]) == 32:
            return args[1]
        if parse_int_literal(args[1]) == 32:
            return args[0]
        return None

    def _bounds_proof(
        self,
        pattern: dict[str, str],
        read: EffectNode,
        lengths: list[SemanticOverlay],
        control: dict[str, Any],
        assembly_block: int,
        node: int,
    ) -> dict[str, Any] | None:
        symbol = pattern["symbol"]
        array = pattern["access"]
        index = pattern["index"]
        normalized_index = self._compact(index)
        sink_block = self._cfg_block(assembly_block, node)
        dominators = self._dominators(control)
        path_states = [self._compact(value) for value in read.attrs.get("path_states") or []]
        for length in lengths:
            attrs = length.attrs
            if str(attrs.get("array_symbol") or attrs.get("array") or "") != symbol:
                continue
            length_target = str(attrs.get("target") or "")
            if not length_target:
                continue
            expected = self._compact(f"lt({index}, {length_target})")
            # A nested loop naturally carries the conjunction of every
            # enclosing true edge in its path state.  Treat a bounds term as
            # proved only when it is one complete conjunct, not merely a
            # substring of another expression.
            if not any(expected == term for state in path_states for term in state.split("&&")):
                continue
            predicate = next((item for item in control.get("blocks") or [] if self._cfg_text_matches(item, index, length_target)), None)
            if not predicate:
                continue
            predicate_block = str(predicate.get("block_id") or "")
            if not predicate_block or not self._dominates(dominators, predicate_block, sink_block):
                continue
            return {
                "predicate": f"({index} < {length_target})",
                "array_length": f"{array}.length",
                "predicate_cfg_node": predicate_block,
                "edge": "true",
                "index": normalized_index,
            }
        return None

    @staticmethod
    def _cfg_text_matches(block: dict[str, Any], index: str, length: str) -> bool:
        terminator = block.get("terminator") or {}
        text = str(terminator.get("text") or "")
        return MemoryArrayPatternLifter._compact(f"lt({index}, {length})") in MemoryArrayPatternLifter._compact(text)

    @staticmethod
    def _dominators(control: dict[str, Any]) -> dict[str, set[str]]:
        nodes = {str(item.get("block_id")) for item in control.get("blocks") or [] if item.get("block_id")}
        predecessors: dict[str, set[str]] = {node: set() for node in nodes}
        for edge in control.get("edges") or []:
            source, target = str(edge.get("from") or ""), str(edge.get("to") or "")
            if source in nodes and target in nodes:
                predecessors[target].add(source)
        entries = {node for node in nodes if not predecessors[node]}
        dom = {node: ({node} if node in entries else set(nodes)) for node in nodes}
        changed = True
        while changed:
            changed = False
            for node in nodes - entries:
                preds = predecessors[node]
                value = {node} | (set.intersection(*(dom[pred] for pred in preds)) if preds else set())
                if value != dom[node]:
                    dom[node] = value
                    changed = True
        return dom

    @staticmethod
    def _dominates(dominators: dict[str, set[str]], source: str, target: str) -> bool:
        return source == target or source in dominators.get(target, set())

    @staticmethod
    def _cfg_block(assembly_block: int, node: int) -> str:
        return f"bb_asm{assembly_block}_n{node}"

    @staticmethod
    def _assembly_block_id(value: Any) -> int | None:
        match = re.search(r"asm_block_(\d+)", str(value or ""))
        return int(match.group(1)) if match else None

    @staticmethod
    def _identifier(value: str) -> bool:
        return bool(re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", value))

    @staticmethod
    def _int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _compact(value: Any) -> str:
        return re.sub(r"\s+", "", str(value or ""))
