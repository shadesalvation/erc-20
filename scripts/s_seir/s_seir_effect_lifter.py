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
from s_seir_memory_ssa import (
    byte_slice_complete,
    coalesce_byte_cells,
    resolve_memory_read_with_loops,
    slice_signature,
    words_from_byte_slice,
)
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
        self.non_storage_state_names: set[str] = set()

    def effect(self, k, refs, attrs):
        return EffectNode(self.ids.new("eff"), k, [r for r in refs if r], attrs)

    def lift(
        self,
        unit: FunctionUnit,
        memory_results: dict[int, Any],
        control: dict[str, Any] | None = None,
        *,
        include_solidity: bool = False,
    ):
        effects = []
        facts = []
        self.non_storage_state_names = set((getattr(unit, "constant_values", {}) or {}).keys())
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
                "solidity_yul_boundary": getattr(res, "boundary_context", {}) or {},
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
                    # The assignment target and the CALL-family effect are
                    # one Yul AST operation.  Preserve that structural link
                    # on the completed call effect so the semantic overlay
                    # owns the call-status result instead of making a second
                    # downstream call-shaped ValueDef necessary.
                    if names:
                        attrs["result"] = names[0]
                        attrs["result_versions"] = self.created_value_versions(res, nid, names)
                    if value_effect is not None:
                        attrs["result_value_effect"] = value_effect.effect_id
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
        if include_solidity:
            solidity_stmt_text = {
                stmt.stmt_id: stmt.text
                for stmt in unit.source_statements
                if stmt.lang == "solidity"
            }
            solidity_effects, represented_solidity_refs = self.lift_solidity_control(control or {}, solidity_stmt_text)
            effects.extend(solidity_effects)
            for stmt in unit.source_statements:
                if stmt.lang == "solidity" and stmt.stmt_id not in represented_solidity_refs:
                    t = stmt.text.strip()
                    if t.startswith("return"):
                        effects.append(self.effect("Return", [stmt.stmt_id], {"text": t, "language": "solidity", "source": "ast_text_fallback"}))
                    elif t.startswith("revert"):
                        effects.append(self.effect("Revert", [stmt.stmt_id], {"payload": t.removeprefix("revert").strip(), "language": "solidity", "source": "ast_text_fallback"}))
                    elif t.startswith("if"):
                        effects.append(self.effect("Branch", [stmt.stmt_id], {"condition": t, "language": "solidity", "source": "ast_text_fallback"}))
        return self.dedupe_effects(effects), facts

    def lift_solidity_control(
        self,
        control: dict[str, Any],
        stmt_text: dict[str, str] | None = None,
    ) -> tuple[list[EffectNode], set[str]]:
        """Lift SlithIR-SSA archived on Solidity CFG blocks into shared S-SEIR effects."""
        stmt_text = stmt_text or {}
        blocks = [block for block in control.get("blocks", []) if block.get("kind") == "solidity"]
        if not blocks:
            return [], set()
        paths = self.solidity_block_path_states(control)
        reference_defs: dict[str, dict[str, Any]] = {}
        value_defs: dict[str, str] = {}
        effects: list[EffectNode] = []
        represented: set[str] = set()

        for block in blocks:
            attrs = block.get("attrs") or {}
            operations = attrs.get("solidity_atomic_ops") or attrs.get("slithir_ssa") or attrs.get("slithir") or []
            refs = list(block.get("stmts") or [])
            if operations:
                represented.update(refs)
            block_id = str(block.get("block_id") or "")
            path_states = paths.get(block_id) or ["entry"]
            node_id = attrs.get("slither_node_id")
            for operation in operations:
                kind = str(operation.get("kind") or "")
                lvalue = operation.get("lvalue")
                lvalue_key = self.slithir_value_key(lvalue)
                common = {
                    "cfg_block_id": block_id,
                    "cfg_node_id": node_id,
                    "language": "solidity",
                    "source": "slithir_ssa" if operation.get("ssa") else "slithir",
                    "path_states": path_states,
                    "slithir": operation,
                }
                if operation.get("atom_id"):
                    common.update({
                        "atomic_operation_id": operation.get("atom_id"),
                        "atomic_kind": operation.get("atomic_kind"),
                        "atomic_sequence": operation.get("sequence"),
                    })

                if kind == "Index":
                    left = self.resolve_slithir_value(operation.get("variable_left"), value_defs, reference_defs)
                    right = self.resolve_slithir_value(operation.get("variable_right"), value_defs, reference_defs)
                    parent = self.reference_info(operation.get("variable_left"), reference_defs)
                    state_variable = parent.get("state_variable") if parent else self.state_variable_name(operation.get("variable_left"))
                    keys = list(parent.get("keys") or []) if parent else []
                    keys.append(right)
                    access = f"{left}[{right}]"
                    info = {
                        "access": access,
                        "state_variable": state_variable,
                        "keys": keys,
                        "reference_kind": "index",
                        "type": (lvalue or {}).get("type"),
                    }
                    if lvalue_key:
                        reference_defs[lvalue_key] = info
                        value_defs[lvalue_key] = access
                    effects.append(self.effect("ValueDef", refs, {
                        **common,
                        "targets": [self.slithir_source_name(lvalue)] if lvalue else [],
                        "target_versions": self.slithir_target_versions(lvalue),
                        "value": access,
                        "atomic_operation": "Index",
                        "reference_definition": info,
                    }))
                    continue

                if kind == "Member":
                    left = self.resolve_slithir_value(operation.get("variable_left"), value_defs, reference_defs)
                    member = self.slithir_source_name(operation.get("variable_right"))
                    parent = self.reference_info(operation.get("variable_left"), reference_defs)
                    state_variable = parent.get("state_variable") if parent else self.state_variable_name(operation.get("variable_left"))
                    access = f"{left}.{member}"
                    info = {
                        "access": access,
                        "state_variable": state_variable,
                        "keys": list(parent.get("keys") or []) if parent else [],
                        "member": member,
                        "reference_kind": "member",
                        "type": (lvalue or {}).get("type"),
                    }
                    if lvalue_key:
                        reference_defs[lvalue_key] = info
                        value_defs[lvalue_key] = access
                    effects.append(self.effect("ValueDef", refs, {
                        **common,
                        "targets": [self.slithir_source_name(lvalue)] if lvalue else [],
                        "target_versions": self.slithir_target_versions(lvalue),
                        "value": access,
                        "atomic_operation": "Member",
                        "reference_definition": info,
                    }))
                    continue

                if kind == "Assignment":
                    rvalue = operation.get("rvalue") or self.first_slithir_value(operation.get("read"))
                    value = self.resolve_slithir_value(rvalue, value_defs, reference_defs)
                    target_ref = self.reference_info(lvalue, reference_defs)
                    if (target_ref and self.is_storage_reference_info(target_ref)) or self.is_storage_state(lvalue):
                        info = target_ref or {
                            "access": self.slithir_source_name(lvalue),
                            "state_variable": self.state_variable_name(lvalue),
                            "keys": [],
                            "reference_kind": "state_variable",
                            "type": (lvalue or {}).get("type"),
                        }
                        effects.extend(self.solidity_storage_reads(operation, refs, common, value_defs, reference_defs, exclude={lvalue_key}))
                        effects.append(self.effect("StorageWrite", refs, {
                            **common,
                            "typed_access": True,
                            **info,
                            "value": value,
                            "value_ssa": self.slithir_value_key(rvalue),
                        }))
                    else:
                        effects.extend(self.solidity_storage_reads(operation, refs, common, value_defs, reference_defs))
                        effects.append(self.effect("ValueDef", refs, {
                            **common,
                            "targets": [self.slithir_source_name(lvalue)] if lvalue else [],
                            "target_versions": self.slithir_target_versions(lvalue),
                            "value": value,
                            "value_ssa": self.slithir_value_key(rvalue),
                            "atomic_operation": "Assignment",
                        }))
                    if lvalue_key:
                        value_defs[lvalue_key] = value
                    continue

                if kind in {"Binary", "Unary", "TypeConversion", "Length", "Unpack", "InitArray", "NewArray", "NewStructure", "NewElementaryType", "NewContract"}:
                    effects.extend(self.solidity_storage_reads(operation, refs, common, value_defs, reference_defs))
                    value = self.slithir_operation_expression(operation, value_defs, reference_defs)
                    if lvalue_key:
                        value_defs[lvalue_key] = value
                    effects.append(self.effect("ValueDef", refs, {
                        **common,
                        "targets": [self.slithir_source_name(lvalue)] if lvalue else [],
                        "target_versions": self.slithir_target_versions(lvalue),
                        "value": value,
                        "atomic_operation": kind,
                    }))
                    continue

                if kind in {"Phi", "PhiCallback"}:
                    inputs = [self.resolve_slithir_value(v, value_defs, reference_defs) for v in operation.get("read") or []]
                    source_inputs = [self.slithir_source_name(v) for v in operation.get("read") or []]
                    unique_sources = list(dict.fromkeys(item for item in source_inputs if item))
                    value = unique_sources[0] if len(unique_sources) == 1 else f"phi({', '.join(inputs)})"
                    if lvalue_key:
                        value_defs[lvalue_key] = value
                    effects.append(self.effect("ValueDef", refs, {
                        **common,
                        "targets": [self.slithir_source_name(lvalue)] if lvalue else [],
                        "target_versions": self.slithir_target_versions(lvalue),
                        "value": value,
                        "atomic_operation": "Phi",
                        "phi_inputs": inputs,
                    }))
                    continue

                if kind == "Condition":
                    effects.extend(self.solidity_storage_reads(operation, refs, common, value_defs, reference_defs))
                    condition_value = self.first_slithir_value(operation.get("read"))
                    effects.append(self.effect("Branch", refs, {
                        **common,
                        "condition": self.resolve_slithir_value(condition_value, value_defs, reference_defs),
                        "condition_ssa": self.slithir_value_key(condition_value),
                    }))
                    continue

                if kind == "Return":
                    effects.extend(self.solidity_storage_reads(operation, refs, common, value_defs, reference_defs))
                    values = [self.resolve_slithir_value(v, value_defs, reference_defs) for v in operation.get("values") or []]
                    effects.append(self.effect("Return", refs, {
                        **common,
                        "values": values,
                        "value": values[0] if len(values) == 1 else values,
                        "value_versions": [self.slithir_value_key(v) for v in operation.get("values") or []],
                    }))
                    continue

                if kind == "EventCall":
                    effects.extend(self.solidity_storage_reads(operation, refs, common, value_defs, reference_defs))
                    arguments = [self.resolve_slithir_value(v, value_defs, reference_defs) for v in operation.get("arguments") or []]
                    effects.append(self.effect("EventLog", refs, {
                        **common,
                        "source_event": True,
                        "event_name": operation.get("name"),
                        "arguments": arguments,
                        "argument_versions": [self.slithir_value_key(v) for v in operation.get("arguments") or []],
                    }))
                    continue

                if kind in {"InternalCall", "InternalDynamicCall", "HighLevelCall", "LibraryCall", "LowLevelCall", "Send", "Transfer"}:
                    effects.extend(self.solidity_storage_reads(operation, refs, common, value_defs, reference_defs))
                    call_kind = self.solidity_call_effect_kind(kind)
                    arguments = [self.resolve_slithir_value(v, value_defs, reference_defs) for v in operation.get("arguments") or []]
                    destination = self.resolve_slithir_value(operation.get("destination"), value_defs, reference_defs)
                    function = operation.get("function") or {}
                    function_name = function.get("name") or operation.get("function_name") or function.get("full_name")
                    invocation = f"{destination + '.' if destination else ''}{function_name}({', '.join(arguments)})"
                    call_attrs = {
                        **common,
                        "typed_call": True,
                        "call_kind": kind,
                        "target": destination or function.get("contract"),
                        "function": function_name,
                        "function_signature": function.get("full_name"),
                        "canonical_function": function.get("canonical_name"),
                        "arguments": arguments,
                        "result": self.slithir_source_name(lvalue) if lvalue else None,
                        "result_version": lvalue_key,
                        "value": self.resolve_slithir_value(operation.get("call_value"), value_defs, reference_defs),
                        "gas": self.resolve_slithir_value(operation.get("call_gas"), value_defs, reference_defs),
                    }
                    effects.append(self.effect(call_kind, refs, self.clean_dict(call_attrs)))
                    if lvalue_key:
                        value_defs[lvalue_key] = invocation
                    continue

                if kind == "SolidityCall":
                    effects.extend(self.solidity_storage_reads(operation, refs, common, value_defs, reference_defs))
                    function = operation.get("function") or {}
                    function_name = str(function.get("full_name") or function.get("name") or "")
                    arguments = [self.resolve_slithir_value(v, value_defs, reference_defs) for v in operation.get("arguments") or []]
                    if function_name.startswith("require(") or function_name.startswith("assert("):
                        effects.append(self.effect("Require", refs, {
                            **common,
                            "condition": arguments[0] if arguments else None,
                            "arguments": arguments,
                            "builtin": function_name.split("(", 1)[0],
                        }))
                    elif function_name.startswith("revert("):
                        effects.append(self.effect("Revert", refs, {
                            **common,
                            "payload": arguments,
                            "arguments": arguments,
                            "source_revert": True,
                        }))
                    else:
                        value = self.solidity_call_expression(operation, function_name, arguments, refs, stmt_text)
                        if lvalue_key:
                            value_defs[lvalue_key] = value
                        effects.append(self.effect("ValueDef", refs, {
                            **common,
                            "targets": [self.slithir_source_name(lvalue)] if lvalue else [],
                            "target_versions": self.slithir_target_versions(lvalue),
                            "value": value,
                            "atomic_operation": "SolidityCall",
                            "source_expression": operation.get("source_expression"),
                        }))
                    continue

                if kind == "Delete":
                    target = operation.get("variable") or lvalue
                    info = self.reference_info(target, reference_defs)
                    if (info and self.is_storage_reference_info(info)) or self.is_storage_state(target):
                        info = info or {
                            "access": self.slithir_source_name(target),
                            "state_variable": self.state_variable_name(target),
                            "keys": [],
                            "reference_kind": "state_variable",
                            "type": (target or {}).get("type"),
                        }
                        effects.append(self.effect("StorageWrite", refs, {**common, "typed_access": True, **info, "value": "0", "delete": True}))
        return effects, represented

    def solidity_storage_reads(
        self,
        operation: dict[str, Any],
        refs: list[str],
        common: dict[str, Any],
        value_defs: dict[str, str],
        reference_defs: dict[str, dict[str, Any]],
        exclude: set[str | None] | None = None,
    ) -> list[EffectNode]:
        out: list[EffectNode] = []
        seen: set[str] = set()
        exclude = exclude or set()
        values: list[dict[str, Any]] = []
        for key in ("rvalue", "variable", "variable_left", "variable_right", "destination", "call_value", "call_gas"):
            value = operation.get(key)
            if isinstance(value, dict):
                values.append(value)
        values.extend(v for v in operation.get("arguments") or [] if isinstance(v, dict))
        values.extend(v for v in operation.get("values") or [] if isinstance(v, dict))
        values.extend(v for v in operation.get("read") or [] if isinstance(v, dict))
        for value in values:
            key = self.slithir_value_key(value)
            if key in exclude or key in seen:
                continue
            info = self.reference_info(value, reference_defs)
            if not info and self.is_storage_state(value):
                info = {
                    "access": self.slithir_source_name(value),
                    "state_variable": self.state_variable_name(value),
                    "keys": [],
                    "reference_kind": "state_variable",
                    "type": value.get("type"),
                }
            if not info:
                continue
            if not self.is_storage_reference_info(info):
                continue
            seen.add(key)
            out.append(self.effect("StorageRead", refs, {
                **common,
                "typed_access": True,
                **info,
                "value": None,
                "access_version": key,
            }))
        return out

    @staticmethod
    def solidity_call_effect_kind(kind: str) -> str:
        if kind in {"InternalCall", "InternalDynamicCall"}:
            return "InternalCall"
        if kind == "LibraryCall":
            return "LibraryCall"
        if kind == "LowLevelCall":
            return "ExternalCall"
        if kind in {"Send", "Transfer"}:
            return "ExternalCall"
        return "ExternalCall"

    @staticmethod
    def slithir_target_versions(value: dict[str, Any] | None) -> dict[str, list[str]]:
        if not value:
            return {}
        name = EffectLifter.slithir_source_name(value)
        key = EffectLifter.slithir_value_key(value)
        return {name: [key]} if name and key else {}

    @staticmethod
    def first_slithir_value(values: Any) -> dict[str, Any] | None:
        return values[0] if isinstance(values, list) and values and isinstance(values[0], dict) else None

    @staticmethod
    def slithir_value_key(value: dict[str, Any] | None) -> str:
        return str((value or {}).get("text") or "")

    @staticmethod
    def slithir_source_name(value: dict[str, Any] | None) -> str:
        if not value:
            return ""
        if value.get("is_constant") or value.get("is_solidity_builtin"):
            text = str(value.get("text") or "")
            return {"True": "true", "False": "false"}.get(text, text)
        return str(value.get("base_name") or value.get("name") or value.get("text") or "")

    @staticmethod
    def is_slithir_state(value: dict[str, Any] | None) -> bool:
        return bool(value and value.get("is_state"))

    def is_storage_state(self, value: dict[str, Any] | None) -> bool:
        """Slither marks contract constants as state variables as well.

        S-SEIR keeps constants in the source/type environment, but they are not
        runtime storage reads or writes.
        """
        return self.is_slithir_state(value) and self.slithir_source_name(value) not in self.non_storage_state_names

    @staticmethod
    def is_storage_reference_info(info: dict[str, Any] | None) -> bool:
        """Return true only for typed references rooted in contract storage.

        SlithIR Index/Member operations also describe calldata, memory, enum and
        struct-field references. S-SEIR keeps those ValueDef references for
        expression recovery, but they must not be lifted as StorageRead/Write
        unless the reference chain is rooted in a real state variable.
        """
        if not info:
            return False
        return bool(info.get("state_variable")) or info.get("reference_kind") == "state_variable"

    @staticmethod
    def state_variable_name(value: dict[str, Any] | None) -> str | None:
        return EffectLifter.slithir_source_name(value) if EffectLifter.is_slithir_state(value) else None

    @staticmethod
    def reference_info(value: dict[str, Any] | None, reference_defs: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
        key = EffectLifter.slithir_value_key(value)
        return reference_defs.get(key)

    @classmethod
    def resolve_slithir_value(
        cls,
        value: dict[str, Any] | None,
        value_defs: dict[str, str],
        reference_defs: dict[str, dict[str, Any]],
    ) -> str:
        if not value:
            return ""
        key = cls.slithir_value_key(value)
        if key in reference_defs:
            return str(reference_defs[key].get("access") or key)
        if key in value_defs and str(value.get("kind") or "").startswith("Temporary"):
            return value_defs[key]
        return cls.slithir_source_name(value)

    @classmethod
    def slithir_operation_expression(
        cls,
        operation: dict[str, Any],
        value_defs: dict[str, str],
        reference_defs: dict[str, dict[str, Any]],
    ) -> str:
        kind = str(operation.get("kind") or "")
        if kind == "Binary":
            left = cls.resolve_slithir_value(operation.get("variable_left"), value_defs, reference_defs)
            right = cls.resolve_slithir_value(operation.get("variable_right"), value_defs, reference_defs)
            op = cls.slithir_operator(operation.get("operator"))
            return f"({left} {op} {right})"
        if kind == "Unary":
            value = cls.resolve_slithir_value(operation.get("variable"), value_defs, reference_defs)
            op = cls.slithir_unary_operator(operation.get("operator"))
            return f"({op}{value})"
        if kind == "TypeConversion":
            value = cls.resolve_slithir_value(operation.get("variable"), value_defs, reference_defs)
            target_type = (operation.get("lvalue") or {}).get("type") or "unknown"
            return f"{target_type}({value})"
        if kind == "Length":
            value = cls.resolve_slithir_value(operation.get("variable"), value_defs, reference_defs)
            return f"{value}.length"
        reads = [cls.resolve_slithir_value(v, value_defs, reference_defs) for v in operation.get("read") or []]
        if len(reads) == 1:
            return reads[0]
        source = operation.get("source_expression")
        return str(source or operation.get("text") or kind)

    @classmethod
    def solidity_call_expression(
        cls,
        operation: dict[str, Any],
        function_name: str,
        arguments: list[str],
        refs: list[str] | None = None,
        stmt_text: dict[str, str] | None = None,
    ) -> str:
        callee = cls.pretty_solidity_call_name(function_name)
        source_from_stmt = cls.source_call_expression(callee, refs or [], stmt_text or {})
        if source_from_stmt:
            return source_from_stmt
        source = str(operation.get("source_expression") or "").strip()
        if source:
            return source
        pretty_args = [
            cls.pretty_solidity_call_argument(callee, index, argument)
            for index, argument in enumerate(arguments)
        ]
        return f"{callee}({', '.join(pretty_args)})"

    @classmethod
    def source_call_expression(cls, callee: str, refs: list[str], stmt_text: dict[str, str]) -> str | None:
        if not callee.startswith("abi."):
            return None
        for ref in refs:
            text = str(stmt_text.get(str(ref)) or "")
            expr = cls.extract_balanced_call(text, callee)
            if expr:
                return expr
        return None

    @staticmethod
    def extract_balanced_call(text: str, callee: str) -> str | None:
        start = text.find(f"{callee}(")
        if start < 0:
            return None
        depth = 0
        quote: str | None = None
        escaped = False
        for index in range(start + len(callee), len(text)):
            char = text[index]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
                continue
            if char in {'"', "'"}:
                quote = char
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return text[start:index + 1].strip()
        return None

    @staticmethod
    def pretty_solidity_call_name(function_name: str) -> str:
        name = str(function_name or "").strip()
        if name.endswith("()"):
            name = name[:-2]
        return name

    @staticmethod
    def pretty_solidity_call_argument(callee: str, index: int, argument: str) -> str:
        text = str(argument or "").strip()
        if callee == "abi.encodeWithSignature" and index == 0:
            if not (text.startswith('"') or text.startswith("'")):
                return f'"{text}"'
        return text

    @staticmethod
    def slithir_operator(operator: Any) -> str:
        text = str(operator or "")
        mapping = {
            "ADDITION": "+", "SUBTRACTION": "-", "MULTIPLICATION": "*", "DIVISION": "/", "MODULO": "%",
            "LESS": "<", "GREATER": ">", "LESS_EQUAL": "<=", "GREATER_EQUAL": ">=", "EQUAL": "==", "NOT_EQUAL": "!=",
            "AND": "&&", "OR": "||", "CARET": "^", "LEFT_SHIFT": "<<", "RIGHT_SHIFT": ">>",
            "POWER": "**", "ANDAND": "&&", "OROR": "||",
        }
        return mapping.get(text.split(".")[-1], text if text in {"+", "-", "*", "/", "%", "<", ">", "<=", ">=", "==", "!=", "&&", "||", "&", "|", "^", "<<", ">>", "**"} else text.split(".")[-1].lower())

    @staticmethod
    def slithir_unary_operator(operator: Any) -> str:
        text = str(operator or "").split(".")[-1]
        return {"BANG": "!", "TILD": "~", "MINUS_PRE": "-", "PLUS_PRE": "+"}.get(text, text.lower())

    @staticmethod
    def clean_dict(value: dict[str, Any]) -> dict[str, Any]:
        return {key: item for key, item in value.items() if item is not None and item != ""}

    @staticmethod
    def solidity_block_path_states(control: dict[str, Any], max_states: int = 24) -> dict[str, list[str]]:
        """Enumerate actual entry-to-block predicates on the unified CFG.

        A control-dependency closure describes nesting, not path alternatives.
        Treating it as a conjunction loses paths such as ``!A`` versus
        ``A && !B`` after an early return.  The unified Slither+Yul CFG already
        has the required branch and terminal edges, so propagate predicates on
        those edges directly.
        """
        blocks = {str(block.get("block_id")): block for block in control.get("blocks", []) if isinstance(block, dict)}
        incoming: dict[str, int] = {block_id: 0 for block_id in blocks}
        outgoing: dict[str, list[dict[str, Any]]] = {block_id: [] for block_id in blocks}
        for edge in control.get("edges", []) or []:
            src, dst = str(edge.get("from") or ""), str(edge.get("to") or "")
            if src not in blocks or dst not in blocks:
                continue
            outgoing[src].append(edge)
            incoming[dst] += 1
        roots = [block_id for block_id, count in incoming.items() if count == 0]
        if not roots and blocks:
            roots = [next(iter(blocks))]
        states: dict[str, list[tuple[str, ...]]] = {block_id: [] for block_id in blocks}
        queue: list[tuple[str, tuple[str, ...]]] = [(root, ()) for root in roots]
        seen: set[tuple[str, tuple[str, ...]]] = set()
        while queue:
            block_id, predicates = queue.pop(0)
            marker = (block_id, predicates)
            if marker in seen or len(states[block_id]) >= max_states:
                continue
            seen.add(marker)
            states[block_id].append(predicates)
            terminator = blocks[block_id].get("terminator") or {}
            if terminator.get("kind") in {"Return", "Revert", "Stop"}:
                continue
            condition = str(terminator.get("condition") or "").strip()
            for edge in outgoing.get(block_id, []):
                edge_kind = str(edge.get("kind") or "")
                next_predicates = predicates
                true_edge = edge_kind in {"true", "if_true"} or edge_kind.startswith("true:")
                false_edge = edge_kind in {"false", "if_false"} or edge_kind.startswith("false:")
                predicate = condition if true_edge else (f"!({condition})" if false_edge and condition else "")
                if predicate and predicate not in next_predicates:
                    if EffectLifter.path_has_opposite(next_predicates, predicate):
                        continue
                    next_predicates = (*next_predicates, predicate)
                queue.append((str(edge.get("to")), next_predicates))
        return {
            block_id: [" && ".join(predicates) if predicates else "entry" for predicates in candidates]
            for block_id, candidates in states.items()
        }

    @staticmethod
    def path_has_opposite(predicates: tuple[str, ...], predicate: str) -> bool:
        predicate = str(predicate).strip()
        if predicate.startswith("!(") and predicate.endswith(")"):
            opposite = predicate[2:-1].strip()
        else:
            opposite = f"!({predicate})"
        return opposite in predicates

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
        cls.attach_call_output_ranges(effects, calls, writes)

    @classmethod
    def attach_call_output_ranges(
        cls,
        effects: list[EffectNode],
        calls: list[EffectNode],
        writes: list[EffectNode],
    ) -> None:
        """Expand later MemorySSA sink queries with exact CALL-output paths.

        CALL-family instructions conditionally overwrite memory.  A guard that
        reads ``mload(returndatasize())`` does not prove that the output range
        was written: the zero-returndata path reads another address and keeps
        the pre-call output memory intact.  Preserve both states and let the
        sink resolver consume them independently.
        """
        query_attrs = {
            "MemoryHash": ("memory_read", "ptr", "size"),
            "EventLog": ("data_memory", "data_ptr", "data_size"),
            "Return": ("payload_memory", "payload_ptr", "payload_size"),
            "Revert": ("payload_memory", "payload_ptr", "payload_size"),
            "Call": ("input_memory", "input_ptr", "input_size"),
            "StaticCall": ("input_memory", "input_ptr", "input_size"),
            "DelegateCall": ("input_memory", "input_ptr", "input_size"),
            "CallCode": ("input_memory", "input_ptr", "input_size"),
        }
        order = {effect.effect_id: index for index, effect in enumerate(effects)}
        for consumer in effects:
            spec = query_attrs.get(consumer.kind)
            if not spec:
                continue
            memory_attr, ptr_attr, size_attr = spec
            query = consumer.attrs.get(memory_attr)
            byte_slice = (query or {}).get("byte_slice") if isinstance(query, dict) else None
            if not isinstance(byte_slice, dict):
                continue
            query_ptr = int_text(str(consumer.attrs.get(ptr_attr) or ""))
            query_size = int_text(str(consumer.attrs.get(size_attr) or ""))
            consumer_node = cls.effect_node_id(consumer)
            if query_ptr is None or query_size is None or consumer_node is None:
                continue
            path_items = byte_slice.get("path_slices") or []
            expanded_path_items: list[dict[str, Any]] = []
            changed = False
            for item in path_items:
                path = str(item.get("path") or "entry")
                candidates = []
                for call in calls:
                    call_node = cls.effect_node_id(call)
                    output_ptr = int_text(str(call.attrs.get("output_ptr") or ""))
                    output_size = int_text(str(call.attrs.get("output_size") or ""))
                    if call_node is None or call_node >= consumer_node or output_ptr is None or not output_size:
                        continue
                    if not cls.ranges_overlap(query_ptr, query_size, output_ptr, output_size):
                        continue
                    if not any(cls.path_condition_implies(path, producer_path) for producer_path in call.attrs.get("path_states") or ["entry"]):
                        continue
                    if cls.memory_range_rewritten_between(writes, output_ptr, output_size, call_node, consumer_node):
                        continue
                    guarded_value = cls.guarded_call_output_value(path, call)
                    if guarded_value is None:
                        continue
                    candidates.append((call, guarded_value))
                if not candidates:
                    expanded_path_items.append(item)
                    continue
                call, value = max(candidates, key=lambda pair: (cls.effect_node_id(pair[0]) or -1, order.get(pair[0].effect_id, -1)))
                fixed_size = cls.fixed_call_returndata_size(call)
                if fixed_size is None:
                    expanded_path_items.append(item)
                    continue
                returned = {
                    **item,
                    "path": cls.append_path_condition(path, f"returndatasize_after({call.effect_id}) == 0x{fixed_size:02x}"),
                    "slices": cls.overlay_call_output_slices(
                        item.get("slices") or [],
                        query_ptr,
                        query_size,
                        int_text(str(call.attrs.get("output_ptr"))) or 0,
                        int_text(str(call.attrs.get("output_size"))) or 0,
                        value,
                        call,
                    ),
                    "call_output_effect": call.effect_id,
                    "call_memory_outcome": "returndata_copied",
                }
                returned["complete"] = byte_slice_complete(returned["slices"], query_size)
                preserved = {
                    **item,
                    "path": cls.append_path_condition(path, f"returndatasize_after({call.effect_id}) == 0x00"),
                    "slices": [dict(part) for part in item.get("slices") or []],
                    "call_output_effect": call.effect_id,
                    "call_memory_outcome": "no_returndata_preserve_pre_call_memory",
                }
                expanded_path_items.extend([returned, preserved])
                changed = True
            if not changed:
                continue
            byte_slice["path_slices"] = expanded_path_items
            byte_slice["complete"] = bool(expanded_path_items) and all(item.get("complete") for item in expanded_path_items)
            byte_slice["call_output_path_expansion"] = True
            signatures = {slice_signature(item.get("slices") or []) for item in expanded_path_items}
            byte_slice["slices"] = expanded_path_items[0].get("slices") or [] if len(signatures) == 1 else []
            byte_slice["packed_semantics"] = [str(item.get("extraction")) for item in byte_slice.get("slices") or []]
            words = words_from_byte_slice(byte_slice)
            if words:
                query["words"] = words
            query["complete"] = byte_slice["complete"]
            query["has_unknown"] = not byte_slice["complete"]

    @staticmethod
    def ranges_overlap(left: int, left_size: int, right: int, right_size: int) -> bool:
        return max(left, right) < min(left + left_size, right + right_size)

    @classmethod
    def memory_range_rewritten_between(
        cls,
        writes: list[EffectNode],
        pointer: int,
        size: int,
        start_node: int,
        end_node: int,
    ) -> bool:
        for write in writes:
            node = cls.effect_node_id(write)
            address = int_text(str(write.attrs.get("address") or ""))
            if node is None or address is None or not start_node < node < end_node:
                continue
            width = 1 if write.attrs.get("write_kind") == "mstore8" else 32
            if cls.ranges_overlap(pointer, size, address, width):
                return True
        return False

    @classmethod
    def guarded_call_output_value(cls, path: str, call: EffectNode) -> str | None:
        for atom in cls.path_atoms(path):
            value = cls.equality_value_for_call_output(atom, call, expected=True)
            if value is not None:
                return value
        return None

    @staticmethod
    def path_atoms(path: Any) -> list[str]:
        return [part.strip() for part in str(path or "entry").split(" && ") if part.strip() and part.strip() != "entry"]

    @classmethod
    def equality_value_for_call_output(cls, expression: str, call: EffectNode, expected: bool) -> str | None:
        text = str(expression or "").strip()
        if text.startswith("!(") and text.endswith(")"):
            return cls.equality_value_for_call_output(text[2:-1], call, not expected)
        name, args = call_parts(text)
        if name == "iszero" and len(args) == 1:
            return cls.equality_value_for_call_output(args[0], call, not expected)
        if name != "eq" or len(args) != 2 or not expected:
            return None
        left, right = map(str, args)
        if cls.is_call_output_load(left, call):
            return right
        if cls.is_call_output_load(right, call):
            return left
        return None

    @classmethod
    def is_call_output_load(cls, expression: str, call: EffectNode) -> bool:
        name, args = call_parts(str(expression or "").strip())
        if name != "mload" or len(args) != 1:
            return False
        pointer = str(args[0]).strip()
        if not cls.is_returndata_size_expression(pointer):
            return False
        fixed_size = cls.fixed_call_returndata_size(call)
        return (
            fixed_size is not None
            and int_text(str(call.attrs.get("output_ptr") or "")) == fixed_size
            and (int_text(str(call.attrs.get("output_size") or "")) or 0) >= 32
        )

    @classmethod
    def fixed_call_returndata_size(cls, call: EffectNode) -> int | None:
        if call.attrs.get("op") != "staticcall":
            return None
        target = int_text(str(call.attrs.get("target") or ""))
        return cls.FIXED_PRECOMPILE_RETURNDATA.get(target) if target is not None else None

    @staticmethod
    def append_path_condition(path: str, condition: str) -> str:
        path = str(path or "entry").strip()
        if not path or path == "entry":
            return condition
        atoms = EffectLifter.path_atoms(path)
        if condition in atoms:
            return path
        return f"{path} && {condition}"

    @staticmethod
    def is_returndata_size_expression(expression: str) -> bool:
        name, args = call_parts(str(expression or "").strip())
        return name == "returndatasize" and not args

    @classmethod
    def overlay_call_output_slices(
        cls,
        slices: list[dict[str, Any]],
        query_ptr: int,
        query_size: int,
        output_ptr: int,
        output_size: int,
        value: str,
        call: EffectNode,
    ) -> list[dict[str, Any]]:
        cells: dict[int, dict[str, Any]] = {}
        for item in slices:
            start = int(item.get("query_offset") or 0)
            width = int(item.get("size") or 0)
            source_offset = int(item.get("source_offset") or 0)
            for delta in range(width):
                cells[start + delta] = {
                    "query_offset": start + delta,
                    "source_value": item.get("source_value"),
                    "source_offset": source_offset + delta,
                    "source_width": int(item.get("source_width") or 32),
                    "source_version": item.get("source_version"),
                    "source_node_id": item.get("source_node_id"),
                    "source_kind": item.get("source_kind"),
                    "origin_src": item.get("origin_src"),
                    "overlap_count": item.get("overlap_count", 1),
                }
        overlap_start = max(query_ptr, output_ptr)
        # The guard proves only the first returned word.
        output_size = min(output_size, 32)
        overlap_end = min(query_ptr + query_size, output_ptr + output_size)
        for address in range(overlap_start, overlap_end):
            query_offset = address - query_ptr
            cells[query_offset] = {
                "query_offset": query_offset,
                "source_value": value,
                "source_offset": address - output_ptr,
                "source_width": output_size,
                "source_version": f"{call.effect_id}:output",
                "source_node_id": cls.effect_node_id(call),
                "source_kind": "call_output",
                "origin_src": (call.stmt_refs or [None])[0],
                "overlap_count": int((cells.get(query_offset) or {}).get("overlap_count", 0)) + 1,
            }
        return coalesce_byte_cells(cells, query_size)

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
        paths = EffectLifter.solidity_block_path_states(control)
        out: dict[int, list[str]] = {}
        for raw_block_id, boundary in (control.get("assembly_boundaries") or {}).items():
            try:
                assembly_block = int(raw_block_id)
            except (TypeError, ValueError):
                continue
            entry_block = str(boundary.get("entry_block") or "")
            entry_paths = paths.get(entry_block) or []
            if entry_paths:
                out[assembly_block] = list(dict.fromkeys(entry_paths))
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
            if assembly_block in out:
                continue
            entry_paths = paths.get(dst) or []
            if entry_paths:
                out[assembly_block] = list(dict.fromkeys(entry_paths))
        return out

    @staticmethod
    def memory_query(res: Any, nid: int, pointer: str, length: str | None, reason: str, function_loop_context: list[dict[str, Any]]):
        query = resolve_memory_read_with_loops(res, nid, pointer, length, reason)
        byte_slice = query.get("byte_slice") if isinstance(query, dict) else None
        if isinstance(byte_slice, dict) and byte_slice.get("path_slices"):
            expanded = []
            for item in byte_slice.get("path_slices") or []:
                for path in EffectLifter.merge_function_path_prefixes(
                    getattr(res, "function_path_prefixes", []) or [],
                    str(item.get("path") or "entry"),
                ):
                    expanded.append({**item, "path": path})
            byte_slice["path_slices"] = expanded
            signatures = {slice_signature(item.get("slices") or []) for item in expanded}
            byte_slice["slices"] = expanded[0].get("slices") or [] if len(signatures) == 1 and expanded else []
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
