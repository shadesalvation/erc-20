#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_ROOT = ROOT / "scripts"
for candidate in (SCRIPT_ROOT / "legacy_yul", SCRIPT_ROOT / "s_seir"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from assembly_ast_cfg import discover_solc
from s_seir_pipeline import build_sseir
from s_seir_semantic_fact_adapter import build_function_level_semantic_fact_payload


SAMPLES = {
    "guarded": ROOT / "人工构造样例/02_基础ERC20_状态变量_构造函数_事件_错误_modifier/contracts/GuardedERC20.sol",
    "typed": ROOT / "人工构造样例/04_类型系统_数组_mapping_struct_enum_UDVT/contracts/TypedSnapshotERC20.sol",
    "control": ROOT / "人工构造样例/05_表达式_运算符_控制流_checked_unchecked/contracts/ControlFlowERC20.sol",
    "abi": ROOT / "人工构造样例/06_ABI_低级调用_trycatch_new_合约类型/contracts/AbiFactoryAndProbe.sol",
    "assembly": ROOT / "人工构造样例/07_内联Assembly_memorysafe_Yul操作/contracts/AssemblyERC20.sol",
}


def table(function):
    result = getattr(function, "_sseir_solidity_atomic_operations", None)
    assert isinstance(result, dict)
    assert result["operation_count"] == len(result["operations"])
    assert len({item["atom_id"] for item in result["operations"]}) == len(result["operations"])
    return result


def operation(function, predicate):
    return next(item for item in table(function)["operations"] if predicate(item))


def run() -> None:
    solc = discover_solc(None)
    assert solc, "solc is required"
    with tempfile.TemporaryDirectory(prefix="sseir_atomic_ops_") as directory:
        workdir = Path(directory)
        analyzed = {
            name: build_sseir(source, solc_bin=solc, workdir=workdir, branch_preprocess=False)
            for name, source in SAMPLES.items()
        }

    approve = next(fn for fn in analyzed["guarded"] if fn.function == "approve")
    nested_write = operation(
        approve,
        lambda item: item.get("atomic_kind") == "StateWrite"
        and (item.get("storage_access") or {}).get("state_variable") == "allowance",
    )
    assert nested_write["storage_access"]["access"] == "allowance[msg.sender][spender]"
    assert nested_write["storage_access"]["keys"] == ["msg.sender", "spender"]
    assert nested_write["expression"] == "value"
    assert not any(effect.attrs.get("language") == "solidity" for effect in approve.effects)
    facts = build_function_level_semantic_fact_payload(analyzed["guarded"])["solidity_facts"]
    assert facts and all(
        fact.get("semantic", {}).get("atomic_operation_count") == 1
        for fact in facts
    )
    assert len(facts) == sum(table(fn)["operation_count"] for fn in analyzed["guarded"])
    assert any(
        fact.get("kind") == "StateWrite"
        and fact.get("lvalue") == "allowance[msg.sender][spender]"
        and fact.get("rvalue") == "value_1"
        and fact.get("depends_on")
        and fact.get("condition") == "!(spender == address(0))"
        for fact in facts
    )
    approve_facts = [fact for fact in facts if fact.get("function") == "approve"]
    allowance_location = next(
        fact for fact in approve_facts
        if fact.get("kind") == "StorageLocationResolve"
        and (fact.get("semantic") or {}).get("location", {}).get("access")
        == "allowance[msg.sender][spender]"
    )
    allowance_write = next(
        fact for fact in approve_facts
        if fact.get("kind") == "StateWrite"
        and fact.get("lvalue") == "allowance[msg.sender][spender]"
    )
    assert allowance_write["depends_on"] == [allowance_location["fact_id"]]
    assert allowance_write["semantic"]["location"] == allowance_location["semantic"]["location"]
    assert allowance_location["fact_role"] == "support"
    assert allowance_write["fact_role"] == "effect"
    assert allowance_write["semantic"]["value"] == "value"
    print("PASS storage location: Solidity mapping writes use a canonical location fact")

    transfer_from_facts = [fact for fact in facts if fact.get("function") == "transferFrom"]
    allowance_read = next(
        fact for fact in transfer_from_facts
        if fact.get("kind") == "StateRead"
        and fact.get("rvalue") == "allowance[from][msg.sender]"
    )
    read_location = next(
        fact for fact in transfer_from_facts
        if fact.get("fact_id") in allowance_read.get("depends_on", [])
    )
    assert read_location["kind"] == "StorageLocationResolve"
    assert allowance_read["semantic"]["location"] == read_location["semantic"]["location"]
    print("PASS storage read: implicit SlithIR dereference is an explicit StateRead atom")
    print("PASS guarded: nested mapping assignment is an atomic StateWrite")

    transfer = next(fn for fn in analyzed["typed"] if fn.function == "_transferWithHook")
    transfer_table = table(transfer)
    indexed_writes = [
        item for item in transfer_table["operations"]
        if item.get("atomic_kind") == "StateWrite"
        and (item.get("storage_access") or {}).get("state_variable") == "lastThreeTransfers"
    ]
    assert [item["storage_access"]["keys"] for item in indexed_writes] == [["0"], ["1"], ["2"]]
    assert all(item.get("result_ssa") for item in indexed_writes)
    print("PASS typed: fixed-array reference chains and ordered writes are preserved")

    typed_facts = build_function_level_semantic_fact_payload(analyzed["typed"])["solidity_facts"]
    guarded_writes = [
        fact for fact in typed_facts
        if fact.get("function") == "_transferWithHook" and fact.get("kind") == "StateWrite"
    ]
    assert guarded_writes
    assert all(
        fact.get("guard_conditions") == [
            "(flags[from] != AccountFlag.Frozen)",
            "(balanceOf[from] >= (value + feeFn(value)))",
        ]
        and fact.get("condition")
        == "((flags[from] != AccountFlag.Frozen)) && ((balanceOf[from] >= (value + feeFn(value))))"
        for fact in guarded_writes
    )
    print("PASS guards: dominating Solidity requires annotate later state writes once each")

    bit_ops = next(fn for fn in analyzed["control"] if fn.function == "bitOps")
    computes = [
        item for item in table(bit_ops)["operations"]
        if item.get("atomic_kind") == "ValueCompute"
    ]
    assert len(computes) >= 3
    assert all("cfg_block_id" in item and "source_span" in item for item in computes)
    assert any(item.get("operator") in {"&", "BITWISE_AND"} for item in computes)
    print("PASS control: nested expressions are split into ordered SSA computations")

    distribute = next(fn for fn in analyzed["control"] if fn.function == "distribute")
    predicates = [
        item for item in table(distribute)["operations"]
        if item.get("atomic_kind") == "ControlPredicate"
    ]
    assert predicates and all(item.get("control_only") for item in predicates)
    assert any(item.get("path_conditions") for item in predicates)
    print("PASS control metadata: branch expressions are marked control-only with CFG conditions")

    constructor_facts = [fact for fact in facts if fact.get("function") == "constructor"]
    multiply = next(
        fact for fact in constructor_facts
        if fact.get("kind") == "ValueCompute" and fact.get("rvalue") == "initialSupply_1 * 2"
    )
    max_write = next(
        fact for fact in constructor_facts
        if fact.get("kind") == "StateWrite" and fact.get("lvalue") == "maxOwnerMint"
    )
    assert max_write["rvalue"] == "TMP_0"
    assert max_write["depends_on"] == [multiply["fact_id"]]
    assert "initialSupply * 2" not in max_write["rvalue"]
    print("PASS final facts: complex Solidity statements remain split into dependent atomic facts")

    abi_payload = build_function_level_semantic_fact_payload(analyzed["abi"])
    assert abi_payload["solidity_facts"]
    assert all(
        fact.get("semantic", {}).get("atomic_operation_count") == 1
        for fact in abi_payload["solidity_facts"]
    )
    assert any(fact.get("kind") in {"ExternalCall", "LowLevelCall"} for fact in abi_payload["solidity_facts"])
    print("PASS ABI/calls: calls are atomic facts with immediate SSA arguments")

    mixed_payload = build_function_level_semantic_fact_payload(analyzed["assembly"])
    assert mixed_payload["solidity_fact_count"] > 0 and mixed_payload["yul_fact_count"] > 0
    hash_facts = [fact for fact in mixed_payload["facts"] if fact.get("function") == "hashMemorySafe"]
    yul_hash = next(fact for fact in hash_facts if fact.get("source_lang") == "yul")
    solidity_return = next(
        fact for fact in hash_facts
        if fact.get("source_lang") == "solidity" and fact.get("kind") == "Return"
    )
    assert yul_hash["order"]["cfg_block_order"] < solidity_return["order"]["cfg_block_order"]
    assert solidity_return["control_predecessors"] == [yul_hash["fact_id"]]
    assert not any(fact.get("source_lang") == "yul" and fact.get("kind") == "Return" for fact in hash_facts)
    print("PASS mixed frontend: Solidity and Yul facts share the function CFG order without duplicate return")

    assembly_move_facts = [
        fact for fact in mixed_payload["facts"]
        if fact.get("function") == "_assemblyMove" and fact.get("source_lang") == "yul"
    ]
    yul_location = next(
        fact for fact in assembly_move_facts
        if fact.get("kind") == "StorageLocationResolve"
        and (fact.get("semantic") or {}).get("location", {}).get("access") == "balanceOf[to]"
    )
    yul_read = next(
        fact for fact in assembly_move_facts
        if fact.get("kind") == "StateRead" and fact.get("rvalue") == "balanceOf[to]"
    )
    yul_write = next(
        fact for fact in assembly_move_facts
        if fact.get("kind") == "StateWrite" and fact.get("lvalue") == "balanceOf[to]"
    )
    assert yul_location["fact_id"] in yul_read["depends_on"]
    assert yul_location["fact_id"] in yul_write["depends_on"]
    assert yul_read["fact_id"] in yul_write["depends_on"]
    assert yul_location["semantic"]["location"] == yul_write["semantic"]["location"]
    assert yul_location["fact_role"] == "support"
    assert yul_read["fact_role"] == yul_write["fact_role"] == "effect"
    print("PASS Yul parity: MappingSlot, StateRead, and StateWrite form one dependency chain")


if __name__ == "__main__":
    run()
