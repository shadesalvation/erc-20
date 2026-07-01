#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from dataclasses import asdict,is_dataclass
from pathlib import Path
from typing import Any
from assembly_ast_cfg import compile_source_ast, discover_solc
from assembly_event_ir import parse_events_from_source
from assembly_memory_ssa import analyze_block
from s_seir_control_builder import ControlBuilder
from s_seir_effect_lifter import EffectLifter
from s_seir_expr_roles import ExpressionRoleAnalyzer
from s_seir_legacy_adapter import LegacyRecoveryAdapter
from s_seir_model import FunctionSSEIR
from s_seir_overlay_builder import SemanticOverlayBuilder
from s_seir_projection import ProjectionPolicyClassifier
from s_seir_source_collector import SourceStatementCollector
from s_seir_type_env import TypeEnv

def json_ready(v:Any)->Any:
    if is_dataclass(v): return json_ready(asdict(v))
    if isinstance(v,dict): return {str(k):json_ready(x) for k,x in v.items()}
    if isinstance(v,(list,tuple)): return [json_ready(x) for x in v]
    return v

def build_sseir(source_path:Path, solc_bin:str|None=None, slither_bin:str|None=None, workdir:Path|None=None):
    solc=solc_bin or discover_solc(None)
    ast=compile_source_ast(source_path,solc)
    events=parse_events_from_source(source_path)
    legacy_report,legacy_error=LegacyRecoveryAdapter.build_report(source_path,slither_bin,solc_bin or solc,workdir or Path.cwd())
    legacy=LegacyRecoveryAdapter(legacy_report)
    out=[]
    for unit in SourceStatementCollector(source_path,ast).collect():
        type_env=TypeEnv(unit); control=ControlBuilder().build(unit); mem={}
        for block in unit.assembly_blocks: mem[block.block_id]=analyze_block(block)
        roles=ExpressionRoleAnalyzer().analyze(unit,type_env,mem)
        effects,facts=EffectLifter().lift(unit,mem)
        le,lo,lf=legacy.adapt_unit(unit); effects.extend(le); facts.extend(lf)
        if legacy_error: facts.append(legacy_error)
        overlays=SemanticOverlayBuilder(events,include_shallow_overlays=legacy_report is None).build(unit,type_env,roles,effects)
        overlays.extend(lo)
        policies=ProjectionPolicyClassifier().classify(overlays)
        out.append(FunctionSSEIR(unit.function_id,unit.contract,unit.function,unit.signature,unit.source_statements,control,roles,effects,overlays,policies,json_ready(facts)))
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
        lines.append('  ProjectionPolicies:')
        for p in fn.projection_policies: lines.append(f'    {p.policy_id} target={p.target_overlay} exact={p.exact_solidity_equivalent} output={p.output_kind} reason={p.reason}')
        lines.append('  AnalysisFacts:')
        for f in fn.analysis_facts: lines.append(f'    {json.dumps(json_ready(f),ensure_ascii=False)}')
        lines.append('')
    return '\n'.join(lines)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('source',type=Path); ap.add_argument('-o','--output',type=Path,default=Path('outputs/sseir.json')); ap.add_argument('--text-output',type=Path,default=Path('outputs/sseir.txt')); ap.add_argument('--solc-bin'); ap.add_argument('--slither-bin'); ap.add_argument('--workdir',type=Path,default=Path('.'))
    a=ap.parse_args(); fns=build_sseir(a.source,a.solc_bin,a.slither_bin,a.workdir.resolve())
    a.output.parent.mkdir(parents=True,exist_ok=True); a.text_output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps([x.to_dict() for x in fns],indent=2,ensure_ascii=False),encoding='utf-8'); a.text_output.write_text(render_text(fns),encoding='utf-8')
    print(f'Wrote {a.output}'); print(f'Wrote {a.text_output}')
if __name__=='__main__': main()
