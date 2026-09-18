"""Verify P0-T2 revision artifacts and scope; does not evaluate recovery semantics.

The acceptance judgments cite the capability map's manual code review. File,
coverage, regression counts, probe reproducibility and mutation checks below
are automatic checks, not proofs of the semantic conclusions themselves.
"""
from pathlib import Path
import ast
import difflib
import hashlib
import json
import re
import subprocess
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
DOCUMENTS = ['PROJECT_STATUS.md', 'docs/architecture/sfir_capability_map.md',
             'docs/architecture/repository_overview.md', 'docs/task_reports/P0-T2.md']

def hashes():
    values = {str(f.relative_to(ROOT)): hashlib.sha256(f.read_bytes()).hexdigest()
              for d in ['scripts', 'tests', 'docs', 'skills'] for f in (ROOT/d).rglob('*')
              if f.is_file() and '__pycache__' not in str(f) and OUT not in f.parents}
    values.update({name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
                   for name in ['AGENTS.md', 'PROJECT_STATUS.md']})
    return values

before = json.loads((OUT/'hashes_before.json').read_text())
after = hashes()
changed = [p for p in sorted(before.keys() | after.keys()) if before.get(p) != after.get(p)]
assert set(changed) == set(DOCUMENTS), changed
source_files = [p for p in after if p.startswith(('scripts/', 'tests/'))]
original = json.loads((ROOT/'docs/task_reports/P0-T2_evidence/source_hashes_before.json').read_text())
assert {p: after[p] for p in source_files} == original
baseline = json.loads((OUT/'baseline/results.json').read_text())
final = json.loads((OUT/'final/results.json').read_text())
for result in [baseline, final]:
    assert result['entrypoints_planned'] == 26
    assert result['counts'] == {'PASS': 26, 'FAIL': 0, 'SKIP': 0, 'TIMEOUT': 0}
    assert len(result['results']) == 26
    assert all(row['returncode'] == 0 for row in result['results'])
assert [r['name'] for r in baseline['results']] == [r['name'] for r in final['results']]
probe_counts = {}
for name in ['capability_probes.json', 'sink_impacts.json']:
    data = json.loads((OUT/name).read_text())
    assert data == json.loads((OUT/'final_probes'/name).read_text())
    assert len(data['checks']) == 6 and all(c['status'] == 'PASS' for c in data['checks'])
    probe_counts[name] = len(data['checks'])
cap = (ROOT/DOCUMENTS[1]).read_text()
semantic_rows = ['Basic Block', 'CFG Edge', 'Branch / Condition', 'Variable Definition',
    'Variable Use', 'Expression', 'Storage Load', 'Storage Store', 'Yul sload / sstore',
    'External Call', 'DelegateCall', 'StaticCall', 'Ether Transfer', 'Event', 'Return',
    'Revert', 'Calldata', 'Memory', 'msg.sender', 'msg.value']
analysis_rows = ['Feasible Control / Guard', 'Definition Flow', 'Value Flow',
                'Sink Dependency', 'Transition Reconstruction']
for row in semantic_rows + analysis_rows:
    assert '| '+row+' |' in cap, row
for number in range(1, 8):
    assert '| '+str(number)+'. ' in cap
for number in range(1, 9):
    assert '| G'+str(number)+'：' in cap
alias_impacts = ['Reaching Definitions 中同一持久位置', 'Value Flow Graph 中 Storage Load/Store',
                 'StorageWrite backward slicing 中', 'Multi-Sink persistent-state dependency recovery']
for name in alias_impacts:
    assert '| '+name in cap
assert not re.search(r'P2-T[2468]', cap), 'future task IDs must not be capability gates'
links = []
source_index = {}
pending_outputs = {OUT/'verification.json', OUT/'acceptance.json'}
for name in DOCUMENTS:
    doc = ROOT/name
    for target in re.findall(r'\]\(([^)]+)\)', doc.read_text()):
        if '://' in target:
            continue
        path = (doc.parent/target.split('#')[0]).resolve()
        assert path.exists() or path in pending_outputs, (name, target)
        links.append({'document': name, 'target': target})
        if path.suffix == '.py' and path.is_relative_to(ROOT/'scripts'):
            source_index[str(path.relative_to(ROOT))] = [
                {'symbol': node.name, 'line': node.lineno}
                for node in ast.walk(ast.parse(path.read_text()))
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
    assert all(line == line.rstrip() for line in doc.read_text().splitlines()), name
# Track task-specific changes even though these documents are currently untracked.
diff = []
for name in DOCUMENTS:
    old = (OUT/(Path(name).name+'.before')).read_text().splitlines(keepends=True)
    new = (ROOT/name).read_text().splitlines(keepends=True)
    diff.extend(difflib.unified_diff(old, new, fromfile=name+' (before revision)', tofile=name))
(OUT/'document_changes.diff').write_text(''.join(diff))
for name, args in [('git_status_after.txt', ['git', 'status', '--porcelain=v1']),
                   ('git_diff_after.txt', ['git', 'diff'])]:
    (OUT/name).write_bytes(subprocess.check_output(args, cwd=ROOT))
assert (OUT/'git_diff_before.txt').read_bytes() == (OUT/'git_diff_after.txt').read_bytes(), 'pre-existing tracked diff changed'
subprocess.run(['git', 'diff', '--check'], cwd=ROOT, check=True)
items = [
    ('five_analysis_capabilities', 'PASS', 'capability map: five named analysis rows'),
    ('supported_claims_have_code', 'PASS', 'manual review: map code index and per-row symbols; verification code_symbol_index'),
    ('missing_claims_explicit', 'PASS', 'map gap columns and G1-G8 minimal boundaries'),
    ('erc20_alias_sufficiency_conclusion', 'PASS', 'map alias conclusion; IR storage binding/RD; E0-E3'),
    ('insufficiency_recorded_without_implementation', 'PASS', 'map impact/minimal boundaries; unchanged 123 source/test hashes'),
    ('four_alias_affected_analyses', 'PASS', 'map alias impact table; capability_probes.json; sink_impacts.json'),
    ('no_speculative_support', 'PASS', 'manual review distinguishes final-bridge observations, frontend coverage, and downstream inferences'),
    ('necessary_regressions', 'PASS', 'baseline/results.json; final/results.json: 26/26 each'),
    ('characterization_probes', 'PASS', '6 base + 6 sink observations, identical final JSON reruns'),
    ('scope_and_handoff', 'PASS', 'hashes; document_changes.diff; git status/diff; current status/report'),
    ('frozen_interface_change_adr', 'N/A', 'no interface/semantic/experiment-contract changes'),
    ('benchmark_evaluator_solver_experiment', 'N/A', 'not performed; no SMT backend, not counted as PASS'),
]
acceptance = {'task': 'P0-T2', 'status': 'COMPLETE',
    'basis': 'revised user request: capability names and minimum gap boundaries, not future task IDs',
    'items': [{'id': i, 'status': s, 'evidence': e} for i, s, e in items],
    'historical_acceptance': '../P0-T2_evidence/acceptance.json (preserved, superseded for current task)',
    'next_allowed': 'await user authorization for P0-T3; stop after P0-T2'}
verification = {'status': 'PASS', 'changed_existing_files': changed,
    'source_and_test_files_unchanged': len(source_files),
    'source_snapshot_matches_original_P0_T2': True,
    'preexisting_tracked_diff_preserved': True,
    'other_recorded_docs_skills_and_task_evidence_preserved': True,
    'baseline_counts': baseline['counts'], 'final_counts': final['counts'],
    'probe_counts': probe_counts, 'probe_reruns_identical': True,
    'semantic_rows': semantic_rows, 'analysis_rows': analysis_rows,
    'alias_impacts': alias_impacts, 'minimum_boundaries': ['G'+str(i) for i in range(1, 9)],
    'local_links_checked': links, 'code_symbol_index': source_index,
    'scope_note': 'Automatic checks support artifact integrity; semantic judgments are the cited manual code review, not a semantic-equivalence benchmark.'}
for name, data in [('acceptance.json', acceptance), ('verification.json', verification)]:
    (OUT/name).write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
print('PASS handoff checks:', len(changed), 'documents changed;', len(source_files),
      'source/test files unchanged; 26/26 baseline and final; 12/12 probes; P0-T2 COMPLETE')
