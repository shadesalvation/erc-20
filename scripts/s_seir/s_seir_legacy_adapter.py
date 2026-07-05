#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path as _SSEIRPath
import sys as _sseir_sys
_SSEIR_ROOT = _SSEIRPath(__file__).resolve().parents[1]
for _sseir_path in (_SSEIR_ROOT / "legacy_yul", _SSEIR_ROOT / "s_seir"):
    _sseir_text = str(_sseir_path)
    if _sseir_text not in _sseir_sys.path:
        _sseir_sys.path.insert(0, _sseir_text)
import tempfile
from pathlib import Path
from typing import Any
from assembly_arithmetic_compare_ir import attach_arithmetic_recovery
from assembly_condition_revert_ir import recover_reverts_for_block
from assembly_event_ir import attach_event_recovery
from assembly_external_call_ir import attach_external_call_recovery
from assembly_recovery_pipeline import discover_binary, generate_slither_inputs, build_branch_expanded_source, attach_branch_materialization, external_output_replacements_by_op
from assembly_semantic_ir import strip_ssa
from assembly_storage_ir import build_storage_report
from s_seir_id import IdAllocator
from s_seir_model import EffectNode, FunctionUnit, SemanticOverlay
class LegacyRecoveryAdapter:
    def __init__(self,report:dict[str,Any]|None): self.report=report or {}; self.ids=IdAllocator(); self.blocks_by_function=self.index_blocks(self.report)
    @staticmethod
    def build_report(source_path:Path, slither_bin:str|None=None, solc_bin:str|None=None, workdir:Path|None=None):
        slither=discover_binary(slither_bin,'SLITHER_BIN',['.venv/bin/slither'],'slither'); solc=discover_binary(solc_bin,'SOLC_BIN',['.venv/bin/solc'],'solc')
        if not slither or not solc: return None, {'kind':'LegacyRecoveryUnavailable','reason':'slither_or_solc_not_found','slither':slither,'solc':solc}
        try:
            with tempfile.TemporaryDirectory(prefix='sseir_legacy_') as tmp:
                tmp=Path(tmp); branch_source=tmp/source_path.name
                analysis_source, branch_report, rewrite_count = build_branch_expanded_source(source_path, solc, branch_source)
                slithir, variables = generate_slither_inputs(analysis_source, slither, solc, workdir or Path.cwd(), tmp)
                report=build_storage_report(analysis_source, slithir, variables, solc)
                report['original_source']=str(source_path); report['branch_expanded_source']='internal temporary source'; report['branch_rewrite_count']=rewrite_count; report['branch_materialization_report']=branch_report
                attach_arithmetic_recovery(report); attach_external_call_recovery(report)
                for block in report.get('assembly_blocks',[]): block['condition_revert_recovery']=recover_reverts_for_block(block)
                attach_event_recovery(report, analysis_source); attach_branch_materialization(report)
                return report, None
        except Exception as exc:
            return None, {'kind':'LegacyRecoveryUnavailable','reason':type(exc).__name__,'message':str(exc)}
    @staticmethod
    def index_blocks(report):
        d={}
        for b in report.get('assembly_blocks',[]):
            c=b.get('context',{}); d.setdefault((c.get('contract',''),c.get('function','')),[]).append(b)
        return d
    def adapt_unit(self,unit:FunctionUnit):
        effects=[]; overlays=[]; facts=[]
        for ord,b in enumerate(self.blocks_by_function.get((unit.contract,unit.function),[])):
            lookup=self.statement_refs_for_block(unit,ord); facts.append({'kind':'LegacyRecoveryBlock','legacy_block_id':b.get('block_id'),'contract':unit.contract,'function':unit.function,'ordinal':ord,'branch_materialization':b.get('branch_materialization',{})})
            self.storage_nodes(b,lookup,effects,overlays); self.require_nodes(b,lookup,effects,overlays); self.event_nodes(b,lookup,effects,overlays); self.external_nodes(b,lookup,effects,overlays); self.external_output_nodes(b,lookup,effects,overlays); self.arith_nodes(b,lookup,effects,overlays)
        return effects,overlays,facts
    @staticmethod
    def statement_refs_for_block(unit,ord):
        if ord>=len(unit.assembly_blocks): return {}
        label=f'asm_block_{unit.assembly_blocks[ord].block_id}'; stmts=[s for s in unit.source_statements if s.lang=='yul' and s.block_id==label]
        return {i:[s.stmt_id] for i,s in enumerate(stmts)}
    def refs(self,l,i): return l.get(i,[]) if i is not None else []
    def eff(self,k,refs,attrs): return EffectNode(self.ids.new('legacy_eff'),k,refs,attrs)
    def ov(self,k,e,attrs): return SemanticOverlay(self.ids.new('legacy_ov'),k,[e.effect_id],e.stmt_refs,attrs)
    @staticmethod
    def clean(x):
        if isinstance(x,str): return strip_ssa(x) or x
        if isinstance(x,list): return [LegacyRecoveryAdapter.clean(v) for v in x]
        if isinstance(x,dict): return {k:LegacyRecoveryAdapter.clean(v) for k,v in x.items() if k!='section' and k!='_memory_ssa_result'}
        return x
    def storage_nodes(self,b,l,effects,overlays):
        for it in b.get('storage_recovery',{}).get('storage_ir',[]):
            sec=it.get('section'); refs=self.refs(l,it.get('op_index'))
            if sec=='slot_computation':
                e=self.eff('StorageSlotComputation',refs,self.clean(it)); effects.append(e); overlays.append(self.ov('MappingSlot' if 'mapping' in it.get('kind','') else 'StorageSlot',e,{'target':self.clean(it.get('target')),'expression':self.clean(it.get('expression')),'state_variable':it.get('state_variable'),'slot_kind':it.get('kind'),'control_path':self.clean(it.get('control_path',[])),'yul':it.get('yul')}))
            elif sec=='storage_read':
                e=self.eff('StorageRead',refs,self.clean(it)); effects.append(e); overlays.append(self.ov('MappingRead' if 'mapping' in it.get('kind','') else 'StateVariableRead',e,{'access':self.clean(it.get('access')),'target':self.clean(it.get('target')),'state_variable':it.get('state_variable'),'slot':self.clean(it.get('slot')),'solidity_like':self.clean(it.get('solidity_like')),'inline':it.get('inline',False),'control_path':self.clean(it.get('control_path',[])),'yul':it.get('yul')}))
            elif sec=='storage_write':
                e=self.eff('StorageWrite',refs,self.clean(it)); effects.append(e); overlays.append(self.ov('MappingWrite' if 'mapping' in it.get('kind','') else 'StateVariableWrite',e,{'access':self.clean(it.get('access')),'value':self.clean(it.get('value_solidity')),'state_variable':it.get('state_variable'),'slot':self.clean(it.get('slot')),'solidity_like':self.clean(it.get('solidity_like')),'unchecked_arithmetic_candidate':it.get('unchecked_arithmetic_candidate',False),'control_path':self.clean(it.get('control_path',[])),'yul':it.get('yul')}))
    def require_nodes(self,b,l,effects,overlays):
        for it in b.get('condition_revert_recovery',{}).get('reverts',[]):
            e=self.eff('Require',self.refs(l,it.get('op_index')),self.clean(it)); effects.append(e); overlays.append(self.ov('RequireOverlay',e,{'require_like':self.clean(it.get('require_like')),'nearest_condition':self.clean(it.get('nearest_condition')),'merged_conditions':self.clean(it.get('merged_conditions',[])),'division_guards':it.get('division_guards',[]),'control_path':self.clean(it.get('control_path',[])),'yul':it.get('yul'),'elided_by_native_precompile': self.require_elided_by_native_precompile(b,it)}))
    def event_nodes(self,b,l,effects,overlays):
        rec=b.get('event_recovery',{})
        for it in rec.get('events',[]):
            e=self.eff('EventLog',self.refs(l,it.get('op_index')),self.clean(it)); effects.append(e); overlays.append(self.ov('EventEmit',e,{'event':it.get('event_name'),'signature':it.get('signature'),'topic0':it.get('topic0'),'args':self.clean(it.get('args',[])),'emit_like':self.clean(it.get('emit_like')),'notes':it.get('notes',[]),'control_path':self.clean(it.get('control_path',[])),'yul':it.get('yul')}))
        for it in rec.get('unmatched_logs',[]):
            e=self.eff('EventLog',self.refs(l,it.get('op_index')),self.clean(it)); effects.append(e); overlays.append(self.ov('EventEmit',e,{'event':it.get('event_name','unknownEvent'),'signature':None,'topic0':it.get('topic0'),'topics':it.get('topics',[]),'data':it.get('data'),'notes':it.get('notes',['unknown_event_topic0']),'yul':it.get('yul')}))
    def external_nodes(self,b,l,effects,overlays):
        for it in b.get('external_call_recovery',{}).get('calls',[]):
            e=self.eff('ExternalCall',self.refs(l,it.get('op_index')),self.clean(it)); effects.append(e)
            kind='PrecompileCall' if it.get('native_precompile') or it.get('precompile') else {'delegatecall':'DelegateCallOverlay','staticcall':'StaticCallOverlay'}.get(it.get('call_kind'),'LowLevelCall')
            overlays.append(self.ov(kind,e,{'call_kind':it.get('call_kind'),'target':it.get('target'),'target_solidity':it.get('target_solidity'),'precompile':it.get('precompile'),'native_precompile':self.clean(it.get('native_precompile')),'gas':it.get('gas'),'value':it.get('value'),'input':self.clean(it.get('input')),'output_range':it.get('output_range'),'success_result':it.get('success_result'),'selector':it.get('selector'),'arguments':self.clean(it.get('arguments',[])),'complete_static_abi':it.get('complete_static_abi',False),'solidity_like':self.clean(it.get('solidity_like')),'yul':it.get('yul')}))

    def require_elided_by_native_precompile(self,b,it):
        nearest=it.get('nearest_condition')
        if not nearest: return False
        conds={op['index']:op.get('semantic',{}).get('condition') for op in b.get('source_yul_semantic_ir',[]) if op.get('kind')=='condition'}
        for call in b.get('external_call_recovery',{}).get('calls',[]):
            native=call.get('native_precompile') or {}
            if native.get('elides_success_check') and conds.get(call.get('op_index'))==nearest:
                return True
        return False

    def external_output_nodes(self,b,l,effects,overlays):
        for op_index,line in external_output_replacements_by_op(b).items():
            e=self.eff('ExternalOutputRead',self.refs(l,op_index),{'op_index':op_index,'solidity_like':line})
            effects.append(e)
            overlays.append(self.ov('PrecompileOutputRead',e,{'solidity_like':line,'op_index':op_index}))
    def arith_nodes(self,b,l,effects,overlays):
        for it in b.get('arithmetic_compare_recovery',{}).get('entries',[]):
            e=self.eff('ExpressionNormalization',self.refs(l,it.get('op_index')),self.clean(it)); effects.append(e); overlays.append(self.ov('ExpressionNormalization',e,{'target':it.get('target'),'context':it.get('context'),'expression':self.clean(it.get('expression')),'solidity_like':self.clean(it.get('solidity_like')),'division_guards':it.get('division_guards',[]),'helpers':it.get('helpers',[]),'control_path':self.clean(it.get('control_path',[])),'yul':it.get('yul')}))
