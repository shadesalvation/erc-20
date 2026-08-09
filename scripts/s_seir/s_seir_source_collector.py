#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path as _SSEIRPath
import sys as _sseir_sys
_SSEIR_ROOT = _SSEIRPath(__file__).resolve().parents[1]
for _sseir_path in (_SSEIR_ROOT / "legacy_yul", _SSEIR_ROOT / "s_seir"):
    _sseir_text = str(_sseir_path)
    if _sseir_text not in _sseir_sys.path:
        _sseir_sys.path.insert(0, _sseir_text)
from pathlib import Path
from typing import Any, Iterable
from assembly_ast_cfg import AssemblyAstBlock, FunctionContext, function_name, parameter_text, parse_src, yul_children, yul_statement_text
from s_seir_id import IdAllocator
from s_seir_model import FunctionUnit, SourceStatement, VariableInfo
Json=dict[str,Any]
STATEMENT_NODE_TYPES={'ExpressionStatement','VariableDeclarationStatement','IfStatement','ForStatement','WhileStatement','DoWhileStatement','Return','RevertStatement','EmitStatement','TryStatement','UncheckedBlock'}
def iter_ast(v:Any)->Iterable[Json]:
    if isinstance(v,dict):
        if isinstance(v.get('nodeType'),str): yield v
        for c in v.values(): yield from iter_ast(c)
    elif isinstance(v,list):
        for c in v: yield from iter_ast(c)
def src_text(source:str,src:str)->str:
    a,b=parse_src(str(src or ''))
    if b<=a:
        return ''
    # solc source mappings are UTF-8 byte offsets, not Python character offsets.
    raw=source.encode('utf-8')[a:b].decode('utf-8',errors='replace')
    return ' '.join(raw.strip().split())
def variable_info(node:Json,kind:str,storage_slot:int|None=None)->VariableInfo:
    t=node.get('typeDescriptions',{}).get('typeString')
    if not t:
        tn=node.get('typeName') or {}; t=tn.get('name') or tn.get('nodeType') or 'unknown'
    return VariableInfo(node.get('name',''),kind,str(t),node.get('storageLocation') or None,str(node.get('src','')),storage_slot)
def collect_variables(fn:Json):
    params=[variable_info(x,'parameter') for x in fn.get('parameters',{}).get('parameters',[])]
    rets=[variable_info(x,'return') for x in fn.get('returnParameters',{}).get('parameters',[])]
    locals=[]
    for n in iter_ast(fn.get('body') or {}):
        if n.get('nodeType')=='VariableDeclarationStatement':
            for d in n.get('declarations',[]):
                if isinstance(d,dict) and d.get('name'): locals.append(variable_info(d,'local'))
    return params,rets,locals
def collect_state_variables(contract:Json):
    out=[]; slot=0
    for n in contract.get('nodes',[]):
        if isinstance(n,dict) and n.get('nodeType')=='VariableDeclaration' and n.get('stateVariable') and not n.get('constant'):
            out.append(variable_info(n,'state',slot)); slot+=1
    return out
def literal_expression_text(source:str,node:Json|None)->str|None:
    if not isinstance(node,dict):
        return None
    if node.get('nodeType')=='Literal':
        value=node.get('value')
        if value is not None:
            return str(value)
    text=src_text(source,str(node.get('src','')))
    return text or None
def collect_constant_values(contract:Json,source:str):
    out={}
    for n in contract.get('nodes',[]):
        if not isinstance(n,dict) or n.get('nodeType')!='VariableDeclaration':
            continue
        if not n.get('constant'):
            continue
        info=variable_info(n,'constant')
        if not info.name:
            continue
        out[info.name]={
            'name':info.name,
            'type_string':info.type_string,
            'value':literal_expression_text(source,n.get('value')),
            'src':info.src,
        }
    return out
def collect_struct_definitions(contract:Json):
    structs={}
    for n in contract.get('nodes',[]):
        if not isinstance(n,dict) or n.get('nodeType')!='StructDefinition':
            continue
        name=n.get('name')
        canonical=n.get('canonicalName') or (f"{contract.get('name')}.{name}" if name else None)
        fields=[]
        for index,m in enumerate(n.get('members',[]) or []):
            if not isinstance(m,dict):
                continue
            info=variable_info(m,'struct_field')
            fields.append({'name':info.name,'type_string':info.type_string,'offset':index*32,'index':index,'src':info.src})
        item={'name':name,'canonical_name':canonical,'fields':fields,'src':str(n.get('src',''))}
        if name:
            structs[name]=item
        if canonical:
            structs[canonical]=item
    return structs
def yul_nodes(root:Json)->list[Json]:
    out=[]
    def visit(n):
        out.append(n)
        for c in yul_children(n): visit(c)
    visit(root); return out
def is_yul_statement(n:Json)->bool:
    return n.get('nodeType') in {'YulVariableDeclaration','YulAssignment','YulExpressionStatement','YulIf','YulSwitch','YulCase','YulForLoop','YulBreak','YulContinue','YulLeave'}
def src_start(src:str)->int:
    a,_b=parse_src(str(src or ''))
    return a
class SourceStatementCollector:
    def __init__(self, source_path:Path, ast:Json): self.source_path=source_path; self.source=source_path.read_text(encoding='utf-8'); self.ast=ast; self.next_asm=1
    def collect(self)->list[FunctionUnit]:
        units=[]
        for contract in [n for n in iter_ast(self.ast) if n.get('nodeType')=='ContractDefinition']:
            for fn in contract.get('nodes',[]):
                if isinstance(fn,dict) and fn.get('nodeType') in {'FunctionDefinition','ModifierDefinition'}: units.append(self.collect_function(contract.get('name','<anonymous>'),fn))
        return units
    def collect_function(self,contract:str,fn:Json)->FunctionUnit:
        contract_node=next(c for c in iter_ast(self.ast) if c.get('nodeType')=='ContractDefinition' and c.get('name')==contract)
        ids=IdAllocator(); params,rets,locals_=collect_variables(fn); state_vars=collect_state_variables(contract_node); name=function_name(fn); sig=f"{name}({', '.join(v.type_string for v in params)})"; fid=f"{contract}.{sig}"
        ctx=FunctionContext(contract=contract,name=name,visibility=fn.get('visibility'),parameters=[parameter_text(p) for p in fn.get('parameters',{}).get('parameters',[])])
        unit=FunctionUnit(fid,contract,name,sig,fn,params,rets,locals_,state_vars)
        setattr(unit,'struct_definitions',collect_struct_definitions(contract_node))
        setattr(unit,'constant_values',collect_constant_values(contract_node,self.source))
        def add_sol(n,block_id=None): unit.source_statements.append(SourceStatement(ids.new('sol_s'),'solidity',src_text(self.source,str(n.get('src',''))) or n.get('nodeType',''),str(n.get('src','')),fid,block_id,{'nodeType':n.get('nodeType')}))
        def add_yul(block):
            label=f'asm_block_{block.block_id}'
            for y in yul_nodes(block.yul_ast):
                if is_yul_statement(y): unit.source_statements.append(SourceStatement(ids.new('asm_s'),'yul',yul_statement_text(y),str(y.get('src','')),fid,label,{'nodeType':y.get('nodeType'),'assembly_block':block.block_id}))
        for n in iter_ast(fn.get('body') or {}):
            if n.get('nodeType')=='InlineAssembly':
                y=n.get('AST') or n.get('ast')
                if isinstance(y,dict):
                    block=AssemblyAstBlock(self.next_asm,self.source_path,self.source,n,y,ctx); self.next_asm+=1; unit.assembly_blocks.append(block); add_yul(block)
            elif n.get('nodeType') in STATEMENT_NODE_TYPES: add_sol(n)
        unit.assembly_blocks.sort(key=lambda block: src_start(str(block.solidity_node.get('src',''))))
        unit.source_statements.sort(key=lambda stmt: (src_start(stmt.src), 0 if stmt.lang=='solidity' else 1, stmt.stmt_id))
        return unit
