#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path as _SSEIRPath
import sys as _sseir_sys
_SSEIR_ROOT = _SSEIRPath(__file__).resolve().parents[1]
for _sseir_path in (_SSEIR_ROOT / "legacy_yul", _SSEIR_ROOT / "s_seir"):
    _sseir_text = str(_sseir_path)
    if _sseir_text not in _sseir_sys.path:
        _sseir_sys.path.insert(0, _sseir_text)
from s_seir_id import IdAllocator
from s_seir_model import ProjectionPolicy
class ProjectionPolicyClassifier:
    def __init__(self): self.ids=IdAllocator()
    def classify(self,overlays): return [self.one(o) for o in overlays]
    def one(self,o):
        k=o.kind
        if k=='RawRevertBytes': return self.pol(o,False,'assembly_preserved','raw_revert_payload')
        if k=='CustomErrorRevert': return self.pol(o,True,'solidity_statement','custom_error_revert')
        if k in {'StorageSlot','MappingSlot'}: return self.pol(o,True,'solidity_expression','mapping_slot_expr' if k=='MappingSlot' else 'storage_slot_computation')
        if k in {'StateVariableRead','MappingRead'}: return self.pol(o,True,'solidity_expression','mapping_slot_read' if k=='MappingRead' else 'state_variable_read')
        if k in {'StateVariableWrite','MappingWrite'}: return self.pol(o,True,'solidity_statement','mapping_slot_write' if k=='MappingWrite' else 'state_variable_write')
        if k=='RequireOverlay':
            if o.attrs.get('elided_by_native_precompile'):
                return self.pol(o,False,'semantic_comment','require_elided_by_native_precompile')
            return self.pol(o,True,'solidity_statement','empty_revert_guard')
        if k=='EventEmit':
            ev=o.attrs.get('event') or ''
            return self.pol(o,not (ev.startswith('UnknownEvent') or ev=='unknownEvent'), 'solidity_statement' if not (ev.startswith('UnknownEvent') or ev=='unknownEvent') else 'semantic_comment', 'known_event_log' if not (ev.startswith('UnknownEvent') or ev=='unknownEvent') else 'unknown_event_log')
        if k in {'PrecompileCall','PrecompileOutputRead'}: return self.pol(o,True,'solidity_statement','known_precompile_call')
        if k in {'LowLevelCall','StaticCallOverlay','DelegateCallOverlay'}:
            known=bool(o.attrs.get('selector') or o.attrs.get('complete_static_abi'))
            return self.pol(o,known,'low_level_call_statement','known_call_abi' if known else 'unknown_call_abi')
        if k=='ExpressionNormalization': return self.pol(o,not bool(o.attrs.get('helpers')),'solidity_expression' if not o.attrs.get('helpers') else 'semantic_comment','yul_expression_normalized' if not o.attrs.get('helpers') else 'expression_uses_yul_helper')
        return self.pol(o,False,'unresolved','unsupported_overlay')
    def pol(self,o,exact,out,reason): return ProjectionPolicy(self.ids.new('pol'),o.overlay_id,exact,out,reason,o.stmt_refs)
