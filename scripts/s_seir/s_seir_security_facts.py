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

from s_seir_id import IdAllocator
from s_seir_model import EffectNode, SecurityFact, SemanticOverlay


class SecurityFactBuilder:
    def __init__(self) -> None:
        self.ids = IdAllocator()

    def build(self, effects: list[EffectNode], overlays: list[SemanticOverlay]) -> list[SecurityFact]:
        out: list[SecurityFact] = []
        effect_by_id = {effect.effect_id: effect for effect in effects}
        for overlay in overlays:
            out.extend(self.from_overlay(overlay, effect_by_id))
        out.extend(self.external_call_before_state_update(effects))
        out.extend(self.event_state_links(out))
        return out

    def fact(self, kind: str, attrs: dict[str, Any], overlay: SemanticOverlay | None = None, effects: list[EffectNode] | None = None, stmt_refs: list[str] | None = None) -> SecurityFact:
        source_overlays = [overlay.overlay_id] if overlay else []
        source_effects = [effect.effect_id for effect in effects or []]
        refs = stmt_refs if stmt_refs is not None else (overlay.stmt_refs if overlay else [])
        return SecurityFact(self.ids.new('sf'), kind, attrs, source_overlays, source_effects, refs)

    def from_overlay(self, overlay: SemanticOverlay, effect_by_id: dict[str, EffectNode]) -> list[SecurityFact]:
        effects = [effect_by_id[eid] for eid in overlay.effects if eid in effect_by_id]
        attrs = overlay.attrs
        if overlay.kind in {'MappingRead', 'StateVariableRead'}:
            state_var = self.state_var(attrs)
            account = self.account(attrs)
            kind = 'BalanceRead' if account else 'StateRead'
            data = {'state_var': state_var, 'account': account} if account else {'state_var': state_var, 'access': attrs.get('access') or attrs.get('slot')}
            return [self.fact(kind, self.clean_attrs(data), overlay, effects)]
        if overlay.kind in {'MappingWrite', 'StateVariableWrite'}:
            state_var = self.state_var(attrs)
            access = str(attrs.get('access') or attrs.get('slot') or '')
            value = attrs.get('value') or attrs.get('value_solidity') or attrs.get('solidity_like')
            keys = self.bracket_keys(access)
            if len(keys) >= 2:
                return [self.fact('AllowanceUpdate', self.clean_attrs({'state_var': state_var, 'owner': keys[0], 'spender': keys[1], 'value': value}), overlay, effects)]
            account = self.account(attrs) or (keys[0] if keys else None)
            delta = self.delta(value, account)
            kind = 'BalanceUpdate' if account else 'StateUpdate'
            data = {'state_var': state_var, 'account': account, 'delta': delta, 'value': value} if account else {'state_var': state_var, 'value': value}
            return [self.fact(kind, self.clean_attrs(data), overlay, effects)]
        if overlay.kind == 'EventEmit':
            event = attrs.get('event') or attrs.get('event_name')
            if event == 'Transfer':
                args = attrs.get('args') or []
                flat = self.flat_args(args)
                return [self.fact('TransferEvent', self.clean_attrs({'from': self.item(flat, 0), 'to': self.item(flat, 1), 'amount': self.item(flat, 2)}), overlay, effects)]
            return [self.fact('EventEmission', self.clean_attrs({'event': event, 'signature': attrs.get('signature')}), overlay, effects)]
        if overlay.kind in {'LowLevelCall', 'StaticCallOverlay', 'DelegateCallOverlay', 'PrecompileCall', 'PathConditionedPrecompileCall', 'ExternalCall'}:
            call_type = attrs.get('call_kind') or attrs.get('op') or overlay.kind
            return [self.fact('ExternalCall', self.clean_attrs({'target': attrs.get('target_solidity') or attrs.get('target'), 'value': attrs.get('value', '0'), 'call_type': call_type, 'selector': attrs.get('selector')}), overlay, effects)]
        if overlay.kind == 'RequireOverlay':
            condition = attrs.get('condition') or attrs.get('nearest_condition') or attrs.get('require_like')
            return [self.fact('GuardCondition', self.clean_attrs({'condition': condition, 'on_fail': 'revert'}), overlay, effects)]
        if overlay.kind == 'RawRevertBytes':
            return [self.fact('TransparentRevertBubble', self.clean_attrs({'source_object': attrs.get('source_object'), 'payload': attrs.get('payload')}), overlay, effects)]
        return []

    def external_call_before_state_update(self, effects: list[EffectNode]) -> list[SecurityFact]:
        out: list[SecurityFact] = []
        call_effects = [e for e in effects if e.kind in {'Call', 'StaticCall', 'DelegateCall', 'ExternalCall'}]
        write_effects = [e for e in effects if e.kind == 'StorageWrite']
        for call in call_effects:
            for write in write_effects:
                if self.first_ref(call) and self.first_ref(write) and self.first_ref(call) < self.first_ref(write):
                    out.append(self.fact('ExternalCallBeforeStateUpdate', {'call': call.effect_id, 'state_update': write.effect_id}, None, [call, write], list(dict.fromkeys(call.stmt_refs + write.stmt_refs))))
        return out

    def event_state_links(self, facts: list[SecurityFact]) -> list[SecurityFact]:
        transfers = [f for f in facts if f.kind == 'TransferEvent']
        updates = [f for f in facts if f.kind == 'BalanceUpdate']
        if not transfers or not updates:
            return []
        out = []
        for event in transfers:
            out.append(SecurityFact(self.ids.new('sf'), 'EventStateLink', {'event': 'Transfer', 'state_updates': [u.fact_id for u in updates]}, [event.fact_id], [], event.stmt_refs))
        return out

    @staticmethod
    def first_ref(effect: EffectNode) -> str:
        return effect.stmt_refs[0] if effect.stmt_refs else ''

    @staticmethod
    def state_var(attrs: dict[str, Any]) -> Any:
        return attrs.get('state_var') or attrs.get('state_variable') or attrs.get('stateVariable')

    @staticmethod
    def bracket_keys(text: str) -> list[str]:
        return re.findall(r'\[([^\]]+)\]', text or '')

    def account(self, attrs: dict[str, Any]) -> Any:
        access = str(attrs.get('access') or '')
        keys = self.bracket_keys(access)
        if keys:
            return keys[-1] if len(keys) == 1 else keys[0]
        key = attrs.get('key')
        if isinstance(key, list):
            return key[-1] if key else None
        return key

    @staticmethod
    def delta(value: Any, account: Any = None) -> Any:
        text = str(value or '').strip()
        while text.startswith('(') and text.endswith(')'):
            text = text[1:-1].strip()
        compact = text.replace(' ', '')
        m = re.search(r'-([A-Za-z_][A-Za-z0-9_]*)$', compact)
        if m:
            return '-' + m.group(1)
        m = re.search(r'\+([A-Za-z_][A-Za-z0-9_]*)$', compact)
        if m:
            return '+' + m.group(1)
        m = re.match(r'sub\([^,]+,\s*([^\)]+)\)', text)
        if m:
            return '-' + m.group(1).strip()
        m = re.match(r'add\([^,]+,\s*([^\)]+)\)', text)
        if m:
            return '+' + m.group(1).strip()
        return text or None

    @staticmethod
    def flat_args(args: Any) -> list[Any]:
        if not isinstance(args, list):
            return []
        out = []
        for item in args:
            if isinstance(item, dict):
                out.append(item.get('value') or item.get('name') or item.get('text') or item.get('solidity'))
            else:
                out.append(item)
        return out

    @staticmethod
    def item(items: list[Any], index: int) -> Any:
        return items[index] if index < len(items) else None

    @staticmethod
    def clean_attrs(attrs: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in attrs.items() if v is not None and v != ''}
