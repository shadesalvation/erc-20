#!/usr/bin/env python3
from __future__ import annotations
from assembly_event_ir import EventDecl, normalize_topic_value
from s_seir_id import IdAllocator
from s_seir_model import EffectNode, ExpressionRole, FunctionUnit, SemanticOverlay
class SemanticOverlayBuilder:
    def __init__(self,events_by_contract:dict[str,list[EventDecl]]|None=None,include_shallow_overlays:bool=True): self.ids=IdAllocator(); self.events_by_contract=events_by_contract or {}; self.include_shallow_overlays=include_shallow_overlays
    def build(self,unit, type_env, expr_roles, effects):
        overlays=[]; overlays+=self.raw_revert_bytes(type_env,expr_roles,effects); overlays+=self.solidity_custom_errors(effects)
        if self.include_shallow_overlays: overlays+=self.storage_overlays(effects)+self.event_overlays(unit,effects)+self.call_overlays(effects)
        return overlays
    def ov(self,k,e,attrs): return SemanticOverlay(self.ids.new('ov'),k,[e.effect_id],e.stmt_refs,attrs)
    def raw_revert_bytes(self,type_env,roles,effects):
        out=[]; mem=[e for e in effects if e.kind=='MemoryRead']; by={}
        for r in roles: by.setdefault(r.stmt_ref,[]).append(r)
        for e in effects:
            if e.kind!='Revert' or not e.attrs.get('payload_ptr'): continue
            ptr=next((r for ref in e.stmt_refs for r in by.get(ref,[]) if r.role=='revert_payload_ptr'),None)
            obj=ptr.attrs.get('object') if ptr else None
            size=e.attrs.get('payload_size')
            if obj and type_env.is_bytes_memory(obj) and (size==f'{obj}.length' or any(m.attrs.get('value')==size and m.attrs.get('read_from')==obj for m in mem)):
                out.append(self.ov('RawRevertBytes',e,{'source_object':obj,'payload':f'{obj}[0:{obj}.length]','guard':next((r.text for r in roles if r.role=='guard_condition' and obj in r.text),None)}))
        return out
    def solidity_custom_errors(self,effects):
        return [self.ov('CustomErrorRevert',e,{'error':e.attrs.get('payload')}) for e in effects if e.kind=='Revert' and isinstance(e.attrs.get('payload'),str) and e.attrs.get('payload').strip()]
    def storage_overlays(self,effects):
        out=[]
        for e in effects:
            if e.kind=='StorageRead': out.append(self.ov('StateVariableRead',e,dict(e.attrs)))
            elif e.kind=='StorageWrite': out.append(self.ov('StateVariableWrite',e,dict(e.attrs)))
        return out
    def event_overlays(self,unit,effects):
        evs=self.events_for_contract(unit.contract); out=[]
        for e in effects:
            if e.kind!='EventLog': continue
            ev=self.match_event_name(e,evs); out.append(self.ov('EventEmit',e,{'event':ev.name if ev else 'unknownEvent','signature':ev.signature if ev else None,'topic0':ev.topic0 if ev else None,**e.attrs}))
        return out
    def call_overlays(self,effects):
        m={'Call':'LowLevelCall','StaticCall':'StaticCallOverlay','DelegateCall':'DelegateCallOverlay','CallCode':'LowLevelCall'}
        return [self.ov(m[e.kind],e,dict(e.attrs)) for e in effects if e.kind in m]
    def events_for_contract(self,contract):
        out=[]; seen=set()
        for group in [self.events_by_contract.get(contract,[]), *self.events_by_contract.values()]:
            for e in group:
                key=e.signature+(' anonymous' if e.anonymous else '')
                if key not in seen: out.append(e); seen.add(key)
        return out
    @staticmethod
    def match_event_name(effect,events):
        topics=effect.attrs.get('topics',[])
        if not topics: return None
        t0=normalize_topic_value(topics[0])
        for e in events:
            indexed=len([p for p in e.params if p.indexed])
            if e.anonymous and len(topics)==indexed: return e
            if (not e.anonymous) and len(topics)==indexed+1 and normalize_topic_value(e.topic0)==t0: return e
        return None
