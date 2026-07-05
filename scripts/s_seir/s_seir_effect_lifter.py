#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path as _SSEIRPath
import sys as _sseir_sys
_SSEIR_ROOT = _SSEIRPath(__file__).resolve().parents[1]
for _sseir_path in (_SSEIR_ROOT / "legacy_yul", _SSEIR_ROOT / "s_seir"):
    _sseir_text = str(_sseir_path)
    if _sseir_text not in _sseir_sys.path:
        _sseir_sys.path.insert(0, _sseir_text)

from typing import Any

from assembly_ast_cfg import yul_statement_text, yul_expression
from s_seir_memory_ssa import resolve_memory_read_with_loops
from assembly_memory_ssa import direct_call, statement_expression
from s_seir_id import IdAllocator
from s_seir_model import EffectNode, FunctionUnit
from s_seir_yul_normalize import normalize_expr


class EffectLifter:
    SEMANTIC_CALLS = {
        "mload", "keccak256", "calldatacopy", "codecopy", "returndatacopy", "mcopy", "extcodecopy",
        "sload", "sstore", "call", "staticcall", "delegatecall", "callcode", "revert", "return",
        "log0", "log1", "log2", "log3", "log4",
    }

    def __init__(self):
        self.ids = IdAllocator()

    def effect(self, k, refs, attrs):
        return EffectNode(self.ids.new("eff"), k, [r for r in refs if r], attrs)

    def lift(self, unit: FunctionUnit, memory_results: dict[int, Any], control: dict[str, Any] | None = None):
        effects = []
        facts = []
        lookup = {(s.block_id, s.text, s.src): s.stmt_id for s in unit.source_statements if s.lang == "yul"}
        text_lookup = {(s.block_id, s.text): s.stmt_id for s in unit.source_statements if s.lang == "yul"}
        loop_contexts = (control or {}).get("loop_contexts", {})
        for block in unit.assembly_blocks:
            res = memory_results.get(block.block_id)
            label = f"asm_block_{block.block_id}"
            block_loop_context = loop_contexts.get(block.block_id) or loop_contexts.get(str(block.block_id)) or []
            if not res:
                continue
            facts.append({
                "kind": "MemorySSAQueryLayer",
                "assembly_block": block.block_id,
                "scope": getattr(res, "scope", "s_seir_function_memoryssa_view"),
                "function_loop_context": block_loop_context,
                "loop_alignment": "s_seir_controls_all_loop_contexts_legacy_yul_memoryssa_backend",
                "features": [
                    "path_states",
                    "linear_aliases",
                    "branch_candidates",
                    "loop_phi_facts",
                    "range_summary_facts",
                    "memory_top_facts",
                ],
            })
            for d in getattr(res, "memory_definitions", {}).values():
                effects.append(self.effect("MemoryWrite", [self.ref(res, d.node_id, lookup, text_lookup, label)], {
                    "address": d.address,
                    "value": d.value,
                    "memory_version": d.version,
                    "origin_node": f"N{d.node_id}",
                    "write_kind": d.kind,
                    "aliases": [a.__dict__ for a in getattr(d, "aliases", ())],
                }))
            for rec in getattr(res, "loop_records", {}).values():
                fact = {
                    "kind": "LoopMemoryRecord",
                    "loop_id": rec.loop_id,
                    "assembly_block": block.block_id,
                    "header_node": rec.header_node_id,
                    "condition": rec.condition_expr,
                    "bound": getattr(rec, "bound", None).__dict__ if getattr(rec, "bound", None) else None,
                    "memory_effects": [e.__dict__ for e in rec.memory_effects],
                    "status": rec.status,
                }
                facts.append(fact)
                effects.append(self.effect("LoopMemorySummary", [label], fact))
            for nid, node in res.node_ast.items():
                stmt = self.stmt_ref(node, lookup, text_lookup, label)
                expr = statement_expression(node) or node.get("condition")
                call, args = direct_call(expr)
                if call not in self.SEMANTIC_CALLS:
                    call, args = self.find_semantic_call(expr)
                vals = [yul_expression(a) for a in args]
                names = self.assigned(node)
                if node.get("nodeType") == "YulIf":
                    condition = yul_expression(node.get("condition"))
                    effects.append(self.effect("Branch", [stmt], {"condition": condition, "condition_normalized": normalize_expr(condition), "cfg_node_id": nid, "language": "yul", "path_states": self.node_path_states(res, nid)}))
                value_expr = self.value_expression(node)
                if names and value_expr is not None:
                    raw_value = yul_expression(value_expr)
                    effects.append(self.effect("ValueDef", [stmt], {
                        "targets": names,
                        "value": raw_value,
                        "value_normalized": normalize_expr(raw_value),
                        "cfg_node_id": nid,
                        "language": "yul",
                        "path_states": self.node_path_states(res, nid),
                        "target_versions": self.created_value_versions(res, nid, names),
                    }))
                if call == "mload" and len(vals) == 1:
                    effects.append(self.effect("MemoryRead", [stmt], {
                        "read_from": vals[0],
                        "value": names[0] if names else None,
                        "value_versions": self.created_value_versions(res, nid, names),
                        "cfg_node_id": nid,
                        "path_states": self.node_path_states(res, nid),
                        "memory_read": self.memory_query(res, nid, vals[0], None, "mload", block_loop_context),
                    }))
                elif call == "keccak256" and len(vals) == 2:
                    effects.append(self.effect("MemoryHash", [stmt], {
                        "op": call,
                        "ptr": vals[0],
                        "size": vals[1],
                        "value": names[0] if names else None,
                        "value_versions": self.created_value_versions(res, nid, names),
                        "cfg_node_id": nid,
                        "path_states": self.node_path_states(res, nid),
                        "memory_read": self.memory_query(res, nid, vals[0], vals[1], "keccak256", block_loop_context),
                    }))
                elif call in {"calldatacopy", "codecopy", "returndatacopy", "mcopy", "extcodecopy"}:
                    effects.append(self.effect("MemoryCopy", [stmt], {"op": call, "args": vals, "cfg_node_id": nid, "path_states": self.node_path_states(res, nid)}))
                elif call == "sload" and len(vals) == 1:
                    effects.append(self.effect("StorageRead", [stmt], {
                        "slot": vals[0],
                        "slot_versions": self.reaching_value_versions(res, nid, vals[0]),
                        "value": names[0] if names else None,
                        "value_versions": self.created_value_versions(res, nid, names),
                        "cfg_node_id": nid,
                        "path_states": self.node_path_states(res, nid),
                    }))
                elif call == "sstore" and len(vals) == 2:
                    effects.append(self.effect("StorageWrite", [stmt], {
                        "slot": vals[0],
                        "slot_versions": self.reaching_value_versions(res, nid, vals[0]),
                        "value": vals[1],
                        "value_versions": self.reaching_value_versions(res, nid, vals[1]),
                        "cfg_node_id": nid,
                        "path_states": self.node_path_states(res, nid),
                    }))
                elif call in {"call", "staticcall", "delegatecall", "callcode"}:
                    attrs = {"op": call, "args": vals, "cfg_node_id": nid, "path_states": self.node_path_states(res, nid)}
                    self.attach_call_memory(attrs, res, nid, vals, call, block_loop_context)
                    effects.append(self.effect({"call": "Call", "staticcall": "StaticCall", "delegatecall": "DelegateCall", "callcode": "CallCode"}[call], [stmt], attrs))
                elif call and call.startswith("log") and call[3:].isdigit():
                    attrs = {"op": call, "data_ptr": vals[0] if len(vals) > 0 else None, "data_size": vals[1] if len(vals) > 1 else None, "topics": vals[2:], "cfg_node_id": nid, "path_states": self.node_path_states(res, nid)}
                    if len(vals) >= 2:
                        attrs["data_memory"] = self.memory_query(res, nid, vals[0], vals[1], "event_log_data", block_loop_context)
                    effects.append(self.effect("EventLog", [stmt], attrs))
                elif call == "revert" and len(vals) >= 2:
                    effects.append(self.effect("Revert", [stmt], {
                        "payload_ptr": vals[0],
                        "payload_size": vals[1],
                        "cfg_node_id": nid,
                        "path_states": self.node_path_states(res, nid),
                        "payload_memory": self.memory_query(res, nid, vals[0], vals[1], "revert_payload", block_loop_context),
                    }))
                elif call == "return" and len(vals) >= 2:
                    effects.append(self.effect("Return", [stmt], {
                        "payload_ptr": vals[0],
                        "payload_size": vals[1],
                        "cfg_node_id": nid,
                        "path_states": self.node_path_states(res, nid),
                        "payload_memory": self.memory_query(res, nid, vals[0], vals[1], "return_payload", block_loop_context),
                    }))
            for phi in getattr(res, "memory_phis", {}).values():
                facts.append({"kind": "MemoryPhi", **phi.__dict__})
            for sm in getattr(res, "range_summaries", {}).values():
                facts.append({"kind": "MemoryRangeSummary", **sm.__dict__})
            for top in getattr(res, "memory_tops", []):
                facts.append({"kind": "MemoryTop", **top.__dict__})
        for stmt in unit.source_statements:
            if stmt.lang == "solidity":
                t = stmt.text.strip()
                if t.startswith("return"):
                    effects.append(self.effect("Return", [stmt.stmt_id], {"text": t, "language": "solidity"}))
                elif t.startswith("revert"):
                    effects.append(self.effect("Revert", [stmt.stmt_id], {"payload": t.removeprefix("revert").strip(), "language": "solidity"}))
                elif t.startswith("if"):
                    effects.append(self.effect("Branch", [stmt.stmt_id], {"condition": t, "language": "solidity"}))
        return self.dedupe_effects(effects), facts


    @staticmethod
    def dedupe_effects(effects):
        out = []
        seen = set()
        dedupe_kinds = {"Branch", "Call", "StaticCall", "DelegateCall", "CallCode"}
        for e in effects:
            if e.kind in dedupe_kinds:
                attrs = e.attrs
                key = (e.kind, tuple(e.stmt_refs), attrs.get("condition"), attrs.get("op"), tuple(attrs.get("args") or []))
                if key in seen:
                    continue
                seen.add(key)
            out.append(e)
        return out

    @staticmethod
    def find_semantic_call(expr):
        if not isinstance(expr, dict):
            return None, []
        call, args = direct_call(expr)
        if call in EffectLifter.SEMANTIC_CALLS:
            return call, args
        for value in expr.values():
            if isinstance(value, dict):
                found, found_args = EffectLifter.find_semantic_call(value)
                if found:
                    return found, found_args
            elif isinstance(value, list):
                for item in value:
                    found, found_args = EffectLifter.find_semantic_call(item)
                    if found:
                        return found, found_args
        return None, []



    @staticmethod
    def created_value_versions(res: Any, nid: int, names: list[str]) -> dict[str, list[str]]:
        versions: dict[str, list[str]] = {name: [] for name in names}
        for definition in getattr(res, "value_definitions", {}).values():
            if getattr(definition, "node_id", None) != nid:
                continue
            name = getattr(definition, "name", None)
            if name in versions and definition.version not in versions[name]:
                versions[name].append(definition.version)
        return {name: vals for name, vals in versions.items() if vals}

    @staticmethod
    def reaching_value_versions(res: Any, nid: int, expr: str | None) -> list[str]:
        if not expr or not str(expr).replace('_', '').replace('$', '').isalnum():
            return []
        name = str(expr)
        out: list[str] = []
        for state in res.states_at(nid):
            definition = getattr(state, 'values', {}).get(name)
            if definition and definition.version not in out:
                out.append(definition.version)
        return out

    @staticmethod
    def node_path_states(res: Any, nid: int) -> list[str]:
        states = []
        for state in res.states_at(nid):
            text = ' && '.join(state.predicates) if getattr(state, 'predicates', ()) else 'entry'
            if text not in states:
                states.append(text)
        return states

    @staticmethod
    def memory_query(res: Any, nid: int, pointer: str, length: str | None, reason: str, function_loop_context: list[dict[str, Any]]):
        query = resolve_memory_read_with_loops(res, nid, pointer, length, reason)
        EffectLifter.attach_word_value_versions(res, nid, query)
        query["function_loop_context"] = function_loop_context
        query["loop_alignment"] = "function_level_solidity_loop_context_plus_yul_loop_memoryssa"
        if function_loop_context:
            query["inside_function_level_loop"] = True
        return query

    @staticmethod
    def attach_word_value_versions(res: Any, nid: int, query: dict[str, Any]) -> None:
        for word in query.get('words') or []:
            value = word.get('value')
            if not EffectLifter.simple_identifier(value):
                continue
            versions: list[str] = []
            for definition in word.get('definitions') or []:
                node_id = definition.get('node_id')
                if node_id is None:
                    continue
                for version in EffectLifter.reaching_value_versions(res, int(node_id), str(value)):
                    if version not in versions:
                        versions.append(version)
            for candidate in word.get('branch_candidates') or []:
                definition = candidate.get('definition') or {}
                node_id = definition.get('node_id')
                if node_id is None:
                    continue
                for version in EffectLifter.reaching_value_versions(res, int(node_id), str(value)):
                    if version not in versions:
                        versions.append(version)
            if not versions:
                versions = EffectLifter.reaching_value_versions(res, nid, str(value))
            if versions:
                word['value_versions'] = versions

    @staticmethod
    def simple_identifier(value: Any) -> bool:
        text = str(value or '')
        if not text:
            return False
        return all(ch.isalnum() or ch in {'_', '$'} for ch in text) and not text[0].isdigit()

    @staticmethod
    def attach_call_memory(attrs: dict[str, Any], res: Any, nid: int, vals: list[str], call: str, block_loop_context: list[dict[str, Any]]) -> None:
        if call in {"call", "callcode"} and len(vals) >= 7:
            attrs.update({"gas": vals[0], "target": vals[1], "value": vals[2], "input_ptr": vals[3], "input_size": vals[4], "output_ptr": vals[5], "output_size": vals[6]})
            attrs["input_memory"] = EffectLifter.memory_query(res, nid, vals[3], vals[4], f"{call}_input", block_loop_context)
            attrs["output_memory_query"] = {"pointer": vals[5], "length": vals[6], "role": f"{call}_output_range"}
        elif call in {"staticcall", "delegatecall"} and len(vals) >= 6:
            attrs.update({"gas": vals[0], "target": vals[1], "input_ptr": vals[2], "input_size": vals[3], "output_ptr": vals[4], "output_size": vals[5]})
            attrs["input_memory"] = EffectLifter.memory_query(res, nid, vals[2], vals[3], f"{call}_input", block_loop_context)
            attrs["output_memory_query"] = {"pointer": vals[4], "length": vals[5], "role": f"{call}_output_range"}


    @staticmethod
    def value_expression(n):
        if n.get("nodeType") in {"YulVariableDeclaration", "YulAssignment"}:
            return n.get("value")
        return None

    @staticmethod
    def assigned(n):
        if n.get("nodeType") == "YulVariableDeclaration":
            return [x.get("name") for x in n.get("variables", []) if x.get("name")]
        if n.get("nodeType") == "YulAssignment":
            return [x.get("name") for x in n.get("variableNames", []) if x.get("name")]
        return []

    def ref(self, res, nid, lookup, text_lookup, label):
        return self.stmt_ref(res.node_ast.get(nid, {}), lookup, text_lookup, label)

    @staticmethod
    def stmt_ref(node, lookup, text_lookup, label):
        text = yul_statement_text(node)
        src = str(node.get("src", ""))
        return lookup.get((label, text, src)) or text_lookup.get((label, text)) or label
