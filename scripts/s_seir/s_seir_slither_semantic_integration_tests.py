#!/usr/bin/env python3
"""Regression for Slither alias transport into final SFIR."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT / "scripts" / "legacy_yul", ROOT / "scripts" / "s_seir"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from assembly_ast_cfg import discover_solc
from s_seir_pipeline import build_sseir
from s_seir_semantic_fact_adapter import build_function_level_semantic_fact_ir_payload
from s_seir_semantic_fact_ir import SemanticFactIRBridge
from s_seir_semantic_fact_render import render_fact_cfg_text, render_sfir_c_like, render_sfir_node_c_like
from s_seir_solidity_atomic_ops import SolidityAtomicOperationExtractor
from s_seir_solidity_semantic_lifter import SoliditySemanticLifter


SOURCE = ROOT / "tests" / "fixtures" / "solidity_complex_sfir.sol"
SURFACE_SOURCE = ROOT / "tests" / "fixtures" / "slither_operation_surface.sol"
CONTROL_SOURCE = ROOT / "tests" / "fixtures" / "slither_control_surface.sol"
BUILTIN_SOURCE = ROOT / "tests" / "fixtures" / "slither_builtin_surface.sol"
INHERITANCE_SOURCE = ROOT / "tests" / "fixtures" / "slither_inheritance_surface.sol"


def run() -> None:
    solc = discover_solc(None)
    assert solc, "solc is required"
    with tempfile.TemporaryDirectory(prefix="sseir_slither_semantic_") as directory:
        functions = build_sseir(SOURCE, solc_bin=solc, workdir=Path(directory), branch_preprocess=False)
        surface_functions = build_sseir(SURFACE_SOURCE, solc_bin=solc, workdir=Path(directory), branch_preprocess=False)
        control_functions = build_sseir(CONTROL_SOURCE, solc_bin=solc, workdir=Path(directory), branch_preprocess=False)
        builtin_functions = build_sseir(BUILTIN_SOURCE, solc_bin=solc, workdir=Path(directory), branch_preprocess=False)
        inheritance_functions = build_sseir(INHERITANCE_SOURCE, solc_bin=solc, workdir=Path(directory), branch_preprocess=False)

    settle = next(item for item in functions if item.function == "settle")
    table = getattr(settle, "_sseir_solidity_atomic_operations")
    alias = next(item for item in table["operations"] if item.get("source_expression") == "sender = accounts[msg.sender]")
    debit = next(item for item in table["operations"] if item.get("source_expression") == "sender.balance -= actual")

    assert alias["atomic_kind"] == "ValueAssign"
    assert alias["lvalue"]["is_storage"] is True
    assert alias["lvalue"]["refers_to"][0]["name"] == "accounts"
    assert alias["storage_alias"]["location"]["access"] == "accounts[msg.sender]"
    assert debit["atomic_kind"] == "StateWrite"
    assert debit["storage_access"]["access"] == "accounts[msg.sender].balance"
    assert debit["lvalue"]["points_to"]["refers_to"][0]["name"] == "accounts"

    payload = build_function_level_semantic_fact_ir_payload(functions)
    sfir = next(item for item in payload["functions"] if item["function"] == "settle")
    debit_node = next(
        item for item in sfir["semantic_nodes"]
        if item.get("kind") == "StateWrite" and item.get("lvalue") == "accounts[msg.sender].balance"
    )
    assert debit_node["semantic"]["location"]["access"] == "accounts[msg.sender].balance"
    assert debit_node["fact_ssa"]["writes"][0]["binding_id"].endswith(
        "storage_location:accounts[msg.sender].balance"
    )
    quote = next(
        item for item in sfir["semantic_nodes"]
        if item.get("kind") == "ExternalCall" and item.get("semantic", {}).get("function") == "quote"
    )
    assert quote["semantic"]["target"] == "oracle_1"
    assert quote["semantic"]["function_signature"] == "quote(address,uint256)"
    assert quote["semantic"]["arguments"] == ["recipient_1", "actual_3"]

    surface_payload = build_function_level_semantic_fact_ir_payload(surface_functions)
    exercise = next(item for item in surface_payload["functions"] if item["function"] == "exercise")
    nodes = exercise["semantic_nodes"]
    new_array = next(item for item in nodes if item.get("kind") == "NewArray")
    structure = next(item for item in nodes if item.get("kind") == "NewStructure")
    named_call = next(item for item in nodes if item.get("kind") == "ExternalCall" and item.get("semantic", {}).get("function") == "echo")
    low_level = next(item for item in nodes if item.get("kind") == "LowLevelCall")
    unpack = next(item for item in nodes if item.get("kind") == "TupleUnpack")
    creation = next(item for item in nodes if item.get("kind") == "NewContract")
    assert new_array["semantic"]["array_type"] == "uint256[]"
    assert new_array["semantic"]["length"] == "2"
    assert structure["semantic"]["structure"] == "Pair"
    assert structure["semantic"]["argument_names"] == ["left", "right"]
    assert named_call["semantic"]["argument_names"] == ["value", "recipient"]
    assert low_level["semantic"]["call_kind"] == "call"
    assert low_level["semantic"]["call_value"] == "0"
    assert unpack["semantic"]["tuple"] == low_level["lvalue"]
    assert unpack["semantic"]["index"] == 0
    assert creation["semantic"]["contract"] == "SurfaceChild"
    assert creation["semantic"]["salt"] == "salt_1"
    rendered = render_sfir_c_like(surface_payload)
    assert "new uint256[](2)" in rendered
    assert "Pair({left: REF_3, right: REF_4})" in rendered
    assert "lowLevelTarget_1.call{value: 0}(TMP_6)" in rendered
    assert "new SurfaceChild{value: 0, salt: salt_1}(echoed_1)" in rendered
    assert "return (result_1, lowLevelOk_1);" in rendered

    control_payload = build_function_level_semantic_fact_ir_payload(control_functions)
    dispatch = next(item for item in control_payload["functions"] if item["function"] == "dynamicDispatch")
    dynamic_call = next(item for item in dispatch["semantic_nodes"] if item.get("kind") == "InternalDynamicCall")
    # The source-level callable is ``selected``; the current FactSSA version
    # is deliberately retained independently in the read relation.
    assert dynamic_call["semantic"]["function"] == "selected"
    assert "selected_3" in dynamic_call["reads"]
    assert dynamic_call["semantic"]["function_type"] == "function(uint256) returns(uint256)"
    assert dynamic_call["semantic"]["dynamic_function_ssa"] == "selected_3"
    assert dynamic_call["semantic"].get("target") in {None, ""}
    dispatch_write = next(item for item in dispatch["semantic_nodes"] if item.get("kind") == "StateWrite")
    assert dispatch_write["semantic"]["value"] == "result_1"

    recover = next(item for item in control_payload["functions"] if item["function"] == "recover")
    recover_blocks = recover["fact_cfg"]["blocks"]
    try_block = next(item for item in recover_blocks if (item.get("terminator") or {}).get("kind") == "Try")
    catch_roles = {
        (item.get("terminator") or {}).get("catch_role")
        for item in recover_blocks if (item.get("terminator") or {}).get("kind") == "Catch"
    }
    try_edges = [item for item in recover["fact_cfg"]["edges"] if item.get("from") == try_block["block_id"]]
    assert {item["kind"] for item in try_edges} == {"try_success", "catch:Error", "catch:bytes"}
    assert catch_roles == {"try_success", "catch:Error", "catch:bytes"}
    success_write = next(
        item for item in recover["semantic_nodes"]
        if item.get("kind") == "StateWrite" and item.get("semantic", {}).get("value") == "received_1"
    )
    assert success_write["semantic"]["access"] == "total"
    control_rendered = render_sfir_c_like(control_payload)
    assert "try { goto" in control_rendered
    assert "catch (Error) { goto" in control_rendered
    assert "catch (bytes) { goto" in control_rendered
    assert "total = received_1;" in control_rendered
    assert "total = selected(value);" not in control_rendered

    guarded = next(item for item in control_payload["functions"] if item["function"] == "guarded")
    modifier = next(item for item in control_payload["functions"] if item["function"] == "aboveZero")
    modifier_apply = next(item for item in guarded["semantic_nodes"] if item.get("kind") == "ModifierApply")
    assert not any(item.get("kind") == "InternalCall" for item in guarded["semantic_nodes"])
    assert guarded["declaration"]["declaration_kind"] == "function"
    assert guarded["declaration"]["applied_modifiers"][0]["canonical_name"] == "SlitherControlSurface.aboveZero(uint256)"
    assert modifier["declaration"]["declaration_kind"] == "modifier"
    assert modifier_apply["semantic"]["modifier"] == "aboveZero"
    assert modifier_apply["semantic"]["modifier_function_id"] == "SlitherControlSurface.aboveZero(uint256)"
    assert "placeholder resumes_wrapped_function_body" in modifier_apply["semantic"]["execution_model"]
    assert any(
        (block.get("terminator") or {}).get("kind") == "ModifierPlaceholder"
        for block in modifier["fact_cfg"]["blocks"]
    )
    assert "modifier aboveZero(uint256 value)" in control_rendered
    assert "function guarded(uint256 value) external returns (uint256)" in control_rendered
    assert "apply modifier aboveZero(value_1);" in control_rendered
    assert "modifier placeholder: resume wrapped function body" in control_rendered
    modifier_links = control_payload["modifier_application_links"]
    assert len(modifier_links) == 1
    modifier_link = modifier_links[0]
    assert modifier_link["resolution"] == "resolved"
    assert modifier_link["caller_function_id"] == guarded["function_id"]
    assert modifier_link["modifier_apply_semantic_id"] == modifier_apply["semantic_id"]
    assert modifier_link["modifier_function_id"] == modifier["function_id"]
    assert len(modifier_link["modifier_placeholder_blocks"]) == 1
    assert modifier_link["caller_continuation"]["following_semantic_ids"]
    cfg_text = render_fact_cfg_text(control_payload)
    assert "Modifier application links" in cfg_text
    assert modifier_link["modifier_function_id"] in cfg_text

    direct = next(item for item in control_payload["functions"] if item["function"] == "directInternal")
    internal_call = next(item for item in direct["semantic_nodes"] if item.get("kind") == "InternalCall")
    internal_link = next(
        item for item in control_payload["direct_call_links"]
        if item["call_semantic_id"] == internal_call["semantic_id"]
    )
    add_one = next(item for item in control_payload["functions"] if item["function"] == "addOne")
    assert internal_link["kind"] == "internal_call"
    assert internal_link["resolution"] == "resolved"
    assert internal_link["callee_function_id"] == add_one["function_id"]

    library_link = next(
        item for item in surface_payload["direct_call_links"]
        if item["kind"] == "library_call"
    )
    assert library_link["resolution"] == "resolved"
    external_declaration_link = next(
        item for item in surface_payload["direct_call_links"]
        if item["kind"] == "external_call_declaration" and item["callee_canonical_name"] == "INamedTarget.echo(uint256,address)"
    )
    assert external_declaration_link["resolution"] == "resolved"
    assert "Direct call declaration links" in render_fact_cfg_text(surface_payload)

    dynamic_link = next(
        item for item in control_payload["dynamic_call_links"]
        if item["call_semantic_id"] == dynamic_call["semantic_id"]
    )
    assert dynamic_link["resolution"] == "resolved_complete"
    assert dynamic_link["function_ssa"] == "selected_3"
    assert dynamic_link["candidate_canonical_names"] == [
        "SlitherControlSurface.addOne(uint256)",
        "SlitherControlSurface.addTwo(uint256)",
    ]
    assert dynamic_link["callee_function_ids"] == [
        "SlitherControlSurface.addOne(uint256)",
        "SlitherControlSurface.addTwo(uint256)",
    ]
    assert "Dynamic call candidate links" in render_fact_cfg_text(control_payload)

    loop = next(item for item in control_payload["functions"] if item["function"] == "loopControl")
    loop_terminators = {
        (block.get("terminator") or {}).get("kind")
        for block in loop["fact_cfg"]["blocks"]
    }
    loop_edge_kinds = {edge["kind"] for edge in loop["fact_cfg"]["edges"]}
    assert {"Break", "Continue"} <= loop_terminators
    assert {"break", "continue"} <= loop_edge_kinds
    assert "break;" in control_rendered
    assert "continue;" in control_rendered

    builtin_payload = build_function_level_semantic_fact_ir_payload(builtin_functions)
    codecs = next(item for item in builtin_payload["functions"] if item["function"] == "codecs")
    builtin_nodes = {item["kind"]: item for item in codecs["semantic_nodes"]}
    assert builtin_nodes["AbiEncode"]["semantic"]["builtin_signature"] == "abi.encodePacked()"
    assert builtin_nodes["AbiDecode"]["semantic"]["builtin_signature"] == "abi.decode()"
    assert builtin_nodes["HashCompute"]["semantic"]["builtin_signature"] == "keccak256(bytes)"
    assert builtin_nodes["ModularArithmetic"]["semantic"]["builtin_signature"] == "addmod(uint256,uint256,uint256)"
    guards = next(item for item in builtin_payload["functions"] if item["function"] == "guards")
    assert any(item["kind"] == "Assert" for item in guards["semantic_nodes"])
    assert any(
        item["kind"] == "Revert"
        and item.get("semantic", {}).get("function", "").startswith("revert BuiltinFailure")
        for item in guards["semantic_nodes"]
    )
    builtin_rendered = render_sfir_c_like(builtin_payload)
    assert "TMP_1 = abi.encodePacked(value_1, TMP_0);" in builtin_rendered
    assert "TMP_2 = abi.decode(payload_1, uint256);" in builtin_rendered
    assert "TMP_3 = keccak256(packed_1);" in builtin_rendered
    assert "TMP_4 = addmod(value_1, 3, 11);" in builtin_rendered
    assert builtin_rendered.count("revert BuiltinFailure(value_1);") == 1
    cryptography = next(item for item in builtin_payload["functions"] if item["function"] == "cryptography")
    crypto_by_kind = {item["kind"]: item for item in cryptography["semantic_nodes"] if item["kind"] != "ValueAssign"}
    hash_signatures = [
        item["semantic"]["builtin_signature"]
        for item in cryptography["semantic_nodes"] if item["kind"] == "HashCompute"
    ]
    assert hash_signatures == ["sha256(bytes)", "ripemd160(bytes)"]
    assert crypto_by_kind["SignatureRecover"]["semantic"]["builtin_signature"] == "ecrecover(bytes32,uint8,bytes32,bytes32)"
    assert crypto_by_kind["GasQuery"]["semantic"]["builtin_signature"] == "gasleft()"
    assert crypto_by_kind["BlockHashQuery"]["semantic"]["builtin_signature"] == "blockhash(uint256)"
    destroy = next(item for item in builtin_payload["functions"] if item["function"] == "destroy")
    self_destruct = next(item for item in destroy["semantic_nodes"] if item["kind"] == "SelfDestruct")
    assert self_destruct["semantic"]["function"] == "selfdestruct"
    assert self_destruct["semantic"]["arguments"] == ["beneficiary_1"]
    destroy_block = next(
        block for block in destroy["fact_cfg"]["blocks"]
        if self_destruct["semantic_id"] in block.get("semantic_ids", [])
    )
    assert destroy_block["terminator"]["kind"] == "SelfDestruct"
    assert "TMP_9 = sha256(payload_1);" in builtin_rendered
    assert "TMP_10 = ripemd160(payload_1);" in builtin_rendered
    assert "TMP_11 = ecrecover(hash_1, v_1, r_1, s_1);" in builtin_rendered
    assert "TMP_12 = gasleft();" in builtin_rendered
    assert "TMP_14 = blockhash(TMP_13);" in builtin_rendered
    assert "selfdestruct(beneficiary_1);" in builtin_rendered
    code_metrics = next(item for item in builtin_payload["functions"] if item["function"] == "codeMetrics")
    code_query = next(item for item in code_metrics["semantic_nodes"] if item["kind"] == "AddressPropertyRead")
    assert code_query["semantic"]["builtin_signature"] == "code(address)"
    assert code_query["semantic"]["address"] == "target_1"
    assert code_query["semantic"]["property"] == "code"
    assert "TMP_16 = target_1.code;" in builtin_rendered
    account_metrics = next(item for item in builtin_payload["functions"] if item["function"] == "accountMetrics")
    address_properties = [
        (item["semantic"]["property"], item["semantic"]["address"])
        for item in account_metrics["semantic_nodes"] if item["kind"] == "AddressPropertyRead"
    ]
    assert address_properties == [("balance", "target_1"), ("codehash", "target_1")]
    assert "TMP_17 = target_1.balance;" in builtin_rendered
    assert "TMP_18 = target_1.codehash;" in builtin_rendered
    payments = next(item for item in builtin_payload["functions"] if item["function"] == "payments")
    payment_calls = [item for item in payments["semantic_nodes"] if item["kind"] == "ValueTransferCall"]
    assert [(item["semantic"]["method"], item.get("lvalue"), item["semantic"]["value"]) for item in payment_calls] == [
        ("send", "TMP_19", "1"),
        ("transfer", None, "2"),
    ]
    assert "TMP_19 = recipient_1.send(1);" in builtin_rendered
    assert "recipient_1.transfer(2);" in builtin_rendered
    assert "recipient_1 = recipient_1.transfer(2);" not in builtin_rendered
    concatenate = next(item for item in builtin_payload["functions"] if item["function"] == "concatenate")
    concat_signatures = [
        item["semantic"]["builtin_signature"]
        for item in concatenate["semantic_nodes"] if item["kind"] == "Concat"
    ]
    assert concat_signatures == ["bytes.concat()", "string.concat()"]
    assert "TMP_21 = bytes.concat(left_1, right_1);" in builtin_rendered
    assert "TMP_22 = string.concat(prefix_1, suffix_1);" in builtin_rendered
    clear_credit = next(item for item in builtin_payload["functions"] if item["function"] == "clearCredit")
    clear_read = next(item for item in clear_credit["semantic_nodes"] if item["kind"] == "StateRead")
    clear_write = next(item for item in clear_credit["semantic_nodes"] if item["kind"] == "StateWrite")
    assert clear_read["semantic"]["access"] == "credits[account]"
    assert clear_write["semantic"]["access"] == "credits[account]"
    assert clear_write["semantic"]["value"] == "0"
    assert clear_write["fact_ssa"]["writes"][0]["binding_id"].endswith(
        "storage_location:credits[account]"
    )
    assert "credits[account] = 0;" in builtin_rendered
    invert = next(item for item in builtin_payload["functions"] if item["function"] == "invert")
    unary = next(item for item in invert["semantic_nodes"] if item["kind"] == "ValueCompute")
    assert unary["semantic"]["operator"] == "!"
    assert unary["rvalue"] == "!value_1"
    assert "TMP_23 = !value_1;" in builtin_rendered

    inheritance_payload = build_function_level_semantic_fact_ir_payload(inheritance_functions)
    derived_constructor = next(
        item for item in inheritance_payload["functions"]
        if item["function_id"] == "SlitherInheritanceSurface.constructor(uint256)"
    )
    base_constructor = next(
        item for item in inheritance_payload["functions"]
        if item["function_id"] == "SlitherBaseSurface.constructor(uint256)"
    )
    derived_describe = next(
        item for item in inheritance_payload["functions"]
        if item["function_id"] == "SlitherInheritanceSurface.describe(uint256)"
    )
    receive = next(item for item in inheritance_payload["functions"] if item["function"] == "receive")
    fallback = next(item for item in inheritance_payload["functions"] if item["function"] == "fallback")
    base_call = next(item for item in derived_constructor["semantic_nodes"] if item["kind"] == "BaseConstructorCall")
    assert derived_constructor["declaration"]["is_constructor"] is True
    assert receive["declaration"]["is_receive"] is True
    assert fallback["declaration"]["is_fallback"] is True
    assert derived_describe["declaration"]["is_override"] is True
    assert [item["canonical_name"] for item in derived_describe["declaration"]["overrides"]] == [
        "SlitherBaseSurface.describe(uint256)"
    ]
    assert base_call["semantic"]["constructor_contract"] == "SlitherBaseSurface"
    assert base_call["semantic"]["arguments"] == ["7"]
    inheritance_contract = next(
        item for item in inheritance_payload["contracts"] if item["name"] == "SlitherInheritanceSurface"
    )
    assert [item["name"] for item in inheritance_contract["immediate_inheritance"]] == ["SlitherBaseSurface"]
    assert [item["name"] for item in inheritance_contract["inheritance"]] == ["SlitherBaseSurface"]
    constructor_link = next(
        item for item in inheritance_payload["base_constructor_links"]
        if item["caller_function_id"] == derived_constructor["function_id"]
    )
    assert constructor_link["resolution"] == "resolved"
    assert constructor_link["callee_function_id"] == base_constructor["function_id"]
    assert constructor_link["call_semantic_ids"] == [base_call["semantic_id"]]
    inheritance_rendered = render_sfir_c_like(inheritance_payload)
    assert "constructor(uint256 initialReceived) {" in inheritance_rendered
    assert "SlitherBaseSurface(7); /* base constructor */" in inheritance_rendered
    assert "receive() external payable {" in inheritance_rendered
    assert "fallback() external payable {" in inheritance_rendered
    inheritance_cfg_text = render_fact_cfg_text(inheritance_payload)
    assert "Contract declaration relations" in inheritance_cfg_text
    assert "Base constructor declaration links" in inheritance_cfg_text

    # Slither declares CodeSize as a concrete SSA operation.  It must enter
    # the value transport rather than being downgraded to an opaque generic
    # operation before the lifter sees it.
    assert "CodeSize" in SolidityAtomicOperationExtractor.VALUE_KINDS

    # The source-level SOLIDITY_FUNCTIONS registry contains builtins that do
    # not always have a direct Solidity spelling in our fixtures.  A known
    # generic builtin remains reconstructible through its exact signature;
    # a future signature and a future SlithIR class remain in final SFIR with
    # explicit diagnostics instead of being guessed or discarded.
    lifter = SoliditySemanticLifter()
    value = {"text": "value_1", "name": "value_1", "base_name": "value", "type": "uint256"}
    known_atom = {
        "atom_id": "known_builtin", "kind": "SolidityCall", "function": {
            "name": "prevrandao", "full_name": "prevrandao()",
        }, "lvalue": {"text": "TMP_known", "name": "TMP_known", "type": "uint256"},
        "result_ssa": "TMP_known", "read": [], "arguments": [], "runtime_operation": True,
    }
    unknown_builtin_atom = {
        "atom_id": "unknown_builtin", "kind": "SolidityCall", "function": {
            "name": "futureBuiltin", "full_name": "futureBuiltin(uint256)",
        }, "lvalue": {"text": "TMP_unknown", "name": "TMP_unknown", "type": "uint256"},
        "result_ssa": "TMP_unknown", "read": [value], "arguments": [value], "runtime_operation": True,
    }
    unknown_operation_atom = {
        "atom_id": "unknown_operation", "kind": "FutureSlithIROperation", "text": "FUTURE value_1",
        "lvalue": {"text": "TMP_future", "name": "TMP_future", "type": "uint256"},
        "result_ssa": "TMP_future", "read": [value], "runtime_operation": True,
    }
    fallback_facts = lifter.atomic_facts(
        {"function_id": "FallbackHarness.f()", "contract": "FallbackHarness", "function": "f"},
        {"function_id": "FallbackHarness.f()", "operations": [known_atom, unknown_builtin_atom, unknown_operation_atom]},
    )
    assert fallback_facts[0]["kind"] == "BuiltinCall"
    assert fallback_facts[0]["semantic"]["builtin_signature"] == "prevrandao()"
    assert fallback_facts[0]["semantic"]["model_status"] == "generic_known"
    assert fallback_facts[1]["kind"] == "UnmodeledBuiltinCall"
    assert fallback_facts[1]["semantic"]["model_status"] == "unmodeled"
    assert fallback_facts[2]["kind"] == "UnmodeledSlithIROperation"
    assert fallback_facts[2]["semantic"]["slithir_kind"] == "FutureSlithIROperation"
    fallback_diagnostics = []
    fallback_nodes = SemanticFactIRBridge()._semantic_nodes(
        "FallbackHarness.f()", fallback_facts, [], fallback_diagnostics
    )
    assert {item["reason"] for item in fallback_diagnostics} == {
        "unknown_slither_solidity_function_signature", "unknown_slithir_operation_kind",
    }
    assert "unmodeled Slither builtin: futureBuiltin(uint256); args=[value_1]" in (
        render_sfir_node_c_like(fallback_nodes[1]) or ""
    )
    assert "unmodeled SlithIR operation: FutureSlithIROperation" in (
        render_sfir_node_c_like(fallback_nodes[2]) or ""
    )

    for checked_payload in (payload, surface_payload, control_payload, builtin_payload, inheritance_payload):
        assert not any(
            node.get("semantic", {}).get("model_status") == "unmodeled"
            for function in checked_payload["functions"] for node in function["semantic_nodes"]
        )
    print("PASS Slither transport: aliases, calls, constructions, dynamic calls, try/catch, modifiers, loop exits, builtins, inheritance, special entries, FactSSA, and final-SFIR rendering")


if __name__ == "__main__":
    run()
