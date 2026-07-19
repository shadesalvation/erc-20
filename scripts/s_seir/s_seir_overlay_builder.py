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

from assembly_event_ir import EventDecl, is_static_word_type, normalize_topic_value
from assembly_external_call_ir import PRECOMPILES, native_precompile_call
from assembly_semantic_ir import parse_int_literal, parse_memory_address, strip_ssa
from s_seir_id import IdAllocator
from s_seir_model import EffectNode, FunctionUnit, SemanticOverlay
from s_seir_selector_registry import normalize_selector_value
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
        overlays: list[SemanticOverlay] = []
        overlays.extend(self.require_overlays(type_env, effects))
        overlays.extend(self.raw_revert_bytes(type_env, expr_roles, effects))
        overlays.extend(self.return_overlays(effects))
        overlays.extend(self.solidity_custom_errors(effects))
        overlays.extend(self.custom_error_selector_overlays(effects))
        overlays.extend(self.storage_overlays(type_env, effects))
        overlays.extend(self.event_overlays(unit, type_env, effects))
        call_overlays = self.call_overlays(effects)
        overlays.extend(call_overlays)
        overlays.extend(self.precompile_output_overlays(effects, call_overlays))
        overlays.extend(self.division_guard_overlays(effects))
        overlays.extend(self.memory_object_construction_overlays(unit, type_env, effects))
        overlays.extend(self.struct_memory_mutation_overlays(unit, type_env, effects))
        overlays.extend(self.abi_call_data_overlays(effects, overlays))
        overlays.extend(self.calldata_word_read_overlays(type_env, effects))
        overlays.extend(self.address_code_overlays(type_env, effects))
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
                out.append(self.ov('RawRevertBytes', e.effect_id, e.stmt_refs, {'source_object': obj, 'payload': f'{obj}[0:{obj}.length]', 'guard': self.nearest_guard(e, effects)}))
        return out

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
            selector_info = self.selector_info_from_partial(e.attrs.get('payload_memory_partial'), preferred_kind='error')
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
            }
            out.append(self.ov('CustomErrorRevert', e.effect_id, e.stmt_refs, attrs))
        return out

    def require_overlays(self, type_env: Any, effects: list[EffectNode]) -> list[SemanticOverlay]:
        out: list[SemanticOverlay] = []
        branches = [e for e in effects if e.kind == 'Branch']
        for e in effects:
            if e.kind != 'Revert' or e.attrs.get('payload'):
                continue
            if str(e.attrs.get('payload_ptr')) != '0' or str(e.attrs.get('payload_size')) != '0':
                continue
            merged = self.mergeable_revert_conditions(e, effects)
            guard = merged[-1] if merged else self.nearest_guard(e, effects, branches)
            condition = self.require_condition(merged or ([guard] if guard else []), type_env)
            out.append(self.ov('RequireOverlay', e.effect_id, e.stmt_refs, {
                'condition': condition,
                'nearest_condition': guard,
                'require_like': f'require({condition});',
                'revert_payload': 'empty',
                'control_path': self.path_states(e),
                'merged_conditions': merged,
                'require_conditions': merged or ([guard] if guard else []),
                'discarded_before_revert': self.discarded_before_revert(e, merged, effects),
                'elided_by_native_precompile': self.is_native_precompile_success_guard(guard),
            }))
        return out

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

    @staticmethod
    def require_condition(conditions: list[str], type_env: Any) -> str:
        clean = [condition for condition in conditions if condition]
        if not clean:
            return 'false'
        if len(clean) == 1:
            return invert_condition(clean[0], type_env)
        joined = ' && '.join(f'({normalize_expr(condition)})' for condition in clean)
        return f'!({joined})'

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

    def storage_overlays(self, type_env: Any, effects: list[EffectNode]) -> list[SemanticOverlay]:
        """Recover storage overlays with storage-consumer-gated, SSA-aware slot activation.

        MemoryHash nodes are keyed by their MemorySSA value versions when
        available. This prevents distinct assignments like `Jfwv := ...` and
        `Jfwv := ...` from collapsing into the same slot symbol.
        """
        out: list[SemanticOverlay] = []
        hash_candidates: dict[str, tuple[EffectNode, dict[str, Any]]] = {}
        activated: set[str] = set()
        slot_expr_by_var: dict[str, dict[str, Any]] = {}
        hash_effect_by_var: dict[str, EffectNode] = {}
        value_defs_by_version = self.value_defs_by_version(effects)

        for e in effects:
            if e.kind != 'MemoryHash':
                continue
            keys = self.hash_result_keys(e)
            if not keys:
                continue
            slot_expr = self.mapping_slot_expr(type_env, e, slot_expr_by_var | {k: v for k, v in hash_candidates_values(hash_candidates).items()})
            if not slot_expr:
                continue
            slot_expr['target'] = e.attrs.get('value')
            slot_expr['target_keys'] = keys
            for key in keys:
                hash_candidates[key] = (e, slot_expr)

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
                'base': slot_expr.get('base'),
                'base_key': slot_expr.get('base_key'),
                'slot_kind': slot_expr.get('slot_kind') or ('mapping_slot' if base_key not in slot_expr_by_var else 'nested_mapping_slot'),
                'activation': 'storage_consumer',
                'resolved_inputs': slot_expr.get('resolved_inputs'),
                'notes': slot_expr.get('notes', []),
            }))

        for e in effects:
            if e.kind in {'StorageRead', 'StorageWrite'}:
                for key in self.storage_slot_keys(e):
                    activate(key)

        for e in effects:
            if e.kind not in {'StorageRead', 'StorageWrite'}:
                continue
            slot = str(e.attrs.get('slot') or '')
            version_keys = list(e.attrs.get('slot_versions') or [])
            if len(version_keys) > 1:
                path_overlay = self.path_conditioned_storage_overlay(e, type_env, version_keys, slot_expr_by_var, hash_effect_by_var)
                if path_overlay:
                    out.append(path_overlay)
                    statuses = {candidate.get('status') for candidate in path_overlay.attrs.get('candidates', [])}
                    resolved_accesses = {
                        candidate.get('access')
                        for candidate in path_overlay.attrs.get('candidates', [])
                        if candidate.get('status') == 'resolved'
                    }
                    if 'unresolved' in statuses or len(resolved_accesses) > 1:
                        continue
            slot_key = self.first_known_key(self.storage_slot_keys(e), slot_expr_by_var)
            value = e.attrs.get('value')
            direct_state = type_env.state_var_by_slot(slot)
            direct_slot = self.direct_storage_slot_info(type_env, e, value_defs_by_version)
            if slot_key:
                slot_expr = slot_expr_by_var[slot_key]
                kind = 'MappingRead' if e.kind == 'StorageRead' else 'MappingWrite'
                attrs = {
                    'access': slot_expr['access'],
                    'target': value if e.kind == 'StorageRead' else None,
                    'value': normalize_expr(value) if e.kind == 'StorageWrite' else None,
                    'value_yul': value if e.kind == 'StorageWrite' else None,
                    'state_variable': slot_expr.get('state_variable'),
                    'storage_reference': slot_expr.get('storage_reference'),
                    'storage_reference_type': slot_expr.get('storage_reference_type'),
                    'storage_reference_kind': slot_expr.get('storage_reference_kind'),
                    'storage_field': slot_expr.get('storage_field'),
                    'key': slot_expr.get('key'),
                    'slot': slot,
                    'slot_key': slot_key,
                    'slot_versions': e.attrs.get('slot_versions'),
                    'slot_effect': hash_effect_by_var.get(slot_key).effect_id if slot_key in hash_effect_by_var else None,
                    'solidity_like': self.storage_solidity_like(kind, slot_expr['access'], value, e),
                    'notes': slot_expr.get('notes', []),
                }
                effects_used = [x for x in [hash_effect_by_var.get(slot_key).effect_id if slot_key in hash_effect_by_var else None, e.effect_id] if x]
                refs = list(dict.fromkeys((hash_effect_by_var.get(slot_key).stmt_refs if slot_key in hash_effect_by_var else []) + e.stmt_refs))
                out.append(self.ov(kind, effects_used, refs, self.clean(attrs)))
            elif direct_state:
                kind = 'StateVariableRead' if e.kind == 'StorageRead' else 'StateVariableWrite'
                attrs = {
                    'access': direct_state.name,
                    'target': value if e.kind == 'StorageRead' else None,
                    'value': normalize_expr(value) if e.kind == 'StorageWrite' else None,
                    'state_variable': direct_state.name,
                    'slot': slot,
                    'solidity_like': f"{value} = {direct_state.name};" if e.kind == 'StorageRead' and value else f"{direct_state.name} = {normalize_expr(value)};",
                }
                out.append(self.ov(kind, e.effect_id, e.stmt_refs, self.clean(attrs)))
            elif direct_slot:
                kind = 'StateVariableRead' if e.kind == 'StorageRead' else 'StateVariableWrite'
                access = f"storage[{direct_slot.get('name') or direct_slot.get('slot')}]"
                state_note = 'state read, manual slot' if e.kind == 'StorageRead' else 'state write, manual slot'
                solidity_like = (
                    f"{value} = {access}; /* {state_note} */"
                    if e.kind == 'StorageRead' and value
                    else f"{access} = {normalize_expr(value)}; /* {state_note} */"
                )
                attrs = {
                    'access': access,
                    'target': value if e.kind == 'StorageRead' else None,
                    'value': normalize_expr(value) if e.kind == 'StorageWrite' else None,
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
                    'notes': ['constant_storage_slot', 'manual_storage_layout', 'writes_contract_state' if e.kind == 'StorageWrite' else 'reads_contract_state'],
                }
                out.append(self.ov(kind, e.effect_id, e.stmt_refs, self.clean(attrs)))
            else:
                kind = 'StateVariableRead' if e.kind == 'StorageRead' else 'StateVariableWrite'
                attrs = {'access': f'storage[{slot}]', 'target': value if e.kind == 'StorageRead' else None, 'value': normalize_expr(value) if e.kind == 'StorageWrite' else None, 'slot': slot, 'slot_versions': e.attrs.get('slot_versions'), 'unresolved_reason': 'unknown_storage_slot'}
                out.append(self.ov(kind, e.effect_id, e.stmt_refs, self.clean(attrs)))
        return out

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
        if value:
            item = getattr(type_env, 'constant', lambda _name: None)(text) or {}
            return {'name': text, 'value': value, 'type_string': item.get('type_string'), 'kind': 'constant_storage_slot'}
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
        access = f"storage[{direct_slot.get('name') or direct_slot.get('slot')}]"
        return self.clean({
            'original': str(expr),
            'access': access,
            'slot': slot_expr,
            'slot_constant': direct_slot.get('name'),
            'slot_value': direct_slot.get('value'),
            'storage_model': 'manual_constant_slot',
            'state_access': True,
            'state_mutation': False,
            'variable_name_inferred': False,
            'solidity_like': f"{access} /* state read, manual slot */",
            'notes': ['constant_storage_slot', 'manual_storage_layout', 'reads_contract_state'],
        })

    def normalize_expression_with_state_reads(
        self,
        type_env: Any,
        expr: Any,
        value_defs_by_name: dict[str, list[EffectNode]],
    ) -> tuple[str, dict[str, Any] | None]:
        state_read = self.manual_slot_state_read_from_expr(type_env, expr, value_defs_by_name)
        if state_read:
            return str(state_read['solidity_like']), state_read
        return normalize_expr(expr), None

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
    ) -> SemanticOverlay | None:
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        value = effect.attrs.get('value')
        for key in version_keys:
            if key in seen:
                continue
            seen.add(key)
            slot_expr = slot_expr_by_var.get(key)
            if slot_expr:
                access = slot_expr['access']
                kind = 'MappingRead' if effect.kind == 'StorageRead' else 'MappingWrite'
                candidates.append(self.clean({
                    'slot_key': key,
                    'status': 'resolved',
                    'overlay_kind': kind,
                    'access': access,
                    'state_variable': slot_expr.get('state_variable'),
                    'storage_reference': slot_expr.get('storage_reference'),
                    'storage_reference_type': slot_expr.get('storage_reference_type'),
                    'storage_reference_kind': slot_expr.get('storage_reference_kind'),
                    'storage_field': slot_expr.get('storage_field'),
                    'key': slot_expr.get('key'),
                    'slot_effect': hash_effect_by_var.get(key).effect_id if key in hash_effect_by_var else None,
                    'solidity_like': self.storage_solidity_like(kind, access, value, effect),
                    'notes': slot_expr.get('notes', []),
                }))
                continue
            direct_state = type_env.state_var_by_slot(key)
            if direct_state:
                kind = 'StateVariableRead' if effect.kind == 'StorageRead' else 'StateVariableWrite'
                candidates.append(self.clean({
                    'slot_key': key,
                    'status': 'resolved',
                    'overlay_kind': kind,
                    'access': direct_state.name,
                    'state_variable': direct_state.name,
                    'solidity_like': f"{value} = {direct_state.name};" if effect.kind == 'StorageRead' and value else f"{direct_state.name} = {normalize_expr(value)};",
                }))
                continue
            candidates.append({
                'slot_key': key,
                'status': 'unresolved',
                'overlay_kind': 'StateVariableRead' if effect.kind == 'StorageRead' else 'StateVariableWrite',
                'access': f'storage[{key}]',
                'unresolved_reason': 'unknown_storage_slot_version',
            })
        if not candidates:
            return None
        overlay_kind = 'PathConditionedStorageRead' if effect.kind == 'StorageRead' else 'PathConditionedStorageWrite'
        attrs = self.clean({
            'slot': effect.attrs.get('slot'),
            'slot_versions': version_keys,
            'target': value if effect.kind == 'StorageRead' else None,
            'value': normalize_expr(value) if effect.kind == 'StorageWrite' else None,
            'value_yul': value if effect.kind == 'StorageWrite' else None,
            'path_states': effect.attrs.get('path_states'),
            'candidates': candidates,
            'note': 'storage_effect_has_multiple_ssa_slot_versions',
        })
        return self.ov(overlay_kind, effect.effect_id, effect.stmt_refs, attrs)

    def mapping_slot_expr(self, type_env: Any, effect: EffectNode, known: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
        words = words_from_memory_query(effect.attrs.get('memory_read'))
        if len(words) < 2:
            return None
        key = words[0].get('value')
        base = words[1].get('value')
        if self.is_unknown_value(key) or self.is_unknown_value(base):
            return None
        notes: list[str] = []
        for w in words[:2]:
            if w.get('discarded_unknown_branch'):
                notes.append('discarded_unknown_memory_branch')
        key_norm = self.value_text(key)
        base_text = self.value_text(base)
        base_key = self.first_known_key(self.word_value_keys(words[1]), known)
        state_var = None
        if base_key:
            prev = known[base_key]
            access = f"{prev['access']}[{key_norm}]"
            state_var = prev.get('state_variable')
            return {
                'access': access,
                'key': key_norm,
                'base': base_text,
                'base_key': base_key,
                'state_variable': state_var,
                'storage_reference': prev.get('storage_reference'),
                'storage_reference_type': prev.get('storage_reference_type'),
                'storage_reference_kind': prev.get('storage_reference_kind'),
                'storage_field': prev.get('storage_field'),
                'slot_kind': 'nested_mapping_slot',
                'resolved_inputs': words[:2],
                'notes': notes,
            }
        else:
            var = type_env.state_var_by_slot(base_text)
            if var:
                state_var = var.name
                access = f'{var.name}[{key_norm}]'
                base_key = base_text
                return {'access': access, 'key': key_norm, 'base': base_text, 'base_key': base_key, 'state_variable': state_var, 'slot_kind': 'mapping_slot', 'resolved_inputs': words[:2], 'notes': notes}
            storage_ref = self.storage_ref_mapping_access(type_env, base, key_norm)
            if storage_ref:
                return {
                    'access': storage_ref['access'],
                    'key': key_norm,
                    'base': base_text,
                    'base_key': base_text,
                    'state_variable': None,
                    'storage_reference': storage_ref.get('root'),
                    'storage_reference_type': storage_ref.get('root_type'),
                    'storage_reference_kind': storage_ref.get('kind'),
                    'storage_field': self.field_ref(storage_ref.get('field')),
                    'slot_kind': 'storage_ref_mapping_slot',
                    'resolved_inputs': words[:2],
                    'notes': list(dict.fromkeys(notes + ['storage_reference_mapping_slot'])),
                }
            return None

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

    @staticmethod
    def storage_solidity_like(kind: str, access: str, value: Any, effect: EffectNode) -> str:
        if kind == 'MappingRead':
            target = effect.attrs.get('value')
            return f'{target} = {access};' if target else f'read {access};'
        return f'{access} = {normalize_expr(value)};'

    def event_overlays(self, unit: FunctionUnit, type_env: Any, effects: list[EffectNode]) -> list[SemanticOverlay]:
        evs = self.events_for_contract(unit.contract)
        out: list[SemanticOverlay] = []
        value_defs_by_name = self.value_defs_by_name(effects)
        for e in effects:
            if e.kind != 'EventLog':
                continue
            raw_topics = e.attrs.get('topics', []) or []
            topics = [self.resolve_constant_expr(type_env, topic) for topic in raw_topics]
            topic_constants = [
                {'name': str(raw), 'value': str(resolved)}
                for raw, resolved in zip(raw_topics, topics)
                if str(raw) != str(resolved)
            ]
            event = self.match_event_name(e, evs, topics)
            data_words = self.event_data_words(e)
            args, notes, argument_state_reads = self.event_args(event, e, type_env, value_defs_by_name, topics, data_words)
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
                'data': data_words,
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
    ) -> tuple[list[Any], list[str], list[dict[str, Any]]]:
        topics = topics if topics is not None else (effect.attrs.get('topics', []) or [])
        data_words = self.event_data_words(effect) if data_words is None else data_words
        data_values = []
        notes: list[str] = []
        argument_state_reads: list[dict[str, Any]] = []

        def render_arg(value: Any) -> str:
            state_read = self.manual_slot_state_read_from_expr(type_env, value, value_defs_by_name)
            if state_read:
                if state_read not in argument_state_reads:
                    argument_state_reads.append(state_read)
                if 'event_argument_state_read' not in notes:
                    notes.append('event_argument_state_read')
                return str(state_read['solidity_like'])
            return normalize_expr(value)

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
            return [render_arg(t) for t in topics[1:]] + data_values, notes, argument_state_reads
        args: list[Any] = []
        topic_index = 0 if event.anonymous else 1
        data_index = 0
        for param in event.params:
            if param.indexed:
                args.append(render_arg(topics[topic_index]) if topic_index < len(topics) else None)
                topic_index += 1
            else:
                args.append(data_values[data_index] if data_index < len(data_values) else None)
                data_index += 1
        if any(a is None for a in args):
            notes.append('incomplete_event_argument_recovery')
        non_indexed_count = len([p for p in event.params if not p.indexed])
        if len(data_words) < non_indexed_count:
            notes.append('memory_data_words_incomplete')
        return args, notes, argument_state_reads

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
            if e.kind not in mapping:
                continue
            attrs = dict(e.attrs)
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

    def expression_overlays(self, type_env: Any, effects: list[EffectNode]) -> list[SemanticOverlay]:
        out: list[SemanticOverlay] = []
        value_defs_by_name = self.value_defs_by_name(effects)
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
                out.append(self.ov('ExpressionNormalization', e.effect_id, e.stmt_refs, {'target': target, 'expression': e.attrs.get('value'), 'solidity_like': self.value_def_like(e), 'context': 'value', 'division_guards': division_guards(e.attrs.get('value'))}))
            elif e.kind == 'Branch':
                condition_text = e.attrs.get('condition_final_temp') or e.attrs.get('condition_normalized') or normalize_expr(e.attrs.get('condition'))
                condition_evaluation = self.condition_evaluation_with_state_reads(type_env, e.attrs.get('condition_evaluation'), value_defs_by_name)
                out.append(self.ov('ExpressionNormalization', e.effect_id, e.stmt_refs, {
                    'expression': e.attrs.get('condition'),
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
            call, args = call_parts(str(effect.attrs.get('value') or ''))
            if call != 'extcodesize' or len(args) != 1 or not target:
                continue
            address = args[0]
            address_expr = normalize_expr(address)
            code_size = f'{address_expr}.code.length'
            target_info = getattr(type_env, 'lookup', lambda _name: None)(target)
            target_type = getattr(target_info, 'type_string', None)
            if getattr(type_env, 'is_bool', lambda _name: False)(target):
                condition = f'({code_size} != 0)'
                out.append(self.ov('AddressHasCode', effect.effect_id, effect.stmt_refs, {
                    'target': target,
                    'target_type': target_type,
                    'address': address,
                    'address_normalized': address_expr,
                    'code_size': code_size,
                    'condition': condition,
                    'source_expression': effect.attrs.get('value'),
                    'solidity_like': f'{target} = {condition};',
                    'solidity_equivalent': True,
                }))
            else:
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
            out.append(self.ov('CalldataWordRead', effect.effect_id, effect.stmt_refs, {
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
            }))
        return out

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
            key = (o.kind, tuple(o.effects), tuple(o.stmt_refs), str(sorted(o.attrs.items())))
            if key not in seen:
                seen.add(key)
                out.append(o)
        return out
