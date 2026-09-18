"""Read-only P0-T2 characterization, not a new production contract or oracle.

Synthetic inputs isolate the final SFIR boundary; they do not prove frontend
coverage. Results record limitations without treating them as desired behavior.
"""
from pathlib import Path
import json
import sys
ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'scripts/s_seir'), str(ROOT / 'scripts/legacy_yul')]
from s_seir_semantic_fact_ir_tests import block, function, solidity
from s_seir_semantic_fact_ir import SemanticFactIRBridge
from s_seir_yul_normalize import normalize_expr

variables = [
    {'name': 'balances', 'kind': 'state', 'declaration_id': 1},
    {'name': 'owner', 'kind': 'parameter', 'declaration_id': 2},
    {'name': 'other', 'kind': 'parameter', 'declaration_id': 3},
    {'name': 'alias', 'kind': 'local', 'declaration_id': 4},
    {'name': 'loaded', 'kind': 'local', 'declaration_id': 5},
]
def state_node(name, key, write, order):
    access = f'balances[{key}]'
    return {**solidity(name, 'b', reads=[key] if write else [access, key],
                       writes=[access] if write else ['loaded']),
            'kind': 'StateWrite' if write else 'StateRead',
            'order': {'operation_order': order},
            'semantic': {'location': {'kind': 'mapping', 'access': access,
                                      'state_variable': 'balances', 'keys': [key]}}}
def build(nodes):
    return SemanticFactIRBridge().build_function(
        function({'blocks': [block('b')], 'edges': []}, variables), nodes, [])
def storage_ref(result, name, role):
    node = next(n for n in result['semantic_nodes'] if n['semantic_id'].endswith(':'+name))
    return next(r for r in node['fact_ssa'][role] if ':storage_location:' in r['binding_id'])

same = build([state_node('write', 'owner', True, 1), state_node('read', 'owner', False, 2)])
assert storage_ref(same, 'write', 'writes')['version'] == storage_ref(same, 'read', 'reads')['version']
alias = {**solidity('alias_assign', 'b', reads=['owner'], writes=['alias']),
         'lvalue': 'alias', 'rvalue': 'owner', 'order': {'operation_order': 0}}
different = build([alias, state_node('write', 'owner', True, 1), state_node('read', 'alias', False, 2)])
assert storage_ref(different, 'write', 'writes')['binding_id'] != storage_ref(different, 'read', 'reads')['binding_id']
reassign = {**solidity('reassign', 'b', reads=['other'], writes=['owner']),
            'lvalue': 'owner', 'rvalue': 'other', 'order': {'operation_order': 2}}
reused = build([state_node('write', 'owner', True, 1), reassign, state_node('read', 'owner', False, 3)])
assert storage_ref(reused, 'write', 'writes')['version'] == storage_ref(reused, 'read', 'reads')['version']
env = build([{**solidity('env', 'b', reads=['msg.sender', 'msg.value'], writes=['loaded']),
              'rvalue': 'msg.sender', 'semantic': {'operand_ssa': ['msg.sender', 'msg.value']}}])
assert env['semantic_nodes'][0]['fact_ssa']['reads'] == []
assert normalize_expr('caller()') == 'msg.sender'
assert normalize_expr('callvalue()') == 'msg.value'
bridge = SemanticFactIRBridge()
assert bridge._storage_location_binding({'kind': 'StateRead', 'semantic': {'location': {
    'kind': 'manual_slot', 'access': 'storage[slot]'}}}, {}) is None
payload = {
    'scope': 'six characterization checks, not semantic-equivalence benchmark',
    'checks': [
        {'id': 'same_path_def_use', 'status': 'PASS', 'observation': 'identical recovered access shares write/read version'},
        {'id': 'different_key_spelling', 'status': 'PASS', 'observation': 'alias = owner is not propagated into storage identity at final bridge'},
        {'id': 'redefined_key_same_spelling', 'status': 'PASS', 'observation': 'same access text still shares storage version after key redefinition; identity lacks key version'},
        {'id': 'environment_factssa', 'status': 'PASS', 'observation': 'environment operands retained as semantics but have no FactSSA read refs'},
        {'id': 'environment_normalization', 'status': 'PASS', 'observation': 'caller/callvalue normalize to msg.sender/msg.value'},
        {'id': 'unresolved_storage_identity', 'status': 'PASS', 'observation': 'manual slot without state root has no exact storage binding'},
    ],
    'same_path': same, 'different_key_spelling': different,
    'redefined_key_same_spelling': reused, 'environment': env,
}
output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name('capability_probes.json')
output.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n')
print('PASS 6 characterization checks:', output)
