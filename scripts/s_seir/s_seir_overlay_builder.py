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

from assembly_event_ir import EventDecl, normalize_topic_value
from s_seir_id import IdAllocator
from s_seir_model import EffectNode, FunctionUnit, SemanticOverlay
from s_seir_yul_normalize import abi_packed_word, division_guards, invert_condition, normalize_expr, words_from_memory_query


def hash_candidates_values(candidates: dict[str, tuple[EffectNode, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    return {key: value for key, (_effect, value) in candidates.items()}


class SemanticOverlayBuilder:
    def __init__(self, events_by_contract: dict[str, list[EventDecl]] | None = None, include_shallow_overlays: bool = True):
        self.ids = IdAllocator()
        self.events_by_contract = events_by_contract or {}
        self.include_shallow_overlays = include_shallow_overlays

    def build(self, unit: FunctionUnit, type_env: Any, expr_roles: list[Any], effects: list[EffectNode]) -> list[SemanticOverlay]:
        overlays: list[SemanticOverlay] = []
        overlays.extend(self.require_overlays(type_env, effects))
        overlays.extend(self.raw_revert_bytes(type_env, expr_roles, effects))
        overlays.extend(self.solidity_custom_errors(effects))
        overlays.extend(self.storage_overlays(type_env, effects))
        overlays.extend(self.event_overlays(unit, effects))
        call_overlays = self.call_overlays(effects)
        overlays.extend(call_overlays)
        overlays.extend(self.precompile_output_overlays(effects, call_overlays))
        overlays.extend(self.division_guard_overlays(effects))
        overlays.extend(self.expression_overlays(effects))
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
                'key': slot_expr.get('key'),
                'base': slot_expr.get('base'),
                'base_key': slot_expr.get('base_key'),
                'slot_kind': 'mapping_slot' if base_key not in slot_expr_by_var else 'nested_mapping_slot',
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
            slot_key = self.first_known_key(self.storage_slot_keys(e), slot_expr_by_var)
            value = e.attrs.get('value')
            direct_state = type_env.state_var_by_slot(slot)
            if slot_key:
                slot_expr = slot_expr_by_var[slot_key]
                kind = 'MappingRead' if e.kind == 'StorageRead' else 'MappingWrite'
                attrs = {
                    'access': slot_expr['access'],
                    'target': value if e.kind == 'StorageRead' else None,
                    'value': normalize_expr(value) if e.kind == 'StorageWrite' else None,
                    'value_yul': value if e.kind == 'StorageWrite' else None,
                    'state_variable': slot_expr.get('state_variable'),
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
            else:
                kind = 'StateVariableRead' if e.kind == 'StorageRead' else 'StateVariableWrite'
                attrs = {'access': f'storage[{slot}]', 'target': value if e.kind == 'StorageRead' else None, 'value': normalize_expr(value) if e.kind == 'StorageWrite' else None, 'slot': slot, 'slot_versions': e.attrs.get('slot_versions'), 'unresolved_reason': 'unknown_storage_slot'}
                out.append(self.ov(kind, e.effect_id, e.stmt_refs, self.clean(attrs)))
        return out

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
        else:
            var = type_env.state_var_by_slot(base_text)
            if not var:
                return None
            state_var = var.name
            access = f'{var.name}[{key_norm}]'
            base_key = base_text
        return {'access': access, 'key': key_norm, 'base': base_text, 'base_key': base_key, 'state_variable': state_var, 'resolved_inputs': words[:2], 'notes': notes}

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
        return value is None or value == 'unknown' or (isinstance(value, list) and not value)

    @staticmethod
    def value_text(value: Any) -> str:
        if isinstance(value, list):
            vals = [normalize_expr(v) for v in value if v not in {None, 'unknown'}]
            return vals[0] if len(vals) == 1 else 'phi(' + ', '.join(vals) + ')'
        return normalize_expr(value)

    @staticmethod
    def storage_solidity_like(kind: str, access: str, value: Any, effect: EffectNode) -> str:
        if kind == 'MappingRead':
            target = effect.attrs.get('value')
            return f'{target} = {access};' if target else f'read {access};'
        return f'{access} = {normalize_expr(value)};'

    def event_overlays(self, unit: FunctionUnit, effects: list[EffectNode]) -> list[SemanticOverlay]:
        evs = self.events_for_contract(unit.contract)
        out: list[SemanticOverlay] = []
        for e in effects:
            if e.kind != 'EventLog':
                continue
            event = self.match_event_name(e, evs)
            args, notes = self.event_args(event, e)
            name = event.name if event else 'unknownEvent'
            out.append(self.ov('EventEmit', e.effect_id, e.stmt_refs, {
                'event': name,
                'signature': event.signature if event else None,
                'topic0': event.topic0 if event else (e.attrs.get('topics') or [None])[0],
                'args': args,
                'topics': [normalize_expr(t) for t in e.attrs.get('topics', [])],
                'data': words_from_memory_query(e.attrs.get('data_memory')),
                'emit_like': f"emit {name}({', '.join(map(str,args))});" if args else None,
                'notes': notes,
            }))
        return out

    def event_args(self, event: EventDecl | None, effect: EffectNode) -> tuple[list[Any], list[str]]:
        topics = effect.attrs.get('topics', []) or []
        data_words = words_from_memory_query(effect.attrs.get('data_memory'))
        data_values = [normalize_expr(w.get('value')) for w in data_words if w.get('value') not in {None, 'unknown'}]
        notes: list[str] = []
        if not event:
            notes.append('unknown_event_topic0')
            return [normalize_expr(t) for t in topics[1:]] + data_values, notes
        args: list[Any] = []
        topic_index = 0 if event.anonymous else 1
        data_index = 0
        for param in event.params:
            if param.indexed:
                args.append(normalize_expr(topics[topic_index]) if topic_index < len(topics) else None)
                topic_index += 1
            else:
                args.append(data_values[data_index] if data_index < len(data_values) else None)
                data_index += 1
        if any(a is None for a in args):
            notes.append('incomplete_event_argument_recovery')
        return args, notes

    def call_overlays(self, effects: list[EffectNode]) -> list[SemanticOverlay]:
        out: list[SemanticOverlay] = []
        mapping = {'Call': 'LowLevelCall', 'StaticCall': 'StaticCallOverlay', 'DelegateCall': 'DelegateCallOverlay', 'CallCode': 'LowLevelCall'}
        for e in effects:
            if e.kind not in mapping:
                continue
            attrs = dict(e.attrs)
            attrs['gas'] = normalize_expr(attrs.get('gas')) if attrs.get('gas') else attrs.get('gas')
            attrs['target_solidity'] = self.target_solidity(attrs.get('target'))
            precompile = self.precompile(attrs)
            if precompile:
                attrs.update(precompile)
                out.append(self.ov('PrecompileCall', e.effect_id, e.stmt_refs, attrs))
            else:
                out.append(self.ov(mapping[e.kind], e.effect_id, e.stmt_refs, attrs))
        return out

    def precompile(self, attrs: dict[str, Any]) -> dict[str, Any] | None:
        target = self.int_value(attrs.get('target'))
        table = {1: 'ecrecover', 2: 'sha256', 3: 'ripemd160', 4: 'identity'}
        if target not in table:
            return None
        words = words_from_memory_query(attrs.get('input_memory'))
        input_size = attrs.get('input_size')
        word_values = [w.get('value') for w in words if w.get('value') not in {None, 'unknown'}]
        native = {'precompile': table[target], 'input_words': [normalize_expr(v) for v in word_values], 'input_memory': attrs.get('input_memory')}
        if target == 2 and len(word_values) == 1:
            packed = abi_packed_word(str(word_values[0]), str(input_size))
            result = f"sha256_result_{attrs.get('cfg_node_id', 'x')}"
            native['native_precompile'] = {'solidity_like': f"bytes32 {result} = sha256({packed});", 'result_name': result, 'result_expression': f'sha256({packed})', 'output_word_expression': f'uint256({result})', 'elides_success_check': True}
            native['solidity_like'] = native['native_precompile']['solidity_like']
        return native

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

    def expression_overlays(self, effects: list[EffectNode]) -> list[SemanticOverlay]:
        out: list[SemanticOverlay] = []
        for e in effects:
            if e.kind == 'ValueDef':
                out.append(self.ov('ExpressionNormalization', e.effect_id, e.stmt_refs, {'target': e.attrs.get('targets', [None])[0], 'expression': e.attrs.get('value'), 'solidity_like': self.value_def_like(e), 'context': 'value', 'division_guards': division_guards(e.attrs.get('value'))}))
            elif e.kind == 'Branch':
                out.append(self.ov('ExpressionNormalization', e.effect_id, e.stmt_refs, {'expression': e.attrs.get('condition'), 'solidity_like': f"if ({e.attrs.get('condition_normalized') or normalize_expr(e.attrs.get('condition'))})", 'context': 'condition', 'division_guards': division_guards(e.attrs.get('condition'))}))
        return out

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
    def match_event_name(effect: EffectNode, events: list[EventDecl]) -> EventDecl | None:
        topics = effect.attrs.get('topics', [])
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
