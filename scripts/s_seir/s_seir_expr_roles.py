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
from assembly_ast_cfg import yul_expression
from assembly_memory_ssa import direct_call, statement_expression
from s_seir_id import IdAllocator
from s_seir_model import ExpressionRole, FunctionUnit

def split_args(text:str)->list[str]:
    out=[]; cur=[]; depth=0
    for ch in text:
        if ch==',' and depth==0:
            out.append(''.join(cur).strip()); cur=[]; continue
        if ch=='(': depth+=1
        elif ch==')': depth-=1
        cur.append(ch)
    if cur: out.append(''.join(cur).strip())
    return out
class ExpressionRoleAnalyzer:
    def __init__(self): self.ids=IdAllocator()
    def role(self,text,norm,role,type_hint,stmt_ref,attrs=None): return ExpressionRole(self.ids.new('expr'),text,norm,role,type_hint,stmt_ref,attrs or {})
    def analyze(self,unit:FunctionUnit,type_env:Any,memory_results:dict[int,Any])->list[ExpressionRole]:
        roles=[]
        for stmt in unit.source_statements:
            if stmt.lang=='solidity' and stmt.text.strip().startswith('if'):
                roles.append(self.role(stmt.text,None,'guard_condition','bool',stmt.stmt_id,{}))
        lookup={(s.block_id,s.text):s.stmt_id for s in unit.source_statements if s.lang=='yul'}
        for block in unit.assembly_blocks:
            res=memory_results.get(block.block_id); label=f'asm_block_{block.block_id}'
            if not res: continue
            for _nid,node in res.node_ast.items():
                expr=statement_expression(node); call,args=direct_call(expr); stmt=lookup.get((label, __import__('assembly_ast_cfg').yul_statement_text(node)),label)
                vals=[yul_expression(a) for a in args]
                if call=='mload' and vals==['0x40']:
                    roles.append(self.role(
                        'mload(0x40)',
                        'free_memory_pointer',
                        'free_memory_pointer',
                        'memory_ptr',
                        stmt,
                        {'memory_slot': '0x40'},
                    ))
                if call=='mload' and len(vals)==1 and type_env.is_bytes_memory(vals[0]): roles.append(self.role(f'mload({vals[0]})',f'{vals[0]}.length','bytes_length_value','uint256',stmt,{'object':vals[0]}))
                if call in {'revert','return'} and len(vals)>=2:
                    obj=self.bytes_data_object(vals[0],type_env)
                    if obj: roles.append(self.role(vals[0],f'{obj}.data',f'{call}_payload_ptr','memory_ptr',stmt,{'object':obj}))
                    roles.append(self.role(vals[1],self.norm_size(vals[1],obj),f'{call}_payload_size','uint256',stmt,{'object':obj} if obj else {}))
                if call=='keccak256' and len(vals)==2:
                    byte_slice=getattr(type_env,'dynamic_bytes_memory_slice',lambda *_args:None)(vals[0],vals[1])
                    ptr_norm=byte_slice.get('data_pointer_normalized') if byte_slice else vals[0]
                    size_norm=byte_slice.get('length_normalized') if byte_slice else vals[1]
                    roles.append(self.role(vals[0],ptr_norm,'memory_slice_start','memory_ptr',stmt,{})); roles.append(self.role(vals[1],size_norm,'memory_slice_size','uint256',stmt,{}))
                    if byte_slice:
                        attrs={'object':byte_slice['object'],'pattern':'dynamic_bytes_memory_data_and_length'}
                        roles.append(self.role(vals[0],ptr_norm,'bytes_data_ptr','memory_ptr',stmt,attrs))
                        roles.append(self.role(vals[1],size_norm,'bytes_length_value','uint256',stmt,attrs))
                if call in {'call','staticcall','delegatecall','callcode'}:
                    names=['call_gas','call_target','call_value','call_input_ptr','call_input_size','call_output_ptr','call_output_size'] if call in {'call','callcode'} else ['call_gas','call_target','call_input_ptr','call_input_size','call_output_ptr','call_output_size']
                    for name,val in zip(names,vals): roles.append(self.role(val,val,name,None,stmt,{'call_kind':call}))
            for rec in getattr(res,'loop_records',{}).values(): roles.append(self.role(rec.condition_expr or 'loop',rec.condition_expr,'loop_bound','bool',label,{'loop_id':rec.loop_id}))
        return roles
    @staticmethod
    def bytes_data_object(expr,type_env):
        if expr.startswith('add(') and expr.endswith(')'):
            a=split_args(expr[4:-1])
            if len(a)==2:
                if a[1] in {'32','0x20'} and type_env.is_bytes_memory(a[0]): return a[0]
                if a[0] in {'32','0x20'} and type_env.is_bytes_memory(a[1]): return a[1]
        return None
    @staticmethod
    def norm_size(size,obj): return f'{obj}.length' if obj and size in {f'mload({obj})', f'{obj}.length'} else size
