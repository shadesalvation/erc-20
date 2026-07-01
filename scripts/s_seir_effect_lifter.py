#!/usr/bin/env python3
from __future__ import annotations
from typing import Any
from assembly_ast_cfg import yul_statement_text, yul_expression
from assembly_memory_ssa import direct_call, statement_expression
from s_seir_id import IdAllocator
from s_seir_model import EffectNode, FunctionUnit
class EffectLifter:
    def __init__(self): self.ids=IdAllocator()
    def effect(self,k,refs,attrs): return EffectNode(self.ids.new('eff'),k,[r for r in refs if r],attrs)
    def lift(self,unit:FunctionUnit,memory_results:dict[int,Any]):
        effects=[]; facts=[]; lookup={(s.block_id,s.text):s.stmt_id for s in unit.source_statements if s.lang=='yul'}
        for block in unit.assembly_blocks:
            res=memory_results.get(block.block_id); label=f'asm_block_{block.block_id}'
            if not res: continue
            for d in getattr(res,'memory_definitions',{}).values(): effects.append(self.effect('MemoryWrite',[self.ref(res,d.node_id,lookup,label)],{'address':d.address,'value':d.value,'memory_version':d.version,'origin_node':f'N{d.node_id}','write_kind':d.kind}))
            for rec in getattr(res,'loop_records',{}).values():
                fact={'kind':'LoopMemoryRecord','loop_id':rec.loop_id,'assembly_block':block.block_id,'header_node':rec.header_node_id,'condition':rec.condition_expr,'bound':getattr(rec,'bound',None).__dict__ if getattr(rec,'bound',None) else None,'memory_effects':[e.__dict__ for e in rec.memory_effects],'status':rec.status}
                facts.append(fact); effects.append(self.effect('LoopMemorySummary',[label],fact))
            for nid,node in res.node_ast.items():
                stmt=self.stmt_ref(node,lookup,label); expr=statement_expression(node); call,args=direct_call(expr); vals=[yul_expression(a) for a in args]; names=self.assigned(node)
                if call=='mload' and len(vals)==1: effects.append(self.effect('MemoryRead',[stmt],{'read_from':vals[0],'value':names[0] if names else None}))
                elif call in {'calldatacopy','codecopy','returndatacopy','mcopy','extcodecopy'}: effects.append(self.effect('MemoryCopy',[stmt],{'op':call,'args':vals}))
                elif call=='sload' and len(vals)==1: effects.append(self.effect('StorageRead',[stmt],{'slot':vals[0],'value':names[0] if names else None}))
                elif call=='sstore' and len(vals)==2: effects.append(self.effect('StorageWrite',[stmt],{'slot':vals[0],'value':vals[1]}))
                elif call in {'call','staticcall','delegatecall','callcode'}: effects.append(self.effect({'call':'Call','staticcall':'StaticCall','delegatecall':'DelegateCall','callcode':'CallCode'}[call],[stmt],{'op':call,'args':vals}))
                elif call and call.startswith('log') and call[3:].isdigit(): effects.append(self.effect('EventLog',[stmt],{'op':call,'data_ptr':vals[0] if len(vals)>0 else None,'data_size':vals[1] if len(vals)>1 else None,'topics':vals[2:]}))
                elif call=='revert' and len(vals)>=2: effects.append(self.effect('Revert',[stmt],{'payload_ptr':vals[0],'payload_size':vals[1]}))
                elif call=='return' and len(vals)>=2: effects.append(self.effect('Return',[stmt],{'payload_ptr':vals[0],'payload_size':vals[1]}))
            for phi in getattr(res,'memory_phis',{}).values(): facts.append({'kind':'MemoryPhi',**phi.__dict__})
            for sm in getattr(res,'range_summaries',{}).values(): facts.append({'kind':'MemoryRangeSummary',**sm.__dict__})
            for top in getattr(res,'memory_tops',[]): facts.append({'kind':'MemoryTop',**top.__dict__})
        for stmt in unit.source_statements:
            if stmt.lang=='solidity':
                t=stmt.text.strip()
                if t.startswith('return'): effects.append(self.effect('Return',[stmt.stmt_id],{'text':t}))
                elif t.startswith('revert'): effects.append(self.effect('Revert',[stmt.stmt_id],{'payload':t.removeprefix('revert').strip()}))
                elif t.startswith('if'): effects.append(self.effect('Branch',[stmt.stmt_id],{'condition':t}))
        return effects,facts
    @staticmethod
    def assigned(n):
        if n.get('nodeType')=='YulVariableDeclaration': return [x.get('name') for x in n.get('variables',[]) if x.get('name')]
        if n.get('nodeType')=='YulAssignment': return [x.get('name') for x in n.get('variableNames',[]) if x.get('name')]
        return []
    def ref(self,res,nid,lookup,label): return self.stmt_ref(res.node_ast.get(nid,{}),lookup,label)
    @staticmethod
    def stmt_ref(node,lookup,label): return lookup.get((label,yul_statement_text(node)),label)
