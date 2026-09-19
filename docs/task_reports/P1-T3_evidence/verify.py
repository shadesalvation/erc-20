#!/usr/bin/env python3
"""Task-only reproducible acceptance/evidence collector; never imported by recovery."""
import ast
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / 'scripts/s_seir'), str(ROOT / 'scripts/legacy_yul')]
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


class Results(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.records.append({'test': test.id(), 'status': 'PASS'})

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.records.append({'test': test.id(), 'status': 'FAIL', 'reason': self._exc_info_to_string(err, test)})

    def addError(self, test, err):
        super().addError(test, err)
        self.records.append({'test': test.id(), 'status': 'FAIL', 'reason': self._exc_info_to_string(err, test)})

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.records.append({'test': test.id(), 'status': 'N/A', 'reason': reason})


all_records = []
for module_name, log_name in [
    ('s_seir_research_abstract_domain_tests', 'targeted.log'),
    ('s_seir_research_regions_tests', 'regions_regression.log'),
    ('s_seir_research_actions_tests', 'actions_regression.log'),
]:
    module = importlib.import_module(module_name)
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    with (OUT / log_name).open('w') as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=Results).run(suite)
    all_records.extend(result.records)
counts = {status: sum(r['status'] == status for r in all_records) for status in ('PASS', 'FAIL', 'N/A')}
save('targeted_tests.json', {'results': all_records, 'counts': counts})

import s_seir_research_abstract_domain as d
from s_seir_research_abstract_domain_tests import fixture
sfir, upstream, fact = fixture()
built = d.build_contract(k=2, finite_cap=4, input_fingerprint=upstream['input_fingerprint'])
contract = built['contract']
intake = d.consume_regions(upstream, contract)
binding = intake['bindings'][0]
region = next(r for r in upstream['regions'] if r['id'] == binding['region_ref'])
candidate = region['payload']['control_state_candidates'][0]
after = d.local_transfer(binding['initial_state'], region, candidate, fact, contract,
                         bindings=sfir['functions'][0]['fact_ssa']['bindings'])
ctx = d.update_context(binding['context'], region, region['payload']['cases'][0]['edge_ref'], contract)
save('handoff_smoke.json', {'status': 'PASS' if d.state_status(after)['completion'] == 'COMPLETE' else 'FAIL',
                          'contract': contract, 'evidence_records': built['evidence_records'],
                          'region_ref': region['id'], 'initial_state': binding['initial_state'],
                          'one_local_transfer': after, 'observed_context': ctx,
                          'upstream_evidence_records': upstream['evidence_records'],
                          'note': 'One local call; no propagation engine or node-context final-state table.'})

criteria = [
    ('Only AbstractDomainContract is a formal artifact', ['contract_only_artifact', 'isolation_shared']),
    ('Frozen payload is exactly eight fields', ['contract_only_artifact']),
    ('Rules/config digest identity and collection reorder stability', ['contract_only_artifact', 'detector_collection_reorder']),
    ('Distinct BOTTOM/FINITE/TOP/UNKNOWN and bounded typed set', ['bottom_finite_top', 'signed_unsigned', 'exhaustive_small']),
    ('Join idempotence, conservative upper bound and canonical ordering', ['join_idempotence', 'exhaustive_small', 'value_and_state_unordered']),
    ('Deterministic local candidate-only transfer', ['constant_local_assignment', 'unsupported_relevant_fact', 'existing_candidate_plus_literal']),
    ('No false precision for missing type/width/signedness/mode', ['missing_type_width', 'pure_wrapping_checked', 'primary_atomic_sfir', 'cast_identity']),
    ('k=1/k=2 bounded context, no iteration budget', ['context_k1_k2', 'context_collection']),
    ('Direct immutable P1-T2 consumption, no detection redo', ['direct_region_consumption', 'isolation_shared']),
    ('Observed cases do not assert real successors', ['context_collection_reorder', 'direct_region_consumption']),
    ('Preserve no-region, unsupported and upstream incompleteness', ['legal_no_region', 'upstream_ambiguous', 'insufficient_region', 'unanchored_unresolved', 'unresolved_evidence']),
    ('No worklist, re-enqueue, whole-region fixed point or AbstractPropagation', ['isolation_shared']),
    ('No semantic edge reconstruction, SMT/SAT/UNSAT or Guard normalization', ['isolation_shared']),
    ('No Def-Use/RD/VFG/StateDependency/order/STIR', ['isolation_shared']),
    ('Reuse shared contract and preserve oracle/second-parser isolation', ['isolation_shared', 'contract_only_artifact']),
    ('Targeted and required regressions pass', []),
    ('Report/status/handoff match implementation', []),
]
report = (ROOT / 'docs/task_reports/P1-T3.md').read_text()
status = (ROOT / 'PROJECT_STATUS.md').read_text()
regression = json.loads((OUT / 'final_regression/results.json').read_text())
bootstrap = json.loads((OUT / 'bootstrap.json').read_text())
production = ROOT / 'scripts/s_seir/s_seir_research_abstract_domain.py'
tree = ast.parse(production.read_text())
exports = [node.name for node in tree.body if isinstance(node, ast.FunctionDef) and not node.name.startswith('_')]
doc_checks = {name: name in report for name in exports}
doc_checks.update({'report_complete': '**P1-T3 = COMPLETE**' in report,
                   'status_complete': '**P1-T3 = COMPLETE**' in status,
                   'handoff': '## P1-T4 Handoff' in report,
                   'baseline': '28 PASS' in report,
                   'next_task_requires_authorization': '等待单独授权 P1-T4' in status})
checks = []
for index, (title, prefixes) in enumerate(criteria, 1):
    tests = [r for r in all_records if r['test'].startswith('s_seir_research_abstract_domain_tests.')
             and any(r['test'].split('.')[-1].startswith('test_' + p) for p in prefixes)]
    passed = bool(tests) and all(r['status'] == 'PASS' for r in tests)
    evidence = [r['test'] for r in tests]
    if index == 16:
        passed = counts['FAIL'] == 0 and counts['N/A'] == 0 and bool(all_records) and regression['counts'] == {'PASS': 29, 'FAIL': 0, 'SKIP': 0, 'TIMEOUT': 0}
        evidence = ['targeted_tests.json', 'final_regression/results.json']
    if index == 17:
        passed = all(doc_checks.values()) and (OUT / 'handoff_smoke.json').is_file()
        evidence = ['../P1-T3.md', '../../../PROJECT_STATUS.md', 'handoff_smoke.json', 'change_summary.json']
    checks.append({'id': index, 'criterion': title, 'status': 'PASS' if passed else 'FAIL', 'evidence': evidence})

tracked_diff = git('diff', '--name-only').splitlines()
untracked = git('ls-files', '--others', '--exclude-standard').splitlines()
allowed = {'PROJECT_STATUS.md', 'scripts/s_seir/s_seir_research_abstract_domain.py',
           'scripts/s_seir/s_seir_research_abstract_domain_tests.py', 'docs/task_reports/P1-T3.md'}
unexpected = [p for p in tracked_diff + untracked if p not in allowed
              and not p.startswith('docs/task_reports/P1-T3_evidence/')
              and p not in bootstrap['preexisting_worktree']]
if unexpected:
    checks[-1]['status'] = 'FAIL'
    checks[-1]['unexpected_changes'] = unexpected
end_head = git('rev-parse', 'HEAD')
version_hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in (production, ROOT / 'scripts/s_seir/s_seir_research_abstract_domain_tests.py',
                            ROOT / 'docs/task_reports/P1-T3.md', ROOT / 'PROJECT_STATUS.md')}
save('change_summary.json', {'branch': git('rev-parse', '--abbrev-ref', 'HEAD'),
                            'start_head': bootstrap['start_head'], 'end_head': end_head,
                            'git_status': git('status', '--short'), 'tracked_diff': tracked_diff,
                            'task_files': sorted(allowed), 'preexisting_preserved': bootstrap['preexisting_worktree'],
                            'unexpected_changes': unexpected, 'document_checks': doc_checks,
                            'sha256': version_hashes,
                            'scope_review': 'Only local domain operations/intake/context; no CFG adjacency construction, traversal, solver, parser, future research artifact or production file IO. Existing producer files unchanged.'})
save('acceptance.json', {'task': 'P1-T3', 'start_head': bootstrap['start_head'], 'end_head': end_head,
                         'criteria': checks, 'counts': {s: sum(c['status'] == s for c in checks) for s in ('PASS', 'FAIL', 'N/A')},
                         'status': 'PASS' if all(c['status'] == 'PASS' for c in checks) else 'FAIL'})
print(json.dumps({'targeted_counts': counts, 'acceptance': {c['id']: c['status'] for c in checks}}, indent=2))
sys.exit(0 if all(c['status'] == 'PASS' for c in checks) else 1)
