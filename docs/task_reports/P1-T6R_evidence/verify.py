#!/usr/bin/env python3
"""Audit persisted P1-T6R results; no recovery/oracle input or aggregate target."""
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
BEFORE = ROOT / 'outputs/module1_sample10_current'
AFTER = ROOT / 'outputs/module1_sample10_p1t6r'
sys.path[:0] = [str(ROOT / 'scripts/s_seir'), str(ROOT / 'scripts/legacy_yul')]
from s_seir_research_contracts import canonical_json, content_digest
from s_seir_research_control_edges import validate_candidate_collection
from s_seir_research_local_refinement import build_candidate_scope, refinement_accounting, validate_refined_edge, Z3Backend
from s_seir_research_guards import validate_module1_result, build_module1_result
from s_seir_research_actions import sfir_input_fingerprint

def read(directory, name):
    return json.load(gzip.open(directory / (name + '.json.gz'), 'rt'))

def stats(directory):
    refinement = read(directory, 'p1_t6_refinement')
    results = read(directory, 'p1_t7_module1_results')
    reasons = Counter()
    for row in refinement['refinements']:
        if row['edge']['payload']['feasibility'] == 'UNRESOLVED':
            reasons.update({r['reason'] for r in row['scope']['incomplete_reasons']})
    return {'actions': sum(len(x['payload']['actions']) for x in results),
            'candidates': len(refinement['refinements']),
            'feasibility': refinement['accounting']['feasibility'],
            'scope_completeness': refinement['accounting']['scope_completeness'],
            'solver_outcomes': refinement['accounting']['outcomes'],
            'functions': dict(sorted(Counter(x['status']['completion'] for x in results).items())),
            'unresolved_reason_candidates': dict(sorted(reasons.items()))}

def graph_witnesses(directory):
    actions = {a['id']: a['payload']['semantic_ref']['locator'] for a in read(directory, 'p1_t1_actions')['actions']}
    candidates = read(directory, 'p1_t5_candidates')
    evidence = {e['id']: e for e in candidates['evidence_records']}
    records = []
    for edge in candidates['edges']:
        p = edge['payload']; c = evidence[edge['evidence_refs'][0]]['result']['carrier']
        endpoints = [actions[p[k]['action_ref']] if p[k]['kind'] == 'ACTION' else p[k]['kind'] for k in ('from', 'to')]
        records.append(canonical_json({'function': edge['function_ref']['sfir_function_id'], 'endpoints': endpoints,
            'blocks': [r['locator'] for r in c['carrier_block_refs']], 'edges': [r['locator'] for r in c['carrier_edge_refs']],
            'origin': c['origin']}))
    return sorted(records)

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    sfir = json.loads((AFTER / 'sfir.json').read_text())
    candidates = read(AFTER, 'p1_t5_candidates'); validate_candidate_collection(candidates)
    upstream = {e['id']: e for e in candidates['edges']}
    refinement = read(AFTER, 'p1_t6_refinement')
    propagation = read(AFTER, 'p1_t4_propagation')
    actions = read(AFTER, 'p1_t1_actions')
    results = read(AFTER, 'p1_t7_module1_results')
    rows = {r['candidate_id']: r for r in refinement['refinements']}
    nodes = {n['semantic_id']: n for f in sfir['functions'] for n in f['semantic_nodes']}
    scopes_equal, queries_valid, solver_replayed = True, True, True
    backend = Z3Backend()
    replayed_calls = 0
    node_findings = []
    for row in refinement['refinements']:
        main_evidence = next(e for e in row['solver_evidence'] if e['id'] == row['feasibility_evidence_ref'])
        validate_refined_edge(row['edge'], upstream[row['candidate_id']], row['guard'], main_evidence, row['scope'])
        rebuilt = build_candidate_scope(sfir, candidates, row['candidate_id'], propagation, config=row['scope']['config'])
        scopes_equal &= rebuilt == row['scope']
        queries_valid &= all(content_digest(e['payload']['query_artifact']) == e['payload']['query_digest'] for e in row['solver_evidence'])
        for evidence in row['solver_evidence']:
            p = evidence['payload']; query = p['query_artifact']
            if query['smt2'] is not None:
                answer = backend.solve(query['assertions'], int(p['timeout_ms']))
                solver_replayed &= (backend.version == p['backend_version'] and answer['outcome'] == p['outcome']
                                    and answer['smt2'] == query['smt2'])
                replayed_calls += 1
        reasons = []
        for reason in row['scope']['incomplete_reasons']:
            kinds = []
            for ref in reason['refs']:
                if isinstance(ref, dict):
                    sid = ref.get('locator', {}).get('semantic_id')
                    if sid in nodes: kinds.append({'semantic_id': sid, 'kind': nodes[sid]['kind']})
            reasons.append({**reason, 'affected_semantic_nodes': kinds})
        node_findings.append({'candidate_id': row['candidate_id'], 'function': row['edge']['function_ref']['canonical_signature'],
            'from_kind': row['edge']['payload']['from']['kind'], 'feasibility': row['edge']['payload']['feasibility'],
            'solver': main_evidence['payload']['outcome'], 'reasons': reasons,
            'transparent_evidence': row['scope']['reachability_transparent_evidence'],
            'state_read_symbols': [s for s in row['scope']['symbol_bindings'] if s['role'] == 'STATE_READ']})
    sealed = []
    seal_equal = True
    for result in results:
        validate_module1_result(result)
        seal_equal &= build_module1_result(refinement, actions, function_ref=result['function_ref']) == result
        sealed += [e for key in ('feasible_edges', 'rejected_edges', 'unresolved_edges') for e in result['payload'][key]]
    protected = ['scripts/s_seir/s_seir_research_' + name + '.py' for name in
                 ('actions', 'regions', 'abstract_domain', 'propagation', 'control_edges', 'guards')]
    protected += ['PROJECT_STATUS.md', 'scripts/s_seir/s_seir_pipeline.py']
    unchanged = {p: (ROOT / p).read_bytes() == subprocess.check_output(['git', 'show', 'HEAD:' + p], cwd=ROOT) for p in protected}
    old_manifest = json.loads((BEFORE / 'run_manifest.json').read_text())
    baseline_unchanged = all(sha(BEFORE / p) == v['sha256'] for p, v in old_manifest['artifacts'].items())
    source = ROOT / old_manifest['source']
    runner = json.loads((HERE / 'final_regression/results.json').read_text())
    no_blanket_read = all(not (r['reason'] == 'carrier operation/failure semantics not covered by predicate closure'
                              and any(n['kind'] in {'StateRead', 'StorageLocationResolve'} for n in r['affected_semantic_nodes']))
                         for f in node_findings for r in f['reasons'])
    checks = {
        'same_candidate_graph_witnesses': graph_witnesses(BEFORE) == graph_witnesses(AFTER),
        'all_candidates_retained': set(rows) == set(upstream) == {e['id'] for e in sealed} and len(rows) == len(sealed),
        'scope_recomputed_equal': scopes_equal,
        'query_digests_recomputed': queries_valid,
        'persisted_solver_calls_replayed': solver_replayed and replayed_calls == int(refinement['accounting']['solver_calls']),
        'accounting_recomputed': refinement['accounting'] == refinement_accounting(refinement['refinements']),
        'p1_t7_reseal_equal_without_solver': seal_equal,
        'p1_t7_feasibility_conserved': all(e['payload']['feasibility'] == rows[e['id']]['edge']['payload']['feasibility'] for e in sealed),
        'partial_sat_stays_unresolved': all(r['edge']['payload']['feasibility'] == 'UNRESOLVED' for r in rows.values()
                                            if r['scope']['scope_completeness'] == 'PARTIAL'),
        'action_rooted_limit_retained': all(r['scope']['scope_completeness'] == 'PARTIAL' for r in rows.values()
                                           if r['edge']['payload']['from']['kind'] == 'ACTION'),
        'no_blanket_state_read_or_location_reason': no_blanket_read,
        'old_slithir_only_association_reason_absent': all('BranchCondition lacks exact typed condition operand association' != r['reason']
                                                        for f in node_findings for r in f['reasons']),
        'protected_production_and_status_unchanged': all(unchanged.values()),
        'historical_sample_baseline_unchanged': baseline_unchanged,
        'historical_task_evidence_unchanged': subprocess.run(['git', 'diff', '--quiet', '--', 'docs/task_reports/P1-T6_evidence',
            'docs/task_reports/P1-T7_evidence', 'docs/task_reports/P1-T6.md', 'docs/task_reports/P1-T7.md'], cwd=ROOT).returncode == 0,
        'source_unchanged': sha(source) == old_manifest['source_sha256'],
        'fresh_source_matches_input': sha(AFTER / 'analysis_source.sol') == sha(source),
        'fingerprints_match': all(a['input_fingerprint'] == sfir_input_fingerprint(sfir) for a in (actions, candidates, refinement)),
        'full_regression_pass': runner['counts'] == {'PASS': runner['entrypoints_planned'], 'FAIL': 0, 'SKIP': 0, 'TIMEOUT': 0},
        'git_diff_check': subprocess.run(['git', 'diff', '--check'], cwd=ROOT).returncode == 0,
    }
    result = {'checks': checks, 'status': 'PASS' if all(checks.values()) else 'FAIL',
              'replayed_solver_calls': replayed_calls, 'backend_version': backend.version,
              'before': stats(BEFORE), 'after': stats(AFTER),
              'reason_count_unit': 'distinct candidate per reason; categories overlap',
              'protected_files_unchanged': unchanged, 'per_candidate': node_findings}
    (HERE / 'acceptance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    (AFTER / 'verification.json').write_text(json.dumps({k: v for k, v in result.items() if k != 'per_candidate'}, ensure_ascii=False, indent=2) + '\n')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    changed = subprocess.check_output(['git', 'diff', '--name-only', '--', 'scripts/s_seir'], cwd=ROOT, text=True).splitlines()
    manifest = {'base_head': head, 'final_working_head': head, 'working_tree_implementation': True,
                'branch': subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip(),
                'source': str(source.relative_to(ROOT)), 'source_sha256': sha(source),
                'reproduce': 'PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/task_reports/P1-T6R_evidence/rerun_sample.py',
                'verify': 'PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/task_reports/P1-T6R_evidence/verify.py',
                'production_and_test_sha256': {p: sha(ROOT / p) for p in changed},
                'artifacts': {str(p.relative_to(AFTER)): {'sha256': sha(p), 'bytes': p.stat().st_size} for p in sorted(AFTER.iterdir())
                              if p.is_file() and p.name != 'run_manifest.json'}}
    (AFTER / 'run_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in {'per_candidate', 'protected_files_unchanged'}}, ensure_ascii=False, indent=2))
    assert all(checks.values()), 'acceptance failure; inspect checks'

if __name__ == '__main__': main()
