#!/usr/bin/env python3
"""P1-T1 focused tests over real SemanticFactIRBridge representations."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import inspect
import json
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
for item in (ROOT / "legacy_yul", ROOT / "s_seir"):
    text = str(item)
    if text not in sys.path:
        sys.path.insert(0, text)

import s_seir_research_actions as actions_module
from s_seir_research_actions import (
    ACTION_KINDS,
    extract_semantic_actions,
    sfir_input_fingerprint,
    validate_semantic_action,
)
from s_seir_research_contracts import (
    ContractValidationError,
    analysis_status,
    canonical_json,
    serialize_artifact,
    stable_identity,
)
from s_seir_semantic_fact_ir import SemanticFactIRBridge
from assembly_ast_cfg import discover_solc
from s_seir_pipeline import build_sseir
from s_seir_semantic_fact_adapter import build_function_level_semantic_fact_ir_payload
from s_seir_yul_semantic_lifter import YulSemanticLifter


FUNCTION_ID = "Token.exercise(address,uint256)"
INPUT_FINGERPRINT = "sha256:" + "1" * 64


def block(block_id: str = "b0") -> dict:
    return {
        "block_id": block_id,
        "kind": "solidity",
        "stmts": [f"stmt_{block_id}"],
        "attrs": {},
        "terminator": {"kind": "Return"},
    }


def function(control: dict | None = None) -> SimpleNamespace:
    variable_names = (
        "owner", "spender", "amount", "target", "arg0", "callValue", "ok",
        "recipient", "transferValue", "eventArg", "returnValue", "reason",
    )
    return SimpleNamespace(
        function_id=FUNCTION_ID,
        contract="Token",
        function="exercise",
        signature="exercise(address,uint256)",
        control=control or {"blocks": [block()], "edges": []},
        _sseir_variable_bindings=[
            {
                "name": name,
                "kind": "parameter",
                "type_string": "uint256",
                "declaration_id": index + 1,
            }
            for index, name in enumerate(variable_names)
        ],
    )


def semantic_node(
    operation_id: str,
    kind: str,
    semantic: dict,
    *,
    reads: list | None = None,
    writes: list | None = None,
    lvalue=None,
    rvalue=None,
    condition: str | None = None,
    anchor: str | None = "b0",
    origin: str = "solidity_atomic_operation",
    fact_role: str = "effect",
    span: str | None = None,
    provenance: dict | None = None,
) -> dict:
    result = {
        "operation_id": operation_id,
        "origin": origin,
        "kind": kind,
        "fact_role": fact_role,
        "reads": list(reads or []),
        "writes": list(writes or []),
        "stmt_refs": [f"stmt_{operation_id}"],
        "order": {"operation_order": int(operation_id.rsplit("_", 1)[-1]) if operation_id.rsplit("_", 1)[-1].isdigit() else 1},
        "semantic": semantic,
        "evidence": {
            "atomic_operation": {
                "atom_id": operation_id,
                "source_span": span or f"fixture.sol:{operation_id}",
                "slithir_kind": kind,
            }
        },
        "semantic_provenance": provenance or ({
            "anchor_cfg_node": anchor,
            "evidence_cfg_nodes": [anchor],
        } if anchor else {}),
    }
    if lvalue is not None:
        result["lvalue"] = lvalue
    if rvalue is not None:
        result["rvalue"] = rvalue
    if condition is not None:
        result["condition"] = condition
    return result


def build_sfir(
    solidity_nodes: list[dict],
    yul_nodes: list[dict] | None = None,
    *,
    diagnostics: list[dict] | None = None,
) -> dict:
    result = SemanticFactIRBridge().build_function(function(), solidity_nodes, yul_nodes or [])
    if diagnostics:
        result["diagnostics"].extend(diagnostics)
    return {
        "schema": "s-seir-semantic-fact-ir/v1",
        "source": "fixture.sol",
        "function_count": 1,
        "semantic_node_count": len(result["semantic_nodes"]),
        "functions": [result],
    }


def full_action_nodes() -> list[dict]:
    return [
        semantic_node(
            "op_1", "StateWrite",
            {
                "operation": "state_write",
                "location": {
                    "kind": "mapping", "access": "balances[owner]",
                    "state_variable": "balances", "keys": ["owner"],
                },
                "keys": ["owner"], "value": "amount",
            },
            reads=["owner", "amount"], writes=["balances[owner]"],
            lvalue="balances[owner]", rvalue="amount",
        ),
        semantic_node(
            "op_2", "ExternalCall",
            {
                "operation": "external_call", "call_kind": "call",
                "target": "target", "arguments": ["arg0"], "call_value": "callValue",
                "call_status_result": "ok",
            },
            reads=["target", "arg0", "callValue"], writes=["ok"], lvalue="ok",
        ),
        semantic_node(
            "op_3", "ValueTransferCall",
            {"operation": "value_transfer_call", "target": "recipient", "value": "transferValue", "method": "transfer"},
            reads=["recipient", "transferValue"],
        ),
        semantic_node(
            "op_4", "EventEmit",
            {"operation": "event_emit", "event": "Transfer", "arguments": ["owner", "recipient", "eventArg"]},
            reads=["owner", "recipient", "eventArg"],
        ),
        semantic_node(
            "op_5", "Revert",
            {"operation": "revert", "payload": "reason", "arguments": ["reason"], "guard": "amount == 0"},
            reads=["reason"], condition="amount == 0",
        ),
        semantic_node(
            "op_6", "Return",
            {"operation": "return", "values": ["returnValue"]},
            reads=["returnValue"], rvalue="returnValue",
        ),
    ]


def by_kind(result: dict, kind: str) -> list[dict]:
    return [item for item in result["actions"] if item["payload"]["kind"] == kind]


def operand(action: dict, role: str) -> dict:
    return next(item for item in action["payload"]["operand_refs"] if item["role"] == role)


class SemanticActionMappingTests(unittest.TestCase):
    def test_real_solidity_frontend_produces_all_six_actions(self) -> None:
        source_text = """// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;
contract ActionSurface {
    mapping(address => uint256) public balances;
    event Changed(address indexed owner, uint256 value);
    function exercise(address payable target, uint256 amount, bool failNow)
        external payable returns (bool)
    {
        balances[msg.sender] = amount;
        (bool ok,) = target.call{value: amount}("");
        target.transfer(amount);
        emit Changed(msg.sender, amount);
        if (failNow) revert("requested");
        return ok;
    }
}
"""
        solc = discover_solc(None)
        self.assertTrue(solc, "solc is required for the real frontend P1-T1 test")
        with tempfile.TemporaryDirectory(prefix="p1_t1_action_surface_") as directory:
            source = Path(directory) / "ActionSurface.sol"
            source.write_text(source_text, encoding="utf-8")
            functions = build_sseir(source, solc_bin=solc, workdir=Path(directory), branch_preprocess=False)
            sfir = build_function_level_semantic_fact_ir_payload(functions, source=str(source))
        result = extract_semantic_actions(sfir, input_fingerprint=INPUT_FINGERPRINT)
        function_actions = [
            item for item in result["actions"]
            if str(item["function_ref"]["canonical_signature"]).startswith("exercise(")
        ]
        self.assertEqual(ACTION_KINDS, {item["payload"]["kind"] for item in function_actions})
        external = [item for item in function_actions if item["payload"]["kind"] == "ExternalCall"]
        transfers = [item for item in function_actions if item["payload"]["kind"] == "EtherTransfer"]
        self.assertEqual(1, len(external))
        self.assertEqual(1, len(transfers))
        self.assertEqual("amount_1", operand(external[0], "value")["value"])

    def test_all_six_frozen_actions_and_shared_contract(self) -> None:
        result = extract_semantic_actions(build_sfir(full_action_nodes()), input_fingerprint=INPUT_FINGERPRINT)
        self.assertEqual(ACTION_KINDS, {item["payload"]["kind"] for item in result["actions"]})
        self.assertEqual(6, len(result["actions"]))
        self.assertTrue(all(item["status"]["solver"] == "NOT_RUN" for item in result["actions"]))
        self.assertTrue(all(item["schema"] == "erc20-research/semantic-action/v1" for item in result["actions"]))
        for item in result["actions"]:
            validate_semantic_action(item)
            self.assertEqual(
                {"kind", "semantic_ref", "operand_refs", "control_anchor", "replacement_refs"},
                set(item["payload"]),
            )
            self.assertTrue(item["evidence_refs"])
            self.assertEqual("CFG_BLOCK", item["payload"]["control_anchor"]["source_kind"])
        self.assertEqual({"EMITTED"}, {item["classification"] for item in result["coverage"]})

    def test_m2_ready_operands_and_storage_refs_survive_projection(self) -> None:
        result = extract_semantic_actions(build_sfir(full_action_nodes()), input_fingerprint=INPUT_FINGERPRINT)
        storage = by_kind(result, "StorageWrite")[0]
        self.assertEqual("balances[owner]", operand(storage, "storage_location")["value"]["access"])
        self.assertEqual(["owner"], operand(storage, "storage_keys")["value"])
        self.assertEqual("amount", operand(storage, "update_value")["value"])
        self.assertEqual("STORAGE_ACCESS", operand(storage, "storage_location")["source_ref"]["source_kind"])

        call = by_kind(result, "ExternalCall")[0]
        self.assertEqual("target", operand(call, "target")["value"])
        self.assertEqual(["arg0"], operand(call, "arguments")["value"])
        self.assertEqual("callValue", operand(call, "value")["value"])
        self.assertEqual("ok", operand(call, "result")["value"])
        self.assertTrue(operand(call, "arguments").get("fact_ssa_refs"))

        emitted = by_kind(result, "Emit")[0]
        self.assertEqual(["owner", "recipient", "eventArg"], operand(emitted, "event_arguments")["value"])
        returned = by_kind(result, "Return")[0]
        self.assertEqual(["returnValue"], operand(returned, "return_values")["value"])
        reverted = by_kind(result, "Revert")[0]
        self.assertEqual("amount == 0", operand(reverted, "failure_condition_source")["value"])
        # No Definition/Use, RD, ValueFlow or dependency conclusion exists in P1-T1.
        serialized = canonical_json(result["actions"])
        for forbidden in ("reaching_definitions", "value_flow", "state_dependency", "semantic_slice"):
            self.assertNotIn(forbidden, serialized)

    def test_solidity_yul_and_mixed_use_one_contract(self) -> None:
        solidity = full_action_nodes()[0]
        yul = semantic_node(
            "yul_2", "StateWrite",
            {
                "operation": "state_write",
                "location": {"kind": "mapping", "access": "balances[owner]", "state_variable": "balances", "keys": ["owner"]},
                "keys": ["owner"], "value": "amount",
            },
            reads=["owner", "amount"], writes=["balances[owner]"],
            lvalue="balances[owner]", rvalue="amount", origin="yul_sseir_semantic_overlay",
            provenance={
                "semantic_source": {"overlay_id": "ov_write", "overlay_kind": "MappingWrite"},
                "anchor_cfg_node": "b0", "evidence_cfg_nodes": ["b0"],
            },
        )
        result = extract_semantic_actions(build_sfir([solidity], [yul]), input_fingerprint=INPUT_FINGERPRINT)
        writes = by_kind(result, "StorageWrite")
        self.assertEqual(2, len(writes))
        self.assertEqual({"solidity", "yul"}, {item["extensions"]["source_representation"] for item in writes})
        self.assertEqual({"StorageWrite"}, {item["payload"]["kind"] for item in writes})

    def test_call_with_value_is_one_external_call_not_ether_transfer(self) -> None:
        node = full_action_nodes()[1]
        result = extract_semantic_actions(build_sfir([node]), input_fingerprint=INPUT_FINGERPRINT)
        self.assertEqual(1, len(by_kind(result, "ExternalCall")))
        self.assertFalse(by_kind(result, "EtherTransfer"))
        self.assertEqual("callValue", operand(result["actions"][0], "value")["value"])


class IdentityTests(unittest.TestCase):
    def test_repeatability_and_canonical_serialization(self) -> None:
        sfir = build_sfir(full_action_nodes())
        first = extract_semantic_actions(sfir)
        second = extract_semantic_actions(deepcopy(sfir))
        self.assertEqual([item["id"] for item in first["actions"]], [item["id"] for item in second["actions"]])
        self.assertEqual(serialize_artifact(first), serialize_artifact(second))
        self.assertEqual(sfir_input_fingerprint(sfir), sfir_input_fingerprint(deepcopy(sfir)))

    def test_traversal_reorder_does_not_change_identity(self) -> None:
        sfir = build_sfir(full_action_nodes())
        reordered = deepcopy(sfir)
        reordered["functions"][0]["semantic_nodes"].reverse()
        sfir["source"] = "/machine-a/work/fixture.sol"
        reordered["source"] = "/machine-b/checkout/fixture.sol"
        first = extract_semantic_actions(sfir)
        second = extract_semantic_actions(reordered)
        self.assertEqual(first["input_fingerprint"], second["input_fingerprint"])
        self.assertEqual([item["id"] for item in first["actions"]], [item["id"] for item in second["actions"]])

    def test_identical_payload_distinct_occurrences_are_not_deduplicated(self) -> None:
        first = semantic_node(
            "emit_1", "EventEmit", {"operation": "event_emit", "event": "Transfer", "arguments": ["owner", "recipient", "eventArg"]},
            reads=["owner", "recipient", "eventArg"], span="fixture.sol:100:10",
        )
        second = semantic_node(
            "emit_2", "EventEmit", {"operation": "event_emit", "event": "Transfer", "arguments": ["owner", "recipient", "eventArg"]},
            reads=["owner", "recipient", "eventArg"], span="fixture.sol:120:10",
        )
        result = extract_semantic_actions(build_sfir([first, second]), input_fingerprint=INPUT_FINGERPRINT)
        emitted = by_kind(result, "Emit")
        self.assertEqual(2, len(emitted))
        self.assertEqual(2, len({item["id"] for item in emitted}))

    def test_missing_stable_provenance_is_run_local_not_fake_stable(self) -> None:
        node = semantic_node("local_1", "Return", {"operation": "return", "values": []})
        node["stmt_refs"] = []
        node["evidence"] = {}
        node["operation_id"] = ""
        result = extract_semantic_actions(build_sfir([node]), input_fingerprint=INPUT_FINGERPRINT)
        action = result["actions"][0]
        self.assertEqual("RUN_LOCAL", action["extensions"]["identity_scope"])
        self.assertIn("INSUFFICIENT_EVIDENCE", {item["code"] for item in action["status"]["diagnostics"]})


class ReplacementAndFailureTests(unittest.TestCase):
    def test_merged_recovery_representation_is_evidence_only(self) -> None:
        canonical = semantic_node(
            "emit_1", "EventEmit", {"operation": "event_emit", "event": "Transfer", "arguments": ["owner", "recipient", "eventArg"]},
            reads=["owner", "recipient", "eventArg"], origin="yul_sseir_semantic_overlay",
            provenance={
                "semantic_source": {"overlay_id": "canonical_event", "overlay_kind": "PathConditionedEventEmit"},
                "anchor_cfg_node": "b0", "evidence_cfg_nodes": ["b0"],
            },
        )
        duplicate = deepcopy(canonical)
        duplicate["semantic_provenance"]["semantic_source"] = {
            "overlay_id": "raw_log_representation", "overlay_kind": "EventEmit"
        }
        # Exercise the same provenance merge helper used by current SFIR Yul
        # canonicalization; only the canonical node crosses the bridge.
        YulSemanticLifter._merge_semantic_sources(canonical, duplicate)
        result = extract_semantic_actions(build_sfir([], [canonical]), input_fingerprint=INPUT_FINGERPRINT)
        self.assertEqual(1, len(by_kind(result, "Emit")))
        self.assertEqual(1, len(result["actions"][0]["payload"]["replacement_refs"]))
        self.assertEqual(["EMITTED", "REPLACED"], sorted(item["classification"] for item in result["coverage"]))

    def test_require_and_assert_project_failure_without_solver(self) -> None:
        require = semantic_node(
            "req_1", "Require", {"operation": "require", "guard": "amount > 0", "on_fail": "revert"},
            reads=["amount"], condition="amount > 0", fact_role="control",
        )
        assertion = semantic_node(
            "assert_2", "Assert", {"operation": "assert", "guard": "amount < 100"},
            reads=["amount"], condition="amount < 100", fact_role="control",
        )
        result = extract_semantic_actions(build_sfir([require, assertion]), input_fingerprint=INPUT_FINGERPRINT)
        self.assertEqual(2, len(by_kind(result, "Revert")))
        self.assertTrue(all(item["extensions"]["projection"] == "require_assert_failure" for item in result["actions"]))
        self.assertTrue(all(item["status"]["solver"] == "NOT_RUN" for item in result["actions"]))
        self.assertEqual({"amount > 0", "amount < 100"}, {
            operand(item, "failure_condition_source")["value"] for item in result["actions"]
        })

    def test_require_without_failure_evidence_is_unresolved(self) -> None:
        require = semantic_node(
            "req_1", "Require", {"operation": "require"},
            reads=["amount"], anchor=None, origin="yul_sseir_semantic_overlay", fact_role="control",
        )
        result = extract_semantic_actions(build_sfir([], [require]), input_fingerprint=INPUT_FINGERPRINT)
        self.assertFalse(result["actions"])
        self.assertEqual("UNRESOLVED", result["coverage"][0]["classification"])
        self.assertEqual("INSUFFICIENT_FAILURE_EVIDENCE", result["coverage"][0]["reason_code"])
        self.assertEqual("NOT_RUN", result["coverage"][0]["status"]["solver"])

    def test_require_failure_and_canonical_revert_are_not_double_counted(self) -> None:
        control = {
            "blocks": [block("guard"), block("failure")],
            "edges": [{"from": "guard", "to": "failure", "kind": "false"}],
        }
        require = semantic_node(
            "req_1", "Require", {"operation": "require", "guard": "amount > 0", "on_fail": "revert"},
            reads=["amount"], condition="amount > 0", anchor="guard", fact_role="control",
            provenance={
                "semantic_source": {"overlay_id": "require", "overlay_kind": "RequireOverlay"},
                "anchor_cfg_node": "guard", "require_failure_cfg_node": "failure",
                "evidence_cfg_nodes": ["guard", "failure"],
            },
        )
        revert = semantic_node(
            "rev_2", "Revert", {"operation": "revert", "arguments": []},
            anchor="failure",
        )
        fn = function(control)
        built = SemanticFactIRBridge().build_function(fn, [require, revert], [])
        payload = {"schema": "s-seir-semantic-fact-ir/v1", "functions": [built]}
        result = extract_semantic_actions(payload, input_fingerprint=INPUT_FINGERPRINT)
        self.assertEqual(1, len(by_kind(result, "Revert")))
        self.assertIn("REPLACED", {item["classification"] for item in result["coverage"]})


class UnknownCoverageAndIsolationTests(unittest.TestCase):
    def test_unknown_operands_and_unanchored_action_are_emitted_with_diagnostics(self) -> None:
        unknown_call = semantic_node(
            "call_1", "ExternalCall", {"operation": "external_call", "arguments": []}, anchor=None,
        )
        result = extract_semantic_actions(build_sfir([unknown_call]), input_fingerprint=INPUT_FINGERPRINT)
        self.assertEqual(1, len(result["actions"]))
        self.assertEqual("EMITTED", result["coverage"][0]["classification"])
        action = result["actions"][0]
        self.assertEqual("UNKNOWN", action["payload"]["control_anchor"]["state"])
        codes = {item["code"] for item in action["status"]["diagnostics"]}
        self.assertIn("UNKNOWN", codes)
        self.assertIn("INSUFFICIENT_EVIDENCE", codes)
        self.assertEqual("PARTIAL", action["status"]["completion"])

    def test_unrecognized_effect_like_occurrence_is_unresolved_not_dropped(self) -> None:
        node = semantic_node("mystery_1", "FutureEffect", {"operation": "future_effect"})
        result = extract_semantic_actions(build_sfir([node]), input_fingerprint=INPUT_FINGERPRINT)
        self.assertFalse(result["actions"])
        self.assertEqual(1, len(result["coverage"]))
        self.assertEqual("UNRESOLVED", result["coverage"][0]["classification"])
        self.assertEqual("UNSUPPORTED_ACTION_KIND", result["coverage"][0]["reason_code"])

    def test_state_read_is_preserved_by_sfir_but_not_an_action(self) -> None:
        node = semantic_node(
            "read_1", "StateRead",
            {"operation": "state_read", "location": {"kind": "mapping", "access": "balances[owner]", "keys": ["owner"]}},
            reads=["balances[owner]"], lvalue="amount", rvalue="balances[owner]",
        )
        result = extract_semantic_actions(build_sfir([node]), input_fingerprint=INPUT_FINGERPRINT)
        self.assertEqual([], result["actions"])
        self.assertEqual([], result["coverage"])
        self.assertEqual("COMPLETE", result["status"]["completion"])

    def test_unsupported_representation_and_upstream_unknown_are_structured(self) -> None:
        unsupported = extract_semantic_actions({"schema": "standalone-yul-object/v1", "functions": []})
        self.assertEqual("PARTIAL", unsupported["status"]["completion"])
        self.assertEqual("UNSUPPORTED", unsupported["status"]["diagnostics"][0]["code"])

        node = full_action_nodes()[0]
        sfir = build_sfir([node])
        semantic_id = sfir["functions"][0]["semantic_nodes"][0]["semantic_id"]
        sfir["functions"][0]["diagnostics"].append({
            "kind": "opaque_storage_operand", "semantic_id": semantic_id, "reason": "opaque key",
        })
        result = extract_semantic_actions(sfir, input_fingerprint=INPUT_FINGERPRINT)
        self.assertEqual("OPAQUE", result["actions"][0]["status"]["diagnostics"][0]["code"])
        self.assertEqual("OPAQUE", result["upstream_diagnostics"][-1]["mapped_code"])

    def test_no_oracle_evaluator_or_source_parser_dependency(self) -> None:
        source = inspect.getsource(actions_module)
        import_lines = [line.strip() for line in source.splitlines() if line.strip().startswith(("import ", "from "))]
        joined = "\n".join(import_lines).lower()
        for forbidden in ("benchmark", "evaluator", "oracle", "solidity_parser", "yul_parser"):
            self.assertNotIn(forbidden, joined)
        signature = inspect.signature(actions_module.extract_semantic_actions)
        self.assertEqual({"sfir_payload", "input_fingerprint", "config"}, set(signature.parameters))

    def test_shared_contract_rejects_invalid_status_and_serializes_large_int(self) -> None:
        with self.assertRaises(ContractValidationError):
            analysis_status(proof="SUCCESS", completion="COMPLETE")
        value = {"word": 2**255, "flag": True, "empty": None}
        encoded = canonical_json(value)
        self.assertIn(f'"word":"{2**255}"', encoded)
        self.assertEqual(encoded, serialize_artifact(value))
        self.assertEqual(stable_identity("x", value), stable_identity("x", json.loads(encoded)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
