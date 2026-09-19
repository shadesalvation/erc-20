#!/usr/bin/env python3
"""P1-T3 semantic tests; fixtures are passed through the real P1-T2 detector."""
from copy import deepcopy
import ast
import inspect
from itertools import combinations, product
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / 'legacy_yul', ROOT / 's_seir'):
    sys.path.insert(0, str(path))

import s_seir_research_abstract_domain as d
from s_seir_research_contracts import canonical_json, ContractValidationError, diagnostic, validate_evidence_record
from s_seir_research_regions_tests import (
    flattened_fixture, run_detector, INPUT_FINGERPRINT, node, action_node,
)
from s_seir_research_regions import detect_flattened_regions, REGION_PAYLOAD_FIELDS
from s_seir_semantic_fact_adapter import SlitherFactAdapter
from s_seir_solidity_semantic_lifter import SoliditySemanticLifter


def contract(k=2, cap=4):
    return d.build_contract(k=k, finite_cap=cap, input_fingerprint=INPUT_FINGERPRINT)['contract']


def literal(value, typ='uint8'):
    return {'kind': 'Constant', 'text': str(value), 'type': typ, 'is_constant': True}


def variable(typ='uint8'):
    return {'kind': 'LocalIRVariable', 'text': 'alpha_1', 'name': 'alpha_1', 'base_name': 'alpha', 'type': typ, 'is_constant': False}


def fixture(op=None):
    sfir = flattened_fixture()
    fn = sfir['functions'][0]
    for fact in fn['semantic_nodes']:
        for direction in ('reads', 'writes'):
            for item in fact['fact_ssa'][direction]:
                if item['base_name'] == 'alpha':
                    item['binding_id'] = 'decl:42:value'
    fn['fact_ssa']['bindings'] = [{'binding_id': 'decl:42:value', 'type': 'uint8', 'declaration_id': 42}]
    target = next(x for x in fn['semantic_nodes'] if x['semantic_id'] == 'set_a')
    op = op or {'kind': 'Assignment', 'lvalue': variable(), 'rvalue': literal(7)}
    adapted = SlitherFactAdapter().facts_from_operations({'function': 'run', 'contract': 'Token'}, [op])[0].to_dict()
    # Keep the detector fixture's occurrence/placement/SSA identity, but use
    # the production adapter's actual kind, semantic and archived operands.
    for key in ('kind', 'semantic', 'evidence', 'rvalue', 'lvalue'):
        target[key] = adapted.get(key)
    _, upstream = run_detector(sfir)
    return sfir, upstream, target


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.c = contract()
        self.sfir, self.upstream, self.fact = fixture()
        self.region = self.upstream['regions'][0]
        self.candidate = self.region['payload']['control_state_candidates'][0]
        self.refs = self.candidate['source_refs']
        self.key = d.candidate_key(self.region, self.candidate)
        self.state = d.make_state(self.region, self.c)
        self.bindings = self.sfir['functions'][0]['fact_ssa']['bindings']

    def val(self, *values, typ='uint8', c=None):
        return d.finite(c or self.c, typ, values, sources=self.refs)

    def transfer(self, fact=None, state=None, **kwargs):
        return d.local_transfer(state or self.state, self.region, self.candidate, fact or self.fact, self.c, bindings=self.bindings, **kwargs)

    def assertUnknown(self, value, text=None):
        self.assertEqual('UNKNOWN', value['tag'])
        self.assertEqual('PARTIAL', value['status']['completion'])
        self.assertEqual('NOT_RUN', value['status']['solver'])
        self.assertTrue(value['status']['diagnostics'])
        if text:
            self.assertIn(text, str(value['status']['diagnostics']))

    def test_contract_only_artifact_eight_fields_digest_and_shared_evidence(self):
        built = d.build_contract(k=2, finite_cap=4, input_fingerprint=INPUT_FINGERPRINT)
        self.assertEqual(d.PAYLOAD_FIELDS, set(built['contract']['payload']))
        self.assertEqual(d.SCHEMA, built['contract']['schema'])
        self.assertEqual('PROVEN', built['contract']['status']['proof'])
        validate_evidence_record(built['evidence_records'][0])
        self.assertEqual([built['evidence_records'][0]['id']], built['contract']['evidence_refs'])
        reordered = dict(reversed(list(built['contract'].items())))
        reordered['payload'] = dict(reversed(list(reordered['payload'].items())))
        d.validate_contract(reordered)
        other_input = d.build_contract(k=2, finite_cap=4, input_fingerprint='different-input')['contract']
        self.assertEqual(self.c['id'], other_input['id'])
        self.assertNotEqual(self.c['id'], contract(k=1)['id'])
        self.assertNotEqual(self.c['id'], contract(cap=2)['id'])
        for key, val in [('k', '1'), ('extra', True)]:
            broken = deepcopy(self.c)
            broken['payload'][key] = val
            with self.assertRaises(ContractValidationError):
                d.validate_contract(broken)

    def test_explicit_config_validation(self):
        for k, cap in [(-1, 4), (33, 4), (True, 4), (2, 0), (2, 257)]:
            with self.assertRaises(ContractValidationError):
                contract(k, cap)

    def test_bottom_finite_top_unknown_are_distinct_and_typed(self):
        values = [d.bottom(), self.val(1), self.val(0, 1, 2, 3, 4), d.unknown('missing type', sources=self.refs)]
        self.assertEqual(['BOTTOM', 'FINITE', 'TOP', 'UNKNOWN'], [v['tag'] for v in values])
        for v in values:
            d.validate_value(v, self.c)
        self.assertEqual({'name': 'uint8', 'bit_width': '8', 'signed': False}, values[1]['type'])
        self.assertEqual(['VALUE'], values[1]['arithmetic_modes'])
        self.assertTrue(values[2]['source_refs'])
        self.assertUnknown(values[3], 'missing type')
        with self.assertRaises(ContractValidationError):
            self.val()

    def test_candidate_handle_stable_same_distinct_and_collection_reorder(self):
        changed = deepcopy(self.region)
        c = changed['payload']['control_state_candidates'][0]
        c['source_refs'].reverse()
        c['candidate']['write_evidence'].reverse()
        self.assertEqual(self.key, d.candidate_key(changed, c))
        distinct = deepcopy(c)
        distinct['source_refs'][0]['locator']['origin_id'] = 'another-origin'
        changed['payload']['control_state_candidates'].append(distinct)
        self.assertNotEqual(self.key, d.candidate_key(changed, distinct))
        self.assertEqual(2, len(d.make_state(changed, self.c)['values']))
        self.assertTrue(self.key.startswith('control-state-handle:'))
        with self.assertRaises(ContractValidationError):
            d.candidate_key(self.region, distinct)

    def test_missing_type_width_signedness_and_provenance(self):
        for typ in (None, 'uint', 'int', 'bytes32', 'uint7', 'uint264'):
            self.assertUnknown(self.val(1, typ=typ), 'type/bit width/signedness')
        self.assertUnknown(d.finite(self.c, 'uint8', [1], sources=[]), 'provenance')
        self.assertUnknown(d.finite(self.c, 'uint8', [1], sources=self.refs, modes=[]), 'arithmetic mode')

    def test_signed_unsigned_and_256bit_literals_never_unbounded(self):
        for typ, n in [('int8', -128), ('int8', 127), ('uint8', 255), ('uint256', (1 << 256) - 1)]:
            v = self.val(n, typ=typ)
            self.assertEqual([str(n)], v['values'])
            self.assertEqual('COMPLETE', v['status']['completion'])
        for typ, n in [('int8', -129), ('int8', 128), ('uint8', -1), ('uint256', 1 << 256)]:
            self.assertUnknown(self.val(n, typ=typ), 'bit width')

    def test_constant_local_assignment_deterministic_and_no_mutation(self):
        before = deepcopy([self.state, self.region, self.fact])
        first = self.transfer()
        second = self.transfer()
        self.assertEqual(first, second)
        self.assertEqual(before, [self.state, self.region, self.fact])
        self.assertEqual(['7'], first['values'][self.key]['values'])
        self.assertEqual('CANDIDATE', d.state_status(first)['proof'])
        self.assertEqual('COMPLETE', d.state_status(first)['completion'])
        self.assertTrue(first['values'][self.key]['source_refs'])

    def test_primary_atomic_sfir_assignment_uses_factssa_type(self):
        atom = {'atom_id': 'a', 'kind': 'Assignment', 'atomic_kind': 'ValueAssign', 'lvalue': variable(),
                'rvalue': literal(11), 'read': [literal(11)], 'result_ssa': 'alpha_1', 'cfg_block_id': 'case_a'}
        fact = SoliditySemanticLifter().atomic_facts({'function': 'run'}, {'operations': [atom]})[0]
        local = deepcopy(self.fact)
        for key in ('kind', 'semantic', 'rvalue', 'evidence', 'lvalue'):
            local[key] = fact.get(key)
        result = self.transfer(local)
        self.assertEqual(['11'], result['values'][self.key]['values'])
        self.assertEqual('COMPLETE', d.state_status(result)['completion'])
        no_type = d.local_transfer(self.state, self.region, self.candidate, local, self.c)
        self.assertUnknown(no_type['values'][self.key], 'missing target type')

    def test_copy_identity_uses_existing_binding_and_preserves_source(self):
        first = self.transfer()
        copy = deepcopy(self.fact)
        copy['evidence']['slither']['rvalue'] = variable()
        copy['fact_ssa']['reads'] = [{'binding_id': 'decl:42:value', 'base_name': 'alpha', 'value': 'alpha_1'}]
        result = self.transfer(copy, first, copy_candidate=self.candidate)
        self.assertEqual(['7'], result['values'][self.key]['values'])
        self.assertTrue(result['values'][self.key]['source_refs'])
        copy['fact_ssa']['reads'][0]['binding_id'] = 'different'
        self.assertUnknown(self.transfer(copy, first, copy_candidate=self.candidate)['values'][self.key], 'unique candidate binding')

    def test_binary_dispatcher_update_through_real_adapter(self):
        op = {'kind': 'Binary', 'lvalue': variable(), 'variable_left': literal(255), 'variable_right': literal(1), 'operator': '+', 'checked': False}
        sfir, upstream, fact = fixture(op)
        region = upstream['regions'][0]
        candidate = region['payload']['control_state_candidates'][0]
        result = d.local_transfer(d.make_state(region, self.c), region, candidate, fact, self.c)
        v = result['values'][d.candidate_key(region, candidate)]
        self.assertEqual(['0'], v['values'])
        self.assertEqual(['WRAPPING'], v['arithmetic_modes'])
        self.assertEqual('COMPLETE', v['status']['completion'])
        fact['evidence']['slither']['checked'] = True
        self.assertUnknown(d.local_transfer(d.make_state(region, self.c), region, candidate, fact, self.c)['values'][d.candidate_key(region, candidate)], 'overflow')

    def test_existing_candidate_plus_literal_local_update(self):
        op = {'kind': 'Binary', 'lvalue': variable(), 'variable_left': variable(), 'variable_right': literal(1), 'operator': '+', 'checked': False}
        sfir, _, fact = fixture(op)
        fact['fact_ssa']['reads'] = [{'binding_id': 'decl:42:value', 'value': 'alpha_1', 'base_name': 'alpha'}]
        _, up = run_detector(sfir)
        region = up['regions'][0]
        candidate = region['payload']['control_state_candidates'][0]
        state = d.make_state(region, self.c)
        key = d.candidate_key(region, candidate)
        state['values'][key] = d.finite(self.c, 'uint8', [254], sources=candidate['source_refs'])
        # Manual composition tests only: no production CFG engine.
        first = d.local_transfer(state, region, candidate, fact, self.c, operand_candidates={'variable_left': candidate})
        second = d.local_transfer(first, region, candidate, fact, self.c, operand_candidates={'variable_left': candidate})
        self.assertEqual(['255'], first['values'][key]['values'])
        self.assertEqual(['0'], second['values'][key]['values'])
        self.assertEqual(['WRAPPING'], second['values'][key]['arithmetic_modes'])
        self.assertEqual('COMPLETE', d.state_status(second)['completion'])
        fact['evidence']['slither']['variable_left']['type'] = 'int8'
        bad = d.local_transfer(state, region, candidate, fact, self.c, operand_candidates={'variable_left': candidate})
        self.assertUnknown(bad['values'][key], 'conflicting candidate operand type')

    def test_pure_wrapping_checked_bitwise_and_unknown_modes(self):
        for typ, a, b, expected in [('uint8', 255, 1, 0), ('int8', 127, 1, -128), ('int8', -128, -1, 127)]:
            operands = [self.val(a, typ=typ), self.val(b, typ=typ)]
            v = d.pure_operation('+', operands, self.c, sources=self.refs, mode='WRAPPING')
            self.assertEqual([str(expected)], v['values'])
            self.assertUnknown(d.pure_operation('+', operands, self.c, sources=self.refs, mode='CHECKED'), 'overflow')
        self.assertEqual(['3'], d.pure_operation('+', [self.val(1), self.val(2)], self.c, sources=self.refs, mode='CHECKED')['values'])
        self.assertEqual(['2'], d.pure_operation('&', [self.val(3), self.val(2)], self.c, sources=self.refs)['values'])
        self.assertUnknown(d.pure_operation('+', [self.val(1), self.val(2)], self.c, sources=self.refs), 'arithmetic mode')
        self.assertUnknown(d.pure_operation('/', [self.val(1), self.val(2)], self.c, sources=self.refs, mode='WRAPPING'), 'unsupported')
        unknown = d.unknown('missing typed operand', sources=self.refs)
        self.assertUnknown(d.pure_operation('+', [d.bottom(), unknown], self.c, sources=self.refs, mode='WRAPPING'), 'missing typed operand')

    def test_alternative_phi_is_conservative_not_path_selection(self):
        for alternatives, tag in [([literal(1), literal(2)], 'FINITE'), ([literal(i) for i in range(5)], 'TOP'), ([literal(1), variable()], 'UNKNOWN')]:
            sfir, upstream, fact = fixture({'kind': 'Phi', 'lvalue': variable(), 'rvalues': alternatives})
            region = upstream['regions'][0]
            candidate = region['payload']['control_state_candidates'][0]
            result = d.local_transfer(d.make_state(region, self.c), region, candidate, fact, self.c)
            v = result['values'][d.candidate_key(region, candidate)]
            self.assertEqual(tag, v['tag'])
            self.assertTrue(v['source_refs'])
            if tag == 'FINITE':
                self.assertEqual(['1', '2'], v['values'])
            if tag == 'UNKNOWN':
                self.assertUnknown(v)

    def test_cast_identity_supported_other_cast_explicitly_unsupported(self):
        for typ, tag in [('uint8', 'FINITE'), ('int8', 'UNKNOWN')]:
            sfir, up, fact = fixture({'kind': 'TypeConversion', 'lvalue': variable(), 'variable': literal(1, typ)})
            r = up['regions'][0]
            c = r['payload']['control_state_candidates'][0]
            v = d.local_transfer(d.make_state(r, self.c), r, c, fact, self.c)['values'][d.candidate_key(r, c)]
            self.assertEqual(tag, v['tag'])
            if tag == 'UNKNOWN':
                self.assertUnknown(v, 'unsupported nonidentity cast')

    def test_unsupported_relevant_fact_and_unrelated_fact(self):
        fact = deepcopy(self.fact)
        fact['evidence']['slither']['kind'] = 'FutureOperation'
        self.assertUnknown(self.transfer(fact)['values'][self.key], 'unsupported')
        fact['semantic_id'] = 'business_variable'
        with self.assertRaises(ContractValidationError):
            self.transfer(fact)
        fact = deepcopy(self.fact)
        fact['fact_ssa']['writes'] = []
        self.assertUnknown(self.transfer(fact)['values'][self.key], 'write association')

    def test_unreached_state_strict_transfer_and_join_identity(self):
        dead = d.make_state(self.region, self.c, reached=False)
        self.assertEqual(dead, self.transfer(state=dead))
        known = self.transfer()
        self.assertEqual(known, d.join_states(dead, known, self.region, self.c))
        self.assertFalse(dead['reached'])
        self.assertEqual('BOTTOM', dead['values'][self.key]['tag'])

    def test_join_idempotence_commutativity_and_upper_bounds(self):
        vals = [d.bottom(), self.val(1), self.val(2, 3), self.val(0, 1, 2, 3, 4), d.unknown('missing type', sources=self.refs)]
        for a in vals:
            self.assertEqual(a, d.join_values(a, a, self.c))
        for a, b in product(vals, repeat=2):
            joined = d.join_values(a, b, self.c)
            self.assertEqual(joined, d.join_values(b, a, self.c))
            self.assertTrue(d.value_leq(a, joined, self.c))
            self.assertTrue(d.value_leq(b, joined, self.c))
        for a, b, c in product(vals[:4], repeat=3):
            self.assertEqual(d.join_values(d.join_values(a, b, self.c), c, self.c), d.join_values(a, d.join_values(b, c, self.c), self.c))

    def test_exhaustive_small_finite_join_soundness_and_cap(self):
        c = contract(cap=2)
        sets = [set(xs) for n in (1, 2) for xs in combinations(range(4), n)]
        for a, b in product(sets, repeat=2):
            joined = d.join_values(self.val(*a, c=c), self.val(*b, c=c), c)
            if len(a | b) <= 2:
                self.assertEqual(a | b, set(map(int, joined['values'])))
            else:
                self.assertEqual('TOP', joined['tag'])
            self.assertEqual('COMPLETE', joined['status']['completion'])
            self.assertTrue(joined['source_refs'])

    def test_unknown_top_and_type_mismatch_preserve_diagnosis(self):
        top = self.val(0, 1, 2, 3, 4)
        unknown = d.unknown('missing type', sources=self.refs)
        result = d.join_values(top, unknown, self.c)
        self.assertUnknown(result, 'missing type')
        self.assertEqual(unknown['status']['diagnostics'], result['status']['diagnostics'])
        self.assertUnknown(d.join_values(self.val(1), self.val(-1, typ='int8'), self.c), 'incompatible')

    def test_state_canonical_stabilization_subsumption_and_context(self):
        a = self.transfer()
        b = deepcopy(a)
        b['values'][self.key] = self.val(7, 8)
        # Use same provenance for the lattice comparison.
        a['values'][self.key] = self.val(7)
        ctx = d.empty_context(self.c)
        self.assertTrue(d.state_leq(a, ctx, b, ctx, self.region, self.c))
        self.assertFalse(d.state_leq(b, ctx, a, ctx, self.region, self.c))
        clone = dict(reversed(list(a.items())))
        self.assertTrue(d.stabilized(a, ctx, clone, deepcopy(ctx), self.region, self.c))
        other_ctx = d.update_context(ctx, self.region, self.region['payload']['cases'][0]['edge_ref'], self.c)
        self.assertFalse(d.stabilized(a, ctx, clone, other_ctx, self.region, self.c))
        self.assertFalse(d.state_leq(a, ctx, b, other_ctx, self.region, self.c))

    def test_value_and_state_unordered_evidence_normalization(self):
        a = deepcopy(self.state)
        a['values'][self.key] = self.val(1, 2)
        b = deepcopy(a)
        b['values'][self.key]['source_refs'].reverse()
        b['values'][self.key]['values'].reverse()
        self.assertEqual(a, d.normalize_state(b, self.region, self.c))
        ctx = d.empty_context(self.c)
        self.assertTrue(d.stabilized(a, ctx, b, ctx, self.region, self.c))
        self.assertEqual(a['values'][self.key], d.join_values(a['values'][self.key], b['values'][self.key], self.c))

    def test_invalid_known_value_mode_and_unknown_success_rejected(self):
        top = self.val(0, 1, 2, 3, 4)
        top['arithmetic_modes'] = ['guess']
        with self.assertRaises(ContractValidationError):
            d.validate_value(top, self.c)
        value = d.unknown('missing', sources=self.refs)
        value['status']['completion'] = 'COMPLETE'
        with self.assertRaises(ContractValidationError):
            d.validate_value(value, self.c)

    def test_context_k1_k2_truncation_and_no_iteration_counter(self):
        arms = [x['edge_ref'] for x in self.region['payload']['cases']]
        for k in (0, 1, 2):
            c = contract(k=k)
            ctx = d.empty_context(c)
            for arm in [arms[0], arms[1], arms[0]]:
                ctx = d.update_context(ctx, self.region, arm, c)
            self.assertEqual(k, len(ctx['elements']))
            self.assertEqual([arms[1], arms[0]][-k:] if k else [], [x['arm_ref'] for x in ctx['elements']])
            # 20 more updates are valid, k is history depth, not loop budget.
            for _ in range(20):
                ctx = d.update_context(ctx, self.region, arms[1], c)
            self.assertEqual(k, len(ctx['elements']))
        a = d.update_context(d.empty_context(self.c), self.region, arms[0], self.c)
        b = d.update_context(d.empty_context(self.c), self.region, arms[1], self.c)
        self.assertNotEqual(a, b)

    def test_context_collection_reorder_observed_only_no_successor_claim(self):
        clone = deepcopy(self.region)
        for key in ('cases', 'region_cfg_refs', 'control_state_candidates', 'entries', 'exits'):
            clone['payload'][key].reverse()
        arm = self.region['payload']['cases'][0]['edge_ref']
        ctx = d.empty_context(self.c)
        self.assertEqual(d.update_context(ctx, self.region, arm, self.c), d.update_context(ctx, clone, arm, self.c))
        elem = d.update_context(ctx, self.region, arm, self.c)['elements'][0]
        self.assertEqual({'region_ref', 'dispatcher_ref', 'arm_ref'}, set(elem))
        bogus = deepcopy(arm)
        bogus['locator']['edge_id'] = 'not-observed'
        with self.assertRaises(ContractValidationError):
            d.update_context(ctx, self.region, bogus, self.c)

    def test_direct_region_consumption_only_initial_unknown_and_immutable_payload(self):
        before = deepcopy(self.upstream)
        result = d.consume_regions(self.upstream, self.c)
        self.assertEqual(before, self.upstream)
        self.assertEqual('BOUND', result['outcome'])
        self.assertEqual('COMPLETE', result['status']['completion'])
        binding = result['bindings'][0]
        self.assertEqual(self.region['id'], binding['region_ref'])
        self.assertEqual(self.region['payload'], binding['structural_input'])
        self.assertEqual(REGION_PAYLOAD_FIELDS, set(binding['structural_input']))
        self.assertUnknown(binding['initial_state']['values'][self.key])
        self.assertEqual(self.upstream['action_input'], result['action_input'])
        self.assertEqual(self.upstream['evidence_records'], result['upstream_evidence_records'])

    def test_legal_no_region_distinct_from_failure(self):
        sfir = flattened_fixture()
        fn = sfir['functions'][0]
        fn['fact_cfg']['edges'] = [e for e in fn['fact_cfg']['edges'] if e['from'] == 'dispatch' or e['from'] == 'entry']
        _, upstream = run_detector(sfir)
        self.assertEqual([], upstream['regions'])
        result = d.consume_regions(upstream, self.c)
        self.assertEqual('NO_REGION', result['outcome'])
        self.assertEqual('COMPLETE', result['status']['completion'])
        self.assertEqual([], result['bindings'])
        self.assertEqual('UNKNOWN', result['status']['proof'])

    def test_upstream_ambiguous_no_exit_irreducible_insufficient_unsupported(self):
        inputs = [flattened_fixture(**kw) for kw in ({'ambiguous': True}, {'no_exit': True}, {'irreducible': True})]
        missing = flattened_fixture()
        missing['functions'][0]['semantic_nodes'] = [n for n in missing['functions'][0]['semantic_nodes'] if n['semantic_id'] != 'condition']
        inputs.append(missing)
        for sfir in inputs:
            _, up = run_detector(sfir)
            result = d.consume_regions(up, self.c)
            self.assertEqual('NO_CONSUMABLE_REGION', result['outcome'])
            self.assertEqual('PARTIAL', result['status']['completion'])
            self.assertEqual(up['candidate_diagnostics'], result['candidate_diagnostics'])
            for diag in up['status']['diagnostics']:
                self.assertIn(diag, result['status']['diagnostics'])
        actions, _ = run_detector(flattened_fixture())
        up = detect_flattened_regions({'schema': 'yul-object/v1'}, actions)
        result = d.consume_regions(up, self.c)
        self.assertEqual('PARTIAL', result['status']['completion'])
        self.assertIn('UNSUPPORTED', str(result['status']['diagnostics']))
        self.assertEqual([], result['bindings'])

    def test_insufficient_region_rejected_without_abstract_state(self):
        up = deepcopy(self.upstream)
        r = up['regions'][0]
        diag = diagnostic('INSUFFICIENT_EVIDENCE', 'missing state evidence', affected_refs=[r['id']])
        r['status'].update(proof='UNKNOWN', completion='PARTIAL', diagnostics=[diag])
        result = d.consume_regions(up, self.c)
        self.assertEqual([], result['bindings'])
        self.assertEqual([r['id']], result['rejected_region_refs'])
        self.assertIn(diag, result['status']['diagnostics'])
        self.assertEqual('PARTIAL', result['status']['completion'])

    def test_unresolved_evidence_and_candidate_diagnostic_rejected(self):
        up = deepcopy(self.upstream)
        up['evidence_records'] = []
        result = d.consume_regions(up, self.c)
        self.assertEqual([], result['bindings'])
        self.assertIn('unresolved upstream evidence', str(result['status']['diagnostics']))
        up = deepcopy(self.upstream)
        diag = diagnostic('UNSUPPORTED', 'candidate type source unavailable', affected_refs=self.refs)
        up['regions'][0]['payload']['control_state_candidates'][0]['status'].update(proof='UNKNOWN', completion='PARTIAL', diagnostics=[diag])
        result = d.consume_regions(up, self.c)
        self.assertEqual([], result['bindings'])
        self.assertIn(diag, result['status']['diagnostics'])

    def test_detector_collection_reorder_preserves_runtime_keys_and_context(self):
        shuffled = deepcopy(self.sfir)
        fn = shuffled['functions'][0]
        fn['semantic_nodes'].reverse()
        fn['fact_cfg']['blocks'].reverse()
        fn['fact_cfg']['edges'].reverse()
        _, up = run_detector(shuffled)
        r = up['regions'][0]
        candidate = r['payload']['control_state_candidates'][0]
        self.assertEqual(self.key, d.candidate_key(r, candidate))
        arm = self.region['payload']['cases'][0]['edge_ref']
        ctx = d.empty_context(self.c)
        self.assertEqual(d.update_context(ctx, self.region, arm, self.c), d.update_context(ctx, r, arm, self.c))
        self.assertEqual(d.make_state(self.region, self.c), d.make_state(r, self.c))

    def test_unanchored_unresolved_action_provenance_not_whitewashed(self):
        sfir = flattened_fixture(merged_action=True)
        sfir['functions'][0]['semantic_nodes'].extend([action_node('unanchored', None), node('future', 'fake', kind='FutureEffect', fact_role='effect')])
        _, up = run_detector(sfir)
        result = d.consume_regions(up, self.c)
        self.assertTrue(result['bindings'])
        self.assertEqual('PARTIAL', result['status']['completion'])
        self.assertEqual(up['action_input'], result['action_input'])
        self.assertEqual('PARTIAL', d.state_status(result['bindings'][0]['initial_state'])['completion'])
        for diag in up['status']['diagnostics']:
            self.assertIn(diag, result['status']['diagnostics'])

    def test_scope_config_errors_raise_not_empty_success(self):
        with self.assertRaises(ContractValidationError):
            d.validate_state(self.state, self.region, contract(cap=1))
        up = deepcopy(self.upstream)
        up['input_fingerprint'] = 'other'
        with self.assertRaises(ContractValidationError):
            d.consume_regions(up, self.c)
        state = deepcopy(self.state)
        state['values']['ordinary-business-variable'] = d.bottom()
        with self.assertRaises(ContractValidationError):
            d.validate_state(state, self.region, self.c)

    def test_isolation_shared_contract_no_future_artifacts_or_io(self):
        source = inspect.getsource(d)
        tree = ast.parse(source)
        imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertIn('s_seir_research_contracts', imports)
        self.assertNotIn('s_seir_research_actions', imports)
        self.assertFalse(any(isinstance(n, ast.While) for n in ast.walk(tree)))
        self.assertFalse(any(isinstance(n, ast.ClassDef) for n in ast.walk(tree)))
        calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertFalse({'open', 'eval', 'exec', 'detect_flattened_regions', 'extract_semantic_actions'}.intersection(calls))
        literals = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        schemas = {s for s in literals if s.startswith('erc20-research/')}
        self.assertEqual({d.SCHEMA}, schemas)
        for name in imports:
            self.assertFalse(any(x in (name or '').lower() for x in ('oracle', 'benchmark', 'evaluator', 'parser', 'z3', 'solver')))
        self.assertEqual(1, sum(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'artifact_envelope' for n in ast.walk(tree)))


if __name__ == '__main__':
    unittest.main(verbosity=2)
