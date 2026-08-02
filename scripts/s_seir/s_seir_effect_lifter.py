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
from assembly_external_call_ir import PRECOMPILES
from s_seir_memory_ssa import resolve_memory_read_with_loops
from assembly_memory_ssa import direct_call, statement_expression
from s_seir_id import IdAllocator
from s_seir_model import EffectNode, FunctionUnit
from s_seir_yul_eval_order import YulEvaluationOrder
from s_seir_yul_normalize import call_parts, int_text, normalize_expr


class EffectLifter:
    FIXED_PRECOMPILE_RETURNDATA = {1: 32, 2: 32, 3: 32}
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
        assembly_entry_conditions = self.assembly_entry_conditions(control or {})
        for block in unit.assembly_blocks:
            block_effect_start = len(effects)
            res = memory_results.get(block.block_id)
            label = f"asm_block_{block.block_id}"
            block_loop_context = loop_contexts.get(block.block_id) or loop_contexts.get(str(block.block_id)) or []
            if not res:
                continue
            setattr(res, "function_path_prefixes", assembly_entry_conditions.get(block.block_id, []))
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
                    "byte_axis_memory_slice",
                ],
            })
            for d in getattr(res, "memory_definitions", {}).values():
                ref = self.ref(res, d.node_id, lookup, text_lookup, label)
                for read_expr, read_slot in self.sload_reads_in_expr(getattr(d, "value", None)):
                    nested_inline_hash = self.inline_keccak_hash_effect(ref, res, d.node_id, read_slot, block_loop_context)
                    if nested_inline_hash:
                        effects.append(nested_inline_hash)
                    read_slot_versions = self.reaching_value_versions(res, d.node_id, read_slot)
                    if nested_inline_hash and nested_inline_hash.attrs.get("inline_slot_key") not in read_slot_versions:
                        read_slot_versions = [nested_inline_hash.attrs["inline_slot_key"]] + read_slot_versions
                    effects.append(self.effect("StorageRead", [ref], {
                        "slot": read_slot,
                        "slot_versions": read_slot_versions,
                        "value": None,
                        "value_versions": {},
                        "cfg_node_id": d.node_id,
                        "path_states": self.node_path_states(res, d.node_id),
                        "expression": read_expr,
                        "nested_in_memory_value": True,
                        "parent_call": d.kind,
                        "parent_memory_address": d.address,
                        "parent_memory_value": d.value,
                        "parent_memory_version": d.version,
                    }))
                effects.append(self.effect("MemoryWrite", [ref], {
                    "address": d.address,
                    "value": d.value,
                    "memory_version": d.version,
                    "origin_node": f"N{d.node_id}",
                    "write_kind": d.kind,
                    "aliases": [a.__dict__ for a in getattr(d, "aliases", ())],
                    "cfg_node_id": d.node_id,
                    "path_states": self.node_path_states(res, d.node_id),
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
                direct_semantic_call = call in self.SEMANTIC_CALLS
                if not direct_semantic_call:
                    call, args = None, []
                vals = [yul_expression(a) for a in args]
                names = self.assigned(node)
                if node.get("nodeType") == "YulIf":
                    condition_node = node.get("condition")
                    condition = yul_expression(condition_node)
                    if self.is_replayed_branch_node(res, nid, condition):
                        continue
                    evaluation = YulEvaluationOrder(stmt).materialize(condition_node, context="condition")
                    branch_effect = self.effect("Branch", [stmt], {
                        "condition": condition,
                        "condition_normalized": normalize_expr(condition),
                        "condition_evaluation": evaluation,
                        "condition_final_temp": evaluation.get("final"),
                        "cfg_node_id": nid,
                        "language": "yul",
                        "path_states": self.node_path_states(res, nid),
                    })
                    effects.extend(self.evaluation_effects(stmt, res, nid, evaluation, branch_effect.effect_id, block_loop_context))
                    effects.append(branch_effect)
                    continue
                if node.get("nodeType") in {"YulBreak", "YulContinue", "YulLeave"}:
                    effects.append(self.effect("ControlTransfer", [stmt], {
                        "op": yul_statement_text(node),
                        "cfg_node_id": nid,
                        "language": "yul",
                        "path_states": self.node_path_states(res, nid),
                    }))
                value_expr = self.value_expression(node)
                value_effect: EffectNode | None = None
                atomized_value: dict[str, Any] | None = None
                if names and value_expr is not None:
                    raw_value = yul_expression(value_expr)
                    atomized_value = self.atomize_value(stmt, value_expr, direct_semantic_call)
                    value_attrs = {
                        "targets": names,
                        "value": raw_value,
                        "value_normalized": normalize_expr(raw_value),
                        "cfg_node_id": nid,
                        "language": "yul",
                        "path_states": self.node_path_states(res, nid),
                        "target_versions": self.created_value_versions(res, nid, names),
                    }
                    if atomized_value:
                        value_attrs["atomized_value"] = atomized_value
                    value_effect = self.effect("ValueDef", [stmt], value_attrs)
                    if atomized_value:
                        effects.extend(self.evaluation_effects(
                            stmt,
                            res,
                            nid,
                            atomized_value,
                            value_effect.effect_id,
                            block_loop_context,
                            context="value",
                        ))
                    effects.append(value_effect)
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
                        "ptr_versions": self.reaching_value_versions(res, nid, vals[0]),
                        "size_versions": self.reaching_value_versions(res, nid, vals[1]),
                        "cfg_node_id": nid,
                        "path_states": self.node_path_states(res, nid),
                        "memory_read": self.memory_query(res, nid, vals[0], vals[1], "keccak256", block_loop_context),
                    }))
                elif call in {"calldatacopy", "codecopy", "returndatacopy", "mcopy", "extcodecopy"}:
                    effects.append(self.effect("MemoryCopy", [stmt], {"op": call, "args": vals, "cfg_node_id": nid, "path_states": self.node_path_states(res, nid)}))
                elif call == "sload" and len(vals) == 1:
                    inline_hash = self.inline_keccak_hash_effect(stmt, res, nid, vals[0], block_loop_context)
                    if inline_hash:
                        effects.append(inline_hash)
                    slot_versions = self.reaching_value_versions(res, nid, vals[0])
                    if inline_hash and inline_hash.attrs.get("inline_slot_key") not in slot_versions:
                        slot_versions = [inline_hash.attrs["inline_slot_key"]] + slot_versions
                    effects.append(self.effect("StorageRead", [stmt], {
                        "slot": vals[0],
                        "slot_versions": slot_versions,
                        "value": names[0] if names else None,
                        "value_versions": self.created_value_versions(res, nid, names),
                        "cfg_node_id": nid,
                        "path_states": self.node_path_states(res, nid),
                    }))
                elif call == "sstore" and len(vals) == 2:
                    inline_hash = self.inline_keccak_hash_effect(stmt, res, nid, vals[0], block_loop_context)
                    if inline_hash:
                        effects.append(inline_hash)
                    slot_versions = self.reaching_value_versions(res, nid, vals[0])
                    if inline_hash and inline_hash.attrs.get("inline_slot_key") not in slot_versions:
                        slot_versions = [inline_hash.attrs["inline_slot_key"]] + slot_versions
                    for read_expr, read_slot in self.sload_reads_in_expr(vals[1]):
                        nested_inline_hash = self.inline_keccak_hash_effect(stmt, res, nid, read_slot, block_loop_context)
                        if nested_inline_hash:
                            effects.append(nested_inline_hash)
                        read_slot_versions = self.reaching_value_versions(res, nid, read_slot)
                        if nested_inline_hash and nested_inline_hash.attrs.get("inline_slot_key") not in read_slot_versions:
                            read_slot_versions = [nested_inline_hash.attrs["inline_slot_key"]] + read_slot_versions
                        effects.append(self.effect("StorageRead", [stmt], {
                            "slot": read_slot,
                            "slot_versions": read_slot_versions,
                            "value": None,
                            "value_versions": {},
                            "cfg_node_id": nid,
                            "path_states": self.node_path_states(res, nid),
                            "expression": read_expr,
                            "nested_in_storage_value": True,
                            "parent_call": "sstore",
                            "parent_slot": vals[0],
                            "parent_value": vals[1],
                        }))
                    effects.append(self.effect("StorageWrite", [stmt], {
                        "slot": vals[0],
                        "slot_versions": slot_versions,
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
                    topic_memory_reads = self.topic_memory_reads(res, nid, vals[2:], block_loop_context)
                    if topic_memory_reads:
                        attrs["topic_memory_reads"] = topic_memory_reads
                    effects.append(self.effect("EventLog", [stmt], attrs))
                elif call == "revert" and len(vals) >= 2:
                    effects.append(self.effect("Revert", [stmt], {
                        "payload_ptr": vals[0],
                        "payload_size": vals[1],
                        "cfg_node_id": nid,
                        "path_states": self.node_path_states(res, nid),
                        "payload_memory": self.memory_query(res, nid, vals[0], vals[1], "revert_payload", block_loop_context),
                        "payload_memory_partial": self.partial_memory_slice(res, nid, vals[0], vals[1], "revert_payload_partial"),
                    }))
                elif call == "return" and len(vals) >= 2:
                    effects.append(self.effect("Return", [stmt], {
                        "payload_ptr": vals[0],
                        "payload_size": vals[1],
                        "cfg_node_id": nid,
                        "path_states": self.node_path_states(res, nid),
                        "payload_memory": self.memory_query(res, nid, vals[0], vals[1], "return_payload", block_loop_context),
                        "payload_memory_partial": self.partial_memory_slice(res, nid, vals[0], vals[1], "return_payload_partial"),
                    }))
            for phi in getattr(res, "memory_phis", {}).values():
                facts.append({"kind": "MemoryPhi", **phi.__dict__})
            for sm in getattr(res, "range_summaries", {}).values():
                facts.append({"kind": "MemoryRangeSummary", **sm.__dict__})
            for top in getattr(res, "memory_tops", []):
                facts.append({"kind": "MemoryTop", **top.__dict__})
            self.attach_cross_statement_call_outputs(
                effects[block_effect_start:],
                memory_resolver=lambda node_id, pointer, reason: self.memory_query(
                    res,
                    node_id,
                    pointer,
                    '0x20',
                    reason,
                    block_loop_context,
                ),
            )
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

    def evaluation_effects(
        self,
        stmt: str | None,
        res: Any,
        nid: int,
        evaluation: dict[str, Any],
        branch_effect_id: str,
        block_loop_context: list[dict[str, Any]],
        context: str = "condition",
    ) -> list[EffectNode]:
        out: list[EffectNode] = []
        previous_output_writes: list[dict[str, Any]] = []
        for step in evaluation.get("steps") or []:
            attrs = {
                **step,
                "cfg_node_id": nid,
                "language": "yul",
                "path_states": self.node_path_states(res, nid),
                "parent_effect": branch_effect_id,
                "evaluation_context": context,
            }
            call = step.get("call")
            vals = [str(v) for v in step.get("raw_args") or []]
            nested_attr = "nested_in_condition" if context == "condition" else "nested_in_value"
            if call == "mload" and len(vals) == 1:
                matching_outputs = [
                    item for item in previous_output_writes
                    if self.same_memory_pointer(vals[0], item.get("output_ptr"))
                ]
                if matching_outputs:
                    attrs["reads_after_call_output"] = matching_outputs
                    attrs["value_from_call_output"] = f"call_output_word({matching_outputs[0].get('call_temp')}, 0)"
                memory_read = self.memory_query(res, nid, vals[0], None, "mload", block_loop_context)
                if matching_outputs:
                    self.override_memory_read_with_call_output(memory_read, matching_outputs[0])
                out.append(self.effect("MemoryRead", [stmt], {
                    "read_from": vals[0],
                    "value": step.get("temp"),
                    "value_versions": {},
                    "cfg_node_id": nid,
                    "path_states": self.node_path_states(res, nid),
                    nested_attr: True,
                    "evaluation_step": dict(attrs),
                    "memory_read": memory_read,
                }))
            elif call == "keccak256" and len(vals) == 2:
                out.append(self.effect("MemoryHash", [stmt], {
                    "op": call,
                    "ptr": vals[0],
                    "size": vals[1],
                    "value": step.get("temp"),
                    "value_versions": {},
                    "ptr_versions": self.reaching_value_versions(res, nid, vals[0]),
                    "size_versions": self.reaching_value_versions(res, nid, vals[1]),
                    "cfg_node_id": nid,
                    "path_states": self.node_path_states(res, nid),
                    nested_attr: True,
                    "evaluation_step": dict(attrs),
                    "memory_read": self.memory_query(res, nid, vals[0], vals[1], "keccak256", block_loop_context),
                }))
            elif call == "sload" and len(vals) == 1:
                inline_hash = self.inline_keccak_hash_effect(stmt, res, nid, vals[0], block_loop_context)
                if inline_hash:
                    out.append(inline_hash)
                slot_versions = self.reaching_value_versions(res, nid, vals[0])
                if inline_hash and inline_hash.attrs.get("inline_slot_key") not in slot_versions:
                    slot_versions = [inline_hash.attrs["inline_slot_key"]] + slot_versions
                out.append(self.effect("StorageRead", [stmt], {
                    "slot": vals[0],
                    "slot_versions": slot_versions,
                    "value": step.get("temp"),
                    "value_versions": {},
                    "cfg_node_id": nid,
                    "path_states": self.node_path_states(res, nid),
                    nested_attr: True,
                    "evaluation_step": dict(attrs),
                }))
            elif call in {"call", "staticcall", "delegatecall", "callcode"}:
                call_attrs = {
                    "op": call,
                    "args": vals,
                    "evaluated_args": step.get("evaluated_args") or vals,
                    "result": step.get("temp"),
                    "cfg_node_id": nid,
                    "path_states": self.node_path_states(res, nid),
                    nested_attr: True,
                    "evaluation_step": dict(attrs),
                }
                self.attach_call_memory(call_attrs, res, nid, vals, call, block_loop_context)
                out.append(self.effect({"call": "Call", "staticcall": "StaticCall", "delegatecall": "DelegateCall", "callcode": "CallCode"}[call], [stmt], call_attrs))
                output_ptr = call_attrs.get("output_ptr")
                output_size = call_attrs.get("output_size")
                if output_ptr is not None:
                    previous_output_writes.append({
                        "call_temp": step.get("temp"),
                        "call": call,
                        "output_ptr": output_ptr,
                        "output_size": output_size,
                        "effect_kind": {"call": "Call", "staticcall": "StaticCall", "delegatecall": "DelegateCall", "callcode": "CallCode"}[call],
                    })
            out.append(self.effect("EvaluationStep", [stmt], attrs))
        return out

    @staticmethod
    def atomize_value(stmt: str | None, value_expr: Any, direct_semantic_call: bool) -> dict[str, Any] | None:
        """Build an atomic view for non-sink RHS expressions.

        Direct semantic sinks such as ``x := sload(slot)`` stay as the original
        single effect so storage/call/log/revert overlays can match their exact
        source pattern. Composite RHS expressions are materialized into the same
        right-to-left Yul evaluation steps used for conditions.
        """
        if direct_semantic_call:
            return None
        call, _args = direct_call(value_expr)
        if not call:
            return None
        atomized = YulEvaluationOrder(stmt).materialize(value_expr, context="value")
        steps = atomized.get("steps") or []
        if not steps:
            return None
        atomized["atomization_model"] = "rhs_atomic_single_operation_steps"
        return atomized

    def is_replayed_branch_node(self, res: Any, nid: int, condition: str) -> bool:
        condition_text = str(condition or "").strip()
        if not condition_text:
            return False
        negated = f"!({condition_text})"
        for state in self.node_path_states(res, nid):
            parts = [part.strip() for part in str(state).split(" && ") if part.strip()]
            if condition_text in parts or negated in parts:
                return True
        return False

    @staticmethod
    def override_memory_read_with_call_output(query: dict[str, Any], call_output: dict[str, Any]) -> None:
        output_size = int_text(call_output.get("output_size") or "")
        complete = output_size is not None and output_size >= 32
        value = f"call_output_word({call_output.get('call_temp')}, 0)"
        query["complete"] = bool(complete)
        query["has_unknown"] = not complete
        query["overridden_by_call_output"] = call_output
        query["words"] = [{
            "offset": 0,
            "offset_expr": "0",
            "address_key": str(call_output.get("output_ptr")),
            "value": value if complete else "unknown",
            "source": "call_output",
            "call_temp": call_output.get("call_temp"),
            "call": call_output.get("call"),
            "output_size": call_output.get("output_size"),
        }]

    @classmethod
    def attach_cross_statement_call_outputs(
        cls,
        effects: list[EffectNode],
        memory_resolver: Any | None = None,
    ) -> None:
        """Connect a later mload to the closest compatible CALL output range.

        Atomic evaluation already handles CALL followed by mload in one Yul
        expression. This closes the same def-use relation across statements in
        the current assembly CFG without replacing the underlying MemorySSA.
        """
        call_kinds = {"Call", "StaticCall", "DelegateCall", "CallCode"}
        calls = [
            effect for effect in effects
            if effect.kind in call_kinds and effect.attrs.get("output_ptr") is not None
        ]
        writes = [effect for effect in effects if effect.kind == "MemoryWrite"]
        for read in (effect for effect in effects if effect.kind == "MemoryRead"):
            query = read.attrs.get("memory_read") or {}
            if query.get("overridden_by_call_output"):
                continue
            read_node = cls.effect_node_id(read)
            if read_node is None:
                continue
            read_ptr = read.attrs.get("read_from")
            if cls.is_returndatasize_pointer(read_ptr, effects, read):
                cls.attach_returndatasize_candidates(
                    read,
                    calls,
                    effects,
                    memory_resolver,
                )
                continue
            compatible = []
            for call in calls:
                call_node = cls.effect_node_id(call)
                if call_node is None or call_node >= read_node:
                    continue
                if not cls.same_memory_pointer(read_ptr, call.attrs.get("output_ptr")):
                    continue
                if not cls.effect_paths_compatible(call, read):
                    continue
                if cls.memory_pointer_rewritten_between(writes, read_ptr, call_node, read_node):
                    continue
                compatible.append(call)
            if not compatible:
                continue
            call = max(compatible, key=lambda item: cls.effect_node_id(item) or -1)
            call_output = {
                "call_temp": call.attrs.get("result") or call.effect_id,
                "call": call.attrs.get("op"),
                "output_ptr": call.attrs.get("output_ptr"),
                "output_size": call.attrs.get("output_size"),
                "effect_kind": call.kind,
                "effect_id": call.effect_id,
                "cfg_node_id": cls.effect_node_id(call),
                "path_states": call.attrs.get("path_states") or ["entry"],
            }
            read.attrs["reads_after_call_output"] = [call_output]
            read.attrs["value_from_call_output"] = f"call_output_word({call_output['call_temp']}, 0)"
            cls.override_memory_read_with_call_output(query, call_output)

    @classmethod
    def attach_returndatasize_candidates(
        cls,
        read: EffectNode,
        calls: list[EffectNode],
        effects: list[EffectNode],
        memory_resolver: Any | None,
    ) -> None:
        read_node = cls.effect_node_id(read)
        if read_node is None:
            return
        effect_order = {effect.effect_id: index for index, effect in enumerate(effects)}
        read_paths = read.attrs.get('path_states') or ['entry']
        selected: dict[str, EffectNode] = {}
        for read_path in read_paths:
            compatible = [
                call for call in calls
                if cls.effect_node_id(call) is not None
                and cls.effect_node_id(call) <= read_node
                and any(
                    cls.path_condition_implies(read_path, producer_path)
                    for producer_path in (call.attrs.get('path_states') or ['entry'])
                )
            ]
            if not compatible:
                continue
            nearest = max(
                compatible,
                key=lambda item: (cls.effect_node_id(item) or -1, effect_order.get(item.effect_id, -1)),
            )
            selected[str(read_path)] = nearest
        candidates: list[dict[str, Any]] = []
        seen: set[tuple[str, str, int]] = set()
        for read_path, call in selected.items():
            target = int_text(str(call.attrs.get('target') or ''))
            fixed_size = cls.FIXED_PRECOMPILE_RETURNDATA.get(target) if target is not None else None
            if fixed_size is None or call.attrs.get('op') != 'staticcall':
                continue
            call_node = cls.effect_node_id(call)
            call_temp = call.attrs.get('result') or call.effect_id
            for size in (fixed_size, 0):
                key = (read_path, call.effect_id, size)
                if key in seen:
                    continue
                seen.add(key)
                pointer = f'0x{size:02x}'
                condition = f'returndatasize_after({call.effect_id}) == {pointer}'
                if read_path != 'entry':
                    condition = f'{read_path} && {condition}'
                resolved_query = None
                value = None
                source = 'memory_ssa'
                output_ptr = int_text(str(call.attrs.get('output_ptr') or ''))
                output_size = int_text(str(call.attrs.get('output_size') or ''))
                if size > 0 and output_ptr == size and output_size is not None and output_size >= 32:
                    value = f'call_output_word({call_temp}, 0)'
                    source = 'call_output'
                    resolved_query = {
                        'query_kind': 'CallOutputMemoryRead',
                        'pointer': pointer,
                        'length': '0x20',
                        'complete': True,
                        'has_unknown': False,
                        'words': [{
                            'offset': 0,
                            'offset_expr': '0',
                            'address_key': pointer,
                            'value': value,
                            'source': source,
                            'call_temp': call_temp,
                            'call': call.attrs.get('op'),
                            'output_size': call.attrs.get('output_size'),
                        }],
                    }
                elif memory_resolver is not None and call_node is not None:
                    resolved_query = memory_resolver(read_node, pointer, 'returndatasize_pointer_candidate')
                    value = cls.single_resolved_memory_value(resolved_query)
                status = 'resolved' if value is not None else 'unresolved'
                candidates.append({
                    'status': status,
                    'condition': condition,
                    'returndata_size': pointer,
                    'pointer': pointer,
                    'value': value if value is not None else 'unknown',
                    'source': source,
                    'memory_read': resolved_query,
                    'call_effect': call.effect_id,
                    'call_temp': call_temp,
                    'call_kind': call.kind,
                    'precompile': PRECOMPILES.get(target),
                    'precompile_address': target,
                    'cfg_path': read_path,
                })
        if not candidates:
            return
        read.attrs['returndatasize_source_calls'] = list(dict.fromkeys(
            candidate['call_effect'] for candidate in candidates
        ))
        read.attrs['returndatasize_pointer_candidates'] = candidates

    @staticmethod
    def single_resolved_memory_value(query: Any) -> str | None:
        if not isinstance(query, dict) or not query.get('complete') or query.get('has_unknown'):
            return None
        words = query.get('words') or []
        if len(words) != 1:
            return None
        value = words[0].get('value')
        if value is None or str(value).strip().lower() == 'unknown':
            return None
        return str(value)

    @classmethod
    def is_returndatasize_pointer(
        cls,
        pointer: Any,
        effects: list[EffectNode],
        read: EffectNode,
        seen: set[str] | None = None,
    ) -> bool:
        text = str(pointer or '').strip()
        name, args = call_parts(text)
        if name == 'returndatasize' and not args:
            return True
        if not cls.simple_identifier(text):
            return False
        seen = set(seen or ())
        if text in seen:
            return False
        seen.add(text)
        read_node = cls.effect_node_id(read)
        definitions = []
        for effect in effects:
            if effect.kind != 'ValueDef' or text not in (effect.attrs.get('targets') or []):
                continue
            node = cls.effect_node_id(effect)
            if read_node is not None and node is not None and node > read_node:
                continue
            if not cls.effect_paths_compatible(effect, read):
                continue
            definitions.append(effect)
        if not definitions:
            return False
        latest = max(definitions, key=lambda item: cls.effect_node_id(item) or -1)
        return cls.is_returndatasize_pointer(
            latest.attrs.get('value'),
            effects,
            read,
            seen,
        )

    @staticmethod
    def effect_node_id(effect: EffectNode) -> int | None:
        try:
            return int(effect.attrs.get("cfg_node_id"))
        except (TypeError, ValueError):
            return None

    @classmethod
    def effect_paths_compatible(cls, producer: EffectNode, consumer: EffectNode) -> bool:
        producer_paths = producer.attrs.get("path_states") or ["entry"]
        consumer_paths = consumer.attrs.get("path_states") or ["entry"]
        return any(
            cls.path_condition_implies(consumer_path, producer_path)
            for producer_path in producer_paths
            for consumer_path in consumer_paths
        )

    @staticmethod
    def path_condition_implies(consumer_path: Any, producer_path: Any) -> bool:
        def atoms(path: Any) -> set[str]:
            return {
                part.strip()
                for part in str(path or "entry").split(" && ")
                if part.strip() and part.strip() != "entry"
            }

        return atoms(producer_path).issubset(atoms(consumer_path))

    @classmethod
    def memory_pointer_rewritten_between(
        cls,
        writes: list[EffectNode],
        pointer: Any,
        start_node: int,
        end_node: int,
    ) -> bool:
        for write in writes:
            node = cls.effect_node_id(write)
            if node is None or not start_node < node < end_node:
                continue
            if cls.same_memory_pointer(pointer, write.attrs.get("address")):
                return True
            for alias in write.attrs.get("aliases") or []:
                if cls.same_memory_pointer(pointer, alias.get("expression")):
                    return True
        return False

    @staticmethod
    def same_memory_pointer(left: Any, right: Any) -> bool:
        return str(left or "").replace(" ", "").lower() == str(right or "").replace(" ", "").lower()

    @classmethod
    def topic_memory_reads(cls, res: Any, nid: int, topics: list[str], block_loop_context: list[dict[str, Any]]) -> list[dict[str, Any]]:
        reads: list[dict[str, Any]] = []
        for topic_index, topic in enumerate(topics):
            for read_expr, ptr in cls.mload_reads_in_expr(topic):
                reads.append({
                    "topic_index": topic_index,
                    "topic": topic,
                    "read_expr": read_expr,
                    "ptr": ptr,
                    "memory_read": cls.memory_query(res, nid, ptr, "0x20", "event_topic_mload", block_loop_context),
                })
        return reads

    @classmethod
    def mload_reads_in_expr(cls, expr: Any) -> list[tuple[str, str]]:
        text = str(expr or "").strip()
        if not text:
            return []
        name, args = call_parts(text)
        out: list[tuple[str, str]] = []
        if name == "mload" and len(args) == 1:
            out.append((text, args[0]))
        for arg in args:
            out.extend(cls.mload_reads_in_expr(arg))
        return out

    @classmethod
    def sload_reads_in_expr(cls, expr: Any) -> list[tuple[str, str]]:
        text = str(expr or "").strip()
        if not text:
            return []
        name, args = call_parts(text)
        out: list[tuple[str, str]] = []
        if name == "sload" and len(args) == 1:
            out.append((text, args[0]))
        for arg in args:
            out.extend(cls.sload_reads_in_expr(arg))
        return out

    def inline_keccak_hash_effect(self, stmt: str | None, res: Any, nid: int, slot: str, block_loop_context: list[Any]) -> EffectNode | None:
        call, args = call_parts(slot)
        if call != "keccak256" or len(args) != 2:
            return None
        slot_key = str(slot)
        inline_slot_key = self.inline_hash_slot_key(slot_key, stmt, res, nid)
        return self.effect("MemoryHash", [stmt], {
            "op": "keccak256",
            "ptr": args[0],
            "size": args[1],
            "value": slot_key,
            "value_versions": {slot_key: [inline_slot_key]},
            "ptr_versions": self.reaching_value_versions(res, nid, args[0]),
            "size_versions": self.reaching_value_versions(res, nid, args[1]),
            "cfg_node_id": nid,
            "path_states": self.node_path_states(res, nid),
            "memory_read": self.memory_query(res, nid, args[0], args[1], "keccak256", block_loop_context),
            "inline_storage_slot": True,
            "inline_slot_key": inline_slot_key,
        })

    @classmethod
    def inline_hash_slot_key(cls, slot_key: str, stmt: str | None, res: Any, nid: int) -> str:
        asm = getattr(res, "assembly_block_id", None)
        scope = f"asm{asm}" if asm is not None else "asm_unknown"
        if stmt:
            scope = f"{scope}_{stmt}"
        return f"{slot_key}__inline_{cls.key_token(scope)}_n{nid}"

    @staticmethod
    def key_token(text: str) -> str:
        return ''.join(ch if ch.isalnum() or ch == '_' else '_' for ch in str(text))


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
            for merged in EffectLifter.merge_function_path_prefixes(getattr(res, "function_path_prefixes", []) or [], text):
                if merged not in states:
                    states.append(merged)
        return states

    @staticmethod
    def merge_function_path_prefixes(prefixes: list[str], local_path: str) -> list[str]:
        clean_prefixes = [str(item).strip() for item in prefixes if str(item or "").strip() and str(item).strip() != "entry"]
        local = str(local_path or "").strip() or "entry"
        if not clean_prefixes:
            return [local]
        out = []
        for prefix in clean_prefixes:
            if local == "entry":
                out.append(prefix)
            elif local.startswith(prefix + " && "):
                out.append(local)
            else:
                out.append(f"{prefix} && {local}")
        return out

    @staticmethod
    def assembly_entry_conditions(control: dict[str, Any]) -> dict[int, list[str]]:
        blocks = {item.get("block_id"): item for item in control.get("blocks", []) if isinstance(item, dict)}
        out: dict[int, list[str]] = {}
        for edge in control.get("edges", []) or []:
            if not isinstance(edge, dict):
                continue
            dst = str(edge.get("to") or "")
            marker = "_n0"
            if not dst.startswith("bb_asm") or not dst.endswith(marker):
                continue
            try:
                assembly_block = int(dst[len("bb_asm"): -len(marker)])
            except Exception:
                continue
            src_block = blocks.get(edge.get("from")) or {}
            terminator = src_block.get("terminator") or {}
            if terminator.get("kind") != "Branch":
                continue
            condition = str(terminator.get("condition") or "").strip()
            if not condition:
                continue
            kind = str(edge.get("kind") or "")
            if kind == "false":
                condition = f"!({condition})"
            elif kind not in {"true", "if_true"}:
                continue
            bucket = out.setdefault(assembly_block, [])
            if condition not in bucket:
                bucket.append(condition)
        return out

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
            input_size = EffectLifter.resolve_literal_value(res, nid, vals[4])
            output_size = EffectLifter.resolve_literal_value(res, nid, vals[6])
            attrs.update({"gas": vals[0], "target": vals[1], "value": vals[2], "input_ptr": vals[3], "input_size": input_size, "input_size_yul": vals[4], "output_ptr": vals[5], "output_size": output_size, "output_size_yul": vals[6]})
            attrs["input_memory"] = EffectLifter.memory_query(res, nid, vals[3], input_size, f"{call}_input", block_loop_context)
            attrs["input_memory_partial"] = EffectLifter.partial_memory_slice(res, nid, vals[3], input_size, f"{call}_input_partial")
            attrs["output_memory_query"] = {"pointer": vals[5], "length": output_size, "role": f"{call}_output_range"}
        elif call in {"staticcall", "delegatecall"} and len(vals) >= 6:
            input_size = EffectLifter.resolve_literal_value(res, nid, vals[3])
            output_size = EffectLifter.resolve_literal_value(res, nid, vals[5])
            attrs.update({"gas": vals[0], "target": vals[1], "input_ptr": vals[2], "input_size": input_size, "input_size_yul": vals[3], "output_ptr": vals[4], "output_size": output_size, "output_size_yul": vals[5]})
            attrs["input_memory"] = EffectLifter.memory_query(res, nid, vals[2], input_size, f"{call}_input", block_loop_context)
            attrs["input_memory_partial"] = EffectLifter.partial_memory_slice(res, nid, vals[2], input_size, f"{call}_input_partial")
            attrs["output_memory_query"] = {"pointer": vals[4], "length": output_size, "role": f"{call}_output_range"}

    @staticmethod
    def resolve_literal_value(res: Any, nid: int, value: str) -> str:
        if int_text(value) is not None:
            return value
        text = str(value or "").strip()
        if not EffectLifter.simple_identifier(text):
            return value
        for state in res.states_at(nid):
            definition = getattr(state, "values", {}).get(text)
            expr = getattr(definition, "expression", None) if definition else None
            rendered = yul_expression(expr) if isinstance(expr, dict) else ""
            if rendered and int_text(rendered) is not None:
                return rendered
        return value

    @staticmethod
    def partial_memory_slice(res: Any, nid: int, pointer: str, length: str | None, reason: str) -> dict[str, Any] | None:
        symbolic_slice = None
        if hasattr(res, "resolve_memory_byte_slice"):
            symbolic_slice = res.resolve_memory_byte_slice(nid, pointer, length, reason)
        start = int_text(pointer)
        size = int_text(length or "")
        if start is None or size is None or size < 0:
            if not symbolic_slice:
                return None
            slices = symbolic_slice.get("slices") or []
            return {
                "query_kind": "PartialMemorySliceResult",
                "reason": reason,
                "pointer": pointer,
                "length": length,
                "size": symbolic_slice.get("size"),
                "complete": symbolic_slice.get("complete"),
                "slices": slices,
                "path_slices": symbolic_slice.get("path_slices"),
                "abi_hint": EffectLifter.abi_hint_from_slices(slices, int(symbolic_slice.get("size") or 0)),
                "byte_slice": symbolic_slice,
            }
        end = start + size
        candidates: list[dict[str, Any]] = []
        for state in res.states_at(nid):
            for definition in getattr(state, "memory", {}).values():
                write_start = int_text(getattr(definition, "address", None))
                if write_start is None:
                    continue
                width = 1 if getattr(definition, "kind", "") == "mstore8" else 32
                write_end = write_start + width
                overlap_start = max(start, write_start)
                overlap_end = min(end, write_end)
                if overlap_start >= overlap_end:
                    continue
                candidates.append({
                    "query_offset": overlap_start - start,
                    "size": overlap_end - overlap_start,
                    "source_address": getattr(definition, "address", None),
                    "source_offset": overlap_start - write_start,
                    "source_width": width,
                    "source_value": getattr(definition, "value", None),
                    "source_version": getattr(definition, "version", None),
                    "source_node_id": getattr(definition, "node_id", None),
                    "source_kind": getattr(definition, "kind", None),
                    "extraction": EffectLifter.partial_extraction(getattr(definition, "value", None), overlap_start - write_start, overlap_end - overlap_start, width),
                })
        merged = EffectLifter.merge_partial_slices(candidates, size)
        return {
            "query_kind": "PartialMemorySliceResult",
            "reason": reason,
            "pointer": pointer,
            "length": length,
            "start": start,
            "size": size,
            "complete": EffectLifter.slice_complete(merged, size),
            "slices": merged,
            "abi_hint": EffectLifter.abi_hint_from_slices(merged, size),
        }

    @staticmethod
    def merge_partial_slices(candidates: list[dict[str, Any]], size: int) -> list[dict[str, Any]]:
        by_key: dict[tuple[int, int, str], dict[str, Any]] = {}
        for item in candidates:
            key = (int(item["query_offset"]), int(item["size"]), str(item.get("source_version")))
            by_key.setdefault(key, item)
        return sorted(by_key.values(), key=lambda item: (int(item["query_offset"]), int(item["size"])))

    @staticmethod
    def slice_complete(slices: list[dict[str, Any]], size: int) -> bool:
        cursor = 0
        for item in sorted(slices, key=lambda it: int(it["query_offset"])):
            offset = int(item["query_offset"])
            width = int(item["size"])
            if offset > cursor:
                return False
            cursor = max(cursor, offset + width)
            if cursor >= size:
                return True
        return size == 0

    @staticmethod
    def partial_extraction(value: Any, source_offset: int, size: int, source_width: int) -> str:
        text = str(value or "").strip()
        if source_width == 32 and source_offset == 0 and size == 32:
            return text
        if source_width == 32 and source_offset + size == 32:
            return f"low_bytes({text}, {size})"
        if source_width == 32 and source_offset == 0:
            return f"high_bytes({text}, {size})"
        return f"bytes({text}, offset={source_offset}, size={size})"

    @staticmethod
    def abi_hint_from_slices(slices: list[dict[str, Any]], size: int) -> dict[str, Any] | None:
        if size == 4 and len(slices) == 1 and slices[0].get("size") == 4:
            return {"kind": "selector", "selector": slices[0].get("extraction"), "source_value": slices[0].get("source_value")}
        if size >= 4 and slices:
            first = slices[0]
            rest = slices[1:]
            if first.get("query_offset") == 0 and first.get("size") == 4:
                return {
                    "kind": "abi_call_data",
                    "selector": first.get("extraction"),
                    "selector_source_value": first.get("source_value"),
                    "arguments": [item.get("extraction") for item in rest if item.get("query_offset", 0) >= 4],
                }
        return None


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
