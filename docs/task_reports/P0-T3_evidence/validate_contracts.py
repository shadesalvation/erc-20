"""P0-T3 document/contract consistency only; never imports recovery or reads oracle.

Structural checks are automatic. Semantic architecture acceptance is a separately
recorded manual review, not an algorithm proof. Failures are fatal and visible.
"""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
EXPECTED = {f'P{phase}-T{i}' for phase, count in enumerate([3, 7, 8, 6, 4, 5, 4])
            for i in range(1, count + 1)}
REQUIRED_ARTIFACTS = {'SemanticAction', 'SemanticControlEdge', 'Guard', 'StorageAddressRelation',
    'Definition', 'Use', 'ValueIdentity', 'ValueFlow', 'CanonicalExpression', 'SemanticSlice',
    'StateDependency', 'SinkDependency', 'SemanticEvent', 'OrderConstraint',
    'TransitionCandidate', 'StateTransitionIR'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def rows(section):
    return [[c.strip() for c in line.strip().strip('|').split('|')]
            for line in section.splitlines() if line.startswith('| ')]


def check_documents(plan, nav, registry):
    tasks = registry['tasks']
    by_id = {t['id']: t for t in tasks}
    require(len(tasks) == len(by_id) == 38, 'duplicate/missing task')
    require(set(by_id) == EXPECTED | {'P2-T1A'}, '37 fixed task set + supplement mismatch')
    require(registry['fixed_count'] == 37 and registry['supplemental'] == {'P2-T1A': 'REQUIRED'},
            'supplement must be REQUIRED and outside fixed count')
    require('P2-T1A = REQUIRED' in nav and 'P2-T1A frozen minimum contract — REQUIRED' in plan,
            'REQUIRED document drift')
    for tid, task in by_id.items():
        require(all(k in task and task[k] for k in ('responsibility', 'input', 'output', 'owner',
                    'interfaces', 'forbidden', 'limitations')), f'{tid} missing card field')
        require(set(task['dependencies']) <= set(by_id), f'{tid} dangling dependency')
        require(len(task['dependencies']) == len(set(task['dependencies'])), f'{tid} duplicate dependency')
        expected_consumers = [t['id'] for t in tasks if tid in t['dependencies']]
        require(task['consumers'] == expected_consumers, f'{tid} consumer/dependency mismatch')
    visiting, done = set(), set()
    def visit(tid):
        require(tid not in visiting, f'dependency cycle at {tid}')
        if tid in done:
            return
        visiting.add(tid)
        for dep in by_id[tid]['dependencies']:
            visit(dep)
        visiting.remove(tid)
        done.add(tid)
    for tid in by_id:
        visit(tid)
    require('P2-T1' in by_id['P2-T1A']['dependencies'], 'P2-T1A must follow P2-T1')
    require('P2-T1A' in by_id['P2-T2']['dependencies'], 'P2-T2 may not bypass P2-T1A')
    plan_rows = [r for r in rows(plan) if re.fullmatch(r'P\d-T\d+A?', r[0])]
    nav_rows = [r for r in rows(nav) if re.fullmatch(r'P\d-T\d+A?', r[0])]
    require(len(plan_rows) == len(nav_rows) == 38, 'task table count drift')
    for task, prow, nrow in zip(tasks, plan_rows, nav_rows):
        core = [task[k] for k in ('id', 'responsibility', 'input', 'output')]
        consumers = ', '.join(task['consumers']) or '项目研究报告'
        require(prow == core + [', '.join(task['dependencies']) or '无', consumers],
                f'{task["id"]} plan table drift')
        require(nrow == core + [consumers], f'{task["id"]} navigation drift')
        if task['id'].startswith('P0'):
            continue
        start = plan.index('#### ' + task['id'] + ' —')
        end = plan.find('\n#### ', start + 1)
        detail = plan[start:end if end != -1 else len(plan)]
        for label in ['Responsibility', 'Phase / Module / Producer', 'Implementation / file owner', 'Interface touched',
                      'Research Reference', 'Upstream work that must be reused',
                      'Work forbidden to redo', 'Acceptance ownership', 'Relevant known limitations']:
            require(label in detail, f'{task["id"]} missing {label}')
        for key in ['owner', 'interfaces', 'forbidden', 'limitations']:
            require(task[key] in detail, f'{task["id"]} detail drift: {key}')
    gaps = [r for r in rows(plan) if re.fullmatch(r'G[1-8]', r[0])]
    require({r[0] for r in gaps} == {f'G{i}' for i in range(1, 9)} and len(gaps) == 8,
            'gap coverage missing/duplicate')
    allowed = {'REUSE EXISTING CAPABILITY', 'ASSIGNED TO EXISTING PLANNED TASK',
               'P2-T1A MINIMUM SUPPLEMENTATION', 'EXPLICIT UNSUPPORTED / RESEARCH-SCOPE LIMITATION'}
    for gap in gaps:
        require(len(gap) == 9 and all(gap), f'{gap[0]} incomplete gap contract')
        owners = re.findall(r'P\d-T\d+A?', gap[3])
        consumers = re.findall(r'P\d-T\d+A?', gap[5])
        require(owners and consumers and set(owners + consumers) <= set(by_id), f'{gap[0]} orphan gap')
        require(set(gap[8].split('; ')) <= allowed, f'{gap[0]} invalid resolution mode')
    interfaces = plan.split('## 4. Interface Freeze Table', 1)[1].split('辅助契约也冻结', 1)[0]
    artifacts = [r for r in rows(interfaces) if r[0] in REQUIRED_ARTIFACTS]
    require(len(artifacts) == 16 and {r[0] for r in artifacts} == REQUIRED_ARTIFACTS,
            'missing critical artifact')
    def upstream(tid):
        result = set()
        for dep in by_id[tid]['dependencies']:
            result.add(dep)
            result.update(upstream(dep))
        return result
    for artifact in artifacts:
        require(len(artifact) == 8 and all(artifact), f'{artifact[0]} missing freeze field')
        producers = re.findall(r'P\d-T\d+A?', artifact[1])
        consumers = re.findall(r'P\d-T\d+A?', artifact[2])
        require(producers and consumers and set(producers + consumers) <= set(by_id),
                f'{artifact[0]} dangling producer/consumer')
        for consumer in consumers:
            require(any(p == consumer or p in upstream(consumer) for p in producers),
                    f'{artifact[0]} producer unreachable from {consumer}')
    for code in ['UNKNOWN', 'TIMEOUT', 'UNSUPPORTED', 'TRUNCATED', 'FALLBACK', 'OPAQUE',
                 'INSUFFICIENT_EVIDENCE', 'SAT', 'UNSAT', 'PROVEN', 'CANDIDATE', 'COMPLETE']:
        require(code in plan.split('### 3.3 AnalysisStatus Contract')[1].split('### 3.4')[0],
                f'missing status {code}')
    relation = plan.split('## 5. P2-T1A')[1].split('## 6.')[0]
    for code in ['SAME', 'DISTINCT', 'MAY_OVERLAP', 'INSUFFICIENT_EVIDENCE']:
        require(f'| {code} |' in relation, f'missing storage relation {code}')
    require('source support insufficient' in plan, 'research source limitation missing')
    return {'fixed_tasks': 37, 'supplemental_tasks': 1, 'acyclic': True,
            'navigation_rows_consistent': 38, 'gap_owners_checked': 8,
            'critical_artifact_producer_consumer_checked': 16, 'P2-T1A': 'REQUIRED'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--final', action='store_true', help='also require complete handoff documents')
    args = parser.parse_args()
    (OUT/'consistency.json').write_text(json.dumps({'status': 'RUNNING'})+'\n')
    plan = (ROOT/'IMPLEMENTATION_PLAN.md').read_text()
    nav = (ROOT/'docs/research/TASK_MAP.md').read_text()
    registry = json.loads((OUT/'task_registry.json').read_text())
    checks = check_documents(plan, nav, registry)
    # Adversarial documentation mutations: validator must reject each broken contract.
    mutants = []
    missing = copy.deepcopy(registry); missing['tasks'].pop(); mutants.append(('missing_task', plan, nav, missing))
    bypass = copy.deepcopy(registry)
    next(t for t in bypass['tasks'] if t['id'] == 'P2-T2')['dependencies'].remove('P2-T1A')
    mutants.append(('bypass_required_alias', plan, nav, bypass))
    cycle = copy.deepcopy(registry); cycle['tasks'][0]['dependencies'] = ['P6-T4']
    for t in cycle['tasks']:
        t['consumers'] = [x['id'] for x in cycle['tasks'] if t['id'] in x['dependencies']]
    mutants.append(('dependency_cycle', plan, nav, cycle))
    mutants.append(('orphan_gap', plan.replace('| G8 |', '| G9 |'), nav, registry))
    mutants.append(('navigation_drift', plan, nav.replace('| SemanticAction |', '| BadAction |', 1), registry))
    mutants.append(('missing_artifact', plan.replace('| StateTransitionIR |', '| MissingSTIR |', 1), nav, registry))
    mutants.append(('collapsed_alias_state', plan.replace('| MAY_OVERLAP |', '| DISTINCT |'), nav, registry))
    rejected = []
    for name, p, n, reg in mutants:
        try:
            check_documents(p, n, reg)
        except ValueError as error:
            rejected.append({'mutation': name, 'status': 'PASS', 'rejection': str(error)})
        else:
            raise ValueError(f'validator accepted broken contract: {name}')
    (OUT/'validator_sensitivity.json').write_text(json.dumps(rejected, ensure_ascii=False, indent=2)+'\n')
    baseline_data = {}
    names = None
    for stage in ['baseline', 'final']:
        data = json.loads((OUT/stage/'results.json').read_text())
        require(data['entrypoints_planned'] == 26 and len(data['results']) == 26, f'{stage} incomplete regression')
        require(data['counts'] == {'PASS': 26, 'FAIL': 0, 'SKIP': 0, 'TIMEOUT': 0}, f'{stage} regression failure')
        require(all(x['returncode'] == 0 and (OUT/stage/x['log']).is_file() for x in data['results']),
                f'{stage} missing result evidence')
        current_names = [x['name'] for x in data['results']]
        if names is not None:
            require(names == current_names, 'baseline/final entrypoint scope changed')
        names = current_names
        baseline_data[stage] = data['counts']
    for path in ['docs/task_reports/P0-T1_followup/final/results.json',
                 'docs/task_reports/P0-T2_revision_evidence/final/results.json']:
        data = json.loads((ROOT/path).read_text())
        require(names == [x['name'] for x in data['results']], f'upstream baseline scope mismatch: {path}')
    (OUT/'baseline_summary.json').write_text(json.dumps(baseline_data, indent=2)+'\n')
    pre = json.loads((OUT/'tracked_hashes_before.json').read_text())
    allowed_changes = {'PROJECT_STATUS.md', 'docs/research/TASK_MAP.md', 'docs/decisions/README.md'}
    modified = []
    for name, digest in pre.items():
        path = ROOT/name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            modified.append(name)
    require(set(modified) <= allowed_changes, 'unauthorized tracked changes: '+repr(modified))
    old_sources = json.loads((ROOT/'docs/task_reports/P0-T2_evidence/source_hashes_before.json').read_text())
    require(all((ROOT/p).is_file() and hashlib.sha256((ROOT/p).read_bytes()).hexdigest() == h
                for p, h in old_sources.items()), 'source/test conflict with upstream handoff')
    untracked = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '-z'], cwd=ROOT).decode().split('\0')
    allowed_new = {'IMPLEMENTATION_PLAN.md', 'docs/research/RESEARCH_FRAMEWORK.md',
                   'docs/decisions/P0-T3-001-interface-freeze.md', 'docs/task_reports/P0-T3.md'}
    require(all(p in allowed_new or p.startswith('docs/task_reports/P0-T3_evidence/')
                for p in untracked if p), 'new out-of-scope files: '+repr(untracked))
    for cmd, file in [(['git', 'status', '--short'], 'git_status_after.txt'),
                      (['git', 'diff', '--stat'], 'git_diff_stat.txt'),
                      (['git', 'diff', '--numstat'], 'git_diff_numstat.txt')]:
        (OUT/file).write_bytes(subprocess.check_output(cmd, cwd=ROOT))
    before_status = (OUT/'git_status_before.txt').read_text().splitlines()
    after_status = (OUT/'git_status_after.txt').read_text().splitlines()
    require(all(line in after_status for line in before_status), 'pre-existing worktree changes not preserved')
    require(subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT).decode() == (OUT/'branch.txt').read_text(), 'branch changed')
    require(subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode() == (OUT/'head.txt').read_text(), 'HEAD changed')
    subprocess.run(['git', 'diff', '--check'], cwd=ROOT, check=True)
    # Include untracked deliverables in review summary; git diff --stat alone excludes them.
    files = sorted(set(modified) | {p for p in untracked if p})
    (OUT/'change_summary.json').write_text(json.dumps({'preexisting_not_owned': before_status,
        'modified_existing': modified, 'new_files': [p for p in files if p not in pre],
        'production_changes': [], 'oracle_changes': []}, ensure_ascii=False, indent=2)+'\n')
    docs = ['IMPLEMENTATION_PLAN.md', 'docs/research/RESEARCH_FRAMEWORK.md',
            'docs/research/TASK_MAP.md', 'PROJECT_STATUS.md', 'docs/task_reports/P0-T3.md',
            'docs/decisions/P0-T3-001-interface-freeze.md']
    for name in docs:
        content = (ROOT/name).read_text()
        for target in re.findall(r'\[[^\]]+\]\(([^)]+)\)', content):
            if '://' not in target:
                require(((ROOT/name).parent/target.split('#')[0]).exists(), f'broken link {name}: {target}')
    framework = (ROOT/'docs/research/RESEARCH_FRAMEWORK.md').read_text()
    for phrase in ['Module 1', 'Module 2', 'Module 3', 'partial order', 'rollback', 'oracle',
                   'Affected Modules', 'Affected Tasks', 'Affected Experiments', 'Required Regression']:
        require(phrase in framework, f'framework missing invariant {phrase}')
    acceptance = json.loads((OUT/'acceptance.json').read_text())
    require(len(acceptance['items']) >= 45, 'incomplete专项验收')
    require(all(x['status'] in {'PASS', 'FAIL', 'N/A'} and x['evidence'] for x in acceptance['items']), 'invalid acceptance item')
    require(all(x['status'] == 'PASS' for x in acceptance['items']), 'failed专项验收')
    if args.final:
        require('P0-T3 = COMPLETE' in (ROOT/'PROJECT_STATUS.md').read_text(), 'status handoff incomplete')
        require('P0-T3 = COMPLETE' in (ROOT/'docs/task_reports/P0-T3.md').read_text(), 'report incomplete')
    result = {'status': 'PASS', 'scope': 'document/contract consistency; no future algorithm validation',
              **checks, 'baseline': baseline_data, 'source_hashes_unchanged': len(old_sources),
              'modified_preexisting_files': modified, 'preexisting_status_preserved': before_status,
              'validator_mutations_rejected': len(rejected), 'manual_acceptance_items': len(acceptance['items']),
              'final_handoff_checked': args.final,
              'artifact_hashes': {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in docs}}
    (OUT/'consistency.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        (OUT/'consistency.json').write_text(json.dumps({'status': 'FAIL', 'error': str(error)}, ensure_ascii=False, indent=2)+'\n')
        raise
