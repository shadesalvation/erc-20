#!/usr/bin/env python3
from __future__ import annotations
from assembly_ast_cfg import build_yul_cfg
from s_seir_model import FunctionUnit
class ControlBuilder:
    def build(self,unit:FunctionUnit)->dict:
        blocks=[]; edges=[]; notes=['function_level_control_skeleton','yul_cfg_embedded_from_assembly_ast_cfg','precise_solidity_cfg_adapter_pending']
        sol=[s for s in unit.source_statements if s.lang=='solidity']
        prev=None
        for i,stmt in enumerate(sol):
            bid=f'bb_sol_{i+1}'; term=self.term(stmt.text); blocks.append({'block_id':bid,'kind':'solidity','stmts':[stmt.stmt_id],'terminator':term})
            if prev: edges.append({'from':prev,'to':bid,'kind':'fallthrough'})
            prev=bid
        for ab in unit.assembly_blocks:
            cfg=build_yul_cfg(ab.yul_ast); label=f'asm_block_{ab.block_id}'
            for n in cfg.nodes:
                blocks.append({'block_id':f'bb_asm{ab.block_id}_n{n.node_id}','kind':'yul','stmts':self.refs(unit,label,n.text),'terminator':{'kind':'YulNode','text':n.text,'node_kind':n.kind}})
            for e in cfg.edges:
                edges.append({'from':f'bb_asm{ab.block_id}_n{e.source}','to':f'bb_asm{ab.block_id}_n{e.target}','kind':e.label})
        return {'blocks':blocks,'edges':edges,'notes':notes}
    @staticmethod
    def refs(unit,label,text): return [s.stmt_id for s in unit.source_statements if s.lang=='yul' and s.block_id==label and s.text==text]
    @staticmethod
    def term(text):
        t=text.strip()
        if t.startswith('if'): return {'kind':'Branch','condition':t}
        if t.startswith('return'): return {'kind':'Return'}
        if t.startswith('revert'): return {'kind':'Revert'}
        return {'kind':'Fallthrough'}
