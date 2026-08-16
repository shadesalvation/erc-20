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
    assert any(
        effect.kind == "StorageWrite"
        and effect.attrs.get("atomic_operation_id") == nested_write["atom_id"]
        for effect in approve.effects
    )
    facts = build_function_level_semantic_fact_payload(analyzed["guarded"])["solidity_facts"]
    assert any(
        fact.get("kind") == "StateWrite"
        and fact.get("lvalue") == "allowance[msg.sender][spender]"
        and fact.get("rvalue") == "value"
        for fact in facts
    )
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


if __name__ == "__main__":
    run()
