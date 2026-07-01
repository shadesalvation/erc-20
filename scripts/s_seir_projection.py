#!/usr/bin/env python3
from __future__ import annotations
from s_seir_id import IdAllocator
from s_seir_model import ProjectionPolicy
class ProjectionPolicyClassifier:
    def __init__(self): self.ids=IdAllocator()
    def classify(self,overlays): return [self.one(o) for o in overlays]
    def one(self,o):
        k=o.kind
        if k=='RawRevertBytes': return self.pol(o,False,'assembly_preserved','raw_revert_payload')
        if k=='CustomErrorRevert': return self.pol(o,True,'solidity_statement','custom_error_revert')
        if k in {'StorageSlot','MappingSlot'}: return self.pol(o,True,'solidity_expression','storage_slot_computation')
        if k in {'StateVariableRead','StateVariableWrite','MappingRead','MappingWrite'}: return self.pol(o,True,'solidity_statement','storage_effect')
        if k=='RequireOverlay':
            if o.attrs.get('elided_by_native_precompile'):
                return self.pol(o,False,'semantic_comment','require_elided_by_native_precompile')
            return self.pol(o,True,'solidity_statement','revert_zero_to_require')
        if k=='EventEmit':
            ev=o.attrs.get('event') or ''
            return self.pol(o,not (ev.startswith('UnknownEvent') or ev=='unknownEvent'), 'solidity_statement' if not (ev.startswith('UnknownEvent') or ev=='unknownEvent') else 'semantic_comment', 'known_event_emit' if not (ev.startswith('UnknownEvent') or ev=='unknownEvent') else 'unknown_event_log')
        if k in {'PrecompileCall','PrecompileOutputRead'}: return self.pol(o,True,'solidity_statement','known_precompile_call')
        if k in {'LowLevelCall','StaticCallOverlay','DelegateCallOverlay'}: return self.pol(o,True,'low_level_call_statement','low_level_call_preserved')
        if k=='ExpressionNormalization': return self.pol(o,not bool(o.attrs.get('helpers')),'solidity_expression' if not o.attrs.get('helpers') else 'semantic_comment','yul_expression_normalized' if not o.attrs.get('helpers') else 'expression_uses_yul_helper')
        return self.pol(o,False,'unresolved','unsupported_overlay')
    def pol(self,o,exact,out,reason): return ProjectionPolicy(self.ids.new('pol'),o.overlay_id,exact,out,reason,o.stmt_refs)
