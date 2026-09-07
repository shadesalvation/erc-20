#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path as _SSEIRPath
import re
import sys as _sseir_sys
_SSEIR_ROOT = _SSEIRPath(__file__).resolve().parents[1]
for _sseir_path in (_SSEIR_ROOT / "legacy_yul", _SSEIR_ROOT / "s_seir"):
    _sseir_text = str(_sseir_path)
    if _sseir_text not in _sseir_sys.path:
        _sseir_sys.path.insert(0, _sseir_text)

from typing import Any

from assembly_event_ir import EventDecl, is_static_word_type, normalize_topic_value
from assembly_external_call_ir import PRECOMPILES, native_precompile_call
from assembly_semantic_ir import parse_int_literal, parse_memory_address, strip_ssa
from s_seir_id import IdAllocator
from s_seir_model import EffectNode, FunctionUnit, SemanticOverlay
from s_seir_selector_registry import normalize_selector_value
from s_seir_sink_resolver import SinkResolver, packed_hash_sink_resolution
from s_seir_yul_normalize import call_parts, division_guards, invert_condition, is_unknown_value as memory_is_unknown_value, normalize_expr, words_from_memory_query


def hash_candidates_values(candidates: dict[str, tuple[EffectNode, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    return {key: value for key, (_effect, value) in candidates.items()}


class SemanticOverlayBuilder:
    def __init__(self, events_by_contract: dict[str, list[EventDecl]] | None = None, include_shallow_overlays: bool = True, selector_registry: dict[str, list[dict[str, Any]]] | None = None):
        self.ids = IdAllocator()
        self.events_by_contract = events_by_contract or {}
        self.include_shallow_overlays = include_shallow_overlays
        self.selector_registry = selector_registry or {}

    def build(self, unit: FunctionUnit, type_env: Any, expr_roles: list[Any], effects: list[EffectNode]) -> list[SemanticOverlay]:
        effects = SinkResolver().attach_all(effects)
        overlays: list[SemanticOverlay] = []
        overlays.extend(self.require_overlays(type_env, effects))
        overlays.extend(self.address_zero_check_overlays(type_env, effects))
        overlays.extend(self.raw_revert_bytes(type_env, expr_roles, effects))
        overlays.extend(self.bytes_content_hash_overlays(type_env, effects))
        overlays.extend(self.return_overlays(effects))
        overlays.extend(self.solidity_custom_errors(effects))
        overlays.extend(self.custom_error_selector_overlays(effects))
        overlays.extend(self.storage_overlays(type_env, effects))
        overlays.extend(self.event_overlays(unit, type_env, effects))
        call_overlays = self.call_overlays(effects)
        overlays.extend(call_overlays)
        overlays.extend(self.precompile_output_overlays(effects, call_overlays))
        # ``attach_cross_statement_call_outputs`` has already proved the
        # MemorySSA def-use relation.  Project that resolved result as a
        # completed semantic overlay before the generic expression projection
        # below, so downstream users need not rediscover it from ``mload``.
        overlays.extend(self.call_output_overlays(effects, call_overlays))
        overlays.extend(self.division_guard_overlays(effects))
        overlays.extend(self.memory_object_construction_overlays(unit, type_env, effects))
        struct_overlays = self.struct_memory_mutation_overlays(unit, type_env, effects)
        overlays.extend(struct_overlays)
        overlays.extend(self.abi_call_data_overlays(effects, overlays))
        overlays.extend(self.calldata_word_read_overlays(type_env, effects))
        overlays.extend(self.address_code_overlays(type_env, effects))
        overlays.extend(self.memory_array_overlays(unit, type_env, effects, struct_overlays))
        overlays.extend(self.memory_array_construction_overlays(unit, type_env, effects))
        overlays.extend(self.expression_overlays(type_env, effects))
        return self.dedupe(overlays)

    def ov(self, kind: str, effect_ids: list[str] | str, refs: list[str], attrs: dict[str, Any]) -> SemanticOverlay:
        if isinstance(effect_ids, str):
            effect_ids = [effect_ids]
        return SemanticOverlay(self.ids.new('ov'), kind, effect_ids, refs, attrs)

    def raw_revert_bytes(self, type_env: Any, roles: list[Any], effects: list[EffectNode]) -> list[SemanticOverlay]:
        out: list[SemanticOverlay] = []
        mem = [e for e in effects if e.kind == 'MemoryRead']
        by_ref: dict[str, list[Any]] = {}
        for role in roles:
            by_ref.setdefault(role.stmt_ref, []).append(role)
        for e in effects:
            if e.kind != 'Revert' or not e.attrs.get('payload_ptr'):
                continue
            ptr = next((r for ref in e.stmt_refs for r in by_ref.get(ref, []) if r.role == 'revert_payload_ptr'), None)
            obj = ptr.attrs.get('object') if ptr else None
            size = e.attrs.get('payload_size')
            if obj and type_env.is_bytes_memory(obj) and (size == f'{obj}.length' or any(m.attrs.get('value') == size and m.attrs.get('read_from') == obj for m in mem)):
                out.append(self.ov('RawRevertBytes', e.effect_id, e.stmt_refs, {'source_object': obj, 'payload': f'{obj}[0:{obj}.length]', 'guard': self.nearest_guard(e, effects), 'sink_resolution': e.attrs.get('sink_resolution')}))
        return out

    def bytes_content_hash_overlays(self, type_env: Any, effects: list[EffectNode]) -> list[SemanticOverlay]:
        """Lift keccak256(bytes.data, bytes.length) without inventing memory writes.

        Solidity-owned dynamic bytes are valid memory sources even when their
        contents were not constructed by mstore inside the assembly block.
        """
        out: list[SemanticOverlay] = []
        value_defs_by_name = self.value_defs_by_name(effects)
        value_defs_by_version = self.value_defs_by_version(effects)
        order = {effect.effect_id: index for index, effect in enumerate(effects)}
        for effect in effects:
            if effect.kind != 'MemoryHash':
                continue
            pattern = self.bytes_content_hash_pattern(
                type_env,
                effect,
                value_defs_by_name,
                value_defs_by_version,
                order,
            )
            if not pattern:
                continue
            supporting = list(dict.fromkeys((pattern.get('via_value_defs') or []) + [effect.effect_id]))
            supporting.sort(key=lambda effect_id: order.get(effect_id, len(order)))
            out.append(self.ov('BytesContentHash', supporting, effect.stmt_refs, {
                **pattern,
                'hash_algorithm': 'keccak256',
                'sink_resolution': effect.attrs.get('sink_resolution'),
                'path_states': self.path_states(effect),
                'pattern': 'dynamic_bytes_memory_data_pointer_and_length',
                'exact_solidity_equivalent': True,
            }))
        return out

    def bytes_content_hash_pattern(
        self,
        type_env: Any,
        effect: EffectNode,
        value_defs_by_name: dict[str, list[EffectNode]] | None = None,
        value_defs_by_version: dict[str, EffectNode] | None = None,
        order: dict[str, int] | None = None,
    ) -> dict[str, Any] | None:
        if effect.kind != 'MemoryHash':
            return None
        value_defs_by_name = value_defs_by_name or {}
        value_defs_by_version = value_defs_by_version or {}
        order = order or {}
        ptr, ptr_defs = self.resolve_unique_hash_operand(
            effect.attrs.get('ptr'),
            effect.attrs.get('ptr_versions') or [],
            effect,
            value_defs_by_name,
            value_defs_by_version,
            order,
        )
        size, size_defs = self.resolve_unique_hash_operand(
            effect.attrs.get('size'),
            effect.attrs.get('size_versions') or [],
            effect,
            value_defs_by_name,
            value_defs_by_version,
            order,
        )
        matched = getattr(type_env, 'dynamic_bytes_memory_slice', lambda *_args: None)(ptr, size)
        if not matched:
            return None
        target = effect.attrs.get('value')
        target_info = getattr(type_env, 'lookup', lambda _name: None)(str(target or ''))
        target_type = str(getattr(target_info, 'type_string', '') or '')
        expression = matched['hash_expression']
        result_expression = f'uint256({expression})' if target_type.strip() == 'uint256' else expression
        return self.clean({
            **matched,
            'target': target,
            'target_type': target_type or None,
            'source_pointer_expression': effect.attrs.get('ptr'),
            'source_length_expression': effect.attrs.get('size'),
            'expression': expression,
            'result_expression': result_expression,
            'solidity_like': f'{target} = {result_expression};' if target else None,
            'via_value_defs': list(dict.fromkeys(ptr_defs + size_defs)),
        })

    @classmethod
    def resolve_unique_hash_operand(
        cls,
        expr: Any,
        versions: list[Any],
        sink: EffectNode,
        value_defs_by_name: dict[str, list[EffectNode]],
        value_defs_by_version: dict[str, EffectNode],
        order: dict[str, int],
        seen: set[str] | None = None,
    ) -> tuple[str, list[str]]:
        text = str(expr or '').strip()
        seen = set(seen or ())
        if not text or text in seen or call_parts(text)[0] is not None or parse_int_literal(text) is not None:
            return text, []
        candidates: list[EffectNode] = []
        for version in versions:
            definition = value_defs_by_version.get(str(version))
            if definition and definition not in candidates:
                candidates.append(definition)
        if not candidates:
            sink_index = order.get(sink.effect_id, len(order))
            candidates = [
                definition
                for definition in value_defs_by_name.get(text, [])
                if order.get(definition.effect_id, -1) < sink_index
                and cls.value_def_covers_sink_paths(definition, sink)
            ]
        if len(candidates) != 1:
            return text, []
        definition = candidates[0]
        resolved, chain = cls.resolve_unique_hash_operand(
            definition.attrs.get('value'),
            [],
            sink,
            value_defs_by_name,
            value_defs_by_version,
            order,
            seen | {text},
        )
        return resolved, [definition.effect_id] + chain

    @classmethod
    def value_def_covers_sink_paths(cls, definition: EffectNode, sink: EffectNode) -> bool:
        def_paths = cls.path_states(definition) or ['entry']
        sink_paths = cls.path_states(sink) or ['entry']
        for sink_path in sink_paths:
            sink_parts = set(cls.condition_parts(sink_path))
            if not any(set(cls.condition_parts(path)).issubset(sink_parts) for path in def_paths):
                return False
        return True

    def solidity_custom_errors(self, effects: list[EffectNode]) -> list[SemanticOverlay]:
        out = []
        for e in effects:
            if e.kind == 'Revert' and isinstance(e.attrs.get('payload'), str) and e.attrs.get('payload').strip():
                out.append(self.ov('CustomErrorRevert', e.effect_id, e.stmt_refs, {'error': e.attrs.get('payload')}))
        return out

    def return_overlays(self, effects: list[EffectNode]) -> list[SemanticOverlay]:
        out: list[SemanticOverlay] = []
        for e in effects:
            if e.kind != 'Return':
                continue
            if e.attrs.get('language') == 'solidity' and 'values' in e.attrs:
                values = list(e.attrs.get('values') or [])
                out.append(self.ov('ReturnValue', e.effect_id, e.stmt_refs, {
                    'values': values,
                    'return_like': f"return {', '.join(map(str, values))};" if values else 'return;',
                    'path_states': self.path_states(e),
                    'exact_solidity_semantics': True,
                    'source': e.attrs.get('source'),
                }))
                continue
            path_overlay = self.path_conditioned_return_overlay(e)
            if path_overlay:
                out.append(path_overlay)
                continue
            ptr = e.attrs.get('payload_ptr')
            size = e.attrs.get('payload_size')
            if ptr is None or size is None:
                continue
            words = words_from_memory_query(e.attrs.get('payload_memory'))
            values = [
                normalize_expr(word.get('value'))
                for word in words
                if word.get('value') is not None and not memory_is_unknown_value(word.get('value'))
            ]
            size_int = parse_int_literal(str(size))
            complete = bool((e.attrs.get('payload_memory') or {}).get('complete'))
            encoding_hint = None
            if size_int == 32 and len(values) == 1 and complete:
                encoding_hint = 'abi_word'
                solidity_like = f"returnRawAbiWord({values[0]});"
            elif size_int is not None and size_int % 32 == 0 and values and complete:
                encoding_hint = 'abi_static_words'
                solidity_like = f"returnRawAbiWords({', '.join(values)});"
            else:
                solidity_like = f"returnRawMemory({normalize_expr(ptr)}, {normalize_expr(size)});"
            attrs = {
                'payload_ptr': ptr,
                'payload_size': size,
                'payload_ptr_normalized': normalize_expr(ptr),
                'payload_size_normalized': normalize_expr(size),
                'payload_memory_complete': complete,
                'encoding_hint': encoding_hint,
                'values': values,
                'words': words,
                'sink_resolution': e.attrs.get('sink_resolution'),
                'partial_slices': (e.attrs.get('payload_memory_partial') or {}).get('slices') or [],
                'solidity_like': solidity_like,
                'solidity_equivalent': False,
                'reason': 'yul_return_terminates_current_evm_call_with_raw_return_data',
                'path_states': self.path_states(e),
            }
            out.append(self.ov('RawReturnData', e.effect_id, e.stmt_refs, attrs))
        return out

    def custom_error_selector_overlays(self, effects: list[EffectNode]) -> list[SemanticOverlay]:
        out: list[SemanticOverlay] = []
        for e in effects:
            if e.kind != 'Revert':
                continue
            if self.is_empty_revert_payload(e):
                continue
            path_overlay = self.path_conditioned_revert_overlay(e)
            if path_overlay:
                out.append(path_overlay)
                continue
            selector_info = self.selector_info_from_payload(e, preferred_kind='error')
            if not selector_info:
                continue
            match = selector_info.get('best_match') or {}
            signature = match.get('signature')
            attrs = {
                'selector': selector_info.get('selector'),
                'selector_source': selector_info.get('selector_source'),
                'selector_match': selector_info,
                'error': signature,
                'revert_like': f"revert {signature};" if signature else None,
                'sink_resolution': e.attrs.get('sink_resolution'),
            }
            out.append(self.ov('CustomErrorRevert', e.effect_id, e.stmt_refs, attrs))
        return out

    @staticmethod
    def is_empty_revert_payload(effect: EffectNode) -> bool:
        return parse_int_literal(str(effect.attrs.get('payload_size') or '')) == 0

    def selector_info_from_payload(self, effect: EffectNode, preferred_kind: str | None = None) -> dict[str, Any] | None:
        byte_slice = (effect.attrs.get('payload_memory') or {}).get('byte_slice') or {}
        if byte_slice.get('complete') and parse_int_literal(str(byte_slice.get('size') or '')) == 4:
            parts = byte_slice.get('packed_semantics') or [
                item.get('extraction')
                for item in byte_slice.get('slices') or []
                if item.get('extraction')
            ]
            if len(parts) == 1:
                selector = normalize_selector_value(parts[0])
                if selector:
                    return self.selector_match_from_value(selector, parts[0], preferred_kind, {'kind': 'selector', 'selector': parts[0]})
        return self.selector_info_from_partial(effect.attrs.get('payload_memory_partial'), preferred_kind=preferred_kind)

    def require_overlays(self, type_env: Any, effects: list[EffectNode]) -> list[SemanticOverlay]:
        out: list[SemanticOverlay] = []
        branches = [e for e in effects if e.kind == 'Branch']
        for e in effects:
            if e.kind == 'Require' and e.attrs.get('language') == 'solidity':
                condition = str(e.attrs.get('condition') or 'unknown')
                arguments = list(e.attrs.get('arguments') or [])
                rendered_arguments = arguments or [condition]
                out.append(self.ov('RequireOverlay', e.effect_id, e.stmt_refs, {
                    'condition': condition,
                    'arguments': arguments,
                    'failure_payload': arguments[1:] if len(arguments) > 1 else [],
                    'require_like': f"{e.attrs.get('builtin') or 'require'}({', '.join(map(str, rendered_arguments))});",
                    'control_path': self.path_states(e),
                    'path_states': self.path_states(e),
                    'exact_solidity_semantics': True,
                    'source': e.attrs.get('source'),
                }))
                continue
            if e.kind != 'Revert' or e.attrs.get('payload'):
                continue
            if not self.is_empty_revert_payload(e):
                continue
            merged = self.mergeable_revert_conditions(e, effects)
            guard = merged[-1] if merged else self.nearest_guard(e, effects, branches)
            require_conditions = merged or ([guard] if guard else [])
            # Keep the source-level guard predicates separate from their
            # evaluation temporaries.  The latter are useful evidence inside
            # S-SEIR, but the former is the canonical Require condition for
            # downstream semantic IR.
            semantic_require_conditions = [
                self.normalize_condition_state_reads(type_env, item)
                for item in require_conditions
                if item
            ]
            evaluated_conditions = [
                self.branch_condition_value(condition, branches)
                for condition in require_conditions
            ]
            condition = self.require_condition(semantic_require_conditions, type_env)
            path_states = self.path_states(e)
            out.append(self.ov('RequireOverlay', e.effect_id, e.stmt_refs, {
                'condition': condition,
                'nearest_condition': guard,
                'require_like': f'require({condition});',
                'revert_payload': 'empty',
                'control_path': path_states,
                'path_states': path_states,
                'merged_conditions': merged,
                'require_conditions': require_conditions,
                'semantic_require_conditions': semantic_require_conditions,
                'evaluated_require_conditions': evaluated_conditions,
                'guard_stmt_refs': self.require_guard_stmt_refs(require_conditions, branches),
                'discarded_before_revert': self.discarded_before_revert(e, merged, effects),
                'elided_by_native_precompile': self.is_native_precompile_success_guard(guard),
                'sink_resolution': e.attrs.get('sink_resolution'),
            }))
        return out

    @staticmethod
    def require_guard_stmt_refs(conditions: list[str], branches: list[EffectNode]) -> list[str]:
        """Return the branch statements whose semantic guard reaches Revert.

        ``conditions`` originate from the recovered control path, and branch
        conditions are S-SEIR semantic identities rather than source-text
        scans.  The result lets SFIR remove only the corresponding derivation
        node after replacing it with the recovered Require operation.
        """
        wanted = {str(condition).strip() for condition in conditions if condition}
        refs: list[str] = []
        for branch in branches:
            if str(branch.attrs.get('condition') or '').strip() not in wanted:
                continue
            refs.extend(str(ref) for ref in branch.stmt_refs if ref)
        return list(dict.fromkeys(refs))

    @staticmethod
    def branch_condition_value(condition: str, branches: list[EffectNode]) -> str:
        text = str(condition or '').strip()
        for branch in branches:
            attrs = branch.attrs or {}
            if str(attrs.get('condition') or '').strip() != text:
                continue
            final_temp = str(attrs.get('condition_final_temp') or '').strip()
            if final_temp:
                return final_temp
            break
        return text

    def mergeable_revert_conditions(self, revert: EffectNode, effects: list[EffectNode]) -> list[str]:
        control_path = self.common_condition_suffix(self.path_states(revert))
        if not control_path:
            return []
        selected = [control_path[-1]]
        for condition in reversed(control_path[:-1]):
            if self.condition_block_has_other_effective_ops(condition, revert, effects):
                break
            selected.insert(0, condition)
        return selected

    def condition_block_has_other_effective_ops(self, condition: str, revert: EffectNode, effects: list[EffectNode]) -> bool:
        revert_node = self.int_node(revert.attrs.get('cfg_node_id'))
        for effect in effects:
            if effect is revert or effect.kind in {'Branch', 'Revert'}:
                continue
            node = self.int_node(effect.attrs.get('cfg_node_id'))
            if revert_node is not None and node is not None and node > revert_node:
                continue
            if any(condition in self.split_path_conditions(path) for path in self.effect_path_states(effect)):
                return True
        return False

    def discarded_before_revert(self, revert: EffectNode, conditions: list[str], effects: list[EffectNode]) -> list[dict[str, Any]]:
        if not conditions:
            return []
        revert_node = self.int_node(revert.attrs.get('cfg_node_id'))
        discarded: list[dict[str, Any]] = []
        side_effects = {'StorageWrite', 'EventLog', 'MemoryCopy', 'Call', 'StaticCall', 'DelegateCall', 'CallCode'}
        for effect in effects:
            if effect.kind not in side_effects:
                continue
            node = self.int_node(effect.attrs.get('cfg_node_id'))
            if revert_node is not None and node is not None and node >= revert_node:
                continue
            if any(all(condition in self.split_path_conditions(path) for condition in conditions) for path in self.effect_path_states(effect)):
                discarded.append({'effect': effect.effect_id, 'kind': effect.kind, 'stmt_refs': effect.stmt_refs})
        return discarded

    def require_condition(self, conditions: list[str], type_env: Any) -> str:
        clean = [condition for condition in conditions if condition]
        if not clean:
            return 'false'
        clean = [self.normalize_condition_state_reads(type_env, condition) for condition in clean]
        if len(clean) == 1:
            return invert_condition(clean[0], type_env)
        joined = ' && '.join(f'({normalize_expr(condition)})' for condition in clean)
        return f'!({joined})'

    def normalize_condition_state_reads(self, type_env: Any, expr: Any, leaf_context: str = 'value') -> str:
        text = str(expr or '').strip()
        name, args = call_parts(text)
        if not name:
            return normalize_expr(text, context=leaf_context)
        direct_state = self.direct_state_read_from_sload_expr(type_env, text)
        if direct_state:
            return str(direct_state['solidity_like'])
        rendered = [self.normalize_condition_state_reads(type_env, arg, 'value') for arg in args]
        return f"{name}({', '.join(rendered)})"

    @classmethod
    def common_condition_suffix(cls, path_states: list[str]) -> list[str]:
        paths = [cls.split_path_conditions(path) for path in path_states if path]
        if not paths:
            return []
        suffix: list[str] = []
        for items in zip(*(reversed(path) for path in paths)):
            if len(set(items)) != 1:
                break
            suffix.insert(0, items[0])
        return suffix

    @staticmethod
    def split_path_conditions(path: str) -> list[str]:
        return [part.strip() for part in str(path or '').split(' && ') if part.strip()]

    @classmethod
    def effect_path_states(cls, effect: EffectNode) -> list[str]:
        states = cls.path_states(effect)
        if states:
            return states
        condition = effect.attrs.get('condition')
        return [str(condition)] if condition else []

    def address_zero_check_overlays(self, type_env: Any, effects: list[EffectNode]) -> list[SemanticOverlay]:
        out: list[SemanticOverlay] = []
        value_defs_by_name = self.value_defs_by_name(effects)
        for effect in effects:
            if effect.kind != 'Branch':
                continue
            condition = effect.attrs.get('condition')
            result = self.address_zero_check_from_condition(type_env, condition, value_defs_by_name)
            if not result:
                continue
            check = result['check']
            variable = result['variable']
            human_condition = f"{variable} == address(0)" if check == 'is_zero' else f"{variable} != address(0)"
            out.append(self.ov('AddressZeroCheck', effect.effect_id, effect.stmt_refs, self.clean({
                'variable': variable,
                'variable_type': result.get('variable_type'),
                'check': check,
                'condition': human_condition,
                'source_expression': condition,
                'source_pattern': result.get('source_pattern'),
                'projection': result.get('projection'),
                'projection_expression': result.get('projection_expression'),
                'via_value_defs': result.get('via_value_defs'),
                'used_by': 'Branch',
                'solidity_like': human_condition,
                'solidity_equivalent': True,
            })))
        return out

    def address_zero_check_from_condition(
        self,
        type_env: Any,
        condition: Any,
        value_defs_by_name: dict[str, list[EffectNode]],
    ) -> dict[str, Any] | None:
        text = self.strip_outer_parens(str(condition or '').strip())
        if not text:
            return None
        call, args = call_parts(text)
        if call == 'iszero' and len(args) == 1:
            inner = self.strip_outer_parens(args[0])
            inner_call, inner_args = call_parts(inner)
            if inner_call == 'iszero' and len(inner_args) == 1:
                return self.address_zero_check_for_projection(type_env, inner_args[0], value_defs_by_name, 'is_nonzero', 'iszero(iszero(address_projection))')
            if inner_call == 'eq' and len(inner_args) == 2:
                eq_projection = self.zero_eq_projection(type_env, inner_args[0], inner_args[1], value_defs_by_name)
                if eq_projection:
                    eq_projection['check'] = 'is_nonzero'
                    eq_projection['source_pattern'] = 'iszero(eq(address_projection, 0))'
                    return eq_projection
            return self.address_zero_check_for_projection(type_env, inner, value_defs_by_name, 'is_zero', 'iszero(address_projection)')
        if call == 'eq' and len(args) == 2:
            result = self.zero_eq_projection(type_env, args[0], args[1], value_defs_by_name)
            if result:
                result['check'] = 'is_zero'
                result['source_pattern'] = 'eq(address_projection, 0)'
                return result
        return self.address_zero_check_for_projection(type_env, text, value_defs_by_name, 'is_nonzero', 'address_projection_as_condition')

    def zero_eq_projection(
        self,
        type_env: Any,
        left: Any,
        right: Any,
        value_defs_by_name: dict[str, list[EffectNode]],
    ) -> dict[str, Any] | None:
        left_zero = self.is_zero_literal(left)
        right_zero = self.is_zero_literal(right)
        if right_zero:
            return self.address_projection(type_env, left, value_defs_by_name)
        if left_zero:
            return self.address_projection(type_env, right, value_defs_by_name)
        return None

    def address_zero_check_for_projection(
        self,
        type_env: Any,
        expr: Any,
        value_defs_by_name: dict[str, list[EffectNode]],
        check: str,
        pattern: str,
    ) -> dict[str, Any] | None:
        projection = self.address_projection(type_env, expr, value_defs_by_name)
        if not projection:
            return None
        projection['check'] = check
        projection['source_pattern'] = pattern
        return projection

    def address_projection(
        self,
        type_env: Any,
        expr: Any,
        value_defs_by_name: dict[str, list[EffectNode]],
        depth: int = 0,
        via: list[str] | None = None,
    ) -> dict[str, Any] | None:
        if depth > 8:
            return None
        via = list(via or [])
        text = self.strip_outer_parens(str(expr or '').strip())
        if not text:
            return None
        if self.is_address_variable(type_env, text):
            info = getattr(type_env, 'lookup', lambda _name: None)(text)
            return {
                'variable': text,
                'variable_type': getattr(info, 'type_string', None),
                'projection': 'identity',
                'projection_expression': text,
                'via_value_defs': via,
            }
        value_defs = value_defs_by_name.get(text) or []
        if value_defs:
            resolved_defs = []
            for value_def in value_defs:
                resolved = self.address_projection(
                    type_env,
                    value_def.attrs.get('value'),
                    value_defs_by_name,
                    depth + 1,
                    via + [value_def.effect_id],
                )
                if not resolved:
                    resolved_defs = []
                    break
                resolved_defs.append(resolved)
            variables = {str(item.get('variable')) for item in resolved_defs if item.get('variable')}
            if resolved_defs and len(variables) == 1:
                resolved = dict(resolved_defs[0])
                resolved['via_value_defs'] = list(dict.fromkeys(
                    effect_id
                    for item in resolved_defs
                    for effect_id in item.get('via_value_defs') or []
                ))
                resolved['projection_expression'] = text
                return resolved
        call, args = call_parts(text)
        if call == 'shl' and len(args) == 2 and self.int_arg(args[0]) == 96:
            resolved = self.address_projection(type_env, args[1], value_defs_by_name, depth + 1, via)
            if resolved:
                resolved['projection'] = 'shl(96, address)'
                resolved['projection_expression'] = text
                return resolved
        if call == 'shr' and len(args) == 2 and self.int_arg(args[0]) == 96:
            resolved = self.address_projection(type_env, args[1], value_defs_by_name, depth + 1, via)
            if resolved:
                resolved['projection'] = f"shr(96, {resolved.get('projection') or 'address_projection'})"
                resolved['projection_expression'] = text
                return resolved
        if call == 'and' and len(args) == 2:
            if self.is_address_mask(args[0]):
                resolved = self.address_projection(type_env, args[1], value_defs_by_name, depth + 1, via)
            elif self.is_address_mask(args[1]):
                resolved = self.address_projection(type_env, args[0], value_defs_by_name, depth + 1, via)
            else:
                resolved = None
            if resolved:
                resolved['projection'] = 'and(address, address_mask)'
                resolved['projection_expression'] = text
                return resolved
        return None

    @staticmethod
    def strip_outer_parens(text: str) -> str:
        text = str(text or '').strip()
        while text.startswith('(') and text.endswith(')'):
            depth = 0
            balanced = True
            for index, ch in enumerate(text):
                if ch == '(':
                    depth += 1
                elif ch == ')':
                    depth -= 1
                    if depth == 0 and index != len(text) - 1:
                        balanced = False
                        break
            if not balanced or depth != 0:
                break
            text = text[1:-1].strip()
        return text

    @staticmethod
    def is_zero_literal(value: Any) -> bool:
        parsed = parse_int_literal(str(value or '').strip())
        return parsed == 0

    @staticmethod
    def int_arg(value: Any) -> int | None:
        return parse_int_literal(str(value or '').strip())

    @staticmethod
    def is_address_mask(value: Any) -> bool:
        parsed = parse_int_literal(str(value or '').strip())
        return parsed == (1 << 160) - 1

    @staticmethod
    def is_address_variable(type_env: Any, name: str) -> bool:
        info = getattr(type_env, 'lookup', lambda _name: None)(str(name).strip())
        return bool(info and 'address' in str(getattr(info, 'type_string', '')))

    def storage_overlays(self, type_env: Any, effects: list[EffectNode]) -> list[SemanticOverlay]:
        """Recover storage overlays with storage-consumer-gated, SSA-aware slot activation.

        MemoryHash nodes are keyed by their MemorySSA value versions when
        available. This prevents distinct assignments like `Jfwv := ...` and
        `Jfwv := ...` from collapsing into the same slot symbol.
        """
        out: list[SemanticOverlay] = []
        typed_effect_ids: set[str] = set()
        for effect in effects:
            if effect.kind not in {'StorageRead', 'StorageWrite'} or not effect.attrs.get('typed_access'):
                continue
            typed_effect_ids.add(effect.effect_id)
            access = str(effect.attrs.get('access') or effect.attrs.get('state_variable') or 'storage[unknown]')
            keys = list(effect.attrs.get('keys') or [])
            is_mapping = bool(keys) or '[' in access
            if is_mapping:
                kind = 'MappingRead' if effect.kind == 'StorageRead' else 'MappingWrite'
            else:
                kind = 'StateVariableRead' if effect.kind == 'StorageRead' else 'StateVariableWrite'
            value = effect.attrs.get('value')
            attrs = {
                'access': access,
                'target': effect.attrs.get('target') if effect.kind == 'StorageRead' else None,
                'value': value if effect.kind == 'StorageWrite' else None,
                'state_variable': effect.attrs.get('state_variable'),
                'keys': keys,
                'key': keys[-1] if keys else None,
                'reference_kind': effect.attrs.get('reference_kind'),
                'type': effect.attrs.get('type'),
                'typed_access': True,
                'access_version': effect.attrs.get('access_version'),
                'value_ssa': effect.attrs.get('value_ssa'),
                'path_states': self.path_states(effect),
                'solidity_like': self.storage_solidity_like(kind, access, value, effect),
                'exact_solidity_semantics': True,
                'source': effect.attrs.get('source'),
            }
            out.append(self.ov(kind, effect.effect_id, effect.stmt_refs, self.clean(attrs)))
        effects = [effect for effect in effects if effect.effect_id not in typed_effect_ids]
        hash_candidates: dict[str, tuple[EffectNode, dict[str, Any]]] = {}
        activated: set[str] = set()
        slot_expr_by_var: dict[str, dict[str, Any]] = {}
        hash_effect_by_var: dict[str, EffectNode] = {}
        value_defs_by_version = self.value_defs_by_version(effects)
        value_defs_by_name = self.value_defs_by_name(effects)
        ambiguous_hash_aliases: set[str] = set()

        for e in effects:
            if e.kind != 'MemoryHash':
                continue
            keys = self.hash_result_keys(e)
            if not keys:
                continue
            slot_expr = (
                self.mapping_slot_expr(type_env, e, slot_expr_by_var | {k: v for k, v in hash_candidates_values(hash_candidates).items()})
                or self.packed_hash_slot_expr(type_env, e, hash_candidates_values(hash_candidates), value_defs_by_name)
            )
            if not slot_expr:
                continue
            slot_expr['target'] = e.attrs.get('value')
            slot_expr['target_keys'] = keys
            for key in keys:
                hash_candidates[key] = (e, slot_expr)
            alias = self.hash_result_alias(e)
            if alias:
                if alias in ambiguous_hash_aliases:
                    continue
                previous = hash_candidates.get(alias)
                if previous and previous[0].effect_id != e.effect_id:
                    hash_candidates.pop(alias, None)
                    ambiguous_hash_aliases.add(alias)
                elif not previous:
                    hash_candidates[alias] = (e, slot_expr)

        def activate(slot_key: str | None) -> None:
            if not slot_key or slot_key in activated or slot_key not in hash_candidates:
                return
            effect, slot_expr = hash_candidates[slot_key]
            base_key = str(slot_expr.get('base_key') or slot_expr.get('base') or '')
            if base_key in hash_candidates:
                activate(base_key)
            activated.add(slot_key)
            slot_expr_by_var[slot_key] = slot_expr
            hash_effect_by_var[slot_key] = effect
            if slot_expr.get('slot_kind') in {'manual_packed_hash_slot', 'path_conditioned_manual_packed_hash_slot'}:
                return
            out.append(self.ov('MappingSlot', effect.effect_id, effect.stmt_refs, {
                'target': slot_expr.get('target'),
                'target_key': slot_key,
                'target_keys': slot_expr.get('target_keys'),
                'expression': slot_expr['access'],
                'state_variable': slot_expr.get('state_variable'),
                'storage_reference': slot_expr.get('storage_reference'),
                'storage_reference_type': slot_expr.get('storage_reference_type'),
                'storage_reference_kind': slot_expr.get('storage_reference_kind'),
                'storage_field': slot_expr.get('storage_field'),
                'key': slot_expr.get('key'),
                'keys': slot_expr.get('keys'),
                'base': slot_expr.get('base'),
                'base_key': slot_expr.get('base_key'),
                'parent_target_key': base_key if base_key in hash_candidates else None,
                'slot_kind': slot_expr.get('slot_kind') or ('mapping_slot' if base_key not in slot_expr_by_var else 'nested_mapping_slot'),
                'result_type': slot_expr.get('result_type'),
                'terminal_storage_value': slot_expr.get('terminal_storage_value'),
                'activation': 'storage_consumer',
                'resolved_inputs': slot_expr.get('resolved_inputs'),
                'byte_slice': slot_expr.get('byte_slice'),
                'packed_semantics': slot_expr.get('packed_semantics'),
                'notes': slot_expr.get('notes', []),
            }))

        for e in effects:
            if e.kind in {'StorageRead', 'StorageWrite'}:
                for key in self.storage_slot_keys(e):
                    activate(key)

        nested_read_accesses: dict[str, str] = {}
        for effect in effects:
            expression = effect.attrs.get('expression')
            if effect.kind != 'StorageRead' or not expression:
                continue
            access = self.storage_access_for_effect(
                type_env,
                effect,
                slot_expr_by_var,
                value_defs_by_version,
            )
            if access:
                nested_read_accesses[str(expression).strip()] = access

        for e in effects:
            if e.kind not in {'StorageRead', 'StorageWrite'}:
                continue
            slot = str(e.attrs.get('slot') or '')
            version_keys = list(e.attrs.get('slot_versions') or [])
            collapsed_path_overlay: SemanticOverlay | None = None
            if len(version_keys) > 1:
                has_slot_expr_path_candidates = any(
                    (slot_expr_by_var.get(key) or {}).get('path_candidates')
                    for key in version_keys
                )
                path_overlay = None if has_slot_expr_path_candidates else self.path_conditioned_storage_overlay(
                    e, type_env, version_keys, slot_expr_by_var, hash_effect_by_var, value_defs_by_name
                )
                if path_overlay:
                    statuses = {candidate.get('status') for candidate in path_overlay.attrs.get('candidates', [])}
                    resolved_accesses = {
                        candidate.get('access')
                        for candidate in path_overlay.attrs.get('candidates', [])
                        if candidate.get('status') == 'resolved'
                    }
                    if 'unresolved' in statuses or len(resolved_accesses) > 1:
                        out.append(path_overlay)
                        continue
                    collapsed_path_overlay = path_overlay
            slot_key = self.first_known_key(self.storage_slot_keys(e), slot_expr_by_var)
            value = e.attrs.get('value')
            value_normalized, value_state_read = self.storage_write_value(
                type_env,
                e,
                value_defs_by_name,
                nested_read_accesses,
            )
            direct_state = type_env.state_var_by_slot(slot) or self.direct_state_slot_alias(type_env, e, value_defs_by_version)
            direct_slot = self.direct_storage_slot_info(type_env, e, value_defs_by_version)
            if slot_key:
                slot_expr = slot_expr_by_var[slot_key]
                if slot_expr.get('path_candidates'):
                    out.append(self.path_conditioned_storage_overlay_from_slot_expr(
                        e, slot_key, slot_expr, hash_effect_by_var.get(slot_key), type_env, value_defs_by_name
                    ))
                    continue
                if slot_expr.get('terminal_storage_value') is False:
                    kind = 'StateVariableRead' if e.kind == 'StorageRead' else 'StateVariableWrite'
                    access = f'storage[{slot_key}]'
                    attrs = {
                        'access': access,
                        'target': value if e.kind == 'StorageRead' else None,
                        'value': value_normalized if e.kind == 'StorageWrite' else None,
                        'value_yul': value if e.kind == 'StorageWrite' else None,
                        'value_state_read': value_state_read,
                        'slot': slot,
                        'slot_key': slot_key,
                        'slot_versions': e.attrs.get('slot_versions'),
                        'unresolved_reason': 'intermediate_mapping_slot_requires_additional_key',
                        'intermediate_access': slot_expr.get('access'),
                        'intermediate_type': slot_expr.get('result_type'),
                        'solidity_like': self.storage_solidity_like(kind, access, value_normalized, e),
                        **self.nested_storage_read_attrs(e, access),
                        'notes': list(dict.fromkeys((slot_expr.get('notes') or []) + ['intermediate_mapping_slot_not_projected'])),
                    }
                    out.append(self.ov(kind, e.effect_id, e.stmt_refs, self.clean(
                        self.with_collapsed_path_storage_attrs(attrs, collapsed_path_overlay)
                    )))
                    continue
                if slot_expr.get('slot_kind') == 'manual_packed_hash_slot':
                    kind = 'StateVariableRead' if e.kind == 'StorageRead' else 'StateVariableWrite'
                    slot_effect = hash_effect_by_var.get(slot_key)
                    slot_derivation = self.manual_packed_slot_derivation(slot_expr, slot_key, slot_effect)
                    attrs = {
                        'access': slot_expr['access'],
                        'target': value if e.kind == 'StorageRead' else None,
                        'value': value_normalized if e.kind == 'StorageWrite' else None,
                        'value_yul': value if e.kind == 'StorageWrite' else None,
                        'value_state_read': value_state_read,
                        'slot': slot,
                        'slot_key': slot_key,
                        'slot_versions': e.attrs.get('slot_versions'),
                        'slot_effect': slot_effect.effect_id if slot_effect else None,
                        'slot_derivation': slot_derivation,
                        'sink_resolution': slot_expr.get('sink_resolution'),
                        'storage_model': 'manual_packed_hash_slot',
                        'state_access': True,
                        'state_mutation': e.kind == 'StorageWrite',
                        'mutation_kind': 'state_write' if e.kind == 'StorageWrite' else None,
                        'variable_name_inferred': False,
                        'solidity_like': self.storage_solidity_like(kind, slot_expr['access'], value_normalized, e),
                        **self.nested_storage_read_attrs(e, slot_expr['access']),
                        'notes': slot_expr.get('notes', []),
                    }
                    effects_used = [x for x in [slot_effect.effect_id if slot_effect else None, e.effect_id] if x]
                    refs = list(dict.fromkeys((slot_effect.stmt_refs if slot_effect else []) + e.stmt_refs))
                    out.append(self.ov(kind, effects_used, refs, self.clean(
                        self.with_collapsed_path_storage_attrs(attrs, collapsed_path_overlay)
                    )))
                    continue
                kind = 'MappingRead' if e.kind == 'StorageRead' else 'MappingWrite'
                attrs = {
                    'access': slot_expr['access'],
                    'target': value if e.kind == 'StorageRead' else None,
                    'value': value_normalized if e.kind == 'StorageWrite' else None,
                    'value_yul': value if e.kind == 'StorageWrite' else None,
                    'value_state_read': value_state_read,
                    'state_variable': slot_expr.get('state_variable'),
                    'storage_reference': slot_expr.get('storage_reference'),
                    'storage_reference_type': slot_expr.get('storage_reference_type'),
                    'storage_reference_kind': slot_expr.get('storage_reference_kind'),
                    'storage_field': slot_expr.get('storage_field'),
                    'key': slot_expr.get('key'),
                    'keys': slot_expr.get('keys'),
                    'parent_target_key': slot_expr.get('base_key') if slot_expr.get('base_key') in slot_expr_by_var else None,
                    'slot': slot,
                    'slot_key': slot_key,
                    'slot_versions': e.attrs.get('slot_versions'),
                    'slot_effect': hash_effect_by_var.get(slot_key).effect_id if slot_key in hash_effect_by_var else None,
                    'slot_derivation': slot_expr.get('slot_derivation'),
                    'sink_resolution': slot_expr.get('sink_resolution'),
                    'result_type': slot_expr.get('result_type'),
                    'terminal_storage_value': slot_expr.get('terminal_storage_value'),
                    'solidity_like': self.storage_solidity_like(kind, slot_expr['access'], value_normalized, e),
                    **self.nested_storage_read_attrs(e, slot_expr['access']),
                    'notes': slot_expr.get('notes', []),
                }
                effects_used = [x for x in [hash_effect_by_var.get(slot_key).effect_id if slot_key in hash_effect_by_var else None, e.effect_id] if x]
                refs = list(dict.fromkeys((hash_effect_by_var.get(slot_key).stmt_refs if slot_key in hash_effect_by_var else []) + e.stmt_refs))
                out.append(self.ov(kind, effects_used, refs, self.clean(
                    self.with_collapsed_path_storage_attrs(attrs, collapsed_path_overlay)
                )))
            elif direct_state:
                kind = 'StateVariableRead' if e.kind == 'StorageRead' else 'StateVariableWrite'
                attrs = {
                    'access': direct_state.name,
                    'target': value if e.kind == 'StorageRead' else None,
                    'value': value_normalized if e.kind == 'StorageWrite' else None,
                    'value_yul': value if e.kind == 'StorageWrite' else None,
                    'value_state_read': value_state_read,
                    'state_variable': direct_state.name,
                    'slot': slot,
                    'slot_versions': e.attrs.get('slot_versions'),
                    'nested_in_memory_value': e.attrs.get('nested_in_memory_value'),
                    'nested_in_storage_value': e.attrs.get('nested_in_storage_value'),
                    'parent_call': e.attrs.get('parent_call'),
                    'parent_memory_address': e.attrs.get('parent_memory_address'),
                    'parent_memory_value': e.attrs.get('parent_memory_value'),
                    'solidity_like': self.storage_solidity_like(kind, direct_state.name, value_normalized, e),
                    **self.nested_storage_read_attrs(e, direct_state.name),
                }
                out.append(self.ov(kind, e.effect_id, e.stmt_refs, self.clean(
                    self.with_collapsed_path_storage_attrs(attrs, collapsed_path_overlay)
                )))
            elif direct_slot:
                kind = 'StateVariableRead' if e.kind == 'StorageRead' else 'StateVariableWrite'
                access = f"storage[{direct_slot.get('name') or direct_slot.get('slot')}]"
                state_note = 'state read, manual slot' if e.kind == 'StorageRead' else 'state write, manual slot'
                base_line = self.storage_solidity_like(kind, access, value_normalized, e)
                solidity_like = f"{base_line.rstrip(';')}; /* {state_note} */"
                attrs = {
                    'access': access,
                    'target': value if e.kind == 'StorageRead' else None,
                    'value': value_normalized if e.kind == 'StorageWrite' else None,
                    'value_yul': value if e.kind == 'StorageWrite' else None,
                    'value_state_read': value_state_read,
                    'slot': slot,
                    'slot_versions': e.attrs.get('slot_versions'),
                    'slot_constant': direct_slot.get('name'),
                    'slot_value': direct_slot.get('value'),
                    'storage_model': 'manual_constant_slot',
                    'state_access': True,
                    'state_mutation': e.kind == 'StorageWrite',
                    'mutation_kind': 'state_write' if e.kind == 'StorageWrite' else None,
                    'variable_name_inferred': False,
                    'solidity_like': solidity_like,
                    **self.nested_storage_read_attrs(e, access),
                    'notes': ['constant_storage_slot', 'manual_storage_layout', 'writes_contract_state' if e.kind == 'StorageWrite' else 'reads_contract_state'],
                }
                out.append(self.ov(kind, e.effect_id, e.stmt_refs, self.clean(
                    self.with_collapsed_path_storage_attrs(attrs, collapsed_path_overlay)
                )))
            else:
                kind = 'StateVariableRead' if e.kind == 'StorageRead' else 'StateVariableWrite'
                access = f'storage[{slot}]'
                attrs = {
                    'access': access,
                    'target': value if e.kind == 'StorageRead' else None,
                    'value': value_normalized if e.kind == 'StorageWrite' else None,
                    'value_yul': value if e.kind == 'StorageWrite' else None,
                    'value_state_read': value_state_read,
                    'slot': slot,
                    'slot_versions': e.attrs.get('slot_versions'),
                    'unresolved_reason': 'unknown_storage_slot',
                    'solidity_like': self.storage_solidity_like(kind, access, value_normalized, e),
                    **self.nested_storage_read_attrs(e, access),
                }
                out.append(self.ov(kind, e.effect_id, e.stmt_refs, self.clean(
                    self.with_collapsed_path_storage_attrs(attrs, collapsed_path_overlay)
                )))
        return out

    @staticmethod
    def with_collapsed_path_storage_attrs(attrs: dict[str, Any], path_overlay: SemanticOverlay | None) -> dict[str, Any]:
        if not path_overlay:
            return attrs
        merged = dict(attrs)
        path_attrs = path_overlay.attrs
        candidates = path_attrs.get('candidates') or []
        merged['path_conditioned_overlay_collapsed'] = True
        merged['path_states'] = list(dict.fromkeys([
            *list(merged.get('path_states') or []),
            *list(path_attrs.get('path_states') or []),
        ]))
        merged['path_candidates'] = candidates
        merged['slot_versions'] = list(dict.fromkeys([
            *list(merged.get('slot_versions') or []),
            *list(path_attrs.get('slot_versions') or []),
        ]))
        merged['notes'] = list(dict.fromkeys([
            *list(merged.get('notes') or []),
            'equivalent_path_conditioned_storage_candidates_collapsed',
        ]))
        return merged

    def direct_state_slot_alias(self, type_env: Any, effect: EffectNode, value_defs_by_version: dict[str, EffectNode]) -> Any | None:
        for version in effect.attrs.get('slot_versions') or []:
            value_def = value_defs_by_version.get(str(version))
            if not value_def:
                continue
            state = getattr(type_env, 'state_var_by_slot', lambda _slot: None)(value_def.attrs.get('value'))
            if state:
                return state
        return None

    @staticmethod
    def value_defs_by_version(effects: list[EffectNode]) -> dict[str, EffectNode]:
        out: dict[str, EffectNode] = {}
        for effect in effects:
            if effect.kind != 'ValueDef':
                continue
            versions = effect.attrs.get('target_versions') or {}
            for names in versions.values():
                for version in names or []:
                    out[str(version)] = effect
        return out

    @staticmethod
    def value_defs_by_name(effects: list[EffectNode]) -> dict[str, list[EffectNode]]:
        out: dict[str, list[EffectNode]] = {}
        for effect in effects:
            if effect.kind != 'ValueDef':
                continue
            for target in effect.attrs.get('targets') or []:
                out.setdefault(str(target), []).append(effect)
        return out

    def direct_storage_slot_info(self, type_env: Any, effect: EffectNode, value_defs_by_version: dict[str, EffectNode]) -> dict[str, Any] | None:
        slot = effect.attrs.get('slot')
        direct = self.constant_slot_info(type_env, slot)
        if direct:
            direct['slot'] = slot
            return direct
        for version in effect.attrs.get('slot_versions') or []:
            value_def = value_defs_by_version.get(str(version))
            if not value_def:
                continue
            value = value_def.attrs.get('value')
            resolved = self.constant_slot_info(type_env, value)
            if resolved:
                resolved['slot'] = slot
                resolved['via_value_def'] = value_def.effect_id
                resolved['via_version'] = version
                return resolved
        return None

    @staticmethod
    def constant_slot_info(type_env: Any, expr: Any) -> dict[str, Any] | None:
        text = str(expr or '').strip()
        if not text:
            return None
        value = getattr(type_env, 'constant_value', lambda _name: None)(text)
        if value is not None and str(value) != '':
            item = getattr(type_env, 'constant', lambda _name: None)(text) or {}
            return {'name': text, 'value': value, 'type_string': item.get('type_string'), 'kind': 'constant_storage_slot'}
        folded = SemanticOverlayBuilder.constant_expr_slot_info(type_env, text)
        if folded:
            return folded
        return None

    @staticmethod
    def constant_expr_slot_info(type_env: Any, expr: str) -> dict[str, Any] | None:
        folded = SemanticOverlayBuilder.eval_uint256_constant_expr(type_env, expr, set())
        if folded is None:
            return None
        value_hex = f"0x{folded:064x}"
        matched_name = None
        constants = getattr(type_env, 'constants', {}) or {}
        for name, item in constants.items():
            value = SemanticOverlayBuilder.eval_uint256_constant_expr(type_env, item.get('value'), {name})
            if value == folded:
                matched_name = name
                break
        return {
            'name': matched_name or expr,
            'value': value_hex,
            'type_string': (constants.get(matched_name) or {}).get('type_string') if matched_name else None,
            'kind': 'constant_expr_storage_slot',
            'slot_expression': expr,
            'matched_constant': matched_name,
        }

    @staticmethod
    def eval_uint256_constant_expr(type_env: Any, expr: Any, seen: set[str] | None = None) -> int | None:
        seen = seen or set()
        text = str(expr or '').strip()
        if not text:
            return None
        while text.startswith('(') and text.endswith(')'):
            stripped = SemanticOverlayBuilder.strip_outer_parens(text)
            if stripped == text:
                break
            text = stripped
        literal = parse_int_literal(text)
        if literal is not None:
            return literal & ((1 << 256) - 1)
        if text in seen:
            return None
        value = getattr(type_env, 'constant_value', lambda _name: None)(text)
        if value is not None and str(value) != '':
            return SemanticOverlayBuilder.eval_uint256_constant_expr(type_env, value, seen | {text})
        name, args = call_parts(text)
        if name == 'not' and len(args) == 1:
            inner = SemanticOverlayBuilder.eval_uint256_constant_expr(type_env, args[0], seen)
            return None if inner is None else (((1 << 256) - 1) ^ inner)
        if name in {'add', 'sub', 'mul', 'div', 'mod', 'and', 'or', 'xor', 'shl', 'shr'} and len(args) == 2:
            left = SemanticOverlayBuilder.eval_uint256_constant_expr(type_env, args[0], seen)
            right = SemanticOverlayBuilder.eval_uint256_constant_expr(type_env, args[1], seen)
            if left is None or right is None:
                return None
            mask = (1 << 256) - 1
            if name == 'add':
                return (left + right) & mask
            if name == 'sub':
                return (left - right) & mask
            if name == 'mul':
                return (left * right) & mask
            if name == 'div':
                return None if right == 0 else left // right
            if name == 'mod':
                return None if right == 0 else left % right
            if name == 'and':
                return left & right
            if name == 'or':
                return left | right
            if name == 'xor':
                return left ^ right
            if name == 'shl':
                return (right << left) & mask
            if name == 'shr':
                return right >> left
        return None

    def direct_storage_slot_info_for_expr(
        self,
        type_env: Any,
        slot_expr: Any,
        value_defs_by_name: dict[str, list[EffectNode]],
    ) -> dict[str, Any] | None:
        direct = self.constant_slot_info(type_env, slot_expr)
        if direct:
            direct['slot'] = slot_expr
            return direct
        text = str(slot_expr or '').strip()
        if not text:
            return None
        matches = []
        for value_def in value_defs_by_name.get(text, []):
            state = getattr(type_env, 'state_var_by_slot', lambda _slot: None)(value_def.attrs.get('value'))
            if state:
                return {
                    'name': state.name,
                    'value': value_def.attrs.get('value'),
                    'type_string': getattr(state, 'type_string', None),
                    'kind': 'state_variable_slot_alias',
                    'slot': slot_expr,
                    'via_value_def': value_def.effect_id,
                }
            resolved = self.constant_slot_info(type_env, value_def.attrs.get('value'))
            if not resolved:
                continue
            resolved = dict(resolved)
            resolved['slot'] = slot_expr
            resolved['via_value_def'] = value_def.effect_id
            matches.append(resolved)
        if not matches:
            return None
        names = {item.get('name') for item in matches}
        values = {item.get('value') for item in matches}
        if len(names) == 1 and len(values) == 1:
            return matches[0]
        return None

    def manual_slot_state_read_from_expr(
        self,
        type_env: Any,
        expr: Any,
        value_defs_by_name: dict[str, list[EffectNode]],
    ) -> dict[str, Any] | None:
        name, args = call_parts(str(expr or '').strip())
        if name != 'sload' or len(args) != 1:
            return None
        slot_expr = args[0].strip()
        direct_slot = self.direct_storage_slot_info_for_expr(type_env, slot_expr, value_defs_by_name)
        if not direct_slot:
            return None
        state_alias = direct_slot.get('kind') == 'state_variable_slot_alias'
        access = str(direct_slot.get('name')) if state_alias else f"storage[{direct_slot.get('name') or direct_slot.get('slot')}]"
        return self.clean({
            'original': str(expr),
            'access': access,
            'slot': slot_expr,
            'slot_constant': direct_slot.get('name'),
            'slot_value': direct_slot.get('value'),
            'storage_model': 'direct_state_slot_alias' if state_alias else 'manual_constant_slot',
            'state_access': True,
            'state_mutation': False,
            'state_variable': direct_slot.get('name') if state_alias else None,
            'variable_name_inferred': False,
            'solidity_like': access if state_alias else f"{access} /* state read, manual slot */",
            'notes': ['direct_state_slot_alias', 'reads_contract_state'] if state_alias else ['constant_storage_slot', 'manual_storage_layout', 'reads_contract_state'],
        })

    def normalize_expression_with_state_reads(
        self,
        type_env: Any,
        expr: Any,
        value_defs_by_name: dict[str, list[EffectNode]],
        nested_read_accesses: dict[str, str] | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        text = str(expr or '').strip()
        if not text:
            return text, None
        nested_access = (nested_read_accesses or {}).get(text)
        if nested_access:
            return nested_access, {
                'original': text,
                'access': nested_access,
                'state_access': True,
                'state_mutation': False,
                'solidity_like': nested_access,
                'notes': ['nested_storage_read', 'reads_contract_state'],
            }
        direct_state = self.direct_state_read_from_sload_expr(type_env, expr)
        if direct_state:
            return str(direct_state['solidity_like']), direct_state
        state_read = self.manual_slot_state_read_from_expr(type_env, expr, value_defs_by_name)
        if state_read:
            return str(state_read['solidity_like']), state_read
        array_read = self.memory_array_read_from_mload_expr(type_env, text)
        if array_read:
            return str(array_read['access']), None
        name, args = call_parts(text)
        if not name:
            return normalize_expr(expr), None
        rendered_args: list[str] = []
        first_state_read = None
        for arg in args:
            rendered, nested_state_read = self.normalize_expression_with_state_reads(
                type_env,
                arg,
                value_defs_by_name,
                nested_read_accesses,
            )
            rendered_args.append(rendered)
            if nested_state_read and first_state_read is None:
                first_state_read = nested_state_read
        return self.render_normalized_call(name, rendered_args, text), first_state_read

    def direct_state_read_from_sload_expr(self, type_env: Any, expr: Any) -> dict[str, Any] | None:
        name, args = call_parts(str(expr or '').strip())
        if name != 'sload' or len(args) != 1:
            return None
        slot_expr = args[0].strip()
        state = getattr(type_env, 'state_var_by_slot', lambda _slot: None)(slot_expr)
        if not state:
            return None
        return self.clean({
            'original': str(expr),
            'access': state.name,
            'slot': slot_expr,
            'state_variable': state.name,
            'storage_model': 'direct_state_slot',
            'state_access': True,
            'state_mutation': False,
            'solidity_like': state.name,
            'notes': ['direct_state_slot', 'reads_contract_state'],
        })

    def normalize_expression_with_memory_arrays(self, type_env: Any, expr: Any) -> str:
        text = str(expr or '').strip()
        if not text:
            return text
        array_read = self.memory_array_read_from_mload_expr(type_env, text)
        if array_read:
            return str(array_read['access'])
        name, args = call_parts(text)
        if not name:
            return normalize_expr(expr)
        rendered_args = [self.normalize_expression_with_memory_arrays(type_env, arg) for arg in args]
        return self.render_normalized_call(name, rendered_args, text)

    @staticmethod
    def render_normalized_call(name: str, args: list[str], original: str) -> str:
        if name == 'add' and len(args) == 2:
            return f"({args[0]} + {args[1]})"
        if name == 'sub' and len(args) == 2:
            return f"({args[0]} - {args[1]})"
        if name == 'mul' and len(args) == 2:
            return f"({args[0]} * {args[1]})"
        if name == 'div' and len(args) == 2:
            return f"({args[0]} / {args[1]})"
        if name == 'mod' and len(args) == 2:
            return f"({args[0]} % {args[1]})"
        if name == 'shl' and len(args) == 2:
            return f"({args[1]} << {args[0]})"
        if name == 'shr' and len(args) == 2:
            return f"({args[1]} >> {args[0]})"
        if name == 'and' and len(args) == 2:
            return f"({args[0]} & {args[1]})"
        if name == 'or' and len(args) == 2:
            return f"({args[0]} | {args[1]})"
        if name == 'xor' and len(args) == 2:
            return f"({args[0]} ^ {args[1]})"
        if name == 'not' and len(args) == 1:
            return f"(~{args[0]})"
        if name == 'eq' and len(args) == 2:
            return f"({args[0]} == {args[1]})"
        if name == 'lt' and len(args) == 2:
            return f"({args[0]} < {args[1]})"
        if name == 'gt' and len(args) == 2:
            return f"({args[0]} > {args[1]})"
        if name == 'iszero' and len(args) == 1:
            return f"({args[0]} == 0)"
        if name == 'caller' and not args:
            return 'msg.sender'
        if name == 'timestamp' and not args:
            return 'block.timestamp'
        if name == 'gas' and not args:
            return 'gasleft()'
        try:
            return normalize_expr(original)
        except Exception:
            return f"{name}({', '.join(args)})"

    @staticmethod
    def resolve_constant_expr(type_env: Any, expr: Any) -> Any:
        text = str(expr or '').strip()
        if not text:
            return expr
        value = getattr(type_env, 'constant_value', lambda _name: None)(text)
        return value if value else expr

    def path_conditioned_storage_overlay(
        self,
        effect: EffectNode,
        type_env: Any,
        version_keys: list[str],
        slot_expr_by_var: dict[str, dict[str, Any]],
        hash_effect_by_var: dict[str, EffectNode],
        value_defs_by_name: dict[str, list[EffectNode]],
    ) -> SemanticOverlay | None:
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        value = effect.attrs.get('value')
        value_normalized, value_state_read = self.storage_write_value(type_env, effect, value_defs_by_name)
        candidate_conditions = self.storage_candidate_conditions_by_version(effect, version_keys, hash_effect_by_var)
        for key in version_keys:
            if key in seen:
                continue
            seen.add(key)
            condition = candidate_conditions.get(key)
            slot_expr = slot_expr_by_var.get(key)
            if slot_expr:
                access = slot_expr['access']
                manual_packed = slot_expr.get('slot_kind') == 'manual_packed_hash_slot'
                kind = (
                    ('StateVariableRead' if effect.kind == 'StorageRead' else 'StateVariableWrite')
                    if manual_packed
                    else ('MappingRead' if effect.kind == 'StorageRead' else 'MappingWrite')
                )
                slot_effect = hash_effect_by_var.get(key)
                candidates.append(self.clean({
                    'slot_key': key,
                    'status': 'resolved',
                    'condition': condition,
                    'overlay_kind': kind,
                    'access': access,
                    'state_variable': slot_expr.get('state_variable'),
                    'storage_reference': slot_expr.get('storage_reference'),
                    'storage_reference_type': slot_expr.get('storage_reference_type'),
                    'storage_reference_kind': slot_expr.get('storage_reference_kind'),
                    'storage_field': slot_expr.get('storage_field'),
                    'key': slot_expr.get('key'),
                    'keys': slot_expr.get('keys'),
                    'slot_effect': slot_effect.effect_id if slot_effect else None,
                    'slot_derivation': self.manual_packed_slot_derivation(slot_expr, key, slot_effect) if manual_packed else None,
                    'result_type': slot_expr.get('result_type'),
                    'terminal_storage_value': slot_expr.get('terminal_storage_value'),
                    'storage_model': 'manual_packed_hash_slot' if manual_packed else None,
                    'state_access': True if manual_packed else None,
                    'state_mutation': effect.kind == 'StorageWrite' if manual_packed else None,
                    'variable_name_inferred': False if manual_packed else None,
                    'solidity_like': self.storage_solidity_like(kind, access, value_normalized, effect),
                    'value': value_normalized if effect.kind == 'StorageWrite' else None,
                    'value_yul': value if effect.kind == 'StorageWrite' else None,
                    'value_state_read': value_state_read,
                    'notes': slot_expr.get('notes', []),
                }))
                continue
            direct_state = type_env.state_var_by_slot(key)
            if direct_state:
                kind = 'StateVariableRead' if effect.kind == 'StorageRead' else 'StateVariableWrite'
                candidates.append(self.clean({
                    'slot_key': key,
                    'status': 'resolved',
                    'condition': condition,
                    'overlay_kind': kind,
                    'access': direct_state.name,
                    'state_variable': direct_state.name,
                    'solidity_like': self.storage_solidity_like(kind, direct_state.name, value_normalized, effect),
                    'value': value_normalized if effect.kind == 'StorageWrite' else None,
                    'value_yul': value if effect.kind == 'StorageWrite' else None,
                    'value_state_read': value_state_read,
                }))
                continue
            candidates.append({
                'slot_key': key,
                'status': 'unresolved',
                'condition': condition,
                'overlay_kind': 'StateVariableRead' if effect.kind == 'StorageRead' else 'StateVariableWrite',
                'access': f'storage[{key}]',
                'unresolved_reason': 'unknown_storage_slot_version',
            })
        if not candidates:
            return None
        candidates = self.dedupe_equivalent_storage_candidates(candidates)
        overlay_kind = 'PathConditionedStorageRead' if effect.kind == 'StorageRead' else 'PathConditionedStorageWrite'
        attrs = self.clean({
            'slot': effect.attrs.get('slot'),
            'slot_versions': version_keys,
            'target': value if effect.kind == 'StorageRead' else None,
            'value': value_normalized if effect.kind == 'StorageWrite' else None,
            'value_yul': value if effect.kind == 'StorageWrite' else None,
            'value_state_read': value_state_read,
            'path_states': effect.attrs.get('path_states'),
            'candidates': candidates,
            'note': 'storage_effect_has_multiple_ssa_slot_versions',
        })
        return self.ov(overlay_kind, effect.effect_id, effect.stmt_refs, attrs)

    @staticmethod
    def dedupe_equivalent_storage_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge SSA variants only after they project to identical semantics.

        Distinct conditions remain distinct candidates. Source slot versions
        are retained in ``slot_keys`` so the semantic model does not lose the
        underlying SSA evidence when equivalent projections are folded.
        """
        out: list[dict[str, Any]] = []
        by_semantics: dict[tuple[str, ...], dict[str, Any]] = {}
        identity_fields = (
            'status', 'condition', 'overlay_kind', 'access', 'target',
            'value', 'value_yul', 'state_variable', 'key', 'result_type',
            'terminal_storage_value', 'storage_model', 'unresolved_reason',
        )
        for candidate in candidates:
            if candidate.get('status') != 'resolved':
                out.append(candidate)
                continue
            identity = tuple(str(candidate.get(field)) for field in identity_fields)
            existing = by_semantics.get(identity)
            if existing is None:
                clone = dict(candidate)
                slot_key = clone.get('slot_key')
                if slot_key is not None:
                    clone['slot_keys'] = list(dict.fromkeys([
                        *list(clone.get('slot_keys') or []),
                        str(slot_key),
                    ]))
                by_semantics[identity] = clone
                out.append(clone)
                continue
            slot_keys = [
                *list(existing.get('slot_keys') or []),
                *list(candidate.get('slot_keys') or []),
            ]
            if candidate.get('slot_key') is not None:
                slot_keys.append(str(candidate.get('slot_key')))
            if slot_keys:
                existing['slot_keys'] = list(dict.fromkeys(map(str, slot_keys)))
            slot_effects = [
                *list(existing.get('slot_effects') or []),
                *list(candidate.get('slot_effects') or []),
            ]
            if existing.get('slot_effect') is not None:
                slot_effects.append(str(existing.get('slot_effect')))
            if candidate.get('slot_effect') is not None:
                slot_effects.append(str(candidate.get('slot_effect')))
            if slot_effects:
                existing['slot_effects'] = list(dict.fromkeys(map(str, slot_effects)))
            existing['notes'] = list(dict.fromkeys([
                *list(existing.get('notes') or []),
                *list(candidate.get('notes') or []),
                'equivalent_ssa_storage_candidates_merged',
            ]))
            existing['merged_candidate_count'] = int(existing.get('merged_candidate_count') or 1) + 1
        return out

    @classmethod
    def storage_candidate_conditions_by_version(
        cls,
        effect: EffectNode,
        version_keys: list[str],
        hash_effect_by_var: dict[str, EffectNode],
    ) -> dict[str, str | None]:
        effect_paths = cls.path_states(effect)
        unique_keys = list(dict.fromkeys(str(key) for key in version_keys))
        unique_paths = list(dict.fromkeys(str(path) for path in effect_paths if path))
        if len(unique_keys) > 1 and len(unique_paths) == len(unique_keys):
            return {key: unique_paths[index] for index, key in enumerate(unique_keys)}

        out: dict[str, str | None] = {}
        for key in unique_keys:
            slot_effect = hash_effect_by_var.get(key)
            slot_paths = cls.path_states(slot_effect) if slot_effect else []
            merged: list[str | None] = []
            if slot_paths:
                for slot_path in slot_paths:
                    for effect_path in effect_paths or [None]:
                        condition = cls.merge_compatible_conditions(effect_path, slot_path)
                        if condition is not None and condition not in merged:
                            merged.append(condition)
            if len(merged) == 1:
                out[key] = merged[0]
            elif not merged and len(effect_paths) == 1:
                out[key] = effect_paths[0]
            else:
                out[key] = None
        return out

    def mapping_slot_expr(self, type_env: Any, effect: EffectNode, known: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
        words = words_from_memory_query(effect.attrs.get('memory_read'), prefer_known_branch=False)
        if len(words) < 2:
            return None
        key = words[0].get('value')
        base = words[1].get('value')
        if self.is_unknown_value(key) or self.is_unknown_value(base):
            return self.path_conditioned_mapping_slot_expr_from_words(type_env, effect, words[:2], known)
        notes: list[str] = []
        for w in words[:2]:
            if w.get('discarded_unknown_branch'):
                notes.append('discarded_unknown_memory_branch')
        key_norm = self.value_text(key)
        base_text = self.value_text(base)
        return self.mapping_slot_expr_from_key_base(type_env, key_norm, base_text, known, words[:2], notes, self.word_value_keys(words[1]))

    def mapping_slot_expr_from_key_base(
        self,
        type_env: Any,
        key_norm: str,
        base_text: str,
        known: dict[str, dict[str, Any]],
        resolved_inputs: Any,
        notes: list[str] | None = None,
        base_lookup_keys: list[str] | None = None,
    ) -> dict[str, Any] | None:
        notes = notes or []
        key_norm = self.storage_key_expr(type_env, key_norm)
        base_key = self.first_known_key(base_lookup_keys or [base_text], known)
        state_var = None
        if base_key:
            prev = known[base_key]
            if prev.get('path_candidates'):
                return None
            access = f"{prev['access']}[{key_norm}]"
            state_var = prev.get('state_variable')
            result_type = self.mapping_value_type(prev.get('result_type'))
            return {
                'access': access,
                'key': key_norm,
                'keys': [*(prev.get('keys') or []), key_norm],
                'base': base_text,
                'base_key': base_key,
                'state_variable': state_var,
                'storage_reference': prev.get('storage_reference'),
                'storage_reference_type': prev.get('storage_reference_type'),
                'storage_reference_kind': prev.get('storage_reference_kind'),
                'storage_field': prev.get('storage_field'),
                'slot_kind': 'nested_mapping_slot',
                'result_type': result_type,
                'terminal_storage_value': not self.is_mapping_type_text(result_type),
                'resolved_inputs': resolved_inputs,
                'notes': notes,
            }
        var = type_env.state_var_by_slot(base_text)
        if var:
            state_var = var.name
            access = f'{var.name}[{key_norm}]'
            base_key = base_text
            result_type = self.mapping_value_type(getattr(var, 'type_string', None))
            return {
                'access': access,
                'key': key_norm,
                'keys': [key_norm],
                'base': base_text,
                'base_key': base_key,
                'state_variable': state_var,
                'slot_kind': 'mapping_slot',
                'result_type': result_type,
                'terminal_storage_value': not self.is_mapping_type_text(result_type),
                'resolved_inputs': resolved_inputs,
                'notes': notes,
            }
        storage_ref = self.storage_ref_mapping_access(type_env, base_text, key_norm)
        if storage_ref:
            result_type = self.mapping_value_type(storage_ref.get('mapping_type'))
            return {
                'access': storage_ref['access'],
                'key': key_norm,
                'keys': [key_norm],
                'base': base_text,
                'base_key': base_text,
                'state_variable': None,
                'storage_reference': storage_ref.get('root'),
                'storage_reference_type': storage_ref.get('root_type'),
                'storage_reference_kind': storage_ref.get('kind'),
                'storage_field': self.field_ref(storage_ref.get('field')),
                'slot_kind': 'storage_ref_mapping_slot',
                'result_type': result_type,
                'terminal_storage_value': not self.is_mapping_type_text(result_type),
                'resolved_inputs': resolved_inputs,
                'notes': list(dict.fromkeys(notes + ['storage_reference_mapping_slot'])),
            }
        return None

    def path_conditioned_mapping_slot_expr_from_words(
        self,
        type_env: Any,
        effect: EffectNode,
        words: list[dict[str, Any]],
        known: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        if len(words) < 2:
            return None
        candidates: list[dict[str, Any]] = []
        for key_candidate, base_candidate in self.aligned_word_branch_candidates(words[0], words[1]):
            condition = self.join_conditions(key_candidate.get('path'), base_candidate.get('path'))
            key_value = key_candidate.get('value')
            base_value = base_candidate.get('value')
            if self.is_unknown_value(key_value) or self.is_unknown_value(base_value):
                candidates.append(self.clean({
                    'status': 'unresolved',
                    'condition': condition,
                    'reason': 'unknown_memory_word_candidate',
                    'key_value': key_value,
                    'base_value': base_value,
                }))
                continue
            key_norm = self.value_text(key_value)
            base_text = self.value_text(base_value)
            resolved = self.mapping_slot_expr_from_key_base(
                type_env,
                key_norm,
                base_text,
                known,
                [key_candidate, base_candidate],
                ['memoryssa_word_branch_candidate'],
                self.word_value_keys(base_candidate),
            )
            if resolved:
                candidates.append(self.clean({
                    'status': 'resolved',
                    'condition': condition,
                    **resolved,
                }))
                continue
            nested = self.path_conditioned_nested_mapping_slot_expr(type_env, key_norm, base_text, condition, known, [key_candidate, base_candidate])
            if nested:
                candidates.extend(nested)
                continue
            candidates.append(self.clean({
                'status': 'unresolved',
                'condition': condition,
                'reason': 'unresolved_word_candidate_mapping_slot',
                'key_value': key_value,
                'base_value': base_value,
            }))
        if not candidates:
            return None
        resolved_accesses = {item.get('access') for item in candidates if item.get('status') == 'resolved'}
        all_resolved = all(item.get('status') == 'resolved' for item in candidates)
        if len(resolved_accesses) == 1 and all_resolved:
            only = next(item for item in candidates if item.get('status') == 'resolved')
            return self.clean({
                key: only.get(key)
                for key in (
                    'access', 'key', 'keys', 'base', 'base_key', 'state_variable',
                    'storage_reference', 'storage_reference_type',
                    'storage_reference_kind', 'storage_field', 'slot_kind',
                    'resolved_inputs', 'notes',
                )
            })
        if not resolved_accesses:
            return None
        return self.clean({
            'access': 'path_conditioned_storage',
            'key': 'path_conditioned',
            'base': 'memoryssa_word_candidates',
            'base_key': None,
            'slot_kind': 'path_conditioned_mapping_slot',
            'path_candidates': candidates,
            'notes': ['memoryssa_word_branch_candidates', 'path_sensitive_memoryssa'],
        })

    def path_conditioned_nested_mapping_slot_expr(
        self,
        type_env: Any,
        key_norm: str,
        base_text: str,
        condition: str | None,
        known: dict[str, dict[str, Any]],
        resolved_inputs: Any,
    ) -> list[dict[str, Any]]:
        base_key = self.first_known_key([base_text], known)
        if not base_key:
            return []
        prev = known[base_key]
        out: list[dict[str, Any]] = []
        for base_candidate in prev.get('path_candidates') or []:
            if base_candidate.get('status') != 'resolved':
                continue
            merged_condition = self.merge_compatible_conditions(condition, base_candidate.get('condition'))
            if merged_condition is None:
                continue
            access = f"{base_candidate.get('access')}[{key_norm}]"
            result_type = self.mapping_value_type(base_candidate.get('result_type'))
            out.append(self.clean({
                'status': 'resolved',
                'condition': merged_condition,
                'access': access,
                'key': key_norm,
                'keys': [*(base_candidate.get('keys') or []), key_norm],
                'base': base_text,
                'base_key': base_key,
                'state_variable': base_candidate.get('state_variable'),
                'storage_reference': base_candidate.get('storage_reference'),
                'storage_reference_type': base_candidate.get('storage_reference_type'),
                'storage_reference_kind': base_candidate.get('storage_reference_kind'),
                'storage_field': base_candidate.get('storage_field'),
                'slot_kind': 'nested_mapping_slot',
                'result_type': result_type,
                'terminal_storage_value': not self.is_mapping_type_text(result_type),
                'resolved_inputs': resolved_inputs,
                'notes': list(dict.fromkeys((base_candidate.get('notes') or []) + ['nested_mapping_slot_from_memoryssa_word_candidate'])),
            }))
        return out

    @classmethod
    def aligned_word_branch_candidates(cls, key_word: dict[str, Any], base_word: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        key_candidates = key_word.get('branch_candidates') or []
        base_candidates = base_word.get('branch_candidates') or []
        if not key_candidates or not base_candidates:
            return []
        out: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for key_candidate in key_candidates:
            for base_candidate in base_candidates:
                if cls.merge_compatible_conditions(key_candidate.get('path'), base_candidate.get('path')) is None:
                    continue
                out.append((key_candidate, base_candidate))
        return out

    @classmethod
    def merge_compatible_conditions(cls, left: Any, right: Any) -> str | None:
        parts: list[str] = []
        for condition in (left, right):
            for part in cls.condition_parts(condition):
                if part not in parts:
                    parts.append(part)
        atoms = set(parts)
        for atom in list(atoms):
            if cls.negated_condition(atom) in atoms:
                return None
        return ' && '.join(parts) if parts else None

    @classmethod
    def join_conditions(cls, left: Any, right: Any) -> str | None:
        return cls.merge_compatible_conditions(left, right)

    @staticmethod
    def condition_parts(condition: Any) -> list[str]:
        text = str(condition or '').strip()
        if not text or text == 'entry':
            return []
        return [part.strip() for part in text.split(' && ') if part.strip() and part.strip() != 'entry']

    @classmethod
    def condition_text(cls, condition: Any) -> str | None:
        parts = cls.condition_parts(condition)
        return ' && '.join(parts) if parts else None

    @staticmethod
    def negated_condition(condition: str) -> str:
        text = str(condition or '').strip()
        if text.startswith('!(') and text.endswith(')'):
            return text[2:-1].strip()
        return f'!({text})'

    def packed_hash_slot_expr(
        self,
        type_env: Any,
        effect: EffectNode,
        known: dict[str, dict[str, Any]] | None = None,
        value_defs_by_name: dict[str, list[EffectNode]] | None = None,
    ) -> dict[str, Any] | None:
        known = known or {}
        value_defs_by_name = value_defs_by_name or {}
        sink_resolution = packed_hash_sink_resolution(effect)
        if sink_resolution and sink_resolution.path_sensitive:
            candidates = []
            for path in sink_resolution.path_resolutions:
                slot_arg = path.arg_resolutions.get('slot')
                if path.status != 'resolved' or not slot_arg or not slot_arg.normalized:
                    candidates.append(self.clean({
                        'status': 'unresolved',
                        'condition': path.condition,
                        'reason': path.reason or 'unresolved_path_memory_slice',
                    }))
                    continue
                access = f"storage[{slot_arg.normalized}]"
                memory_slice = slot_arg.memory_slice or {}
                slices = memory_slice.get('slices') or []
                parts = [str(item.get('extraction')) for item in slices if item.get('extraction')]
                expanded_parts = self.packed_single_word_slot_parts(type_env, slices, value_defs_by_name)
                if expanded_parts:
                    parts = expanded_parts
                    access = f"storage[keccak256(abi.encodePacked({', '.join(parts)}))]"
                notes = list(dict.fromkeys(['byte_axis_memory_slice', 'path_sensitive_sink', *(slot_arg.notes or [])]))
                mapping_slot = self.byte_slice_mapping_slot_expr(type_env, parts, memory_slice, notes, sink_resolution.to_dict(), known)
                if mapping_slot:
                    candidates.append(self.clean({
                        'status': 'resolved',
                        'condition': path.condition,
                        **mapping_slot,
                    }))
                    continue
                notes = list(dict.fromkeys(['manual_packed_hash_slot', *notes]))
                candidates.append(self.clean({
                    'status': 'resolved',
                    'condition': path.condition,
                    'access': access,
                    'slot_kind': 'manual_packed_hash_slot',
                    'packed_inputs': parts,
                    'byte_slice': memory_slice,
                    'slot_derivation': {
                        'kind': 'manual_packed_hash_slot',
                        'hash': 'keccak256',
                        'encoding': self.byte_slice_hash_encoding(memory_slice),
                        'expression': access,
                        'slot_key': slot_arg.ssa_key,
                        'packed_inputs': parts,
                        'byte_slice': memory_slice,
                        'notes': notes,
                    },
                    'notes': notes,
                }))
            resolved_accesses = {item.get('access') for item in candidates if item.get('status') == 'resolved'}
            all_resolved = all(item.get('status') == 'resolved' for item in candidates)
            if len(resolved_accesses) == 1 and all_resolved:
                only = next(item for item in candidates if item.get('status') == 'resolved')
                if only.get('slot_kind') != 'manual_packed_hash_slot':
                    return self.clean({
                        'access': only.get('access'),
                        'key': only.get('key'),
                        'keys': only.get('keys'),
                        'base': only.get('base'),
                        'base_key': only.get('base_key'),
                        'state_variable': only.get('state_variable'),
                        'storage_reference': only.get('storage_reference'),
                        'storage_reference_type': only.get('storage_reference_type'),
                        'storage_reference_kind': only.get('storage_reference_kind'),
                        'storage_field': only.get('storage_field'),
                        'slot_kind': only.get('slot_kind'),
                        'resolved_inputs': only.get('resolved_inputs'),
                        'byte_slice': only.get('byte_slice'),
                        'packed_semantics': only.get('packed_semantics'),
                        'slot_derivation': only.get('slot_derivation'),
                        'sink_resolution': sink_resolution.to_dict(),
                        'notes': list(dict.fromkeys((only.get('notes') or []) + ['path_sensitive_sink_collapsed_same_access'])),
                    })
                return self.clean({
                    'access': only.get('access'),
                    'key': ', '.join(only.get('packed_inputs') or []),
                    'base': 'keccak256_packed_memory',
                    'base_key': None,
                    'slot_kind': 'manual_packed_hash_slot',
                    'resolved_inputs': only.get('packed_inputs'),
                    'byte_slice': only.get('byte_slice'),
                    'packed_semantics': only.get('packed_inputs'),
                    'sink_resolution': sink_resolution.to_dict(),
                    'notes': list(dict.fromkeys((only.get('notes') or []) + ['path_sensitive_sink_collapsed_same_access'])),
                })
            return self.clean({
                'access': 'path_conditioned_storage',
                'key': 'path_conditioned',
                'base': 'keccak256_packed_memory',
                'base_key': None,
                'slot_kind': 'path_conditioned_manual_packed_hash_slot',
                'path_candidates': candidates,
                'sink_resolution': sink_resolution.to_dict(),
                'notes': ['manual_packed_hash_slot', 'byte_axis_memory_slice', 'path_sensitive_sink'],
            })
        memory_read = effect.attrs.get('memory_read') or {}
        byte_slice = memory_read.get('byte_slice') or {}
        if not byte_slice or not byte_slice.get('complete'):
            return None
        slices = byte_slice.get('slices') or []
        if not slices and byte_slice.get('path_slices'):
            return self.path_sensitive_packed_hash_slot_expr(
                type_env,
                effect,
                byte_slice,
                known,
                value_defs_by_name,
                sink_resolution.to_dict() if sink_resolution else None,
            )
        if not slices or any(not item.get('extraction') for item in slices):
            return None
        parts = [str(item.get('extraction')) for item in slices]
        expanded_parts = self.packed_single_word_slot_parts(type_env, slices, value_defs_by_name)
        expanded_single_word = bool(expanded_parts)
        if expanded_parts:
            parts = expanded_parts
        if len(parts) == 1 and int(byte_slice.get('size') or 0) == 32:
            return None
        mapping_slot = self.byte_slice_mapping_slot_expr(
            type_env,
            parts,
            byte_slice,
            ['byte_axis_memory_slice'],
            sink_resolution.to_dict() if sink_resolution else None,
            known,
        )
        if mapping_slot:
            return mapping_slot
        encoding = 'abi.encodePacked' if expanded_single_word else self.byte_slice_hash_encoding(byte_slice)
        access = f"storage[keccak256({encoding}({', '.join(parts)}))]"
        return self.clean({
            'access': access,
            'key': ', '.join(parts),
            'base': 'keccak256_packed_memory',
            'base_key': None,
            'slot_kind': 'manual_packed_hash_slot',
            'resolved_inputs': parts,
            'byte_slice': byte_slice,
            'packed_semantics': parts,
            'sink_resolution': sink_resolution.to_dict() if sink_resolution else None,
            'encoding': encoding,
            'notes': ['manual_packed_hash_slot', 'byte_axis_memory_slice'],
        })

    def path_sensitive_packed_hash_slot_expr(
        self,
        type_env: Any,
        effect: EffectNode,
        byte_slice: dict[str, Any],
        known: dict[str, dict[str, Any]],
        value_defs_by_name: dict[str, list[EffectNode]],
        sink_resolution: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        keys = self.hash_result_keys(effect)
        candidates: list[dict[str, Any]] = []
        for index, path_item in enumerate(byte_slice.get('path_slices') or []):
            condition = self.condition_text(path_item.get('path'))
            slices = path_item.get('slices') or []
            if not path_item.get('complete') or not slices or any(not item.get('extraction') for item in slices):
                candidates.append(self.clean({
                    'status': 'unresolved',
                    'condition': condition,
                    'reason': 'incomplete_path_memory_slice',
                    'slot_key': keys[index] if index < len(keys) else None,
                }))
                continue
            parts = [str(item.get('extraction')) for item in slices]
            expanded_parts = self.packed_single_word_slot_parts(type_env, slices, value_defs_by_name)
            expanded_single_word = bool(expanded_parts)
            if expanded_parts:
                parts = expanded_parts
            notes = ['manual_packed_hash_slot', 'byte_axis_memory_slice', 'path_sensitive_memoryssa']
            mapping_slot = self.byte_slice_mapping_slot_expr(type_env, parts, {'slices': slices, 'size': path_item.get('size') or byte_slice.get('size')}, notes, sink_resolution, known)
            if mapping_slot:
                candidates.append(self.clean({
                    'status': 'resolved',
                    'condition': condition,
                    'slot_key': keys[index] if index < len(keys) else None,
                    **mapping_slot,
                }))
                continue
            if len(parts) == 1 and int(byte_slice.get('size') or 0) == 32:
                candidates.append(self.clean({
                    'status': 'unresolved',
                    'condition': condition,
                    'reason': 'single_word_hash_not_packed_slot_pattern',
                    'slot_key': keys[index] if index < len(keys) else None,
                    'resolved_inputs': parts,
                }))
                continue
            encoding = 'abi.encodePacked' if expanded_single_word else self.byte_slice_hash_encoding({'slices': slices, 'size': path_item.get('size') or byte_slice.get('size')})
            access = f"storage[keccak256({encoding}({', '.join(parts)}))]"
            candidates.append(self.clean({
                'status': 'resolved',
                'condition': condition,
                'slot_key': keys[index] if index < len(keys) else None,
                'access': access,
                'key': ', '.join(parts),
                'base': 'keccak256_packed_memory',
                'base_key': None,
                'slot_kind': 'manual_packed_hash_slot',
                'resolved_inputs': parts,
                'byte_slice': {'query_kind': 'PathMemoryByteSlice', 'path': path_item.get('path'), 'complete': True, 'slices': slices},
                'packed_semantics': parts,
                'slot_derivation': {
                    'kind': 'manual_packed_hash_slot',
                    'hash': 'keccak256',
                    'encoding': encoding,
                    'expression': access,
                    'slot_key': keys[index] if index < len(keys) else None,
                    'packed_inputs': parts,
                    'byte_slice': {'query_kind': 'PathMemoryByteSlice', 'path': path_item.get('path'), 'complete': True, 'slices': slices},
                    'notes': notes,
                },
                'notes': notes,
            }))
        if not candidates:
            return None
        resolved_accesses = {item.get('access') for item in candidates if item.get('status') == 'resolved'}
        all_resolved = all(item.get('status') == 'resolved' for item in candidates)
        if len(resolved_accesses) == 1 and all_resolved:
            only = next(item for item in candidates if item.get('status') == 'resolved')
            return self.clean({
                'access': only.get('access'),
                'key': only.get('key'),
                'keys': only.get('keys'),
                'base': only.get('base'),
                'base_key': only.get('base_key'),
                'slot_kind': only.get('slot_kind'),
                'resolved_inputs': only.get('resolved_inputs'),
                'byte_slice': only.get('byte_slice'),
                'packed_semantics': only.get('packed_semantics'),
                'slot_derivation': only.get('slot_derivation'),
                'sink_resolution': sink_resolution,
                'notes': list(dict.fromkeys((only.get('notes') or []) + ['path_sensitive_memoryssa_collapsed_same_access'])),
            })
        if not resolved_accesses:
            return None
        return self.clean({
            'access': 'path_conditioned_storage',
            'key': 'path_conditioned',
            'base': 'keccak256_packed_memory',
            'base_key': None,
            'slot_kind': 'path_conditioned_manual_packed_hash_slot',
            'path_candidates': candidates,
            'sink_resolution': sink_resolution,
            'notes': ['manual_packed_hash_slot', 'byte_axis_memory_slice', 'path_sensitive_memoryssa'],
        })

    def packed_single_word_slot_parts(
        self,
        type_env: Any,
        slices: list[dict[str, Any]],
        value_defs_by_name: dict[str, list[EffectNode]],
    ) -> list[str] | None:
        if len(slices) != 1:
            return None
        item = slices[0]
        try:
            if int(item.get('query_offset') or 0) != 0 or int(item.get('size') or 0) != 32:
                return None
        except Exception:
            return None
        raw_candidates = [
            item.get('source_value'),
            item.get('extraction'),
        ]
        for raw in raw_candidates:
            parts = self.packed_or_shift_seed_parts(type_env, raw, value_defs_by_name)
            if parts:
                return parts
        return None

    def packed_or_shift_seed_parts(
        self,
        type_env: Any,
        expr: Any,
        value_defs_by_name: dict[str, list[EffectNode]],
    ) -> list[str] | None:
        name, args = call_parts(str(expr or '').strip())
        if name != 'or' or len(args) != 2:
            return None
        for left, right in ((args[0], args[1]), (args[1], args[0])):
            address = self.address_from_shifted_word(type_env, left, value_defs_by_name)
            if not address:
                continue
            seed = normalize_expr(right)
            return [f'bytes20({address})', f'low_bytes({seed}, 12)']
        return None

    def address_from_shifted_word(
        self,
        type_env: Any,
        expr: Any,
        value_defs_by_name: dict[str, list[EffectNode]],
        seen: set[str] | None = None,
    ) -> str | None:
        seen = seen or set()
        text = str(expr or '').strip()
        name, args = call_parts(text)
        if name == 'shl' and len(args) == 2 and parse_int_literal(str(args[0]).strip()) == 96:
            return normalize_expr(args[1])
        if not self.simple_identifier_text(text) or text in seen:
            return None
        candidates = value_defs_by_name.get(text) or []
        if not candidates:
            return None
        resolved: list[str] = []
        for value_def in candidates:
            address = self.address_from_shifted_word(type_env, value_def.attrs.get('value'), value_defs_by_name, seen | {text})
            if address and address not in resolved:
                resolved.append(address)
        if len(resolved) == 1:
            return resolved[0]
        return None

    @staticmethod
    def simple_identifier_text(value: Any) -> bool:
        text = str(value or '').strip()
        if not text:
            return False
        return (text[0].isalpha() or text[0] in {'_', '$'}) and all(ch.isalnum() or ch in {'_', '$'} for ch in text)

    def byte_slice_mapping_slot_expr(
        self,
        type_env: Any,
        parts: list[str],
        byte_slice: dict[str, Any],
        notes: list[str] | None = None,
        sink_resolution: dict[str, Any] | None = None,
        known: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        if len(parts) != 2 or not self.is_full_word_pair(byte_slice):
            return None
        known = known or {}
        key, base = parts[0], parts[1]
        key_norm = self.storage_key_expr(type_env, normalize_expr(key))
        base_key = self.first_known_key([base], known)
        if base_key:
            prev = known[base_key]
            access = f"{prev['access']}[{key_norm}]"
            all_notes = list(dict.fromkeys(['nested_mapping_slot_from_byte_axis', *(notes or [])]))
            result_type = self.mapping_value_type(prev.get('result_type'))
            return self.clean({
                'access': access,
                'key': key_norm,
                'keys': [*(prev.get('keys') or []), key_norm],
                'base': base,
                'base_key': base_key,
                'state_variable': prev.get('state_variable'),
                'storage_reference': prev.get('storage_reference'),
                'storage_reference_type': prev.get('storage_reference_type'),
                'storage_reference_kind': prev.get('storage_reference_kind'),
                'storage_field': prev.get('storage_field'),
                'slot_kind': 'nested_mapping_slot',
                'result_type': result_type,
                'terminal_storage_value': not self.is_mapping_type_text(result_type),
                'resolved_inputs': parts,
                'byte_slice': byte_slice,
                'packed_semantics': parts,
                'slot_derivation': {
                    'kind': 'nested_mapping_slot',
                    'hash': 'keccak256',
                    'encoding': 'abi.encode',
                    'expression': access,
                    'key': key_norm,
                    'base': base,
                    'base_key': base_key,
                    'base_expression': prev.get('access'),
                    'state_variable': prev.get('state_variable'),
                    'byte_slice': byte_slice,
                    'notes': all_notes,
                },
                'sink_resolution': sink_resolution,
                'notes': all_notes,
            })
        state = getattr(type_env, 'state_var_by_slot', lambda _slot: None)(base)
        if not state:
            return None
        is_mapping = getattr(type_env, 'is_mapping_type', lambda _type: False)(getattr(state, 'type_string', None))
        if not is_mapping:
            return None
        access = f'{state.name}[{key_norm}]'
        all_notes = list(dict.fromkeys(['mapping_slot_from_byte_axis', *(notes or [])]))
        result_type = self.mapping_value_type(getattr(state, 'type_string', None))
        return self.clean({
            'access': access,
            'key': key_norm,
            'keys': [key_norm],
            'base': base,
            'base_key': base,
            'state_variable': state.name,
            'slot_kind': 'mapping_slot',
            'result_type': result_type,
            'terminal_storage_value': not self.is_mapping_type_text(result_type),
            'resolved_inputs': parts,
            'byte_slice': byte_slice,
            'packed_semantics': parts,
            'slot_derivation': {
                'kind': 'mapping_slot',
                'hash': 'keccak256',
                'encoding': 'abi.encode',
                'expression': access,
                'key': key_norm,
                'base': base,
                'state_variable': state.name,
                'byte_slice': byte_slice,
                'notes': all_notes,
            },
            'sink_resolution': sink_resolution,
            'notes': all_notes,
        })

    @classmethod
    def is_mapping_type_text(cls, type_string: Any) -> bool:
        return str(type_string or '').strip().startswith('mapping(')

    @classmethod
    def mapping_value_type(cls, type_string: Any) -> str | None:
        text = str(type_string or '').strip()
        if not cls.is_mapping_type_text(text):
            return None
        body = text[len('mapping('):]
        if body.endswith(')'):
            body = body[:-1]
        depth = 0
        index = 0
        while index < len(body):
            ch = body[index]
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            elif depth == 0 and body[index:index + 2] == '=>':
                return body[index + 2:].strip()
            index += 1
        return None

    def storage_key_expr(self, type_env: Any, expr: Any) -> str:
        direct_state = self.direct_state_read_from_sload_expr(type_env, expr)
        if direct_state:
            return str(direct_state.get('access') or direct_state.get('solidity_like') or normalize_expr(expr))
        return normalize_expr(expr)

    @staticmethod
    def is_full_word_pair(byte_slice: dict[str, Any]) -> bool:
        slices = byte_slice.get('slices') or []
        if len(slices) != 2:
            return False
        try:
            first_offset = int(slices[0].get('query_offset') or 0)
            second_offset = int(slices[1].get('query_offset') or 0)
            first_size = int(slices[0].get('size') or 0)
            second_size = int(slices[1].get('size') or 0)
        except Exception:
            return False
        return first_offset == 0 and second_offset == 32 and first_size == 32 and second_size == 32

    @classmethod
    def byte_slice_hash_encoding(cls, byte_slice: dict[str, Any]) -> str:
        slices = byte_slice.get('slices') or []
        try:
            total_size = int(byte_slice.get('size') or 0)
        except Exception:
            total_size = 0
        if slices and total_size > 0 and total_size % 32 == 0:
            expected_offset = 0
            full_words = True
            for item in slices:
                try:
                    offset = int(item.get('query_offset') or 0)
                    size = int(item.get('size') or 0)
                except Exception:
                    full_words = False
                    break
                if offset != expected_offset or size != 32:
                    full_words = False
                    break
                expected_offset += 32
            if full_words and expected_offset == total_size:
                return 'abi.encode'
        return 'abi.encodePacked'

    def memory_hash_expression(
        self,
        effect: EffectNode,
        type_env: Any | None = None,
        value_defs_by_name: dict[str, list[EffectNode]] | None = None,
        value_defs_by_version: dict[str, EffectNode] | None = None,
        order: dict[str, int] | None = None,
    ) -> dict[str, Any] | None:
        if type_env is not None:
            bytes_hash = self.bytes_content_hash_pattern(
                type_env,
                effect,
                value_defs_by_name,
                value_defs_by_version,
                order,
            )
            if bytes_hash:
                return self.clean({
                    'expression': bytes_hash['result_expression'],
                    'encoding': 'dynamic_bytes_content',
                    'resolved_inputs': [bytes_hash['object']],
                    'bytes_content_hash': bytes_hash,
                    'notes': ['memory_hash_from_typed_dynamic_bytes'],
                })
        byte_slice = ((effect.attrs.get('memory_read') or {}).get('byte_slice') or {})
        if not byte_slice.get('complete'):
            return None
        slices = byte_slice.get('slices') or []
        if not slices or any(not item.get('extraction') for item in slices):
            return None
        parts = [normalize_expr(item.get('extraction')) for item in slices]
        encoding = self.byte_slice_hash_encoding(byte_slice)
        return self.clean({
            'expression': f"keccak256({encoding}({', '.join(parts)}))",
            'encoding': encoding,
            'resolved_inputs': parts,
            'byte_slice': byte_slice,
            'notes': ['memory_hash_from_byte_axis'],
        })

    def path_conditioned_storage_overlay_from_slot_expr(
        self,
        effect: EffectNode,
        slot_key: str,
        slot_expr: dict[str, Any],
        slot_effect: EffectNode | None,
        type_env: Any,
        value_defs_by_name: dict[str, list[EffectNode]],
    ) -> SemanticOverlay:
        value = effect.attrs.get('value')
        value_normalized, value_state_read = self.storage_write_value(type_env, effect, value_defs_by_name)
        overlay_kind = 'PathConditionedStorageRead' if effect.kind == 'StorageRead' else 'PathConditionedStorageWrite'
        candidates: list[dict[str, Any]] = []
        effect_paths = self.path_states(effect) or [None]
        for candidate in slot_expr.get('path_candidates') or []:
            merged_conditions = self.compatible_sink_candidate_conditions(effect_paths, candidate.get('condition'))
            if not merged_conditions:
                continue
            for merged_condition in merged_conditions:
                if candidate.get('status') != 'resolved':
                    clone = dict(candidate)
                    if merged_condition:
                        clone['condition'] = merged_condition
                    candidates.append(self.clean(clone))
                    continue
                if candidate.get('terminal_storage_value') is False:
                    candidates.append(self.clean({
                        'status': 'unresolved',
                        'condition': merged_condition,
                        'reason': 'intermediate_mapping_slot_requires_additional_key',
                        'access_candidate': candidate.get('access'),
                        'result_type': candidate.get('result_type'),
                        'resolved_inputs': candidate.get('resolved_inputs'),
                        'notes': list(dict.fromkeys((candidate.get('notes') or []) + ['intermediate_mapping_slot_not_projected'])),
                    }))
                    continue
                access = candidate.get('access')
                manual_packed = candidate.get('slot_kind') == 'manual_packed_hash_slot'
                kind = (
                    ('StateVariableRead' if effect.kind == 'StorageRead' else 'StateVariableWrite')
                    if manual_packed
                    else ('MappingRead' if effect.kind == 'StorageRead' else 'MappingWrite')
                )
                solidity_like = (
                    f"{value} = {access};"
                    if effect.kind == 'StorageRead' and value
                    else f"{access} = {value_normalized};"
                )
                candidates.append(self.clean({
                    **candidate,
                    'condition': merged_condition,
                    'overlay_kind': kind,
                    'solidity_like': solidity_like,
                    'value': value_normalized if effect.kind == 'StorageWrite' else None,
                    'value_yul': value if effect.kind == 'StorageWrite' else None,
                    'value_state_read': value_state_read,
                    'target': value if effect.kind == 'StorageRead' else None,
                    'storage_model': 'manual_packed_hash_slot' if manual_packed else None,
                    'state_access': True if manual_packed else None,
                    'state_mutation': effect.kind == 'StorageWrite' if manual_packed else None,
                    'variable_name_inferred': False if manual_packed else None,
                }))
        effects_used = [x for x in [slot_effect.effect_id if slot_effect else None, effect.effect_id] if x]
        refs = list(dict.fromkeys((slot_effect.stmt_refs if slot_effect else []) + effect.stmt_refs))
        candidates = self.dedupe_equivalent_storage_candidates(candidates)
        return self.ov(overlay_kind, effects_used, refs, self.clean({
            'slot': effect.attrs.get('slot'),
            'slot_key': slot_key,
            'slot_versions': effect.attrs.get('slot_versions'),
            'target': value if effect.kind == 'StorageRead' else None,
            'value': value_normalized if effect.kind == 'StorageWrite' else None,
            'value_yul': value if effect.kind == 'StorageWrite' else None,
            'value_state_read': value_state_read,
            'path_states': effect.attrs.get('path_states'),
            'candidates': candidates,
            'sink_resolution': slot_expr.get('sink_resolution'),
            'storage_model': 'manual_packed_hash_slot' if slot_expr.get('slot_kind') == 'path_conditioned_manual_packed_hash_slot' else None,
            'note': 'storage_effect_has_path_sensitive_sink_resolution',
        }))

    @classmethod
    def compatible_sink_candidate_conditions(cls, effect_paths: list[str | None], candidate_condition: Any) -> list[str | None]:
        out: list[str | None] = []
        for effect_path in effect_paths:
            merged = cls.merge_compatible_conditions(effect_path, candidate_condition)
            if merged is None:
                continue
            if merged not in out:
                out.append(merged)
        return out

    @staticmethod
    def manual_packed_slot_derivation(
        slot_expr: dict[str, Any],
        slot_key: str | None,
        slot_effect: EffectNode | None,
    ) -> dict[str, Any]:
        return {
            'kind': 'manual_packed_hash_slot',
            'hash': 'keccak256',
            'encoding': slot_expr.get('encoding') or 'abi.encodePacked',
            'expression': slot_expr.get('access'),
            'slot_key': slot_key,
            'slot_effect': slot_effect.effect_id if slot_effect else None,
            'packed_inputs': slot_expr.get('packed_semantics') or slot_expr.get('resolved_inputs') or [],
            'byte_slice': slot_expr.get('byte_slice'),
            'notes': slot_expr.get('notes', []),
        }

    @classmethod
    def storage_ref_mapping_access(cls, type_env: Any, base: Any, key: str) -> dict[str, Any] | None:
        root = cls.storage_ref_slot_root(base)
        if not root:
            return None
        return getattr(type_env, 'storage_ref_mapping_access', lambda *_args: None)(root['name'], root['slot_offset'], key)

    @staticmethod
    def storage_ref_slot_root(base: Any) -> dict[str, Any] | None:
        text = str(base or '').strip()
        if not text:
            return None
        if text.endswith('.slot'):
            name = text[:-5].strip()
            return {'name': name, 'slot_offset': 0} if name else None
        call, args = call_parts(text)
        if call == 'add' and len(args) == 2:
            left = SemanticOverlayBuilder.storage_ref_slot_root(args[0])
            right = parse_int_literal(str(args[1]).strip())
            if left and right is not None:
                return {'name': left['name'], 'slot_offset': left['slot_offset'] + right}
            right_root = SemanticOverlayBuilder.storage_ref_slot_root(args[1])
            left_const = parse_int_literal(str(args[0]).strip())
            if right_root and left_const is not None:
                return {'name': right_root['name'], 'slot_offset': right_root['slot_offset'] + left_const}
        m = re.match(r'^\(?\s*([A-Za-z_$][A-Za-z0-9_$]*)\.slot\s*\+\s*(0x[0-9a-fA-F]+|\d+)\s*\)?$', text)
        if m:
            offset = parse_int_literal(m.group(2))
            if offset is not None:
                return {'name': m.group(1), 'slot_offset': offset}
        return None

    @staticmethod
    def hash_result_keys(effect: EffectNode) -> list[str]:
        value = effect.attrs.get('value')
        versions = []
        value_versions = effect.attrs.get('value_versions') or {}
        if value and isinstance(value_versions, dict):
            versions = list(value_versions.get(str(value)) or [])
        return versions or ([str(value)] if value else [])

    @staticmethod
    def hash_result_alias(effect: EffectNode) -> str | None:
        value = str(effect.attrs.get('value') or '').strip()
        if not value:
            return None
        if call_parts(value)[0]:
            return None
        return value

    @staticmethod
    def storage_slot_keys(effect: EffectNode) -> list[str]:
        keys = list(effect.attrs.get('slot_versions') or [])
        slot = effect.attrs.get('slot')
        if slot and slot not in keys:
            keys.append(str(slot))
        return keys

    @staticmethod
    def word_value_keys(word: dict[str, Any]) -> list[str]:
        keys = list(word.get('value_versions') or [])
        value = word.get('value')
        if value and value not in keys:
            keys.append(str(value))
        return keys

    @staticmethod
    def first_known_key(keys: list[str], known: dict[str, Any]) -> str | None:
        for key in keys:
            if key in known:
                return key
        return None

    @staticmethod
    def is_unknown_value(value: Any) -> bool:
        return memory_is_unknown_value(value)

    @staticmethod
    def value_text(value: Any) -> str:
        if isinstance(value, list):
            vals = [normalize_expr(v) for v in value if not memory_is_unknown_value(v)]
            return vals[0] if len(vals) == 1 else 'phi(' + ', '.join(vals) + ')'
        return normalize_expr(value)

    def storage_write_value(
        self,
        type_env: Any,
        effect: EffectNode,
        value_defs_by_name: dict[str, list[EffectNode]],
        nested_read_accesses: dict[str, str] | None = None,
    ) -> tuple[Any, dict[str, Any] | None]:
        value = effect.attrs.get('value')
        if effect.kind != 'StorageWrite':
            return value, None
        return self.normalize_expression_with_state_reads(
            type_env,
            value,
            value_defs_by_name,
            nested_read_accesses,
        )

    def storage_access_for_effect(
        self,
        type_env: Any,
        effect: EffectNode,
        slot_expr_by_var: dict[str, dict[str, Any]],
        value_defs_by_version: dict[str, EffectNode],
    ) -> str | None:
        slot = str(effect.attrs.get('slot') or '')
        slot_key = self.first_known_key(self.storage_slot_keys(effect), slot_expr_by_var)
        if slot_key:
            slot_expr = slot_expr_by_var[slot_key]
            if slot_expr.get('terminal_storage_value') is False:
                return f'storage[{slot_key}]'
            return str(slot_expr.get('access') or '') or None
        direct_state = type_env.state_var_by_slot(slot) or self.direct_state_slot_alias(
            type_env,
            effect,
            value_defs_by_version,
        )
        if direct_state:
            return str(direct_state.name)
        direct_slot = self.direct_storage_slot_info(type_env, effect, value_defs_by_version)
        if direct_slot:
            return f"storage[{direct_slot.get('name') or direct_slot.get('slot')}]"
        return f'storage[{slot}]' if slot else None

    @staticmethod
    def nested_storage_read_attrs(effect: EffectNode, access: str) -> dict[str, Any]:
        if effect.kind != 'StorageRead' or not effect.attrs.get('nested_in_storage_value'):
            return {}
        return {
            'read_expression': access,
            'source_expression': effect.attrs.get('expression'),
            'nested_in_storage_value': True,
            'consumed_by': {
                'kind': effect.attrs.get('parent_call'),
                'slot': effect.attrs.get('parent_slot'),
                'value': effect.attrs.get('parent_value'),
            },
        }

    @staticmethod
    def storage_solidity_like(kind: str, access: str, value: Any, effect: EffectNode) -> str:
        if kind in {'MappingRead', 'StateVariableRead'}:
            target = effect.attrs.get('value')
            return f'{target} = {access};' if target else f'read {access};'
        return f'{access} = {value};'

    def path_conditioned_event_overlay(
        self,
        effect: EffectNode,
        event: EventDecl | None,
        topics: list[Any],
        type_env: Any,
        value_defs_by_name: dict[str, list[EffectNode]],
    ) -> SemanticOverlay | None:
        paths = self.sink_path_values(effect, 'data')
        if not paths:
            return None
        name = event.name if event else 'unknownEvent'
        candidates = []
        argument_state_reads: list[dict[str, Any]] = []
        argument_memory_reads: list[dict[str, Any]] = []
        overlay_notes: list[str] = []
        for item in paths:
            if item.get('status') != 'resolved':
                candidates.append(item)
                continue
            values = item.get('values') or []
            args, notes, state_reads, memory_reads = self.event_args(
                event,
                effect,
                type_env,
                value_defs_by_name,
                topics,
                [{'value': value} for value in values],
                path_condition=item.get('condition'),
            )
            for note in notes:
                if note not in overlay_notes:
                    overlay_notes.append(note)
            for read in state_reads:
                if read not in argument_state_reads:
                    argument_state_reads.append(read)
            for read in memory_reads:
                if read not in argument_memory_reads:
                    argument_memory_reads.append(read)
            candidates.append(self.clean({
                **item,
                'event': name,
                'args': args,
                'notes': list(dict.fromkeys([*(item.get('notes') or []), *notes])),
                'solidity_like': f"emit {name}({', '.join(map(str, args))});",
            }))
        return self.path_overlay_if_needed('PathConditionedEventEmit', effect, candidates, {
            'event': name,
            'signature': event.signature if event else None,
            'topic0': event.topic0 if event else (topics or [None])[0],
            'topics': [normalize_expr(t) for t in topics],
            'argument_state_reads': argument_state_reads,
            'argument_memory_reads': argument_memory_reads,
            'notes': overlay_notes,
        })

    def path_conditioned_revert_overlay(self, effect: EffectNode) -> SemanticOverlay | None:
        paths = self.sink_path_values(effect, 'payload')
        if not paths:
            return None
        candidates = []
        for item in paths:
            if item.get('status') != 'resolved':
                candidates.append(item)
                continue
            values = item.get('values') or []
            selector_source = values[0] if values else None
            selector_info = self.selector_match_from_extraction(selector_source, preferred_kind='error')
            signature = (selector_info.get('best_match') or {}).get('signature') if selector_info else None
            if signature:
                line = f"revert {signature};"
            elif selector_source:
                line = f"revertRawSelector({normalize_expr(selector_source)});"
            else:
                line = "revertRawMemory();"
            candidates.append(self.clean({
                **item,
                'selector_source': selector_source,
                'selector': selector_info.get('selector') if selector_info else normalize_selector_value(selector_source),
                'selector_match': selector_info,
                'error': signature,
                'solidity_like': line,
            }))
        return self.path_overlay_if_needed('PathConditionedCustomErrorRevert', effect, candidates, {})

    def path_conditioned_return_overlay(self, effect: EffectNode) -> SemanticOverlay | None:
        paths = self.sink_path_values(effect, 'payload')
        if not paths:
            return None
        size_int = parse_int_literal(str(effect.attrs.get('payload_size') or ''))
        candidates = []
        for item in paths:
            if item.get('status') != 'resolved':
                candidates.append(item)
                continue
            values = [normalize_expr(v) for v in (item.get('values') or [])]
            if size_int == 32 and len(values) == 1:
                line = f"returnRawAbiWord({values[0]});"
                hint = 'abi_word'
            elif size_int is not None and size_int % 32 == 0 and values:
                line = f"returnRawAbiWords({', '.join(values)});"
                hint = 'abi_static_words'
            else:
                line = f"returnRawMemory({normalize_expr(effect.attrs.get('payload_ptr'))}, {normalize_expr(effect.attrs.get('payload_size'))});"
                hint = None
            candidates.append(self.clean({**item, 'values': values, 'encoding_hint': hint, 'solidity_like': line}))
        return self.path_overlay_if_needed('PathConditionedRawReturnData', effect, candidates, {
            'payload_ptr': effect.attrs.get('payload_ptr'),
            'payload_size': effect.attrs.get('payload_size'),
            'solidity_equivalent': False,
            'reason': 'path_sensitive_yul_return_raw_data',
        })

    def path_conditioned_call_overlay(self, effect: EffectNode, overlay_kind: str, attrs: dict[str, Any]) -> SemanticOverlay | None:
        paths = self.sink_path_values(effect, 'input')
        if not paths:
            return None
        candidates = []
        precompile_name = PRECOMPILES.get(self.int_value(attrs.get('target')))
        detected_precompile = False
        lifted_precompile = False
        for index, item in enumerate(paths):
            if item.get('status') != 'resolved':
                candidates.append(item)
                continue
            values = item.get('values') or []
            selector_source = values[0] if values else None
            selector_info = self.selector_match_from_extraction(selector_source, preferred_kind='function')
            candidate_attrs = dict(attrs)
            candidate_attrs['gas'] = normalize_expr(candidate_attrs.get('gas')) if candidate_attrs.get('gas') else candidate_attrs.get('gas')
            candidate_attrs['target_solidity'] = self.target_solidity(candidate_attrs.get('target'))
            candidate_attrs['selector'] = selector_info.get('selector') if selector_info else normalize_selector_value(selector_source)
            candidate_attrs['selector_match'] = selector_info
            candidate_attrs['selector_signature'] = (selector_info.get('best_match') or {}).get('signature') if selector_info else None
            candidate_attrs['arguments'] = [normalize_expr(v) for v in values[1:]]
            precompile = self.path_precompile_candidate(candidate_attrs, item, index) if precompile_name else None
            if precompile:
                detected_precompile = True
            if precompile and precompile.get('native_precompile'):
                lifted_precompile = True
                line = precompile['native_precompile'].get('solidity_like')
            else:
                line = self.low_level_call_solidity_like(candidate_attrs)
            if not line:
                result = candidate_attrs.get('result')
                prefix = f"{result} = " if result else ""
                input_desc = f"MemorySlice({', '.join(map(str, values))})"
                output = f"memory[{candidate_attrs.get('output_ptr')}:{candidate_attrs.get('output_size')}]"
                line = (
                    f"{prefix}yulCall(gas: {candidate_attrs.get('gas')}, target: {candidate_attrs.get('target_solidity') or candidate_attrs.get('target')}, "
                    f"value: {normalize_expr(candidate_attrs.get('value')) if candidate_attrs.get('value') is not None else '0'}, input: {input_desc}, output: {output});"
                )
            candidates.append(self.clean({
                **item,
                'selector_source': selector_source,
                'selector': candidate_attrs.get('selector'),
                'selector_match': selector_info,
                'selector_signature': candidate_attrs.get('selector_signature'),
                'arguments': candidate_attrs.get('arguments'),
                'precompile': precompile_name if precompile else None,
                'input_words': precompile.get('input_words') if precompile else None,
                'native_precompile': precompile.get('native_precompile') if precompile else None,
                'output_word_inline_expression': precompile.get('output_word_inline_expression') if precompile else None,
                'solidity_like': line,
            }))
        path_kind = 'PathConditionedPrecompileCall' if detected_precompile else f'PathConditioned{overlay_kind}'
        return self.path_overlay_if_needed(path_kind, effect, candidates, {
            'op': effect.attrs.get('op'),
            'target': effect.attrs.get('target'),
            'target_solidity': self.target_solidity(effect.attrs.get('target')),
            'call_overlay_kind': 'PrecompileCall' if detected_precompile else overlay_kind,
            'precompile': precompile_name if detected_precompile else None,
            'native_solidity_projection': lifted_precompile,
            'input_ptr': effect.attrs.get('input_ptr'),
            'input_size': effect.attrs.get('input_size'),
            'output_ptr': effect.attrs.get('output_ptr'),
            'output_size': effect.attrs.get('output_size'),
            'cfg_node_id': effect.attrs.get('cfg_node_id'),
        })

    def path_precompile_candidate(
        self,
        attrs: dict[str, Any],
        item: dict[str, Any],
        path_index: int,
    ) -> dict[str, Any] | None:
        target = self.int_value(attrs.get('target'))
        if target not in PRECOMPILES or attrs.get('op') != 'staticcall':
            return None
        memory_slice = item.get('memory_slice') or {}
        slices = memory_slice.get('slices') or []
        input_size = parse_int_literal(strip_ssa(str(attrs.get('input_size') or '')) or str(attrs.get('input_size') or ''))
        if not memory_slice.get('complete') or input_size is None or not slices:
            return None
        words = []
        covered = 0
        for part in slices:
            size = int(part.get('size') or 0)
            if size <= 0:
                return None
            value = part.get('extraction')
            if str(value or '').startswith('high_bytes(') and part.get('source_value') is not None:
                value = part.get('source_value')
            words.append({
                'value': value,
                'size': size,
                'memory_ssa': part.get('source_version'),
            })
            covered += size
        snapshot = {
            'ptr': attrs.get('input_ptr'),
            'size': attrs.get('input_size'),
            'base': attrs.get('input_ptr'),
            'start': 0,
            'length': input_size,
            'raw_range': f"memory[{attrs.get('input_ptr')} : {attrs.get('input_ptr')} + {attrs.get('input_size')}]",
            'selector': None,
            'arguments': [],
            'complete_static_abi': False,
            'source_writes': [],
            'words': words,
            'complete_memory_ssa': covered == input_size,
        }
        call_args = {
            'gas': str(attrs.get('gas') or ''),
            'target': str(attrs.get('target') or ''),
            'value': str(attrs.get('value') or '0'),
            'input_ptr': str(attrs.get('input_ptr') or ''),
            'input_size': str(attrs.get('input_size') or ''),
            'output_ptr': str(attrs.get('output_ptr') or ''),
            'output_size': str(attrs.get('output_size') or ''),
        }
        node = self.int_node(attrs.get('cfg_node_id')) or 0
        native = native_precompile_call(PRECOMPILES[target], 'staticcall', call_args, snapshot, node)
        result_name = native.get('result_name') if native else None
        solidity_like = str(native.get('solidity_like') or '') if native else ''
        output_expression = native.get('output_word_expression') if native else None
        if native and result_name and '=' in solidity_like:
            rhs = solidity_like.split('=', 1)[1].strip().rstrip(';')
            output_expression = str(output_expression).replace(str(result_name), f'({rhs})')
        return {
            'precompile': PRECOMPILES[target],
            'input_words': [normalize_expr(word.get('value')) for word in words],
            'input_memory': memory_slice,
            'native_precompile': native,
            'output_word_inline_expression': output_expression,
            'solidity_like': native.get('solidity_like') if native else None,
        }

    def path_overlay_if_needed(
        self,
        kind: str,
        effect: EffectNode,
        candidates: list[dict[str, Any]],
        attrs: dict[str, Any],
    ) -> SemanticOverlay | None:
        resolved = [item for item in candidates if item.get('status') == 'resolved' and item.get('solidity_like')]
        if not resolved:
            return None
        unique_lines = {(item.get('condition'), item.get('solidity_like')) for item in resolved}
        sink_resolution = effect.attrs.get('sink_resolution') or {}
        if not sink_resolution.get('path_sensitive') and len(unique_lines) <= 1:
            return None
        notes = list(dict.fromkeys([
            *(attrs.get('notes') or []),
            'path_sensitive_sink_resolution',
        ]))
        return self.ov(kind, effect.effect_id, effect.stmt_refs, self.clean({
            **attrs,
            'candidates': candidates,
            'sink_resolution': sink_resolution,
            'path_states': self.path_states(effect),
            'notes': notes,
        }))

    def sink_path_values(self, effect: EffectNode, role: str, *, path_sensitive_only: bool = True) -> list[dict[str, Any]]:
        sink_resolution = effect.attrs.get('sink_resolution') or {}
        if path_sensitive_only and not sink_resolution.get('path_sensitive'):
            return []
        out = []
        for path in sink_resolution.get('path_resolutions') or []:
            arg = (path.get('arg_resolutions') or {}).get(role) or {}
            memory_slice = arg.get('memory_slice') or {}
            slices = memory_slice.get('slices') or []
            values = [item.get('extraction') for item in slices if item.get('extraction')]
            out.append(self.clean({
                'status': path.get('status') or 'resolved',
                'condition': path.get('condition'),
                'values': values,
                'memory_slice': memory_slice,
                'normalized': arg.get('normalized'),
                'notes': arg.get('notes') or [],
            }))
        return out

    def selector_match_from_extraction(self, value: Any, preferred_kind: str | None = None) -> dict[str, Any] | None:
        selector = normalize_selector_value(value)
        if not selector:
            return None
        return self.selector_match_from_value(selector, value, preferred_kind, {'kind': 'selector', 'selector': value})

    def event_overlays(self, unit: FunctionUnit, type_env: Any, effects: list[EffectNode]) -> list[SemanticOverlay]:
        evs = self.events_for_contract(unit.contract)
        out: list[SemanticOverlay] = []
        value_defs_by_name = self.value_defs_by_name(effects)
        for e in effects:
            if e.kind != 'EventLog':
                continue
            if e.attrs.get('source_event'):
                name = str(e.attrs.get('event_name') or 'unknownEvent')
                arguments = list(e.attrs.get('arguments') or [])
                out.append(self.ov('EventEmit', e.effect_id, e.stmt_refs, {
                    'event': name,
                    'args': arguments,
                    'argument_versions': e.attrs.get('argument_versions') or [],
                    'emit_like': f"emit {name}({', '.join(map(str, arguments))});",
                    'path_states': self.path_states(e),
                    'exact_solidity_semantics': True,
                    'source': e.attrs.get('source'),
                }))
                continue
            raw_topics = e.attrs.get('topics', []) or []
            topics = [self.resolve_constant_expr(type_env, topic) for topic in raw_topics]
            topic_constants = [
                {'name': str(raw), 'value': str(resolved)}
                for raw, resolved in zip(raw_topics, topics)
                if str(raw) != str(resolved)
            ]
            event = self.match_event_name(e, evs, topics)
            path_overlay = self.path_conditioned_event_overlay(
                e,
                event,
                topics,
                type_env,
                value_defs_by_name,
            )
            if path_overlay:
                path_overlay.attrs.update(self.clean({
                    'raw_topics': raw_topics,
                    'topic_constants': topic_constants,
                }))
                if topic_constants and 'resolved_topic_constants' not in path_overlay.attrs.get('notes', []):
                    path_overlay.attrs.setdefault('notes', []).append('resolved_topic_constants')
                out.append(path_overlay)
                continue
            data_words = self.event_data_words(e)
            args, notes, argument_state_reads, argument_memory_reads = self.event_args(event, e, type_env, value_defs_by_name, topics, data_words)
            name = event.name if event else 'unknownEvent'
            data_size = parse_int_literal(str(e.attrs.get('data_size') or '').strip())
            near_misses = [] if event else self.event_topic_near_misses(e, evs, data_size, topics)
            if near_misses:
                notes.append('topic0_prefix_matches_known_event_but_full_topic_mismatch')
            if topic_constants:
                notes.append('resolved_topic_constants')
            if event:
                non_indexed = [param for param in event.params if not param.indexed]
                expected = 32 * len(non_indexed)
                if data_size is not None and data_size != expected:
                    notes.append(f'data_length_{data_size}_does_not_match_expected_{expected}')
                if any(not is_static_word_type(param.type) for param in non_indexed):
                    notes.append('dynamic_non_indexed_data_not_decoded')
            out.append(self.ov('EventEmit', e.effect_id, e.stmt_refs, {
                'event': name,
                'signature': event.signature if event else None,
                'topic0': event.topic0 if event else (topics or [None])[0],
                'args': args,
                'topics': [normalize_expr(t) for t in topics],
                'raw_topics': raw_topics,
                'topic_constants': topic_constants,
                'argument_state_reads': argument_state_reads,
                'argument_memory_reads': argument_memory_reads,
                'data': data_words,
                'sink_resolution': e.attrs.get('sink_resolution'),
                'path_conditioned_data': [
                    word for word in data_words
                    if word.get('path_conditioned')
                ],
                'emit_like': f"emit {name}({', '.join(map(str,args))});" if args else None,
                'topic0_near_misses': near_misses,
                'notes': notes,
            }))
        return out

    def event_args(
        self,
        event: EventDecl | None,
        effect: EffectNode,
        type_env: Any,
        value_defs_by_name: dict[str, list[EffectNode]],
        topics: list[Any] | None = None,
        data_words: list[dict[str, Any]] | None = None,
        path_condition: str | None = None,
    ) -> tuple[list[Any], list[str], list[dict[str, Any]], list[dict[str, Any]]]:
        topics = topics if topics is not None else (effect.attrs.get('topics', []) or [])
        data_words = self.event_data_words(effect) if data_words is None else data_words
        data_values = []
        notes: list[str] = []
        argument_state_reads: list[dict[str, Any]] = []
        argument_memory_reads: list[dict[str, Any]] = []

        def render_arg(value: Any) -> str:
            state_read = self.manual_slot_state_read_from_expr(type_env, value, value_defs_by_name)
            if state_read:
                if state_read not in argument_state_reads:
                    argument_state_reads.append(state_read)
                if 'event_argument_state_read' not in notes:
                    notes.append('event_argument_state_read')
                return str(state_read['solidity_like'])
            return normalize_expr(value)

        def render_topic_arg(param: Any, value: Any, topic_index: int) -> str:
            resolved = self.event_topic_memory_arg(param, value, topic_index, effect, path_condition)
            if resolved:
                record = resolved.get('record')
                if record and record not in argument_memory_reads:
                    argument_memory_reads.append(record)
                note = resolved.get('note')
                if note and note not in notes:
                    notes.append(note)
                return str(resolved.get('value'))
            if str(getattr(param, 'type', '') or '') == 'address':
                projection = self.address_projection(type_env, value, value_defs_by_name)
                if projection and projection.get('variable'):
                    if 'event_topic_address_projection_resolved' not in notes:
                        notes.append('event_topic_address_projection_resolved')
                    return str(projection['variable'])
            return render_arg(value)

        for word in data_words:
            if word.get('path_conditioned'):
                notes.append('data_word_path_conditioned')
                data_values.append('unknown')
                continue
            value = word.get('value')
            if self.is_unknown_value(value):
                notes.append('data_word_unknown')
                continue
            if word.get('symbolic_candidates'):
                notes.append('data_word_has_symbolic_overwrite_candidate')
            data_values.append(render_arg(value))
        if not event:
            notes.append('unknown_event_topic0')
            return [render_arg(t) for t in topics[1:]] + data_values, notes, argument_state_reads, argument_memory_reads
        args: list[Any] = []
        topic_index = 0 if event.anonymous else 1
        data_index = 0
        for param in event.params:
            if param.indexed:
                args.append(render_topic_arg(param, topics[topic_index], topic_index) if topic_index < len(topics) else None)
                topic_index += 1
            else:
                args.append(data_values[data_index] if data_index < len(data_values) else None)
                data_index += 1
        if any(a is None for a in args):
            notes.append('incomplete_event_argument_recovery')
        non_indexed_count = len([p for p in event.params if not p.indexed])
        if len(data_words) < non_indexed_count:
            notes.append('memory_data_words_incomplete')
        return args, notes, argument_state_reads, argument_memory_reads

    def event_topic_memory_arg(
        self,
        param: Any,
        value: Any,
        topic_index: int,
        effect: EffectNode,
        path_condition: str | None = None,
    ) -> dict[str, Any] | None:
        text = str(value or '').strip()
        if not text:
            return None
        param_type = str(getattr(param, 'type', '') or '')
        name, args = call_parts(text)
        read_ptr = None
        transform = None
        if name == 'mload' and len(args) == 1:
            read_ptr = args[0]
            transform = 'word'
        elif name == 'shr' and len(args) == 2 and parse_int_literal(str(args[0]).strip()) == 96:
            inner_name, inner_args = call_parts(args[1])
            if inner_name == 'mload' and len(inner_args) == 1:
                read_ptr = inner_args[0]
                transform = 'shr96'
        if read_ptr is None:
            return None
        read = self.find_topic_memory_read(effect, topic_index, read_ptr)
        if not read:
            return None
        memory_read = read.get('memory_read') or {}
        if transform == 'shr96' and param_type == 'address':
            source = self.address_from_shr96_mload_byte_slice(
                memory_read.get('byte_slice'),
                path_condition,
            )
            if source:
                return {
                    'value': source,
                    'note': 'event_topic_memory_read_resolved',
                    'record': self.clean({
                        'topic_index': topic_index,
                        'param': getattr(param, 'name', None),
                        'param_type': param_type,
                        'raw_expression': text,
                        'resolved': source,
                        'read_ptr': read_ptr,
                        'transform': transform,
                        'path_condition': path_condition,
                        'memory_read': memory_read,
                    }),
                }
        if transform == 'word':
            words = words_from_memory_query(memory_read)
            if len(words) == 1 and not self.is_unknown_value(words[0].get('value')):
                resolved = normalize_expr(words[0].get('value'))
                return {
                    'value': resolved,
                    'note': 'event_topic_memory_read_resolved',
                    'record': self.clean({
                        'topic_index': topic_index,
                        'param': getattr(param, 'name', None),
                        'param_type': param_type,
                        'raw_expression': text,
                        'resolved': resolved,
                        'read_ptr': read_ptr,
                        'transform': transform,
                        'memory_read': memory_read,
                    }),
                }
        return None

    @staticmethod
    def find_topic_memory_read(effect: EffectNode, topic_index: int, ptr: Any) -> dict[str, Any] | None:
        ptr_key = str(ptr or '').replace(' ', '').lower()
        for read in effect.attrs.get('topic_memory_reads') or []:
            if int(read.get('topic_index') or -1) != int(topic_index):
                continue
            read_ptr = str(read.get('ptr') or '').replace(' ', '').lower()
            if read_ptr == ptr_key:
                return read
        return None

    @classmethod
    def address_from_shr96_mload_byte_slice(
        cls,
        byte_slice: dict[str, Any] | None,
        path_condition: str | None = None,
    ) -> str | None:
        if not byte_slice:
            return None
        if parse_int_literal(str(byte_slice.get('size') or '')) != 32:
            return None

        path_slices = byte_slice.get('path_slices') or []
        slice_groups: list[list[dict[str, Any]]] = []
        condition_key = cls.condition_key(path_condition)
        if condition_key:
            slice_groups.extend(
                list(item.get('slices') or [])
                for item in path_slices
                if cls.condition_key(item.get('path')) == condition_key
            )
        if not slice_groups and byte_slice.get('slices'):
            slice_groups.append(list(byte_slice.get('slices') or []))
        if not slice_groups:
            slice_groups.extend(list(item.get('slices') or []) for item in path_slices)

        sources = {
            source
            for slices in slice_groups
            if (source := cls.address_from_consumed_high_bytes(slices)) is not None
        }
        return next(iter(sources)) if len(sources) == 1 else None

    @staticmethod
    def condition_key(value: Any) -> str:
        return re.sub(r'\s+', '', str(value or '').strip())

    @staticmethod
    def address_from_consumed_high_bytes(slices: list[dict[str, Any]]) -> str | None:
        slices = sorted(slices or [], key=lambda item: int(item.get('query_offset') or 0))
        if not slices:
            return None
        first = slices[0]
        query_offset = first.get('query_offset')
        if int(query_offset if query_offset is not None else -1) != 0 or int(first.get('size') or 0) != 20:
            return None
        if int(first.get('source_width') or 32) != 32:
            return None
        raw_source_offset = first.get('source_offset')
        source_offset = int(raw_source_offset if raw_source_offset is not None else 0)
        if source_offset != 12:
            return None
        source_value = first.get('source_value')
        if source_value is None:
            return None
        return normalize_expr(source_value)

    @staticmethod
    def event_data_words(effect: EffectNode) -> list[dict[str, Any]]:
        data_size = parse_int_literal(str(effect.attrs.get('data_size') or '').strip())
        if data_size == 0:
            return []
        return words_from_memory_query(effect.attrs.get('data_memory'))

    def call_overlays(self, effects: list[EffectNode]) -> list[SemanticOverlay]:
        out: list[SemanticOverlay] = []
        mapping = {'Call': 'LowLevelCall', 'StaticCall': 'StaticCallOverlay', 'DelegateCall': 'DelegateCallOverlay', 'CallCode': 'LowLevelCall'}
        for e in effects:
            if e.kind in {'ExternalCall', 'LibraryCall', 'InternalCall'} and e.attrs.get('typed_call'):
                overlay_kind = 'ExternalCall' if e.kind == 'ExternalCall' else e.kind
                target = e.attrs.get('target')
                function = e.attrs.get('function')
                arguments = list(e.attrs.get('arguments') or [])
                result = e.attrs.get('result')
                invocation = f"{target + '.' if target else ''}{function}({', '.join(map(str, arguments))})"
                out.append(self.ov(overlay_kind, e.effect_id, e.stmt_refs, self.clean({
                    **dict(e.attrs),
                    'target_solidity': target,
                    'call_expression': invocation,
                    'solidity_like': f"{result + ' = ' if result else ''}{invocation};",
                    'exact_solidity_semantics': True,
                })))
                continue
            if e.kind not in mapping:
                continue
            attrs = dict(e.attrs)
            path_overlay = self.path_conditioned_call_overlay(e, mapping[e.kind], attrs)
            if path_overlay:
                out.append(path_overlay)
                continue
            resolved_paths = [
                item for item in self.sink_path_values(e, 'input', path_sensitive_only=False)
                if item.get('status') == 'resolved'
            ]
            if len(resolved_paths) == 1:
                resolved = resolved_paths[0]
                values = list(resolved.get('values') or [])
                selector_source = values[0] if values else None
                selector_info = self.selector_match_from_extraction(selector_source, preferred_kind='function')
                attrs['selector'] = selector_info.get('selector') if selector_info else normalize_selector_value(selector_source)
                attrs['selector_match'] = selector_info
                attrs['selector_signature'] = (selector_info.get('best_match') or {}).get('signature') if selector_info else None
                attrs['arguments'] = [normalize_expr(value) for value in values[1:]]
                attrs['decoded_input'] = resolved.get('normalized')
            attrs['gas'] = normalize_expr(attrs.get('gas')) if attrs.get('gas') else attrs.get('gas')
            attrs['target_solidity'] = self.target_solidity(attrs.get('target'))
            selector_info = self.selector_info_from_partial(attrs.get('input_memory_partial'), preferred_kind='function')
            if selector_info:
                attrs['selector'] = selector_info.get('selector')
                attrs['selector_match'] = selector_info
                attrs['selector_signature'] = (selector_info.get('best_match') or {}).get('signature')
                attrs['arguments'] = (attrs.get('input_memory_partial') or {}).get('abi_hint', {}).get('arguments', [])
                attrs['solidity_like'] = self.low_level_call_solidity_like(attrs)
            precompile = self.precompile(attrs)
            if precompile:
                attrs.update(precompile)
                out.append(self.ov('PrecompileCall', e.effect_id, e.stmt_refs, attrs))
            else:
                out.append(self.ov(mapping[e.kind], e.effect_id, e.stmt_refs, attrs))
        return out

    def selector_info_from_partial(self, partial: dict[str, Any] | None, preferred_kind: str | None = None) -> dict[str, Any] | None:
        hint = (partial or {}).get('abi_hint') or {}
        raw_selector = hint.get('selector') or hint.get('selector_source_value')
        selector = normalize_selector_value(raw_selector)
        if not selector:
            return None
        return self.selector_match_from_value(selector, raw_selector, preferred_kind, hint)

    def selector_match_from_value(
        self,
        selector: str,
        raw_selector: Any,
        preferred_kind: str | None,
        hint: dict[str, Any],
    ) -> dict[str, Any]:
        matches = list(self.selector_registry.get(selector.lower(), []))
        if preferred_kind:
            preferred = [item for item in matches if item.get('kind') == preferred_kind]
        else:
            preferred = matches
        best = preferred[0] if preferred else (matches[0] if matches else None)
        return {
            'selector': selector,
            'selector_source': raw_selector,
            'preferred_kind': preferred_kind,
            'best_match': best,
            'matches': matches,
            'match_status': 'matched' if best else 'unmatched',
            'abi_hint': hint,
        }

    @staticmethod
    def low_level_call_solidity_like(attrs: dict[str, Any]) -> str | None:
        signature = attrs.get('selector_signature')
        target = attrs.get('target_solidity') or attrs.get('target')
        if not signature or not target:
            return None
        args = [normalize_expr(arg) for arg in attrs.get('arguments') or []]
        selector_expr = f"bytes4({attrs.get('selector')}) /* {signature} */"
        arg_suffix = (", " + ", ".join(args)) if args else ""
        payload = f"abi.encodeWithSelector({selector_expr}{arg_suffix})"
        value = normalize_expr(attrs.get('value')) if attrs.get('value') is not None else '0'
        evaluated_args = attrs.get('evaluated_args') or []
        gas = str(evaluated_args[0]) if evaluated_args else (normalize_expr(attrs.get('gas')) if attrs.get('gas') else None)
        result = attrs.get('result')
        prefix = f"{result} = " if result else ""
        output_ptr = attrs.get('output_ptr')
        output_size = attrs.get('output_size')
        output = f"memory[{output_ptr}:{output_size}]" if output_ptr is not None and output_size is not None else "memory[unknown]"
        return f"{prefix}yulCall(gas: {gas}, target: {target}, value: {value}, input: {payload}, output: {output});"

    def precompile(self, attrs: dict[str, Any]) -> dict[str, Any] | None:
        target = self.int_value(attrs.get('target'))
        if target not in PRECOMPILES:
            return None
        words = words_from_memory_query(attrs.get('input_memory'))
        path_conditioned_words = [word for word in words if word.get('path_conditioned')]
        word_values = [w.get('value') for w in words if not w.get('path_conditioned') and not self.is_unknown_value(w.get('value'))]
        native = {
            'precompile': PRECOMPILES[target],
            'input_words': [normalize_expr(v) for v in word_values],
            'input_memory': attrs.get('input_memory'),
        }
        if path_conditioned_words:
            native['path_conditioned_input'] = path_conditioned_words
            native['notes'] = ['precompile_input_path_conditioned']
            raw_args = attrs.get('args') or []
            call_expr = normalize_expr(f"{attrs.get('op')}({', '.join(map(str, raw_args))})") if raw_args else str(attrs.get('op'))
            native['solidity_like'] = f"require(({call_expr} != 0)); /* path-conditioned {PRECOMPILES[target]} input */"
        snapshot = self.call_snapshot(attrs, words)
        call_args = {
            'gas': str(attrs.get('gas') or ''),
            'target': str(attrs.get('target') or ''),
            'value': str(attrs.get('value') or '0'),
            'input_ptr': str(attrs.get('input_ptr') or ''),
            'input_size': str(attrs.get('input_size') or ''),
            'output_ptr': str(attrs.get('output_ptr') or ''),
            'output_size': str(attrs.get('output_size') or ''),
        }
        lifted = None
        if not path_conditioned_words:
            lifted = native_precompile_call(PRECOMPILES[target], str(attrs.get('op') or ''), call_args, snapshot, self.int_node(attrs.get('cfg_node_id')) or 0)
        if lifted:
            native['native_precompile'] = lifted
            native['solidity_like'] = lifted.get('solidity_like')
        return native

    @staticmethod
    def call_snapshot(attrs: dict[str, Any], words: list[dict[str, Any]]) -> dict[str, Any]:
        input_size = parse_int_literal(strip_ssa(str(attrs.get('input_size') or '')) or str(attrs.get('input_size') or ''))
        complete = bool(words) and not any(
            word.get('path_conditioned') or SemanticOverlayBuilder.is_unknown_value(word.get('value'))
            for word in words
        )
        rendered_words = []
        for index, word in enumerate(words):
            rendered = {
                'value': str(word.get('value')),
                'size': min(32, max(0, (input_size or 32) - index * 32)),
                'memory_ssa': word.get('memory_ssa'),
            }
            if word.get('path_conditioned'):
                rendered['path_conditioned_candidates'] = word.get('path_conditioned_candidates')
            rendered_words.append(rendered)
        return {
            'ptr': attrs.get('input_ptr'),
            'size': attrs.get('input_size'),
            'base': attrs.get('input_ptr'),
            'start': 0,
            'length': input_size,
            'raw_range': f"memory[{attrs.get('input_ptr')} : {attrs.get('input_ptr')} + {attrs.get('input_size')}]",
            'selector': None,
            'arguments': [],
            'complete_static_abi': False,
            'source_writes': [],
            'words': rendered_words,
            'complete_memory_ssa': complete,
        }

    def precompile_output_overlays(self, effects: list[EffectNode], call_overlays: list[SemanticOverlay]) -> list[SemanticOverlay]:
        out: list[SemanticOverlay] = []
        reads = [e for e in effects if e.kind == 'MemoryRead']
        writes = [e for e in effects if e.kind == 'MemoryWrite']
        for overlay in call_overlays:
            if overlay.kind == 'PathConditionedPrecompileCall':
                out.extend(self.path_conditioned_precompile_output_overlays(overlay, reads, writes))
                continue
            native = overlay.attrs.get('native_precompile') or {}
            output_expr = native.get('output_word_expression')
            if not output_expr:
                continue
            output_ptr = self.norm_ptr(overlay.attrs.get('output_ptr'))
            call_node = self.int_node(overlay.attrs.get('cfg_node_id'))
            for read in reads:
                if self.norm_ptr(read.attrs.get('read_from')) != output_ptr:
                    continue
                read_node = self.int_node(read.attrs.get('cfg_node_id'))
                if call_node is not None and read_node is not None and read_node <= call_node:
                    continue
                if self.memory_rewritten_between(writes, output_ptr, call_node, read_node):
                    continue
                target = read.attrs.get('value')
                out.append(self.ov('PrecompileOutputRead', [overlay.effects[0], read.effect_id], read.stmt_refs, {
                    'source_precompile_overlay': overlay.overlay_id,
                    'target': target,
                    'value': output_expr,
                    'solidity_like': f'{target} = {output_expr};' if target else output_expr,
                }))
                break
        out.extend(self.returndatasize_precompile_output_overlays(reads, call_overlays))
        return out

    def call_output_overlays(
        self,
        effects: list[EffectNode],
        call_overlays: list[SemanticOverlay],
    ) -> list[SemanticOverlay]:
        """Project a MemorySSA-proven ordinary CALL output as one high fact.

        This intentionally consumes the structured relation attached by
        ``EffectLifter.attach_cross_statement_call_outputs``.  It performs no
        source-text recognition and no new reachability or memory analysis.
        Precompile output overlays retain their specialized projections.
        """
        ordinary_kinds = {"LowLevelCall", "StaticCallOverlay", "DelegateCallOverlay"}
        overlays_by_effect: dict[str, list[SemanticOverlay]] = {}
        for overlay in call_overlays:
            if overlay.kind not in ordinary_kinds:
                continue
            for effect_id in overlay.effects:
                overlays_by_effect.setdefault(str(effect_id), []).append(overlay)

        out: list[SemanticOverlay] = []
        for read in effects:
            if read.kind != "MemoryRead":
                continue
            query = read.attrs.get("memory_read") or {}
            resolved_output = query.get("overridden_by_call_output") or {}
            if not isinstance(resolved_output, dict):
                continue
            # The upstream MemorySSA query must establish a complete first
            # word.  A partial or unknown output is deliberately left as the
            # ordinary expression instead of being guessed as a call result.
            if not query.get("complete") or query.get("has_unknown"):
                continue
            source_effect = str(resolved_output.get("effect_id") or "")
            candidates = overlays_by_effect.get(source_effect) or []
            if len(candidates) != 1:
                continue
            call_overlay = candidates[0]
            target = read.attrs.get("value")
            if not target:
                continue
            out.append(self.ov("CallOutputRead", [call_overlay.effects[0], read.effect_id], read.stmt_refs, self.clean({
                "source_call_overlay": call_overlay.overlay_id,
                "target": target,
                "call_kind": call_overlay.attrs.get("op") or call_overlay.attrs.get("call_kind"),
                "target_address": call_overlay.attrs.get("target_solidity") or call_overlay.attrs.get("target"),
                "selector": call_overlay.attrs.get("selector"),
                "selector_signature": call_overlay.attrs.get("selector_signature"),
                "arguments": call_overlay.attrs.get("arguments"),
                "word_index": 0,
                # This is a source-neutral semantic operation, rather than a
                # textual rendering containing an upstream effect identifier.
                "value": "external_call_return_word(0)",
                "solidity_like": f"{target} = external_call_return_word(0);",
                "resolution": "memory_ssa_proven_complete_call_output",
            })))
        return out

    def returndatasize_precompile_output_overlays(
        self,
        reads: list[EffectNode],
        call_overlays: list[SemanticOverlay],
    ) -> list[SemanticOverlay]:
        """Archive path-sensitive mload(returndatasize()) resolutions.

        The effect layer owns the EVM-specific resolution: a fixed-size
        precompile result reads the call output when returndata is one word,
        while zero-length returndata leaves the queried memory word unchanged.
        This method only projects those existing candidates into the unified
        overlay model.
        """
        out: list[SemanticOverlay] = []
        overlays_by_effect: dict[str, SemanticOverlay] = {}
        for overlay in call_overlays:
            for effect_id in overlay.effects:
                overlays_by_effect.setdefault(str(effect_id), overlay)
        for read in reads:
            raw_candidates = read.attrs.get('returndatasize_pointer_candidates') or []
            if not raw_candidates:
                continue
            target = read.attrs.get('value')
            source_effects = list(dict.fromkeys(
                str(candidate.get('call_effect'))
                for candidate in raw_candidates
                if candidate.get('call_effect')
            ))
            source_overlays = list(dict.fromkeys(
                overlays_by_effect[effect_id].overlay_id
                for effect_id in source_effects
                if effect_id in overlays_by_effect
            ))
            candidates = []
            for candidate in raw_candidates:
                value = candidate.get('value')
                candidates.append(self.clean({
                    'status': candidate.get('status'),
                    'condition': candidate.get('condition'),
                    'returndata_size': candidate.get('returndata_size'),
                    'pointer': candidate.get('pointer'),
                    'value': value,
                    'target': target,
                    'source': candidate.get('source'),
                    'call_effect': candidate.get('call_effect'),
                    'call_temp': candidate.get('call_temp'),
                    'precompile': candidate.get('precompile'),
                    'precompile_address': candidate.get('precompile_address'),
                    'memory_read': candidate.get('memory_read'),
                    'solidity_like': f'{target} = {value};'
                    if target and candidate.get('status') == 'resolved'
                    else None,
                }))
            effects_used = list(dict.fromkeys(source_effects + [read.effect_id]))
            out.append(self.ov('PathConditionedPrecompileOutputRead', effects_used, read.stmt_refs, self.clean({
                'source_precompile_overlay': source_overlays[0] if len(source_overlays) == 1 else None,
                'source_precompile_overlays': source_overlays,
                'target': target,
                'pointer_kind': 'returndatasize',
                'candidates': candidates,
                'path_states': read.attrs.get('path_states') or [],
                'sink_resolution': read.attrs.get('sink_resolution'),
            })))
        return out

    def path_conditioned_precompile_output_overlays(
        self,
        overlay: SemanticOverlay,
        reads: list[EffectNode],
        writes: list[EffectNode],
    ) -> list[SemanticOverlay]:
        out: list[SemanticOverlay] = []
        output_ptr = self.norm_ptr(overlay.attrs.get('output_ptr'))
        call_node = self.int_node(overlay.attrs.get('cfg_node_id'))
        native_candidates = [
            candidate for candidate in overlay.attrs.get('candidates') or []
            if (candidate.get('native_precompile') or {}).get('output_word_expression')
        ]
        if not native_candidates:
            return out
        for read in reads:
            if self.norm_ptr(read.attrs.get('read_from')) != output_ptr:
                continue
            read_node = self.int_node(read.attrs.get('cfg_node_id'))
            if call_node is not None and read_node is not None and read_node <= call_node:
                continue
            if self.memory_rewritten_between(writes, output_ptr, call_node, read_node):
                continue
            target = read.attrs.get('value')
            candidates = []
            for candidate in native_candidates:
                value = candidate.get('output_word_inline_expression') or candidate['native_precompile']['output_word_expression']
                candidates.append(self.clean({
                    'status': 'resolved',
                    'condition': candidate.get('condition'),
                    'value': value,
                    'target': target,
                    'source_precompile_condition': candidate.get('condition'),
                    'source_precompile_result': (candidate.get('native_precompile') or {}).get('result_name'),
                    'solidity_like': f'{target} = {value};' if target else value,
                }))
            out.append(self.ov('PathConditionedPrecompileOutputRead', [overlay.effects[0], read.effect_id], read.stmt_refs, {
                'source_precompile_overlay': overlay.overlay_id,
                'target': target,
                'candidates': candidates,
                'path_states': read.attrs.get('path_states') or [],
            }))
            break
        return out

    def abi_call_data_overlays(self, effects: list[EffectNode], overlays: list[SemanticOverlay]) -> list[SemanticOverlay]:
        out: list[SemanticOverlay] = []
        call_overlays = [
            overlay for overlay in overlays
            if overlay.kind in {'LowLevelCall', 'StaticCallOverlay', 'DelegateCallOverlay'}
        ]
        writes = [effect for effect in effects if effect.kind == 'MemoryWrite']
        values = [effect for effect in effects if effect.kind == 'ValueDef']
        struct_reads = [overlay for overlay in overlays if overlay.kind == 'StructFieldRead']
        for call_overlay in call_overlays:
            construction = self.abi_construction_for_call(call_overlay, writes, values, struct_reads)
            if not construction:
                continue
            construction_overlay = self.ov('AbiCallDataConstruction', call_overlay.effects, call_overlay.stmt_refs, construction)
            out.append(construction_overlay)
            encoded_attrs = dict(call_overlay.attrs)
            encoded_attrs['abi_calldata'] = {
                'overlay': construction_overlay.overlay_id,
                **construction,
            }
            encoded_attrs['selector'] = construction.get('selector')
            encoded_attrs['selector_signature'] = construction.get('signature')
            encoded_attrs['selector_match'] = construction.get('selector_match')
            encoded_attrs['arguments'] = [arg.get('value') for arg in construction.get('arguments') or []]
            encoded_attrs['solidity_like'] = self.abi_encoded_low_level_call_solidity_like(encoded_attrs, construction)
            out.append(self.ov('AbiEncodedLowLevelCall', call_overlay.effects, call_overlay.stmt_refs, encoded_attrs))
        return out

    def abi_construction_for_call(
        self,
        call_overlay: SemanticOverlay,
        writes: list[EffectNode],
        values: list[EffectNode],
        struct_reads: list[SemanticOverlay],
    ) -> dict[str, Any] | None:
        attrs = call_overlay.attrs
        input_ptr = attrs.get('input_ptr')
        input_size = attrs.get('input_size')
        if not input_ptr:
            return None
        input_addr = self.safe_memory_address(input_ptr)
        base = str(input_addr.get('base') or '')
        start = input_addr.get('offset')
        if not base or start is None:
            return None
        call_node = self.int_node(attrs.get('cfg_node_id'))
        prior_writes = [
            write for write in writes
            if call_node is None or (self.int_node(write.attrs.get('cfg_node_id')) or -1) <= call_node
        ]
        selector = None
        selector_source = None
        selector_write = None
        head_words: list[dict[str, Any]] = []
        raw_writes: list[dict[str, Any]] = []
        for write in prior_writes:
            for alias in write.attrs.get('aliases') or []:
                if str(alias.get('base')) != base:
                    continue
                offset = self.int_node(alias.get('offset'))
                if offset is None:
                    try:
                        offset = int(alias.get('offset'))
                    except Exception:
                        continue
                value = write.attrs.get('value')
                relative = offset - start
                raw_writes.append({
                    'effect': write.effect_id,
                    'address': write.attrs.get('address'),
                    'base': base,
                    'offset': offset,
                    'relative_offset': relative,
                    'value': value,
                    'value_normalized': normalize_expr(value),
                    'alias': alias,
                })
                if offset <= start and start + 4 <= offset + 32:
                    extraction = self.partial_word_extraction(value, start - offset, 4)
                    normalized = normalize_selector_value(extraction)
                    if normalized:
                        selector = normalized
                        selector_source = extraction
                        selector_write = write.effect_id
                if relative >= 4 and (relative - 4) % 32 == 0:
                    head_words.append({
                        'index': (relative - 4) // 32,
                        'offset': relative,
                        'value': value,
                        'value_normalized': normalize_expr(value),
                        'write_effect': write.effect_id,
                    })
        if not selector:
            return None
        match = self.selector_match(selector, preferred_kind='function')
        size_expr = self.resolve_value_expression(input_size, values)
        dynamic_arg = self.dynamic_array_argument_from_size(size_expr)
        best_match = (match or {}).get('best_match') or {}
        arguments = self.abi_arguments_from_layout(head_words, dynamic_arg, struct_reads, match)
        return {
            'input_ptr': input_ptr,
            'input_size': input_size,
            'input_size_expression': size_expr,
            'base': base,
            'start_offset': start,
            'selector': selector,
            'selector_source': selector_source,
            'selector_write_effect': selector_write,
            'selector_match': match,
            'signature': best_match.get('signature'),
            'head_words': sorted(head_words, key=lambda item: int(item.get('index') or 0)),
            'arguments': arguments,
            'raw_memory_writes': raw_writes,
            'construction_model': 'call_sink_symbolic_base_backward_memoryssa',
            'complete': bool(selector and (arguments or not self.signature_arg_types(best_match.get('signature')))),
        }

    @staticmethod
    def safe_memory_address(expr: Any) -> dict[str, Any]:
        try:
            return parse_memory_address(str(expr))
        except Exception:
            return {'base': str(expr or ''), 'offset': None, 'offset_expr': None, 'expr': str(expr or '')}

    @staticmethod
    def partial_word_extraction(value: Any, source_offset: int, size: int) -> str:
        if source_offset == 0 and size == 32:
            return str(value)
        if source_offset + size == 32:
            return f"low_bytes({value}, {size})"
        if source_offset == 0:
            return f"high_bytes({value}, {size})"
        return f"bytes({value}, offset={source_offset}, size={size})"

    def selector_match(self, selector: str, preferred_kind: str | None = None) -> dict[str, Any] | None:
        matches = list(self.selector_registry.get(str(selector).lower(), []))
        preferred = [item for item in matches if item.get('kind') == preferred_kind] if preferred_kind else matches
        best = preferred[0] if preferred else (matches[0] if matches else None)
        return {
            'selector': selector,
            'preferred_kind': preferred_kind,
            'best_match': best,
            'matches': matches,
            'match_status': 'matched' if best else 'unmatched',
        }

    @staticmethod
    def resolve_value_expression(value: Any, values: list[EffectNode]) -> str:
        text = str(value or '')
        for effect in values:
            if text in [str(target) for target in effect.attrs.get('targets') or []]:
                return str(effect.attrs.get('value') or text)
        return text

    @staticmethod
    def dynamic_array_argument_from_size(expr: Any) -> dict[str, Any] | None:
        text = str(expr or '').replace(' ', '')
        patterns = [
            r'^add\(0x44,shl\(5,mload\(([^()]+)\)\)\)$',
            r'^add\(68,shl\(5,mload\(([^()]+)\)\)\)$',
            r'^add\(0x44,mul\(mload\(([^()]+)\),0x20\)\)$',
            r'^add\(68,mul\(mload\(([^()]+)\),32\)\)$',
        ]
        for pattern in patterns:
            match = __import__('re').match(pattern, text)
            if match:
                return {
                    'kind': 'dynamic_array',
                    'value': match.group(1),
                    'length_expr': f"mload({match.group(1)})",
                    'length_semantic': f"{match.group(1)}.length",
                    'total_size_model': '4 + 32 + 32 + length * 32',
                }
        return None

    def abi_arguments_from_layout(
        self,
        head_words: list[dict[str, Any]],
        dynamic_arg: dict[str, Any] | None,
        struct_reads: list[SemanticOverlay],
        selector_match: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        best_match = (selector_match or {}).get('best_match') or {}
        arg_types = self.signature_arg_types(best_match.get('signature'))
        args: list[dict[str, Any]] = []
        head_by_index = {int(item.get('index') or 0): item for item in head_words}
        if dynamic_arg and head_by_index.get(0, {}).get('value') in {'0x20', '32'}:
            value = dynamic_arg['value']
            item = {
                'index': 0,
                'type': arg_types[0] if arg_types else None,
                'value': value,
                'value_normalized': normalize_expr(value),
                'kind': dynamic_arg.get('kind'),
                'head_offset': head_by_index[0].get('value'),
                'length_expr': dynamic_arg.get('length_expr'),
                'length_semantic': dynamic_arg.get('length_semantic'),
            }
            source = self.struct_read_source_for_value(value, struct_reads)
            if source:
                item['source'] = source
                item['value_semantic'] = f"{source.get('struct_object')}.{(source.get('field') or {}).get('name')}"
            args.append(item)
            return args
        for index, head in sorted(head_by_index.items()):
            value = head.get('value')
            args.append({
                'index': index,
                'type': arg_types[index] if index < len(arg_types) else None,
                'value': value,
                'value_normalized': normalize_expr(value),
                'kind': 'static_word',
                'head_offset': head.get('offset'),
                'write_effect': head.get('write_effect'),
            })
        return args

    @staticmethod
    def signature_arg_types(signature: Any) -> list[str]:
        text = str(signature or '')
        if '(' not in text or not text.endswith(')'):
            return []
        inner = text.split('(', 1)[1][:-1]
        if not inner:
            return []
        return [part.strip() for part in inner.split(',')]

    @staticmethod
    def struct_read_source_for_value(value: Any, struct_reads: list[SemanticOverlay]) -> dict[str, Any] | None:
        for overlay in struct_reads:
            if str(overlay.attrs.get('value')) == str(value):
                return {
                    'overlay': overlay.overlay_id,
                    'struct_object': overlay.attrs.get('struct_object'),
                    'struct_type': overlay.attrs.get('struct_type'),
                    'field': overlay.attrs.get('field'),
                }
        return None

    @staticmethod
    def abi_encoded_low_level_call_solidity_like(attrs: dict[str, Any], construction: dict[str, Any]) -> str | None:
        target = attrs.get('target_solidity') or attrs.get('target')
        selector = construction.get('selector')
        if not target or not selector:
            return None
        signature = construction.get('signature')
        args = [
            normalize_expr(arg.get('value_semantic') or arg.get('value'))
            for arg in construction.get('arguments') or []
            if arg.get('value') is not None or arg.get('value_semantic') is not None
        ]
        selector_expr = f"bytes4({selector})" + (f" /* {signature} */" if signature else "")
        arg_suffix = (", " + ", ".join(args)) if args else ""
        payload = f"abi.encodeWithSelector({selector_expr}{arg_suffix})"
        value = normalize_expr(attrs.get('value')) if attrs.get('value') is not None else '0'
        evaluated_args = attrs.get('evaluated_args') or []
        gas = str(evaluated_args[0]) if evaluated_args else (normalize_expr(attrs.get('gas')) if attrs.get('gas') else None)
        result = attrs.get('result')
        prefix = f"{result} = " if result else ""
        output_ptr = attrs.get('output_ptr')
        output_size = attrs.get('output_size')
        output = f"memory[{output_ptr}:{output_size}]" if output_ptr is not None and output_size is not None else "memory[unknown]"
        call_kind = attrs.get('op') or 'call'
        return f"{prefix}yul{call_kind[0].upper() + call_kind[1:]}(gas: {gas}, target: {target}, value: {value}, input: {payload}, output: {output});"

    def division_guard_overlays(self, effects: list[EffectNode]) -> list[SemanticOverlay]:
        out: list[SemanticOverlay] = []
        for effect in effects:
            if effect.kind not in {'ValueDef', 'Branch', 'StorageWrite'}:
                continue
            exprs = []
            if effect.kind == 'ValueDef':
                exprs.append(effect.attrs.get('value'))
            elif effect.kind == 'Branch':
                exprs.append(effect.attrs.get('condition'))
            elif effect.kind == 'StorageWrite':
                exprs.append(effect.attrs.get('value'))
            for expr in exprs:
                for guard in division_guards(expr):
                    condition = guard.removeprefix('require(').removesuffix(');')
                    out.append(self.ov('RequireOverlay', effect.effect_id, effect.stmt_refs, {
                        'condition': condition,
                        'require_like': guard,
                        'revert_payload': 'division_by_zero_guard',
                        'source_expression': expr,
                    }))
        return out

    @staticmethod
    def norm_ptr(value: Any) -> str:
        return str(value or '').replace(' ', '').lower()

    @staticmethod
    def int_node(value: Any) -> int | None:
        try:
            return int(str(value).removeprefix('N'))
        except Exception:
            return None

    def memory_rewritten_between(self, writes: list[EffectNode], ptr: str, start: int | None, end: int | None) -> bool:
        for write in writes:
            if self.norm_ptr(write.attrs.get('address')) != ptr:
                continue
            node = self.int_node(write.attrs.get('origin_node'))
            if node is None:
                continue
            if start is not None and node <= start:
                continue
            if end is not None and node >= end:
                continue
            return True
        return False

    def memory_object_construction_overlays(self, unit: FunctionUnit, type_env: Any, effects: list[EffectNode]) -> list[SemanticOverlay]:
        writes = [e for e in effects if e.kind == 'MemoryWrite']
        out: list[SemanticOverlay] = []
        allocations = self.manual_memory_allocations(effects)
        allocation_writes = {
            id(allocation): self.memory_writes_inside_allocation(unit, writes, allocation)
            for allocation in allocations
        }
        for allocation in allocations:
            attrs = dict(allocation)
            attrs['stored_values'] = allocation_writes.get(id(allocation), [])
            out.append(self.ov('MemoryRegionAllocate', attrs.get('effects', []), attrs.get('stmt_refs', []), attrs))
            for stored in attrs['stored_values']:
                out.append(self.ov('MemoryRegionWrite', stored.get('effect', []), stored.get('stmt_refs', []), {
                    'region_base': attrs.get('base'),
                    'region_allocation_effect': attrs.get('write_effect'),
                    'address': stored.get('address'),
                    'value': stored.get('value'),
                    'value_normalized': normalize_expr(stored.get('value')),
                    'aliases': stored.get('aliases', []),
                    'stmt_refs': stored.get('stmt_refs', []),
                }))
        for ret in unit.returns:
            if not ret.name or not getattr(type_env, 'is_memory_pointer_return', lambda _x: False)(ret.name):
                continue
            layout = getattr(type_env, 'struct_layout_for_var', lambda _x: None)(ret.name)
            if not layout:
                continue
            bindings = []
            effect_ids = []
            refs = []
            for write in writes:
                field = self.struct_field_write(type_env, ret.name, write)
                if not field:
                    continue
                value = write.attrs.get('value')
                memory_object = self.memory_object_for_field_value(str(value), allocations, allocation_writes)
                item = {
                    'field': field,
                    'value': value,
                    'write_effect': write.effect_id,
                    'semantic': 'manual_memory_object_pointer' if memory_object else 'memory_value',
                }
                if memory_object:
                    item['memory_object'] = memory_object
                bindings.append(item)
                effect_ids.append(write.effect_id)
                refs.extend(write.stmt_refs)
            if not bindings:
                continue
            overlay_attrs = {
                'target': ret.name,
                'type': ret.type_string,
                'struct_type': self.struct_type_ref(layout, ret.type_string),
                'fields': bindings,
                'allocation': self.best_allocation_for_bindings(allocations, bindings),
                'solidity_equivalent': 'not_exact',
                'reason': 'manual_memory_layout_or_custom_allocator',
                'solidity_like': self.return_struct_construction_like(ret.name, ret.type_string, bindings),
            }
            out.append(self.ov('StructInitializationFragment', effect_ids, list(dict.fromkeys(refs)), overlay_attrs))
        return out

    def memory_writes_inside_allocation(self, unit: FunctionUnit, writes: list[EffectNode], allocation: dict[str, Any]) -> list[dict[str, Any]]:
        ret_names = {ret.name for ret in unit.returns if ret.name}
        start = allocation.get('start_node')
        end = allocation.get('end_node')
        stored: list[dict[str, Any]] = []
        for write in writes:
            node = self.int_node(write.attrs.get('cfg_node_id'))
            if node is None:
                continue
            if start is not None and node < start:
                continue
            if end is not None and node > end:
                continue
            if str(write.attrs.get('address')) in {'0x40', '64'}:
                continue
            if self.write_targets_return_object(write, ret_names):
                continue
            aliases = write.attrs.get('aliases') or []
            allocation_aliases = [
                alias for alias in aliases
                if str(alias.get('base')) == 'mload(0x40)' or str(alias.get('base')).startswith('add(mload(0x40),')
            ]
            if not allocation_aliases:
                continue
            stored.append({
                'effect': write.effect_id,
                'address': write.attrs.get('address'),
                'value': write.attrs.get('value'),
                'aliases': allocation_aliases,
                'stmt_refs': write.stmt_refs,
            })
        return stored

    @staticmethod
    def write_targets_return_object(write: EffectNode, ret_names: set[str]) -> bool:
        for alias in write.attrs.get('aliases') or []:
            if str(alias.get('base')) in ret_names:
                return True
        return False

    @staticmethod
    def memory_object_for_field_value(value: str, allocations: list[dict[str, Any]], allocation_writes: dict[int, list[dict[str, Any]]]) -> dict[str, Any] | None:
        for allocation in allocations:
            stored_values = allocation_writes.get(id(allocation), [])
            direct_writes = [item for item in stored_values if str(item.get('address')) == value]
            alias_writes = []
            for item in stored_values:
                for alias in item.get('aliases') or []:
                    if str(alias.get('key')) == value or str(alias.get('base')) == value:
                        alias_writes.append(item)
                        break
            writes = direct_writes or alias_writes
            if not writes:
                continue
            return {
                'allocation': allocation,
                'pointer': value,
                'stored_values': writes,
            }
        return None

    def manual_memory_allocations(self, effects: list[EffectNode]) -> list[dict[str, Any]]:
        reads = [e for e in effects if e.kind == 'MemoryRead' and str(e.attrs.get('read_from')) == '0x40']
        writes = [e for e in effects if e.kind == 'MemoryWrite' and str(e.attrs.get('address')) == '0x40']
        value_defs_by_name = self.value_defs_by_name(effects)
        out = []
        for write in writes:
            start_node = None
            read_effect = None
            write_node = self.int_node(write.attrs.get('cfg_node_id'))
            for read in reads:
                read_node = self.int_node(read.attrs.get('cfg_node_id'))
                if read_node is None or write_node is None or read_node > write_node:
                    continue
                if start_node is None or read_node > start_node:
                    start_node = read_node
                    read_effect = read
            if not self.is_strict_free_memory_pointer_advance(write, read_effect, value_defs_by_name):
                continue
            effects_ids = [write.effect_id]
            refs = list(write.stmt_refs)
            if read_effect:
                effects_ids.insert(0, read_effect.effect_id)
                refs = list(dict.fromkeys(read_effect.stmt_refs + refs))
            out.append({
                'base': 'mload(0x40)',
                'new_free_pointer': normalize_expr(write.attrs.get('value')),
                'write_effect': write.effect_id,
                'read_effect': read_effect.effect_id if read_effect else None,
                'start_node': start_node,
                'end_node': write_node,
                'effects': effects_ids,
                'stmt_refs': refs,
            })
        return out

    def is_strict_free_memory_pointer_advance(
        self,
        write: EffectNode,
        read_effect: EffectNode | None,
        value_defs_by_name: dict[str, list[EffectNode]],
    ) -> bool:
        """Accept only proven free-memory-pointer advances.

        Writes to 0x40 are often temporary scratch writes. A MemoryRegionAllocate
        overlay is emitted only when the new pointer is derived from the old
        free pointer by an add/cursor chain.
        """
        write_node = self.int_node(write.attrs.get('cfg_node_id'))
        base_symbols = {'mload(0x40)'}
        if read_effect is not None:
            read_value = str(read_effect.attrs.get('value') or '').strip()
            if read_value:
                base_symbols.add(read_value)
        for name, defs in value_defs_by_name.items():
            for value_def in defs:
                def_node = self.int_node(value_def.attrs.get('cfg_node_id'))
                if write_node is not None and def_node is not None and def_node > write_node:
                    continue
                if self.expr_is_free_memory_pointer_base(value_def.attrs.get('value'), base_symbols, value_defs_by_name, write_node):
                    base_symbols.add(str(name))
                    break
        value = write.attrs.get('value')
        if self.expr_is_free_memory_pointer_base(value, base_symbols, value_defs_by_name, write_node):
            return False
        return self.expr_is_free_memory_pointer_advance(value, base_symbols, value_defs_by_name, write_node)

    def expr_is_free_memory_pointer_base(
        self,
        expr: Any,
        base_symbols: set[str],
        value_defs_by_name: dict[str, list[EffectNode]],
        before_node: int | None,
        seen: set[str] | None = None,
    ) -> bool:
        text = self.strip_outer_parens(str(expr or '').strip())
        if not text:
            return False
        if text in base_symbols or normalize_expr(text) == 'mload(0x40)':
            return True
        name, args = call_parts(text)
        if name == 'mload' and len(args) == 1 and str(args[0]).strip() == '0x40':
            return True
        if name:
            return False
        value_def = self.latest_value_def_before(text, value_defs_by_name, before_node)
        if not value_def:
            return False
        seen = seen or set()
        if text in seen:
            return False
        return self.expr_is_free_memory_pointer_base(value_def.attrs.get('value'), base_symbols, value_defs_by_name, before_node, seen | {text})

    def expr_depends_on_free_memory_pointer(
        self,
        expr: Any,
        base_symbols: set[str],
        value_defs_by_name: dict[str, list[EffectNode]],
        before_node: int | None,
        seen: set[str] | None = None,
    ) -> bool:
        text = self.strip_outer_parens(str(expr or '').strip())
        if not text:
            return False
        if self.expr_is_free_memory_pointer_base(text, base_symbols, value_defs_by_name, before_node):
            return True
        seen = seen or set()
        if text in seen:
            return False
        name, args = call_parts(text)
        if name:
            return any(self.expr_depends_on_free_memory_pointer(arg, base_symbols, value_defs_by_name, before_node, seen | {text}) for arg in args)
        value_def = self.latest_value_def_before(text, value_defs_by_name, before_node)
        if not value_def:
            return False
        return self.expr_depends_on_free_memory_pointer(value_def.attrs.get('value'), base_symbols, value_defs_by_name, before_node, seen | {text})

    def expr_is_free_memory_pointer_advance(
        self,
        expr: Any,
        base_symbols: set[str],
        value_defs_by_name: dict[str, list[EffectNode]],
        before_node: int | None,
        seen: set[str] | None = None,
    ) -> bool:
        text = self.strip_outer_parens(str(expr or '').strip())
        if not text:
            return False
        seen = seen or set()
        if text in seen:
            return False
        name, args = call_parts(text)
        if name == 'add' and len(args) == 2:
            left_dep = self.expr_depends_on_free_memory_pointer(args[0], base_symbols, value_defs_by_name, before_node, seen | {text})
            right_dep = self.expr_depends_on_free_memory_pointer(args[1], base_symbols, value_defs_by_name, before_node, seen | {text})
            if left_dep == right_dep:
                return False
            added_expr = args[1] if left_dep else args[0]
            return not self.is_zero_literal(added_expr)
        if name:
            return False
        value_def = self.latest_value_def_before(text, value_defs_by_name, before_node)
        if not value_def:
            return False
        return self.expr_is_free_memory_pointer_advance(value_def.attrs.get('value'), base_symbols, value_defs_by_name, before_node, seen | {text})

    @staticmethod
    def latest_value_def_before(
        name: str,
        value_defs_by_name: dict[str, list[EffectNode]],
        before_node: int | None,
    ) -> EffectNode | None:
        candidates = []
        for value_def in value_defs_by_name.get(str(name).strip(), []):
            node = SemanticOverlayBuilder.int_node(value_def.attrs.get('cfg_node_id'))
            if before_node is not None and node is not None and node > before_node:
                continue
            candidates.append((node if node is not None else -1, value_def))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[-1][1]

    def struct_field_write(self, type_env: Any, target: str, write: EffectNode) -> dict[str, Any] | None:
        for alias in write.attrs.get('aliases') or []:
            if str(alias.get('base')) != target:
                continue
            try:
                offset = int(alias.get('offset'))
            except Exception:
                continue
            field = getattr(type_env, 'struct_field_by_offset', lambda *_args: None)(target, offset)
            if not field:
                continue
            return dict(field)
        return None

    def struct_memory_mutation_overlays(self, unit: FunctionUnit, type_env: Any, effects: list[EffectNode]) -> list[SemanticOverlay]:
        writes = [e for e in effects if e.kind == 'MemoryWrite']
        struct_vars = [
            variable for variable in unit.parameters + unit.returns + unit.locals
            if variable.name and getattr(type_env, 'is_memory_struct', lambda _x: False)(variable)
        ]
        if not struct_vars:
            return []

        out: list[SemanticOverlay] = []
        cursor_reads = self.struct_field_cursor_reads(type_env, struct_vars, effects)
        related_by_cursor = self.memory_writes_by_cursor(writes, cursor_reads)

        for items in cursor_reads.values():
            for item in items:
                field = item.get('field') or {}
                struct_object = item.get('struct_object')
                struct_type = item.get('struct_type')
                cursor = item.get('cursor_var')
                read_effect = item.get('read_effect')
                out.append(self.ov('StructFieldRead', read_effect or [], item.get('stmt_refs', []), {
                    'struct_object': struct_object,
                    'struct_type': struct_type,
                    'field': self.field_ref(field),
                    'value': cursor,
                    'read_from': item.get('read_from'),
                    'solidity_like': f"{cursor} = {struct_object}.{field.get('name')};" if cursor and struct_object and field.get('name') else None,
                }))

        for variable in struct_vars:
            layout = getattr(type_env, 'struct_layout_for_var', lambda _x: None)(variable.name)
            if not layout:
                continue
            mutations: list[dict[str, Any]] = []
            effect_ids: list[str] = []
            refs: list[str] = []
            for write in writes:
                field = self.struct_field_write(type_env, variable.name, write)
                if not field:
                    continue
                value = write.attrs.get('value')
                mutation = {
                    'struct_object': variable.name,
                    'struct_type': self.struct_type_ref(layout, variable.type_string),
                    'field': self.field_ref(field),
                    'new_value': value,
                    'new_value_normalized': normalize_expr(value),
                    'write_effect': write.effect_id,
                    'solidity_like': f"{variable.name}.{field.get('name')} = {normalize_expr(value)};",
                }
                old_source = self.cursor_read_for_field(cursor_reads, variable.name, field)
                if old_source:
                    mutation['old_value_source'] = old_source
                mutations.append(mutation)
                effect_ids.append(write.effect_id)
                refs.extend(write.stmt_refs)
                out.append(self.ov('StructFieldWrite', write.effect_id, write.stmt_refs, {
                    'struct_object': variable.name,
                    'struct_type': self.struct_type_ref(layout, variable.type_string),
                    'field': self.field_ref(field),
                    'value': value,
                    'value_normalized': normalize_expr(value),
                    'write_effect': write.effect_id,
                    'solidity_like': mutation['solidity_like'],
                }))
            if not mutations:
                continue
            related = []
            for mutation in mutations:
                source = mutation.get('old_value_source') or {}
                cursor = source.get('cursor_var')
                if cursor:
                    related.extend(related_by_cursor.get(str(cursor), []))
            attrs = {
                'struct_object': variable.name,
                'struct_type': self.struct_type_ref(layout, variable.type_string),
                'mutations': mutations,
                'related_memory_writes': self.dedupe_related_memory_writes(related),
                'semantic_hint': 'struct_field_update',
                'solidity_like': ' '.join(m['solidity_like'] for m in mutations if m.get('solidity_like')),
            }
            out.append(self.ov('StructMutationFragment', effect_ids, list(dict.fromkeys(refs)), attrs))
            for related_write in attrs['related_memory_writes']:
                out.append(self.ov('CursorBasedMemoryWrite', related_write.get('effect', []), related_write.get('stmt_refs', []), {
                    'struct_object': variable.name,
                    'struct_type': self.struct_type_ref(layout, variable.type_string),
                    'cursor_field': related_write.get('cursor_field'),
                    'memory_write': related_write,
                    'field_updates': mutations,
                    'semantic_hint': 'write_word_then_advance_struct_cursor',
                }))
        return out

    def struct_field_cursor_reads(self, type_env: Any, struct_vars: list[Any], effects: list[EffectNode]) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        struct_names = {variable.name for variable in struct_vars if variable.name}
        for effect in effects:
            if effect.kind != 'MemoryRead':
                continue
            read = effect.attrs.get('memory_read') or {}
            address = read.get('address') or {}
            base = str(address.get('base') or '')
            if base not in struct_names:
                continue
            try:
                offset = int(address.get('offset'))
            except Exception:
                continue
            field = getattr(type_env, 'struct_field_by_offset', lambda *_args: None)(base, offset)
            if not field:
                continue
            layout = getattr(type_env, 'struct_layout_for_var', lambda _x: None)(base)
            value = effect.attrs.get('value')
            item = {
                'cursor_var': value,
                'struct_object': base,
                'struct_type': self.struct_type_ref(layout, base),
                'field': self.field_ref(field),
                'read_effect': effect.effect_id,
                'read_from': effect.attrs.get('read_from'),
                'stmt_refs': effect.stmt_refs,
            }
            out.setdefault(base, []).append(item)
        return out

    @staticmethod
    def cursor_read_for_field(cursor_reads: dict[str, list[dict[str, Any]]], struct_object: str, field: dict[str, Any]) -> dict[str, Any] | None:
        for item in cursor_reads.get(struct_object, []):
            item_field = item.get('field') or {}
            if item_field.get('name') == field.get('name') and item_field.get('offset') == field.get('offset'):
                return item
        return None

    @staticmethod
    def memory_writes_by_cursor(writes: list[EffectNode], cursor_reads: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
        cursor_info = {
            str(item.get('cursor_var')): item
            for items in cursor_reads.values()
            for item in items
            if item.get('cursor_var')
        }
        out: dict[str, list[dict[str, Any]]] = {}
        for write in writes:
            address = str(write.attrs.get('address') or '')
            if address not in cursor_info:
                continue
            source = cursor_info[address]
            field = source.get('field') or {}
            address_semantic = f"old({source.get('struct_object')}.{field.get('name')})" if source.get('struct_object') and field.get('name') else f"old({address})"
            out.setdefault(address, []).append({
                'effect': write.effect_id,
                'address': address,
                'address_semantic': address_semantic,
                'cursor_field': field,
                'value': write.attrs.get('value'),
                'value_normalized': normalize_expr(write.attrs.get('value')),
                'stmt_refs': write.stmt_refs,
            })
        return out

    @staticmethod
    def dedupe_related_memory_writes(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            key = str(item.get('effect'))
            if key in seen:
                continue
            out.append(item)
            seen.add(key)
        return out

    @staticmethod
    def struct_type_ref(layout: dict[str, Any] | None, fallback: Any = None) -> str | None:
        if isinstance(layout, dict):
            return layout.get('canonical_name') or layout.get('name') or (str(fallback) if fallback else None)
        return str(fallback) if fallback else None

    @staticmethod
    def field_ref(field: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(field, dict):
            return {}
        return {
            'name': field.get('name'),
            'type_string': field.get('type_string'),
            'offset': field.get('offset'),
            'index': field.get('index'),
            'src': field.get('src'),
        }

    @staticmethod
    def best_allocation_for_bindings(allocations: list[dict[str, Any]], bindings: list[dict[str, Any]]) -> dict[str, Any] | None:
        for binding in bindings:
            memory_object = binding.get('memory_object') or {}
            allocation = memory_object.get('allocation')
            if allocation:
                return allocation
        return allocations[0] if allocations else None

    @staticmethod
    def return_struct_construction_like(target: str, type_string: str, bindings: list[dict[str, Any]]) -> str:
        parts = [f"/* construct return memory struct {target}: {type_string} */"]
        for binding in bindings:
            field = binding.get('field') or {}
            value = normalize_expr(binding.get('value'))
            parts.append(f"{target}.{field.get('name')} = {value};")
        return " ".join(parts)

    def memory_array_overlays(
        self,
        unit: FunctionUnit,
        type_env: Any,
        effects: list[EffectNode],
        semantic_overlays: list[SemanticOverlay] | None = None,
    ) -> list[SemanticOverlay]:
        out: list[SemanticOverlay] = []
        array_params = {
            variable.name: variable
            for variable in unit.parameters
            if variable.name and getattr(type_env, 'is_memory_array_parameter', lambda _name: False)(variable.name)
        }
        array_aliases = self.struct_array_aliases(semantic_overlays or [])
        if not array_params and not array_aliases:
            return out
        for effect in effects:
            if effect.kind != 'MemoryRead':
                continue
            pointer = effect.attrs.get('read_from')
            read = self.memory_array_read_from_pointer(type_env, pointer, array_aliases)
            if not read:
                continue
            attrs = dict(read)
            attrs.update({
                'source_expression': f"mload({pointer})",
                'read_from': pointer,
                'target': effect.attrs.get('value'),
                'cfg_node_id': effect.attrs.get('cfg_node_id'),
                'path_states': effect.attrs.get('path_states'),
                'memory_read': effect.attrs.get('memory_read'),
            })
            out.append(self.ov(read['overlay_kind'], effect.effect_id, effect.stmt_refs, self.clean(attrs)))
        return out

    @staticmethod
    def struct_array_aliases(overlays: list[SemanticOverlay]) -> dict[str, dict[str, Any]]:
        aliases: dict[str, dict[str, Any]] = {}
        for overlay in overlays:
            if overlay.kind != 'StructFieldRead':
                continue
            field = overlay.attrs.get('field') or {}
            type_string = str(field.get('type_string') or '')
            if '[]' not in type_string:
                continue
            alias = str(overlay.attrs.get('value') or '').strip()
            struct_object = str(overlay.attrs.get('struct_object') or '').strip()
            field_name = str(field.get('name') or '').strip()
            if not alias or not struct_object or not field_name:
                continue
            aliases[alias] = {
                'array': alias,
                'array_type': type_string,
                'element_type': SemanticOverlayBuilder.memory_array_element_type_from_type(type_string),
                'semantic_access': f'{struct_object}.{field_name}',
                'source_overlay': overlay.overlay_id,
            }
        return aliases

    def memory_array_construction_overlays(self, unit: FunctionUnit, type_env: Any, effects: list[EffectNode]) -> list[SemanticOverlay]:
        out: list[SemanticOverlay] = []
        value_defs = [e for e in effects if e.kind == 'ValueDef']
        writes = [e for e in effects if e.kind == 'MemoryWrite']
        for ret in unit.returns:
            if not ret.name or not getattr(type_env, 'is_memory_array', lambda _var: False)(ret):
                continue
            base_def = self.return_array_base_def(ret.name, effects)
            if not base_def:
                continue
            base_node = self.int_node(base_def.attrs.get('cfg_node_id'))
            cursor_defs = self.array_cursor_defs(ret.name, value_defs, base_node)
            cursor_names = set(cursor_defs)
            length_writes = [
                write for write in writes
                if self.memory_write_aliases(write, ret.name, 0)
                and self.node_after(write, base_node)
            ]
            free_updates = [
                write for write in writes
                if str(write.attrs.get('address')) in {'0x40', '64'}
                and self.node_after(write, base_node)
            ]
            if not length_writes or not free_updates:
                continue
            element_writes = [
                write for write in writes
                if write not in length_writes
                and write not in free_updates
                and self.node_after(write, base_node)
                and write.attrs.get('write_kind') not in {'loop_phi'}
                and not memory_is_unknown_value(write.attrs.get('value'))
                and self.array_element_write_matches(write, ret.name, cursor_names)
            ]
            if not element_writes:
                continue
            unique_element_writes = self.semantic_unique_effects(element_writes)
            length_write = self.earliest_effect(length_writes)
            free_update = self.latest_effect(free_updates)
            element_pattern = 'cursor_based' if any(str(w.attrs.get('address')) in cursor_names for w in element_writes) else 'indexed'
            effect_ids = list(dict.fromkeys(
                [base_def.effect_id]
                + [d.effect_id for d in cursor_defs.values()]
                + [w.effect_id for w in unique_element_writes]
                + [length_write.effect_id, free_update.effect_id]
            ))
            refs: list[str] = []
            for effect in [base_def, *cursor_defs.values(), *unique_element_writes, length_write, free_update]:
                refs.extend(effect.stmt_refs)
            out.append(self.ov('MemoryArrayConstruction', effect_ids, list(dict.fromkeys(refs)), self.clean({
                'result': ret.name,
                'array_type': ret.type_string,
                'element_type': self.memory_array_element_type_from_type(ret.type_string),
                'base': ret.name,
                'allocation_source': base_def.attrs.get('value') or f"{ret.name} := mload(0x40)",
                'allocation_effect': base_def.effect_id,
                'data_start': f"{ret.name} + 32",
                'cursor_variables': sorted(cursor_names),
                'cursor_defs': [
                    {
                        'cursor': name,
                        'value': effect.attrs.get('value'),
                        'value_normalized': effect.attrs.get('value_normalized') or normalize_expr(effect.attrs.get('value')),
                        'effect': effect.effect_id,
                    }
                    for name, effect in sorted(cursor_defs.items())
                ],
                'element_write_pattern': element_pattern,
                'element_writes': [
                    {
                        'effect': write.effect_id,
                        'address': write.attrs.get('address'),
                        'value': write.attrs.get('value'),
                        'value_normalized': normalize_expr(write.attrs.get('value')),
                        'path_states': write.attrs.get('path_states') or [],
                        'stmt_refs': write.stmt_refs,
                    }
                    for write in unique_element_writes
                ],
                'length_expr': length_write.attrs.get('value'),
                'length_expr_normalized': normalize_expr(length_write.attrs.get('value')),
                'length_write': length_write.effect_id,
                'free_memory_pointer_update': normalize_expr(free_update.attrs.get('value')),
                'free_memory_pointer_write': free_update.effect_id,
                'path_states': self.merge_path_states([base_def, *element_writes, length_write, free_update]),
                'solidity_equivalent': False,
                'reason': 'manual_dynamic_memory_array_construction',
            })))
        return out

    @staticmethod
    def return_array_base_def(name: str, effects: list[EffectNode]) -> EffectNode | None:
        for effect in effects:
            if effect.kind == 'ValueDef' and name in [str(t) for t in effect.attrs.get('targets') or []]:
                if str(effect.attrs.get('value') or '').replace(' ', '') == 'mload(0x40)':
                    return effect
            if effect.kind == 'MemoryRead' and str(effect.attrs.get('read_from')) == '0x40' and str(effect.attrs.get('value')) == name:
                return effect
        return None

    def array_cursor_defs(self, base: str, value_defs: list[EffectNode], base_node: int | None) -> dict[str, EffectNode]:
        out: dict[str, EffectNode] = {}
        for effect in value_defs:
            if not self.node_after(effect, base_node):
                continue
            targets = [str(t) for t in effect.attrs.get('targets') or []]
            if not targets:
                continue
            value = str(effect.attrs.get('value') or '').strip()
            if self.expr_is_base_plus_word(value, base):
                out[targets[0]] = effect
        return out

    @classmethod
    def expr_is_base_plus_word(cls, expr: Any, base: str) -> bool:
        name, args = call_parts(str(expr or '').strip())
        if name != 'add' or len(args) != 2:
            return False
        left, right = args[0].strip(), args[1].strip()
        return (left == base and cls.is_word_literal(right)) or (right == base and cls.is_word_literal(left))

    @staticmethod
    def is_word_literal(value: Any) -> bool:
        parsed = parse_int_literal(str(value or '').strip())
        return parsed == 32

    @classmethod
    def array_element_write_matches(cls, write: EffectNode, base: str, cursor_names: set[str]) -> bool:
        address = str(write.attrs.get('address') or '').strip()
        if address in cursor_names:
            return True
        for alias in write.attrs.get('aliases') or []:
            try:
                offset = int(alias.get('offset'))
            except Exception:
                offset = None
            alias_base = str(alias.get('base') or '')
            if alias_base == base and offset is not None and offset >= 32:
                return True
            if alias_base in cursor_names:
                return True
        parsed = cls.array_base_and_offset(address)
        if parsed and parsed[0] == base:
            return True
        return False

    @staticmethod
    def memory_write_aliases(write: EffectNode, base: str, offset: int) -> bool:
        if str(write.attrs.get('address')) == base and offset == 0:
            return True
        for alias in write.attrs.get('aliases') or []:
            if str(alias.get('base')) != base:
                continue
            try:
                if int(alias.get('offset')) == offset:
                    return True
            except Exception:
                continue
        return False

    def node_after(self, effect: EffectNode, start_node: int | None) -> bool:
        if start_node is None:
            return True
        node = self.int_node(effect.attrs.get('cfg_node_id'))
        return node is None or node >= start_node

    def earliest_effect(self, effects: list[EffectNode]) -> EffectNode:
        return sorted(effects, key=lambda e: self.int_node(e.attrs.get('cfg_node_id')) or 10**9)[0]

    def latest_effect(self, effects: list[EffectNode]) -> EffectNode:
        return sorted(effects, key=lambda e: self.int_node(e.attrs.get('cfg_node_id')) or -1)[-1]

    @staticmethod
    def semantic_unique_effects(effects: list[EffectNode]) -> list[EffectNode]:
        out: list[EffectNode] = []
        seen: set[tuple[Any, ...]] = set()
        for effect in effects:
            key = (
                effect.kind,
                tuple(effect.stmt_refs),
                effect.attrs.get('address'),
                effect.attrs.get('value'),
                effect.attrs.get('cfg_node_id'),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(effect)
        return out

    @staticmethod
    def merge_path_states(effects: list[EffectNode]) -> list[str]:
        out: list[str] = []
        for effect in effects:
            for state in effect.attrs.get('path_states') or []:
                if state not in out:
                    out.append(state)
        return out

    @staticmethod
    def memory_array_element_type_from_type(type_string: Any) -> str | None:
        text = str(type_string or '').replace(' memory', '').replace(' calldata', '').replace(' storage', '').strip()
        return text[:-2] if text.endswith('[]') else None

    def memory_array_read_from_mload_expr(self, type_env: Any, expr: Any) -> dict[str, Any] | None:
        name, args = call_parts(str(expr or '').strip())
        if name != 'mload' or len(args) != 1:
            return None
        read = self.memory_array_read_from_pointer(type_env, args[0])
        if not read:
            return None
        read['source_expression'] = str(expr)
        return read

    def memory_array_read_from_pointer(
        self,
        type_env: Any,
        pointer: Any,
        array_aliases: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        text = str(pointer or '').strip()
        if not text:
            return None
        alias = (array_aliases or {}).get(text)
        if alias:
            semantic_access = alias.get('semantic_access') or text
            return self.clean({
                'overlay_kind': 'MemoryArrayLengthRead',
                'array': text,
                'array_type': alias.get('array_type'),
                'data_location': 'memory',
                'access': f'{semantic_access}.length',
                'element_type': alias.get('element_type'),
                'layout': 'solidity_memory_dynamic_array_length_at_base',
                'derived_from_struct_field': True,
                'source_overlay': alias.get('source_overlay'),
            })
        parsed_alias = self.array_base_and_offset(text)
        if parsed_alias and parsed_alias[0] in (array_aliases or {}):
            base, offset = parsed_alias
            alias = (array_aliases or {})[base]
            index_expr = self.memory_array_index_from_offset(offset)
            semantic_access = alias.get('semantic_access') or base
            return self.clean({
                'overlay_kind': 'MemoryArrayElementRead',
                'array': base,
                'array_type': alias.get('array_type'),
                'data_location': 'memory',
                'element_type': alias.get('element_type'),
                'offset': offset,
                'index': index_expr,
                'access': f'{semantic_access}[{index_expr}]',
                'layout': 'solidity_memory_dynamic_array_elements_after_length_word',
                'derived_from_struct_field': True,
                'source_overlay': alias.get('source_overlay'),
            })
        if getattr(type_env, 'is_memory_array_parameter', lambda _name: False)(text):
            variable = type_env.memory_array_parameter(text)
            return self.clean({
                'overlay_kind': 'MemoryArrayLengthRead',
                'array': text,
                'array_type': getattr(variable, 'type_string', None),
                'data_location': 'memory',
                'access': f'{text}.length',
                'element_type': getattr(type_env, 'memory_array_element_type', lambda _name: None)(text),
                'layout': 'solidity_memory_dynamic_array_length_at_base',
            })
        parsed = self.memory_array_base_and_offset(type_env, text)
        if not parsed:
            return None
        base, offset = parsed
        variable = type_env.memory_array_parameter(base)
        index_expr = self.memory_array_index_from_offset(offset)
        return self.clean({
            'overlay_kind': 'MemoryArrayElementRead',
            'array': base,
            'array_type': getattr(variable, 'type_string', None),
            'data_location': 'memory',
            'element_type': getattr(type_env, 'memory_array_element_type', lambda _name: None)(base),
            'offset': offset,
            'index': index_expr,
            'access': f'{base}[{index_expr}]',
            'layout': 'solidity_memory_dynamic_array_elements_after_length_word',
        })

    @classmethod
    def memory_array_base_and_offset(cls, type_env: Any, expr: str) -> tuple[str, str] | None:
        parsed = cls.array_base_and_offset(expr)
        if not parsed:
            return None
        left, right = parsed
        is_array = getattr(type_env, 'is_memory_array_parameter', lambda _name: False)
        if is_array(left):
            return left, right
        if is_array(right):
            return right, left
        return None

    @staticmethod
    def array_base_and_offset(expr: str) -> tuple[str, str] | None:
        name, args = call_parts(str(expr or '').strip())
        if name != 'add' or len(args) != 2:
            return None
        left, right = args[0].strip(), args[1].strip()
        if not left or not right:
            return None
        return left, right

    @staticmethod
    def memory_array_index_from_offset(offset: Any) -> str:
        text = str(offset or '').strip()
        value = parse_int_literal(text)
        if value is not None:
            index = (value - 32) // 32
            if value >= 32 and (value - 32) % 32 == 0:
                return str(index)
        affine = SemanticOverlayBuilder.memory_array_affine_word_index(text)
        if affine:
            return affine
        return f"(({normalize_expr(text)} - 32) / 32)"

    @staticmethod
    def memory_array_affine_word_index(offset: Any) -> str | None:
        """Recognize common memory-array element offsets.

        Solidity dynamic memory arrays store the length at base + 0 and the
        first element at base + 32. Yul often spells element i as either
        add(base, mul(add(i, 1), 0x20)) or add(base, add(0x20, mul(i, 0x20))).
        Only these linear word-stride forms are simplified here.
        """

        text = str(offset or '').strip()
        mul = SemanticOverlayBuilder.word_stride_mul(text)
        if mul is not None:
            plus_one = SemanticOverlayBuilder.add_constant(mul, 1)
            if plus_one is not None:
                return plus_one
            return f"({normalize_expr(mul)} - 1)"

        name, args = call_parts(text)
        if name != 'add' or len(args) != 2:
            return None
        left, right = args[0].strip(), args[1].strip()
        for const_side, mul_side in ((left, right), (right, left)):
            if parse_int_literal(const_side) != 32:
                continue
            inner = SemanticOverlayBuilder.word_stride_mul(mul_side)
            if inner is not None:
                return normalize_expr(inner)
        return None

    @staticmethod
    def word_stride_mul(expr: Any) -> str | None:
        name, args = call_parts(str(expr or '').strip())
        if name != 'mul' or len(args) != 2:
            return None
        left, right = args[0].strip(), args[1].strip()
        if parse_int_literal(left) == 32:
            return right
        if parse_int_literal(right) == 32:
            return left
        return None

    @staticmethod
    def add_constant(expr: Any, expected: int) -> str | None:
        name, args = call_parts(str(expr or '').strip())
        if name != 'add' or len(args) != 2:
            return None
        left, right = args[0].strip(), args[1].strip()
        if parse_int_literal(left) == expected:
            return normalize_expr(right)
        if parse_int_literal(right) == expected:
            return normalize_expr(left)
        return None

    def expression_overlays(self, type_env: Any, effects: list[EffectNode]) -> list[SemanticOverlay]:
        out: list[SemanticOverlay] = []
        value_defs_by_name = self.value_defs_by_name(effects)
        value_defs_by_version = self.value_defs_by_version(effects)
        order = {effect.effect_id: index for index, effect in enumerate(effects)}
        memory_hash_by_stmt_target: dict[tuple[str, str], dict[str, Any]] = {}
        for e in effects:
            if e.kind != 'MemoryHash':
                continue
            target = e.attrs.get('value')
            if not target:
                continue
            rendered = self.memory_hash_expression(e, type_env, value_defs_by_name, value_defs_by_version, order)
            if not rendered:
                continue
            for ref in e.stmt_refs:
                memory_hash_by_stmt_target[(str(ref), str(target))] = rendered
        for e in effects:
            if e.kind == 'ValueDef':
                target = e.attrs.get('targets', [None])[0]
                if self.is_storage_pointer_slot_target(target):
                    pointer = str(target).removesuffix('.slot')
                    value = e.attrs.get('value')
                    out.append(self.ov('StoragePointerSlotBinding', e.effect_id, e.stmt_refs, {
                        'pointer': pointer,
                        'slot': value,
                        'slot_normalized': normalize_expr(value),
                        'solidity_equivalent': False,
                        'reason': 'storage_reference_slot_binding_requires_inline_assembly',
                        'solidity_like': f"/* storage pointer binding: {pointer}.slot := {normalize_expr(value)} */",
                    }))
                    continue
                atomized_value = e.attrs.get('atomized_value') if isinstance(e.attrs.get('atomized_value'), dict) else None
                expr = self.normalize_expression_with_memory_arrays(type_env, e.attrs.get('value'))
                memory_hash = None
                if target:
                    memory_hash = next(
                        (
                            memory_hash_by_stmt_target.get((str(ref), str(target)))
                            for ref in e.stmt_refs
                            if memory_hash_by_stmt_target.get((str(ref), str(target)))
                        ),
                        None,
                    )
                if memory_hash:
                    expr = str(memory_hash['expression'])
                # Atomization materializes evaluation order for provenance;
                # its ``final`` field is an internal temporary, not the
                # value-language expression.  Keep the normalized expression
                # public and let the semantic lifter use atomized steps only
                # to replace proven atomic reads with recovered semantics.
                out.append(self.ov('ExpressionNormalization', e.effect_id, e.stmt_refs, {
                    'target': target,
                    'expression': e.attrs.get('value'),
                    'expression_normalized': expr,
                    'solidity_like': f"{target} = {expr};" if target else None,
                    'context': 'value',
                    'division_guards': division_guards(e.attrs.get('value')),
                    'memory_hash': memory_hash,
                    'atomized_value': atomized_value,
                }))
            elif e.kind == 'Branch':
                condition_text = e.attrs.get('condition_final_temp') or e.attrs.get('condition_normalized') or normalize_expr(e.attrs.get('condition'))
                # ``condition_final_temp`` is useful derivation evidence, not
                # the semantic condition.  Carry the S-SEIR-normalized form
                # explicitly so downstream SFIR never has to fall back to the
                # raw Yul spelling such as iszero(eq(...)).
                condition_normalized = e.attrs.get('condition_normalized') or normalize_expr(e.attrs.get('condition'), context='condition')
                condition_evaluation = self.condition_evaluation_with_state_reads(type_env, e.attrs.get('condition_evaluation'), value_defs_by_name)
                out.append(self.ov('ExpressionNormalization', e.effect_id, e.stmt_refs, {
                    'expression': e.attrs.get('condition'),
                    'condition_normalized': condition_normalized,
                    'solidity_like': f"if ({condition_text})",
                    'context': 'condition',
                    'condition_final_temp': e.attrs.get('condition_final_temp'),
                    'condition_evaluation': condition_evaluation,
                    'division_guards': division_guards(e.attrs.get('condition')),
                }))
            elif e.kind == 'EvaluationStep':
                temp = e.attrs.get('temp')
                if e.attrs.get('value_from_call_output'):
                    expr = e.attrs.get('value_from_call_output')
                    state_read = None
                else:
                    expr, state_read = self.normalize_expression_with_state_reads(type_env, e.attrs.get('expression'), value_defs_by_name)
                attrs = {
                    'temp': temp,
                    'expression': e.attrs.get('expression'),
                    'expression_normalized': expr,
                    'solidity_like': f"{temp} = {expr};" if temp else None,
                    'order': e.attrs.get('order'),
                    'call': e.attrs.get('call'),
                    'raw_args': e.attrs.get('raw_args'),
                    'evaluated_args': e.attrs.get('evaluated_args'),
                    'parent_effect': e.attrs.get('parent_effect'),
                    'evaluation_model': 'yul_ast_right_to_left_function_call_arguments',
                    'evaluation_context': e.attrs.get('evaluation_context'),
                    'reads_after_call_output': e.attrs.get('reads_after_call_output'),
                    'value_from_call_output': e.attrs.get('value_from_call_output'),
                }
                if state_read:
                    attrs['state_read'] = state_read
                out.append(self.ov('EvaluationStep', e.effect_id, e.stmt_refs, self.clean(attrs)))
        return out

    def condition_evaluation_with_state_reads(
        self,
        type_env: Any,
        condition_evaluation: Any,
        value_defs_by_name: dict[str, list[EffectNode]],
    ) -> Any:
        if not isinstance(condition_evaluation, dict):
            return condition_evaluation
        out = dict(condition_evaluation)
        steps = []
        state_reads = []
        for step in condition_evaluation.get('steps') or []:
            item = dict(step)
            expr, state_read = self.normalize_expression_with_state_reads(type_env, item.get('expression'), value_defs_by_name)
            item['expression_normalized'] = expr
            if state_read:
                item['state_read'] = state_read
                state_reads.append(state_read)
            steps.append(item)
        out['steps'] = steps
        if state_reads:
            out['state_reads'] = state_reads
        return self.clean(out)

    def address_code_overlays(self, type_env: Any, effects: list[EffectNode]) -> list[SemanticOverlay]:
        out: list[SemanticOverlay] = []
        for effect in effects:
            if effect.kind != 'ValueDef':
                continue
            targets = effect.attrs.get('targets') or []
            target = targets[0] if targets else None
            parsed = self.extcodesize_expression(effect.attrs.get('value'))
            if not parsed or not target:
                continue
            address, iszero_depth = parsed
            address_expr = normalize_expr(address)
            code_size = f'{address_expr}.code.length'
            target_info = getattr(type_env, 'lookup', lambda _name: None)(target)
            target_type = getattr(target_info, 'type_string', None)
            if getattr(type_env, 'is_bool', lambda _name: False)(target):
                has_code = iszero_depth % 2 == 0
                condition = f'({code_size} {"!=" if has_code else "=="} 0)'
                out.append(self.ov('AddressHasCode', effect.effect_id, effect.stmt_refs, {
                    'target': target,
                    'target_type': target_type,
                    'address': address,
                    'address_normalized': address_expr,
                    'code_size': code_size,
                    'condition': condition,
                    'check_kind': 'has_code' if has_code else 'has_no_code',
                    'iszero_depth': iszero_depth,
                    'source_expression': effect.attrs.get('value'),
                    'solidity_like': f'{target} = {condition};',
                    'solidity_equivalent': True,
                }))
            elif iszero_depth == 0:
                out.append(self.ov('AddressCodeSize', effect.effect_id, effect.stmt_refs, {
                    'target': target,
                    'target_type': target_type,
                    'address': address,
                    'address_normalized': address_expr,
                    'code_size': code_size,
                    'source_expression': effect.attrs.get('value'),
                    'solidity_like': f'{target} = {code_size};',
                    'solidity_equivalent': True,
                }))
        return out

    @staticmethod
    def extcodesize_expression(value: Any) -> tuple[str, int] | None:
        """Return the extcodesize address and surrounding iszero depth."""
        expression = str(value or '').strip()
        iszero_depth = 0
        while True:
            call, args = call_parts(expression)
            if call != 'iszero' or len(args) != 1:
                break
            iszero_depth += 1
            expression = str(args[0]).strip()
        call, args = call_parts(expression)
        if call != 'extcodesize' or len(args) != 1:
            return None
        return str(args[0]).strip(), iszero_depth

    def calldata_word_read_overlays(self, type_env: Any, effects: list[EffectNode]) -> list[SemanticOverlay]:
        out: list[SemanticOverlay] = []
        for effect in effects:
            if effect.kind != 'ValueDef':
                continue
            targets = effect.attrs.get('targets') or []
            target = targets[0] if targets else None
            call, args = call_parts(str(effect.attrs.get('value') or ''))
            if call != 'calldataload' or len(args) != 1 or not target:
                continue
            offset = args[0]
            offset_expr = normalize_expr(offset)
            target_info = getattr(type_env, 'lookup', lambda _name: None)(target)
            target_type = getattr(target_info, 'type_string', None)
            word_read = self.ov('CalldataWordRead', effect.effect_id, effect.stmt_refs, {
                'target': target,
                'target_type': target_type,
                'source': 'msg.data',
                'offset': offset,
                'offset_normalized': offset_expr,
                'width_bytes': 32,
                'source_expression': effect.attrs.get('value'),
                'solidity_like': f'{target} = calldataWord({offset_expr});',
                'solidity_equivalent': False,
                'reason': 'raw_calldata_word_read_requires_inline_assembly_or_helper',
                'path_states': effect.attrs.get('path_states') or [],
                'target_versions': effect.attrs.get('target_versions') or {},
            })
            out.append(word_read)
            # This is intentionally only a typed-layout *candidate*.  A
            # calldataload beyond calldata returns zero, while ``array[i]``
            # performs a Solidity bounds check.  The post-predicate CFG pass
            # may complete it only after proving the true bounds edge and
            # that the index is unchanged on every path to this read.
            candidate = self.calldata_array_candidate_from_offset(type_env, offset)
            if candidate:
                candidate.update({
                    'target': target,
                    'target_type': target_type,
                    'source_word_overlay': word_read.overlay_id,
                    'source_expression': effect.attrs.get('value'),
                    'candidate_status': 'pending_cfg_bounds_proof',
                })
                out.append(self.ov('CalldataArrayElementCandidate', effect.effect_id, effect.stmt_refs, candidate))
        return out

    def calldata_array_candidate_from_offset(self, type_env: Any, offset: Any) -> dict[str, Any] | None:
        """Recognize ``array.offset + index * 32`` for ABI word arrays.

        The layout identity alone is not a source-equivalence proof.  Callers
        receive a provisional candidate that the CFG completion pass must
        discharge against a dominating ``index < array.length`` true edge.
        """
        text = str(offset or '').strip()
        array = None
        index = None
        if text.endswith('.offset'):
            possible = text.removesuffix('.offset').strip()
            if getattr(type_env, 'is_calldata_array_parameter', lambda _name: False)(possible):
                array, index = possible, '0'
        else:
            parsed = self.array_base_and_offset(text)
            if parsed:
                left, right = parsed
                for base, stride in ((left, right), (right, left)):
                    if not base.endswith('.offset'):
                        continue
                    possible = base.removesuffix('.offset').strip()
                    if not getattr(type_env, 'is_calldata_array_parameter', lambda _name: False)(possible):
                        continue
                    recovered_index = self.calldata_array_word_index(stride)
                    if recovered_index is not None:
                        array, index = possible, recovered_index
                        break
        if not array or index is None:
            return None
        element_type = getattr(type_env, 'calldata_array_element_type', lambda _name: None)(array)
        if not self.is_abi_word_array_element(element_type):
            return None
        variable = getattr(type_env, 'calldata_array_parameter', lambda _name: None)(array)
        return self.clean({
            'array': array,
            'array_type': getattr(variable, 'type_string', None),
            'element_type': element_type,
            'data_location': 'calldata',
            'index': index,
            'access': f'{array}[{index}]',
            'layout': 'abi_calldata_dynamic_array_elements_at_offset',
            'element_encoding': 'single_abi_word',
        })

    @classmethod
    def calldata_array_word_index(cls, stride: Any) -> str | None:
        text = str(stride or '').strip()
        if not text:
            return None
        if parse_int_literal(text) == 0:
            return '0'
        index = cls.word_stride_mul(text)
        return normalize_expr(index) if index is not None else None

    @staticmethod
    def is_abi_word_array_element(element_type: Any) -> bool:
        """Return true only for array elements encoded in exactly one word."""
        text = str(element_type or '').strip().replace(' payable', '')
        if text in {'address', 'bool'}:
            return True
        if re.fullmatch(r'(?:u?int)(?:[0-9]{0,3})?', text):
            bits = text.removeprefix('uint').removeprefix('int')
            return not bits or (bits.isdigit() and 8 <= int(bits) <= 256 and int(bits) % 8 == 0)
        match = re.fullmatch(r'bytes([0-9]{1,2})', text)
        return bool(match and 1 <= int(match.group(1)) <= 32)

    @staticmethod
    def is_storage_pointer_slot_target(target: Any) -> bool:
        text = str(target or '').strip()
        if not text.endswith('.slot'):
            return False
        base = text[:-5]
        return bool(base) and '[' not in base and '(' not in base

    @staticmethod
    def value_def_like(effect: EffectNode) -> str | None:
        targets = effect.attrs.get('targets') or []
        if not targets:
            return None
        return f"{targets[0]} = {effect.attrs.get('value_normalized')};"

    def events_for_contract(self, contract: str) -> list[EventDecl]:
        out: list[EventDecl] = []
        seen: set[str] = set()
        for group in [self.events_by_contract.get(contract, []), *self.events_by_contract.values()]:
            for e in group:
                key = e.signature + (' anonymous' if e.anonymous else '')
                if key not in seen:
                    out.append(e)
                    seen.add(key)
        return out

    @staticmethod
    def match_event_name(effect: EffectNode, events: list[EventDecl], resolved_topics: list[Any] | None = None) -> EventDecl | None:
        topics = resolved_topics if resolved_topics is not None else effect.attrs.get('topics', [])
        if not topics:
            return None
        t0 = normalize_topic_value(topics[0])
        for e in events:
            indexed = len([p for p in e.params if p.indexed])
            if e.anonymous and len(topics) == indexed:
                return e
            if (not e.anonymous) and len(topics) == indexed + 1 and normalize_topic_value(e.topic0) == t0:
                return e
        return None

    @staticmethod
    def event_topic_near_misses(effect: EffectNode, events: list[EventDecl], data_size: int | None, resolved_topics: list[Any] | None = None) -> list[dict[str, Any]]:
        topics = resolved_topics if resolved_topics is not None else (effect.attrs.get('topics', []) or [])
        if not topics:
            return []
        observed = normalize_topic_value(topics[0])
        if not observed or not str(observed).startswith('0x'):
            return []
        out: list[dict[str, Any]] = []
        observed_hex = str(observed)[2:]
        for event in events:
            expected = normalize_topic_value(event.topic0)
            if event.anonymous or not expected or not str(expected).startswith('0x'):
                continue
            indexed = len([param for param in event.params if param.indexed])
            if len(topics) != indexed + 1:
                continue
            non_indexed = [param for param in event.params if not param.indexed]
            expected_data_size = 32 * len(non_indexed)
            if data_size is not None and data_size != expected_data_size:
                continue
            expected_hex = str(expected)[2:]
            if observed_hex == expected_hex:
                continue
            prefix = SemanticOverlayBuilder.matching_prefix_nibbles(observed_hex, expected_hex)
            # Event topic0 is 32 bytes. A 4-byte prefix match is useful as a
            # diagnostic only; exact event recovery still requires all bytes.
            if prefix >= 8:
                out.append({
                    'event': event.name,
                    'signature': event.signature,
                    'expected_topic0': expected,
                    'observed_topic0': observed,
                    'matching_prefix_nibbles': prefix,
                    'reason': 'event_topic0_requires_full_32_byte_match',
                })
        return out

    @staticmethod
    def matching_prefix_nibbles(left: str, right: str) -> int:
        count = 0
        for a, b in zip(left.lower(), right.lower()):
            if a != b:
                break
            count += 1
        return count

    @staticmethod
    def target_solidity(target: Any) -> str | None:
        n = SemanticOverlayBuilder.int_value(target)
        if n is None:
            return normalize_expr(target) if target is not None else None
        return f"address(0x{n:040x})"

    @staticmethod
    def int_value(value: Any) -> int | None:
        try:
            return int(str(value), 0)
        except Exception:
            return None

    @staticmethod
    def is_native_precompile_success_guard(guard: str | None) -> bool:
        text = str(guard or '')
        return 'staticcall' in text and (', 2,' in text or '(2,' in text)

    @staticmethod
    def path_states(effect: EffectNode) -> list[str]:
        direct = effect.attrs.get('path_states')
        if isinstance(direct, list) and direct:
            return direct
        for key in ('payload_memory', 'memory_read', 'input_memory', 'data_memory'):
            q = effect.attrs.get(key)
            if isinstance(q, dict):
                return q.get('path_states') or []
        return []

    def nearest_guard(self, effect: EffectNode, effects: list[EffectNode], branches: list[EffectNode] | None = None) -> str | None:
        branches = branches or [e for e in effects if e.kind == 'Branch']
        node = effect.attrs.get('cfg_node_id')
        if node is not None:
            prev = [b for b in branches if b.attrs.get('cfg_node_id') is not None and b.attrs.get('cfg_node_id') <= node]
            if prev:
                return str(prev[-1].attrs.get('condition'))
        states = self.path_states(effect)
        if states:
            last = str(states[0]).split(' && ')[-1]
            if last.startswith('!(') and last.endswith(')'):
                last = last[2:-1]
            return last
        return None

    @staticmethod
    def clean(attrs: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in attrs.items() if v is not None}

    @staticmethod
    def dedupe(overlays: list[SemanticOverlay]) -> list[SemanticOverlay]:
        out: list[SemanticOverlay] = []
        seen: set[tuple[Any, ...]] = set()
        for o in overlays:
            semantic_key = SemanticOverlayBuilder.semantic_dedupe_key(o)
            if semantic_key is not None:
                existing = next((item for item in out if SemanticOverlayBuilder.semantic_dedupe_key(item) == semantic_key), None)
                if existing:
                    SemanticOverlayBuilder.merge_overlay(existing, o)
                    continue
            key = (o.kind, tuple(o.effects), tuple(o.stmt_refs), str(sorted(o.attrs.items())))
            if key not in seen:
                seen.add(key)
                out.append(o)
        return out

    @staticmethod
    def semantic_dedupe_key(overlay: SemanticOverlay) -> tuple[Any, ...] | None:
        attrs = overlay.attrs
        if overlay.kind == 'MemoryRegionAllocate':
            return (
                overlay.kind,
                normalize_expr(attrs.get('base')),
                normalize_expr(attrs.get('new_free_pointer')),
                attrs.get('start_node'),
                attrs.get('end_node'),
                tuple(attrs.get('stmt_refs') or overlay.stmt_refs),
            )
        if overlay.kind == 'MemoryArrayConstruction':
            return (
                overlay.kind,
                attrs.get('result'),
                attrs.get('array_type'),
                normalize_expr(attrs.get('allocation_source')),
                normalize_expr(attrs.get('length_expr')),
                normalize_expr(attrs.get('free_memory_pointer_update')),
            )
        return None

    @staticmethod
    def merge_overlay(target: SemanticOverlay, incoming: SemanticOverlay) -> None:
        target.effects = list(dict.fromkeys([*target.effects, *incoming.effects]))
        target.stmt_refs = list(dict.fromkeys([*target.stmt_refs, *incoming.stmt_refs]))
        target.attrs['effects'] = target.effects
        target.attrs['stmt_refs'] = target.stmt_refs
        for key in ('path_states',):
            merged = list(dict.fromkeys([*(target.attrs.get(key) or []), *(incoming.attrs.get(key) or [])]))
            if merged:
                target.attrs[key] = merged
        merged_effects = list(target.attrs.get('merged_effects') or [])
        merged_effects.append({
            'overlay_id': incoming.overlay_id,
            'effects': incoming.effects,
            'stmt_refs': incoming.stmt_refs,
        })
        target.attrs['merged_effects'] = merged_effects
        if target.kind == 'MemoryArrayConstruction':
            target.attrs['element_writes'] = SemanticOverlayBuilder.merge_dict_list(
                target.attrs.get('element_writes') or [],
                incoming.attrs.get('element_writes') or [],
                ('effect', 'address', 'value'),
            )
        if target.kind == 'MemoryRegionAllocate':
            target.attrs['stored_values'] = SemanticOverlayBuilder.merge_dict_list(
                target.attrs.get('stored_values') or [],
                incoming.attrs.get('stored_values') or [],
                ('effect', 'address', 'value'),
            )

    @staticmethod
    def merge_dict_list(left: list[dict[str, Any]], right: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for item in [*left, *right]:
            key = tuple(item.get(name) for name in keys)
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out
