"""Characterize existing SFIR edges; no alias implementation, VFG or slicer.

Synthetic final-bridge inputs isolate a known alias gap. This is not evidence
that every Solidity/Yul frontend emits these inputs, or that a future slicer
has already failed. No production code or expected oracle is changed.
"""
from pathlib import Path
import json
import runpy
import sys
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent
OUT.mkdir(parents=True, exist_ok=True)
sys.argv = [str(ROOT/'docs/task_reports/P0-T2_evidence/probe_capabilities.py'), str(OUT/'capability_probes.json')]
base = runpy.run_path(sys.argv[0])
state_node, solidity, build = (base[k] for k in ('state_node', 'solidity', 'build'))
checks = []
for kind in ['StateWrite', 'ExternalCall', 'ValueTransferCall', 'EventEmit', 'Return', 'Revert']:
    alias = {**solidity('alias_assign', 'b', reads=['owner'], writes=['alias']),
             'lvalue': 'alias', 'rvalue': 'owner', 'order': {'operation_order': 0}}
    write = state_node('prior_write', 'owner', True, 1)
    write.update(lvalue='balances[owner]', rvalue='other', reads=['owner', 'other'])
    write['semantic']['value'] = 'other'
    read = state_node('state_read', 'alias', False, 2)
    read.update(lvalue='loaded', rvalue='balances[alias]')
    if kind == 'StateWrite':
        sink = state_node('sink', 'other', True, 3)
        sink.update(lvalue='balances[other]', rvalue='loaded', reads=['other', 'loaded'])
        sink['semantic']['value'] = 'loaded'
    else:
        fields = {
            'ExternalCall': {'target': 'other', 'function': 'consume', 'arguments': ['loaded']},
            'ValueTransferCall': {'target': 'other', 'value': 'loaded', 'method': 'send'},
            'EventEmit': {'event': 'Observed', 'arguments': ['loaded']},
            'Return': {'values': ['loaded']},
            'Revert': {'function': 'Failure', 'arguments': ['loaded']},
        }[kind]
        sink = {**solidity('sink', 'b', reads=['loaded']), 'kind': kind,
                'order': {'operation_order': 3}, 'semantic': fields}
    result = build([alias, write, read, sink])
    ids = {n['semantic_id'].rsplit(':', 1)[-1]: n['semantic_id'] for n in result['semantic_nodes']}
    edges = result['semantic_edges']
    read_to_sink = [e for e in edges if e['from'] == ids['state_read'] and e['to'] == ids['sink'] and e['kind'] == 'data']
    write_to_read = [e for e in edges if e['from'] == ids['prior_write'] and e['to'] == ids['state_read']]
    assert read_to_sink, (kind, edges)
    assert not write_to_read, (kind, edges)
    checks.append({'sink_kind': kind, 'status': 'PASS',
                   'observation': 'load-to-operand data edge exists; alias-equivalent prior storage write has no edge to load',
                   'scope': 'final-bridge edge characterization; no future dependency algorithm executed',
                   'input_facts': [alias, write, read, sink], 'sfir': result})
(OUT/'sink_impacts.json').write_text(json.dumps({'checks': checks}, ensure_ascii=False, indent=2)+'\n')
print('PASS', len(checks), 'sink edge characterizations')
