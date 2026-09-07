#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / 'legacy_yul', ROOT / 's_seir'):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from s_seir_calldata_array_lifter import CalldataArrayLifter
from s_seir_model import EffectNode, FunctionUnit, SemanticOverlay, VariableInfo
from s_seir_overlay_builder import SemanticOverlayBuilder
from s_seir_semantic_fact_adapter import SSeirFactAdapter
from s_seir_type_env import TypeEnv


def function_unit() -> FunctionUnit:
    return FunctionUnit(
        function_id='Test.sum(address[])', contract='Test', function='sum',
        signature='sum(address[])', ast_node={},
        parameters=[VariableInfo('accounts', 'parameter', 'address[]', 'calldata')],
    )


def read_effect() -> EffectNode:
    return EffectNode('eff_read', 'ValueDef', ['asm_read'], {
        'targets': ['account'],
        'value': 'calldataload(add(accounts.offset, mul(i, 0x20)))',
        'cfg_node_id': 2,
        'language': 'yul',
    })


def control() -> dict:
    return {
        'blocks': [
            {'block_id': 'entry', 'stmts': [], 'attrs': {'node_id': 0}},
            {'block_id': 'header', 'stmts': ['asm_condition'], 'attrs': {'node_id': 1}},
            {'block_id': 'read', 'stmts': ['asm_read'], 'attrs': {'node_id': 2}},
            {'block_id': 'exit', 'stmts': [], 'attrs': {'node_id': 3}},
        ],
        'edges': [
            {'from': 'entry', 'to': 'header', 'kind': 'next'},
            {'from': 'header', 'to': 'read', 'kind': 'true: lt(i, accounts.length)'},
            {'from': 'header', 'to': 'exit', 'kind': 'loop exit'},
        ],
        'control_dependencies': [{
            'controller': 'header', 'dependent': 'read',
            'edge_kind': 'true: lt(i, accounts.length)',
        }],
    }


def predicate() -> SemanticOverlay:
    return SemanticOverlay('pred_bounds', 'Predicate', [], ['asm_condition'], {
        'predicate_id': 'pred_bounds',
        'expression': '(i < accounts.length)',
        'status': 'resolved',
        'semantic_anchor_cfg_node': 'header',
    })


def overlays_and_effects():
    effect = read_effect()
    overlays = SemanticOverlayBuilder().calldata_word_read_overlays(TypeEnv(function_unit()), [effect])
    assert {item.kind for item in overlays} == {'CalldataWordRead', 'CalldataArrayElementCandidate'}
    return overlays, [effect]


def test_cfg_proven_calldata_array_read_replaces_word_read() -> None:
    overlays, effects = overlays_and_effects()
    completed = CalldataArrayLifter().lift(TypeEnv(function_unit()), effects, overlays + [predicate()], control())
    kinds = [item.kind for item in completed]
    assert 'CalldataWordRead' not in kinds
    assert 'CalldataArrayElementCandidate' not in kinds
    high = next(item for item in completed if item.kind == 'CalldataArrayElementRead')
    assert high.attrs['access'] == 'accounts[i]'
    assert high.attrs['solidity_equivalent'] is True
    assert high.attrs['bounds_proof']['controller'] == 'header'

    fn = {
        'contract': 'Test', 'function': 'sum', 'signature': 'sum(address[])',
        'source_statements': [{'stmt_id': 'asm_read', 'lang': 'yul'}],
    }
    fact = SSeirFactAdapter().plain_overlay_fact(fn, high.to_dict(), {'asm_read': 'yul'}, {})
    assert fact is not None
    result = fact.to_dict()
    assert result['rvalue'] == 'accounts[i]'
    assert result['semantic']['operation'] == 'calldata_array_element_read'
    assert 'calldataload' not in str(result)


def test_missing_bounds_proof_keeps_conservative_word_read() -> None:
    overlays, effects = overlays_and_effects()
    completed = CalldataArrayLifter().lift(TypeEnv(function_unit()), effects, overlays, control())
    assert [item.kind for item in completed] == ['CalldataWordRead']


def test_cfg_bounds_edge_without_materialized_dependency_is_sufficient() -> None:
    overlays, effects = overlays_and_effects()
    cfg = control()
    cfg.pop('control_dependencies')
    completed = CalldataArrayLifter().lift(TypeEnv(function_unit()), effects, overlays + [predicate()], cfg)
    high = next(item for item in completed if item.kind == 'CalldataArrayElementRead')
    assert high.attrs['access'] == 'accounts[i]'


def test_index_redefinition_on_true_path_keeps_conservative_word_read() -> None:
    overlays, effects = overlays_and_effects()
    effects.append(EffectNode('eff_redefine', 'ValueDef', ['asm_change_i'], {
        'targets': ['i'], 'value': 'add(i, 1)', 'cfg_node_id': 4, 'language': 'yul',
    }))
    guarded = control()
    guarded['blocks'].insert(3, {
        'block_id': 'change_index', 'stmts': ['asm_change_i'], 'attrs': {'node_id': 4},
    })
    guarded['edges'] = [
        {'from': 'entry', 'to': 'header', 'kind': 'next'},
        {'from': 'header', 'to': 'change_index', 'kind': 'true: lt(i, accounts.length)'},
        {'from': 'change_index', 'to': 'read', 'kind': 'next'},
        {'from': 'header', 'to': 'exit', 'kind': 'loop exit'},
    ]
    guarded['control_dependencies'] = [{
        'controller': 'header', 'dependent': 'read',
        'edge_kind': 'true: lt(i, accounts.length)',
    }]
    completed = CalldataArrayLifter().lift(TypeEnv(function_unit()), effects, overlays + [predicate()], guarded)
    assert [item.kind for item in completed] == ['CalldataWordRead', 'Predicate']


if __name__ == '__main__':
    test_cfg_proven_calldata_array_read_replaces_word_read()
    test_missing_bounds_proof_keeps_conservative_word_read()
    test_cfg_bounds_edge_without_materialized_dependency_is_sufficient()
    test_index_redefinition_on_true_path_keeps_conservative_word_read()
    print('s-seir calldata array lifter tests: ok')
