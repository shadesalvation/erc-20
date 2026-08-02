#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path as _SSEIRPath
import sys as _sseir_sys
_SSEIR_ROOT = _SSEIRPath(__file__).resolve().parents[1]
for _sseir_path in (_SSEIR_ROOT / "legacy_yul", _SSEIR_ROOT / "s_seir"):
    _sseir_text = str(_sseir_path)
    if _sseir_text not in _sseir_sys.path:
        _sseir_sys.path.insert(0, _sseir_text)

import re
from typing import Any

from s_seir_model import EffectNode, ExpressionRole, FunctionUnit, SemanticOverlay
from s_seir_yul_normalize import normalize_expr


class SemanticNormalizer:
    """Canonical S-SEIR layer.

    This pass does not import legacy recovery reports. It takes S-SEIR-native
    effects/overlays and fills the expression-role graph plus small canonical
    facts that help downstream consumers avoid module-specific formats.
    """

    def normalize(
        self,
        unit: FunctionUnit,
        type_env: Any,
        roles: list[ExpressionRole],
        effects: list[EffectNode],
        overlays: list[SemanticOverlay],
    ) -> tuple[list[ExpressionRole], list[EffectNode], list[SemanticOverlay], list[dict[str, Any]]]:
        out_roles = list(roles)
        next_expr = self.next_index(roles, 'expr')
        facts: list[dict[str, Any]] = [{
            'kind': 'SSEIRCanonicalization',
            'mode': 'native_s_seir_semantic_graph',
            'legacy_adapter_used': False,
            'function': unit.function_id,
            'state_variables': [v.to_dict() for v in unit.state_variables],
        }]

        def add_role(text: Any, role: str, stmt_ref: str | None, normalized: Any = None, type_hint: str | None = None, attrs: dict[str, Any] | None = None) -> None:
            nonlocal next_expr
            if not stmt_ref or text in {None, ''}:
                return
            text_s = str(text)
            norm_s = str(normalized) if normalized is not None else normalize_expr(text_s)
            key = (text_s, role, stmt_ref, norm_s)
            for r in out_roles:
                if (r.text, r.role, r.stmt_ref, r.normalized) == key:
                    if attrs:
                        r.attrs.update(attrs)
                    return
            out_roles.append(ExpressionRole(f'expr_{next_expr}', text_s, norm_s, role, type_hint, stmt_ref, attrs or {}))
            next_expr += 1

        for e in effects:
            ref = e.stmt_refs[0] if e.stmt_refs else None
            if e.kind == 'MemoryHash':
                bytes_slice = getattr(type_env, 'dynamic_bytes_memory_slice', lambda *_args: None)(e.attrs.get('ptr'), e.attrs.get('size'))
                ptr_normalized = bytes_slice.get('data_pointer_normalized') if bytes_slice else e.attrs.get('ptr')
                size_normalized = bytes_slice.get('length_normalized') if bytes_slice else e.attrs.get('size')
                add_role(e.attrs.get('ptr'), 'memory_slice_start', ref, ptr_normalized, 'memory_ptr', {'effect': e.effect_id})
                add_role(e.attrs.get('size'), 'memory_slice_size', ref, size_normalized, 'uint256', {'effect': e.effect_id})
                if bytes_slice:
                    attrs = {'effect': e.effect_id, 'object': bytes_slice.get('object'), 'pattern': 'dynamic_bytes_memory_data_and_length'}
                    add_role(e.attrs.get('ptr'), 'bytes_data_ptr', ref, ptr_normalized, 'memory_ptr', attrs)
                    add_role(e.attrs.get('size'), 'bytes_length_value', ref, size_normalized, 'uint256', attrs)
            elif e.kind == 'MemoryRead':
                add_role(e.attrs.get('read_from'), 'memory_slice_start', ref, e.attrs.get('read_from'), 'memory_ptr', {'effect': e.effect_id, 'semantic': 'memory_read'})
                add_role(e.attrs.get('value'), 'memory_read_value', ref, e.attrs.get('value'), None, {'effect': e.effect_id, 'value_versions': e.attrs.get('value_versions')})
                if self.is_free_memory_pointer_read(e.attrs.get('read_from')):
                    add_role(
                        f"mload({e.attrs.get('read_from')})",
                        'free_memory_pointer',
                        ref,
                        'free_memory_pointer',
                        'memory_ptr',
                        {'effect': e.effect_id, 'memory_slot': '0x40', 'value': e.attrs.get('value')},
                    )
            elif e.kind in {'StorageRead', 'StorageWrite'}:
                add_role(e.attrs.get('slot'), 'storage_slot_expr', ref, e.attrs.get('slot'), 'storage_slot', {'effect': e.effect_id, 'slot_versions': e.attrs.get('slot_versions')})
                if e.kind == 'StorageWrite':
                    add_role(e.attrs.get('value'), 'storage_write_value', ref, normalize_expr(e.attrs.get('value')), None, {'effect': e.effect_id})
            elif e.kind in {'Call', 'StaticCall', 'DelegateCall', 'CallCode'}:
                add_role(e.attrs.get('target'), 'call_target', ref, e.attrs.get('target_solidity') or e.attrs.get('target'), 'address', {'effect': e.effect_id, 'call_kind': e.attrs.get('op')})
                add_role(e.attrs.get('gas'), 'call_gas', ref, normalize_expr(e.attrs.get('gas')), 'uint256', {'effect': e.effect_id})
                add_role(e.attrs.get('input_ptr'), 'call_input_ptr', ref, e.attrs.get('input_ptr'), 'memory_ptr', {'effect': e.effect_id})
                add_role(e.attrs.get('input_size'), 'call_input_size', ref, e.attrs.get('input_size'), 'uint256', {'effect': e.effect_id})
                add_role(e.attrs.get('output_ptr'), 'call_output_ptr', ref, e.attrs.get('output_ptr'), 'memory_ptr', {'effect': e.effect_id})
                add_role(e.attrs.get('output_size'), 'call_output_size', ref, e.attrs.get('output_size'), 'uint256', {'effect': e.effect_id})
            elif e.kind == 'EventLog':
                add_role(e.attrs.get('data_ptr'), 'memory_slice_start', ref, e.attrs.get('data_ptr'), 'memory_ptr', {'effect': e.effect_id, 'semantic': 'event_data'})
                add_role(e.attrs.get('data_size'), 'memory_slice_size', ref, e.attrs.get('data_size'), 'uint256', {'effect': e.effect_id, 'semantic': 'event_data'})
                topics = e.attrs.get('topics') or []
                if topics:
                    add_role(topics[0], 'event_topic0', ref, normalize_expr(topics[0]), 'bytes32', {'effect': e.effect_id})
                for i, topic in enumerate(topics[1:]):
                    add_role(topic, 'event_indexed_argument', ref, normalize_expr(topic), None, {'effect': e.effect_id, 'index': i})
            elif e.kind == 'Branch':
                add_role(e.attrs.get('condition'), 'branch_condition', ref, e.attrs.get('condition_normalized'), 'bool', {'effect': e.effect_id})

        for o in overlays:
            ref = o.stmt_refs[0] if o.stmt_refs else None
            if o.kind == 'MappingSlot':
                add_role(o.attrs.get('expression'), 'mapping_slot_expr', ref, o.attrs.get('expression'), 'storage_slot', {'overlay': o.overlay_id, 'state_variable': o.attrs.get('state_variable'), 'storage_reference': o.attrs.get('storage_reference'), 'storage_reference_kind': o.attrs.get('storage_reference_kind'), 'storage_field': o.attrs.get('storage_field')})
                add_role(o.attrs.get('key'), 'mapping_key_material', ref, o.attrs.get('key'), None, {'overlay': o.overlay_id, 'storage_reference': o.attrs.get('storage_reference')})
            elif o.kind in {'MappingRead', 'MappingWrite'}:
                add_role(o.attrs.get('slot'), 'mapping_slot_expr', ref, o.attrs.get('access'), 'storage_slot', {'overlay': o.overlay_id, 'state_variable': o.attrs.get('state_variable'), 'storage_reference': o.attrs.get('storage_reference'), 'storage_reference_kind': o.attrs.get('storage_reference_kind'), 'storage_field': o.attrs.get('storage_field')})
                add_role(o.attrs.get('access'), 'state_access_expr', ref, o.attrs.get('access'), None, {'overlay': o.overlay_id, 'state_variable': o.attrs.get('state_variable'), 'storage_reference': o.attrs.get('storage_reference'), 'storage_reference_kind': o.attrs.get('storage_reference_kind'), 'storage_field': o.attrs.get('storage_field')})
            elif o.kind in {'StateVariableRead', 'StateVariableWrite'}:
                slot_derivation = o.attrs.get('slot_derivation') or {}
                add_role(o.attrs.get('slot'), 'storage_slot_expr', ref, o.attrs.get('access'), 'storage_slot', {'overlay': o.overlay_id, 'storage_model': o.attrs.get('storage_model'), 'slot_constant': o.attrs.get('slot_constant'), 'slot_derivation_kind': slot_derivation.get('kind')})
                add_role(o.attrs.get('access'), 'state_access_expr', ref, o.attrs.get('access'), None, {'overlay': o.overlay_id, 'storage_model': o.attrs.get('storage_model'), 'state_mutation': o.attrs.get('state_mutation'), 'slot_derivation_kind': slot_derivation.get('kind')})
                if slot_derivation.get('kind') == 'manual_packed_hash_slot':
                    add_role(slot_derivation.get('expression') or o.attrs.get('access'), 'manual_storage_slot_expr', ref, slot_derivation.get('expression') or o.attrs.get('access'), 'storage_slot', {'overlay': o.overlay_id, 'slot_derivation': slot_derivation.get('kind')})
                    for i, item in enumerate(slot_derivation.get('packed_inputs') or []):
                        add_role(item, 'packed_hash_material', ref, item, 'bytes', {'overlay': o.overlay_id, 'slot_derivation': slot_derivation.get('kind'), 'index': i})
            elif o.kind in {'PathConditionedStorageRead', 'PathConditionedStorageWrite'}:
                add_role(o.attrs.get('slot'), 'storage_slot_expr', ref, o.attrs.get('slot'), 'storage_slot', {'overlay': o.overlay_id, 'slot_versions': o.attrs.get('slot_versions')})
                for i, candidate in enumerate(o.attrs.get('candidates') or []):
                    add_role(candidate.get('slot_key'), 'storage_slot_version', ref, candidate.get('slot_key'), 'storage_slot', {'overlay': o.overlay_id, 'candidate_index': i, 'status': candidate.get('status')})
                    add_role(candidate.get('access'), 'state_access_expr', ref, candidate.get('access'), None, {'overlay': o.overlay_id, 'candidate_index': i, 'overlay_kind': candidate.get('overlay_kind'), 'status': candidate.get('status')})
            elif o.kind == 'RequireOverlay':
                add_role(o.attrs.get('condition'), 'guard_condition', ref, o.attrs.get('condition'), 'bool', {'overlay': o.overlay_id, 'nearest_condition': o.attrs.get('nearest_condition')})
            elif o.kind == 'EventEmit':
                add_role(o.attrs.get('topic0'), 'event_topic0', ref, o.attrs.get('topic0'), 'bytes32', {'overlay': o.overlay_id, 'event': o.attrs.get('event')})
                for i, arg in enumerate(o.attrs.get('args') or []):
                    add_role(arg, 'abi_argument', ref, arg, None, {'overlay': o.overlay_id, 'event': o.attrs.get('event'), 'index': i})
            elif o.kind in {'LowLevelCall', 'StaticCallOverlay', 'DelegateCallOverlay', 'PrecompileCall', 'PathConditionedPrecompileCall'}:
                add_role(o.attrs.get('target'), 'call_target', ref, o.attrs.get('target_solidity'), 'address', {'overlay': o.overlay_id})
                if o.attrs.get('selector'):
                    add_role(o.attrs.get('selector'), 'abi_selector', ref, o.attrs.get('selector'), 'bytes4', {'overlay': o.overlay_id})
                for i, arg in enumerate(o.attrs.get('input_words') or []):
                    add_role(arg, 'abi_argument', ref, arg, None, {'overlay': o.overlay_id, 'call_input_word': i})
            elif o.kind == 'AbiCallDataConstruction':
                add_role(o.attrs.get('input_ptr'), 'abi_calldata_ptr', ref, o.attrs.get('input_ptr'), 'memory_ptr', {'overlay': o.overlay_id})
                add_role(o.attrs.get('input_size'), 'abi_calldata_size', ref, o.attrs.get('input_size_expression') or o.attrs.get('input_size'), 'uint256', {'overlay': o.overlay_id})
                add_role(o.attrs.get('selector'), 'abi_selector', ref, o.attrs.get('selector'), 'bytes4', {'overlay': o.overlay_id, 'signature': o.attrs.get('signature')})
                for arg in o.attrs.get('arguments') or []:
                    add_role(arg.get('value'), 'abi_argument', ref, arg.get('value_semantic') or arg.get('value_normalized') or arg.get('value'), arg.get('type'), {'overlay': o.overlay_id, 'index': arg.get('index'), 'kind': arg.get('kind')})
            elif o.kind == 'AbiEncodedLowLevelCall':
                add_role(o.attrs.get('target'), 'call_target', ref, o.attrs.get('target_solidity') or o.attrs.get('target'), 'address', {'overlay': o.overlay_id})
                add_role(o.attrs.get('selector'), 'abi_selector', ref, o.attrs.get('selector'), 'bytes4', {'overlay': o.overlay_id, 'signature': o.attrs.get('selector_signature')})
                for i, arg in enumerate(o.attrs.get('arguments') or []):
                    add_role(arg, 'abi_argument', ref, normalize_expr(arg), None, {'overlay': o.overlay_id, 'index': i})
            elif o.kind == 'RawRevertBytes':
                add_role(o.attrs.get('payload'), 'revert_payload_ptr', ref, o.attrs.get('payload'), 'bytes', {'overlay': o.overlay_id})
            elif o.kind == 'BytesContentHash':
                add_role(o.attrs.get('data_pointer'), 'bytes_data_ptr', ref, o.attrs.get('data_pointer_normalized'), 'memory_ptr', {'overlay': o.overlay_id, 'object': o.attrs.get('object')})
                add_role(o.attrs.get('length_expression'), 'bytes_length_value', ref, o.attrs.get('length_normalized'), 'uint256', {'overlay': o.overlay_id, 'object': o.attrs.get('object')})
                add_role(o.attrs.get('hash_input'), 'hash_input', ref, o.attrs.get('hash_input'), o.attrs.get('object_type'), {'overlay': o.overlay_id, 'object': o.attrs.get('object')})
            elif o.kind == 'RawReturnData':
                add_role(o.attrs.get('payload_ptr'), 'return_payload_ptr', ref, o.attrs.get('payload_ptr_normalized') or o.attrs.get('payload_ptr'), 'memory_ptr', {'overlay': o.overlay_id, 'encoding_hint': o.attrs.get('encoding_hint')})
                add_role(o.attrs.get('payload_size'), 'return_payload_size', ref, o.attrs.get('payload_size_normalized') or o.attrs.get('payload_size'), 'uint256', {'overlay': o.overlay_id, 'encoding_hint': o.attrs.get('encoding_hint')})
                for i, value in enumerate(o.attrs.get('values') or []):
                    add_role(value, 'return_payload_value', ref, value, None, {'overlay': o.overlay_id, 'index': i, 'encoding_hint': o.attrs.get('encoding_hint')})
            elif o.kind in {'AddressHasCode', 'AddressCodeSize'}:
                add_role(o.attrs.get('address'), 'address_code_target', ref, o.attrs.get('address_normalized') or o.attrs.get('address'), 'address', {'overlay': o.overlay_id})
                add_role(o.attrs.get('code_size'), 'address_code_size', ref, o.attrs.get('code_size'), 'uint256', {'overlay': o.overlay_id})
                if o.kind == 'AddressHasCode':
                    add_role(o.attrs.get('condition'), 'address_has_code_condition', ref, o.attrs.get('condition'), 'bool', {'overlay': o.overlay_id, 'target': o.attrs.get('target')})
            elif o.kind == 'AddressZeroCheck':
                add_role(o.attrs.get('variable'), 'address_zero_checked_value', ref, o.attrs.get('variable'), o.attrs.get('variable_type') or 'address', {'overlay': o.overlay_id, 'check': o.attrs.get('check'), 'projection': o.attrs.get('projection')})
                add_role(o.attrs.get('source_expression'), 'guard_condition', ref, o.attrs.get('condition'), 'bool', {'overlay': o.overlay_id, 'semantic': 'address_zero_check'})
            elif o.kind == 'CalldataWordRead':
                add_role(o.attrs.get('offset'), 'calldata_read_offset', ref, o.attrs.get('offset_normalized') or o.attrs.get('offset'), 'uint256', {'overlay': o.overlay_id, 'source': o.attrs.get('source')})
                add_role(o.attrs.get('source_expression'), 'calldata_word_value', ref, o.attrs.get('solidity_like'), o.attrs.get('target_type'), {'overlay': o.overlay_id, 'target': o.attrs.get('target'), 'width_bytes': o.attrs.get('width_bytes')})
            elif o.kind == 'StructFieldRead':
                field = o.attrs.get('field') or {}
                add_role(o.attrs.get('read_from'), 'struct_field_ptr', ref, o.attrs.get('read_from'), 'memory_ptr', {'overlay': o.overlay_id, 'struct_object': o.attrs.get('struct_object'), 'field': field.get('name')})
                add_role(o.attrs.get('value'), 'struct_field_value', ref, o.attrs.get('value'), field.get('type_string'), {'overlay': o.overlay_id, 'access': f"{o.attrs.get('struct_object')}.{field.get('name')}"})
            elif o.kind == 'StructFieldWrite':
                field = o.attrs.get('field') or {}
                add_role(o.attrs.get('value'), 'struct_field_write_value', ref, o.attrs.get('value_normalized') or o.attrs.get('value'), field.get('type_string'), {'overlay': o.overlay_id, 'access': f"{o.attrs.get('struct_object')}.{field.get('name')}"})
            elif o.kind == 'MemoryArrayLengthRead':
                add_role(o.attrs.get('source_expression'), 'memory_array_length_value', ref, o.attrs.get('access'), 'uint256', {'overlay': o.overlay_id, 'array': o.attrs.get('array'), 'array_type': o.attrs.get('array_type')})
            elif o.kind == 'MemoryArrayElementRead':
                add_role(o.attrs.get('source_expression'), 'memory_array_element_value', ref, o.attrs.get('access'), o.attrs.get('element_type'), {'overlay': o.overlay_id, 'array': o.attrs.get('array'), 'array_type': o.attrs.get('array_type'), 'index': o.attrs.get('index')})
            elif o.kind == 'MemoryArrayConstruction':
                add_role(o.attrs.get('base'), 'memory_array_constructed_base', ref, o.attrs.get('base'), 'memory_ptr', {'overlay': o.overlay_id, 'array_type': o.attrs.get('array_type'), 'result': o.attrs.get('result')})
                add_role(o.attrs.get('length_expr'), 'memory_array_constructed_length', ref, o.attrs.get('length_expr_normalized') or o.attrs.get('length_expr'), 'uint256', {'overlay': o.overlay_id, 'result': o.attrs.get('result')})
                for i, write in enumerate(o.attrs.get('element_writes') or []):
                    add_role(write.get('value'), 'memory_array_constructed_element', ref, write.get('value_normalized') or normalize_expr(write.get('value')), o.attrs.get('element_type'), {'overlay': o.overlay_id, 'result': o.attrs.get('result'), 'index': i, 'write_effect': write.get('effect')})
            elif o.kind in {'StructInitializationFragment', 'StructMutationFragment'}:
                for i, mutation in enumerate(o.attrs.get('mutations') or o.attrs.get('fields') or []):
                    field = mutation.get('field') or {}
                    value = mutation.get('new_value') if 'new_value' in mutation else mutation.get('value')
                    add_role(value, 'struct_field_write_value', ref, mutation.get('new_value_normalized') or normalize_expr(value), field.get('type_string'), {'overlay': o.overlay_id, 'field': field.get('name'), 'index': i})
            elif o.kind in {'MemoryRegionAllocate', 'MemoryRegionWrite', 'CursorBasedMemoryWrite'}:
                add_role(o.attrs.get('address') or o.attrs.get('base'), 'memory_region_ptr', ref, o.attrs.get('address') or o.attrs.get('base'), 'memory_ptr', {'overlay': o.overlay_id, 'overlay_kind': o.kind})
                add_role(o.attrs.get('value') or o.attrs.get('new_free_pointer'), 'memory_region_value', ref, o.attrs.get('value_normalized') or o.attrs.get('value') or o.attrs.get('new_free_pointer'), None, {'overlay': o.overlay_id, 'overlay_kind': o.kind})

        facts.extend(self.branch_materialization_facts(effects, overlays, out_roles))
        facts.extend(self.unresolved_facts(overlays))
        return out_roles, effects, overlays, facts

    @staticmethod
    def next_index(roles: list[ExpressionRole], prefix: str) -> int:
        max_seen = 0
        for r in roles:
            m = re.match(rf'^{re.escape(prefix)}_(\d+)$', r.expr_id)
            if m:
                max_seen = max(max_seen, int(m.group(1)))
        return max_seen + 1

    @classmethod
    def branch_materialization_facts(
        cls,
        effects: list[EffectNode],
        overlays: list[SemanticOverlay] | None = None,
        roles: list[ExpressionRole] | None = None,
    ) -> list[dict[str, Any]]:
        facts = []
        overlays_by_effect: dict[str, list[SemanticOverlay]] = {}
        for overlay in overlays or []:
            for effect_id in overlay.effects:
                overlays_by_effect.setdefault(effect_id, []).append(overlay)
        roles_by_effect: dict[str, list[ExpressionRole]] = {}
        for role in roles or []:
            effect_id = role.attrs.get('effect')
            if effect_id:
                roles_by_effect.setdefault(str(effect_id), []).append(role)
        for e in effects:
            for key in ('memory_read', 'payload_memory', 'input_memory', 'data_memory'):
                q = e.attrs.get(key)
                if isinstance(q, dict) and (q.get('has_phi') or q.get('has_unknown') or q.get('empty_range') or q.get('loop_facts') or q.get('top_facts')):
                    raw_unknown = bool(q.get('has_unknown', False))
                    sink_status = cls.sink_resolution_status(e.attrs.get('sink_resolution'))
                    resolving_overlays = cls.query_resolving_overlays(key, overlays_by_effect.get(e.effect_id, []))
                    resolving_roles = cls.query_resolving_roles(key, roles_by_effect.get(e.effect_id, []))
                    if q.get('complete') and not raw_unknown:
                        final_status = 'resolved'
                        resolved_by = 'memory_ssa'
                    elif sink_status == 'resolved':
                        final_status = 'resolved'
                        resolved_by = 'sink_resolver'
                    elif resolving_overlays:
                        final_status = 'resolved'
                        resolved_by = 'semantic_overlay_pattern'
                    elif resolving_roles:
                        final_status = 'resolved'
                        resolved_by = 'expression_role'
                    elif raw_unknown:
                        final_status = 'unresolved'
                        resolved_by = None
                    else:
                        final_status = 'resolved' if q.get('complete') else 'not_applicable'
                        resolved_by = 'memory_ssa' if final_status == 'resolved' else None
                    facts.append({
                        'kind': 'SemanticSinkMemoryQuery',
                        'effect': e.effect_id,
                        'effect_kind': e.kind,
                        'query_field': key,
                        'has_phi': q.get('has_phi', False),
                        'has_unknown': raw_unknown and final_status != 'resolved',
                        'memory_query_has_unknown': raw_unknown,
                        'memory_query_complete': q.get('complete', False),
                        'empty_range': q.get('empty_range', False),
                        'final_status': final_status,
                        'resolved_by': resolved_by,
                        'resolution_overlays': [overlay.overlay_id for overlay in resolving_overlays],
                        'resolution_roles': [role.expr_id for role in resolving_roles],
                        'semantic_value': resolving_roles[0].normalized if resolving_roles else None,
                        'loop_facts': q.get('loop_facts', []),
                        'top_facts': q.get('top_facts', []),
                        'path_states': q.get('path_states', []),
                    })
        return facts

    @staticmethod
    def sink_resolution_status(resolution: Any) -> str | None:
        if not isinstance(resolution, dict):
            return None
        paths = resolution.get('path_resolutions') or []
        if not paths:
            return None
        statuses = {str(path.get('status') or 'resolved') for path in paths}
        if statuses == {'resolved'}:
            return 'resolved'
        if 'resolved' in statuses:
            return 'partially_resolved'
        return 'unresolved'

    @staticmethod
    def query_resolving_overlays(query_field: str, overlays: list[SemanticOverlay]) -> list[SemanticOverlay]:
        resolving_kinds = {
            'input_memory': {'AbiCallDataConstruction', 'AbiEncodedLowLevelCall', 'PrecompileCall', 'PathConditionedPrecompileCall'},
            'payload_memory': {'RequireOverlay', 'CustomErrorRevert', 'RawRevertBytes', 'PathConditionedCustomErrorRevert'},
            'memory_read': {
                'MappingSlot', 'MappingRead', 'MappingWrite', 'StateVariableRead', 'StateVariableWrite',
                'StructFieldRead', 'MemoryArrayLengthRead', 'MemoryArrayElementRead',
                'MemoryRegionAllocate', 'PrecompileOutputRead', 'PathConditionedPrecompileOutputRead',
                'BytesContentHash',
            },
        }.get(query_field, set())
        return [overlay for overlay in overlays if overlay.kind in resolving_kinds]

    @staticmethod
    def query_resolving_roles(query_field: str, roles: list[ExpressionRole]) -> list[ExpressionRole]:
        resolving_kinds = {
            'memory_read': {'free_memory_pointer'},
        }.get(query_field, set())
        return [role for role in roles if role.role in resolving_kinds]

    @staticmethod
    def is_free_memory_pointer_read(value: Any) -> bool:
        text = str(value or '').strip().lower()
        return text in {'0x40', '64'}

    @staticmethod
    def unresolved_facts(overlays: list[SemanticOverlay]) -> list[dict[str, Any]]:
        out = []
        for o in overlays:
            reason = o.attrs.get('unresolved_reason')
            if reason:
                out.append({'kind': 'UnresolvedOverlay', 'overlay': o.overlay_id, 'overlay_kind': o.kind, 'reason': reason, 'stmt_refs': o.stmt_refs})
        return out
