#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path as _SSEIRPath
import sys as _sseir_sys
_SSEIR_ROOT = _SSEIRPath(__file__).resolve().parents[1]
for _sseir_path in (_SSEIR_ROOT / "legacy_yul", _SSEIR_ROOT / "s_seir"):
    _sseir_text = str(_sseir_path)
    if _sseir_text not in _sseir_sys.path:
        _sseir_sys.path.insert(0, _sseir_text)
import argparse,json,re,tempfile
from dataclasses import asdict,is_dataclass
from pathlib import Path
from typing import Any
from assembly_ast_cfg import compile_source_ast, discover_solc
from assembly_event_ir import parse_events_from_source
from s_seir_branch_materialization import build_branch_materialization_nodes
from s_seir_branch_preprocess import build_branch_preprocessed_analysis_source
from s_seir_control_builder import ControlBuilder
from s_seir_cfg_export import write_function_cfg_dot_files
from s_seir_effect_lifter import EffectLifter
from s_seir_expr_roles import ExpressionRoleAnalyzer
from s_seir_llm_assembly_export import write_llm_assembly_compact_json, write_llm_assembly_compact_text, write_llm_assembly_json, write_llm_assembly_text
from s_seir_memory_ssa import build_memory_ssa_views
from s_seir_semantic_normalizer import SemanticNormalizer
from s_seir_model import FunctionSSEIR
from s_seir_overlay_builder import SemanticOverlayBuilder
from s_seir_selector_registry import build_selector_registry
from s_seir_source_collector import SourceStatementCollector
from s_seir_solidity_like_export import write_solidity_like_text
from s_seir_storage_layout import apply_storage_layout, extract_storage_layout
from s_seir_security_facts import SecurityFactBuilder
from s_seir_type_env import TypeEnv

def json_ready(v:Any)->Any:
    if is_dataclass(v): return json_ready(asdict(v))
    if isinstance(v,dict): return {str(k):json_ready(x) for k,x in v.items()}
    if isinstance(v,(list,tuple)): return [json_ready(x) for x in v]
    return v

def extract_function_source_text(unit:Any, source_text:str)->str:
    candidates=find_function_source_candidates(unit,source_text)
    if candidates:
        with_assembly=[item for item in candidates if 'assembly' in item]
        return (with_assembly or candidates)[0]
    fn_src=str(unit.ast_node.get('src',''))
    try:
        start,length=(int(x) for x in fn_src.split(':')[:2])
    except Exception:
        return ''
    if start < 0 or length <= 0 or start >= len(source_text):
        return ''
    return source_text[start:start+length]

def find_function_source_candidates(unit:Any, source_text:str)->list[str]:
    name=str(getattr(unit,'function','') or '')
    if name == 'constructor':
        pattern=r'\bconstructor\s*\('
    elif name == 'fallback':
        pattern=r'\bfallback\s*\('
    elif name == 'receive':
        pattern=r'\breceive\s*\('
    else:
        pattern=rf'\bfunction\s+{re.escape(name)}\s*\('
    out=[]
    for match in re.finditer(pattern,source_text):
        extracted=extract_braced_function_at(source_text,match.start(),match.end())
        if extracted:
            out.append(extracted)
    return out

def extract_braced_function_at(source_text:str,start:int,search_from:int)->str:
    brace=source_text.find('{',search_from)
    semi=source_text.find(';',search_from)
    if brace < 0 or (semi >= 0 and semi < brace):
        return ''
    depth=0
    i=brace
    state='code'
    while i < len(source_text):
        ch=source_text[i]
        nxt=source_text[i+1] if i+1 < len(source_text) else ''
        if state == 'code':
            if ch == '/' and nxt == '/':
                state='line_comment'; i+=2; continue
            if ch == '/' and nxt == '*':
                state='block_comment'; i+=2; continue
            if ch in {'"', "'"}:
                quote=ch; state='string_'+quote; i+=1; continue
            if ch == '{':
                depth+=1
            elif ch == '}':
                depth-=1
                if depth == 0:
                    return source_text[start:i+1]
        elif state == 'line_comment':
            if ch == '\n':
                state='code'
        elif state == 'block_comment':
            if ch == '*' and nxt == '/':
                state='code'; i+=2; continue
        elif state.startswith('string_'):
            quote=state[-1]
            if ch == '\\\\':
                i+=2; continue
            if ch == quote:
                state='code'
        i+=1
    return ''

def build_sseir(source_path:Path, solc_bin:str|None=None, slither_bin:str|None=None, workdir:Path|None=None, branch_preprocess:bool=True, branch_preprocess_output:Path|None=None, branch_report_output:Path|None=None):
    solc=solc_bin or discover_solc(None)
    original_source_path=source_path
    branch_preprocess_report=None
    branch_rewrite_count=0
    temp_context=None
    if branch_preprocess:
        if branch_preprocess_output is None:
            temp_context=tempfile.TemporaryDirectory(prefix='sseir_branch_')
            branch_source=Path(temp_context.name)/source_path.name
        else:
            branch_source=branch_preprocess_output
        branch_result=build_branch_preprocessed_analysis_source(source_path,solc,branch_source)
        source_path=branch_result.source_path
        branch_preprocess_report=branch_result.report
        branch_rewrite_count=branch_result.rewrite_count
        if branch_report_output is not None:
            branch_report_output.parent.mkdir(parents=True,exist_ok=True)
            branch_report_output.write_text(branch_preprocess_report,encoding='utf-8')
    ast=compile_source_ast(source_path,solc)
    source_text=source_path.read_text(encoding='utf-8')
    events=parse_events_from_source(source_path)
    selector_registry=build_selector_registry(ast,source_text)
    storage_layouts=extract_storage_layout(source_path,solc)
    out=[]
    for unit in SourceStatementCollector(source_path,ast).collect():
        apply_storage_layout(unit,storage_layouts)
        type_env=TypeEnv(unit); control=ControlBuilder(source_path, solc, slither_bin, workdir or Path.cwd()).build(unit); mem=build_memory_ssa_views(unit,control)
        roles=ExpressionRoleAnalyzer().analyze(unit,type_env,mem)
        effects,facts=EffectLifter().lift(unit,mem,control)
        branch_effects,branch_facts=build_branch_materialization_nodes(unit,mem); effects.extend(branch_effects); facts.extend(branch_facts)
        overlays=SemanticOverlayBuilder(events,include_shallow_overlays=True,selector_registry=selector_registry).build(unit,type_env,roles,effects)
        roles,effects,overlays,normalizer_facts=SemanticNormalizer().normalize(unit,type_env,roles,effects,overlays)
        facts.extend(normalizer_facts)
        facts.append({'kind':'SSEIRStructTable','structs':getattr(unit,'struct_definitions',{}) or {}})
        facts.append({'kind':'SSEIRSelectorRegistry','selector_count':len(selector_registry),'selectors':selector_registry})
        facts.append({'kind':'SSEIRBranchPreprocess','original_source':str(original_source_path),'analysis_source':str(source_path),'rewrite_count':branch_rewrite_count,'enabled':branch_preprocess,'flattened_imports':bool(getattr(branch_result,'flattened',False)) if branch_preprocess else False,'imported_files':getattr(branch_result,'imported_files',[]) if branch_preprocess else [],'preprocessed_files':getattr(branch_result,'preprocessed_files',[]) if branch_preprocess else []})
        security_facts=SecurityFactBuilder().build(effects,overlays)
        fn=FunctionSSEIR(unit.function_id,unit.contract,unit.function,unit.signature,unit.source_statements,control,roles,effects,overlays,security_facts,json_ready(facts))
        fn_src=str(unit.ast_node.get('src',''))
        setattr(fn,'_sseir_function_source',{'src':fn_src,'text':extract_function_source_text(unit,source_text)})
        setattr(fn,'_sseir_assembly_sources',[{'block_id':f'asm_block_{b.block_id}','src':b.src,'text':b.snippet} for b in unit.assembly_blocks])
        out.append(fn)
    if temp_context is not None:
        temp_context.cleanup()
    return out

def render_text(functions):
    lines=[]
    for fn in functions:
        lines.append(f'Function {fn.contract}.{fn.signature}'); lines.append(f'  function_id: {fn.function_id}')
        lines.append('  SourceStatements:')
        for s in fn.source_statements: lines.append(f'    {s.stmt_id} [{s.lang}] {s.text}')
        lines.append('  Control:'); lines.append(f"    blocks: {len(fn.control.get('blocks',[]))}"); lines.append(f"    edges: {len(fn.control.get('edges',[]))}")
        for n in fn.control.get('notes',[]): lines.append(f'    note: {n}')
        lines.append('  ExpressionRoles:')
        for r in fn.expr_roles: lines.append(f"    {r.expr_id} {r.role}: {r.text}{(' -> '+r.normalized) if r.normalized else ''} @ {r.stmt_ref}")
        lines.append('  Effects:')
        for e in fn.effects: lines.append(f"    {e.effect_id} {e.kind} refs={e.stmt_refs} attrs={json.dumps(json_ready(e.attrs),ensure_ascii=False)}")
        lines.append('  SemanticOverlays:')
        for o in fn.semantic_overlays: lines.append(f"    {o.overlay_id} {o.kind} effects={o.effects} attrs={json.dumps(json_ready(o.attrs),ensure_ascii=False)}")
        lines.append('  SecurityFacts:')
        for sf in fn.security_facts: lines.append(f"    {sf.fact_id} {sf.kind} overlays={sf.source_overlays} effects={sf.source_effects} attrs={json.dumps(json_ready(sf.attrs),ensure_ascii=False)}")
        lines.append('  AnalysisFacts:')
        for f in fn.analysis_facts: lines.append(f'    {json.dumps(json_ready(f),ensure_ascii=False)}')
        lines.append('')
    return '\n'.join(lines)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('source',type=Path); ap.add_argument('-o','--output',type=Path,default=Path('outputs/sseir.json')); ap.add_argument('--text-output',type=Path,default=Path('outputs/sseir.txt')); ap.add_argument('--cfg-dot-dir',type=Path); ap.add_argument('--llm-assembly-output',type=Path); ap.add_argument('--llm-assembly-text-output',type=Path); ap.add_argument('--llm-assembly-compact-output',type=Path); ap.add_argument('--llm-assembly-compact-text-output',type=Path); ap.add_argument('--solidity-like-output',type=Path); ap.add_argument('--branch-preprocessed-output',type=Path); ap.add_argument('--branch-report-output',type=Path); ap.add_argument('--no-branch-preprocess',action='store_true'); ap.add_argument('--solc-bin'); ap.add_argument('--slither-bin'); ap.add_argument('--workdir',type=Path,default=Path('.'))
    a=ap.parse_args(); fns=build_sseir(a.source,a.solc_bin,a.slither_bin,a.workdir.resolve(),branch_preprocess=not a.no_branch_preprocess,branch_preprocess_output=a.branch_preprocessed_output,branch_report_output=a.branch_report_output)
    a.output.parent.mkdir(parents=True,exist_ok=True); a.text_output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps([x.to_dict() for x in fns],indent=2,ensure_ascii=False),encoding='utf-8'); a.text_output.write_text(render_text(fns),encoding='utf-8')
    print(f'Wrote {a.output}'); print(f'Wrote {a.text_output}')
    if a.cfg_dot_dir:
        paths=write_function_cfg_dot_files(fns,a.cfg_dot_dir)
        print(f'Wrote {len(paths)} CFG DOT files to {a.cfg_dot_dir}')
    if a.llm_assembly_output:
        write_llm_assembly_json(fns,a.llm_assembly_output)
        print(f'Wrote {a.llm_assembly_output}')
    if a.llm_assembly_text_output:
        write_llm_assembly_text(fns,a.llm_assembly_text_output)
        print(f'Wrote {a.llm_assembly_text_output}')
    if a.llm_assembly_compact_output:
        write_llm_assembly_compact_json(fns,a.llm_assembly_compact_output)
        print(f'Wrote {a.llm_assembly_compact_output}')
    if a.llm_assembly_compact_text_output:
        write_llm_assembly_compact_text(fns,a.llm_assembly_compact_text_output)
        print(f'Wrote {a.llm_assembly_compact_text_output}')
    if a.solidity_like_output:
        write_solidity_like_text(fns,a.solidity_like_output)
        print(f'Wrote {a.solidity_like_output}')
if __name__=='__main__': main()
