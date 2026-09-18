#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT / "legacy_yul", ROOT / "s_seir"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from assembly_ast_cfg import discover_solc
from s_seir_pipeline import build_sseir
from s_seir_semantic_fact_adapter import (
    build_function_level_semantic_fact_payload,
    build_function_level_semantic_fact_ir_payload,
)


SOURCE = """pragma solidity ^0.8.26;

interface ITarget {
    function ping(uint256 value) external returns (uint256);
}

contract MappingCase {
    mapping(address => mapping(address => uint256)) private allowances;
    event Approval(address indexed owner, address indexed spender, uint256 amount);

    function update(address spender, uint256 amount) external returns (uint256) {
        allowances[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return allowances[msg.sender][spender];
    }
}

contract BranchCase {
    uint256 private total;

    function choose(bool flag, uint256 amount) external {
        require(amount != 0, "zero");
        if (flag) {
            total = amount;
        } else {
            total = amount + 1;
        }
    }
}

contract CallCase {
    function relay(ITarget target, uint256 value) external returns (uint256) {
        uint256 result = target.ping(value);
        return twice(result);
    }

    function twice(uint256 value) internal pure returns (uint256) {
        return value * 2;
    }
}

contract MixedYulCase {
    mapping(address => uint256) private balances;

    function load(address who) external view returns (uint256 result) {
        assembly {
            mstore(0, who)
            mstore(32, balances.slot)
            result := sload(keccak256(0, 64))
        }
    }
}

contract ConstantContextCase {
    address internal constant FIXED = address(0x1234);
    string internal constant LABEL = "fixed";

    function label() external pure returns (string memory) {
        return LABEL;
    }

    function guarded(address target) external pure returns (bool result) {
        if (target == FIXED) {
            assembly { result := 1 }
        }
    }
}

contract StateContextCase {
    address internal FIXED;

    function guarded(address target) external view returns (bool result) {
        if (target == FIXED) {
            assembly { result := 1 }
        }
    }
}

contract BoundaryCase {
    uint256 private stored;

    function guarded(bool flag, uint256 amount) external returns (uint256 result) {
        uint256 localValue = amount + 1;
        if (flag) {
            assembly {
                result := add(localValue, sload(stored.slot))
            }
        }
    }

    function nested(bool outer, bool inner, uint256 amount) external returns (uint256 result) {
        if (outer) {
            if (inner) {
                assembly {
                    result := amount
                }
            }
        }
    }

    function loopWrite(uint256 count, uint256 value) external pure returns (uint256 result) {
        while (count != 0) {
            assembly {
                mstore(0, value)
                result := mload(0)
            }
            count--;
        }
    }
}

contract SolidityReferenceKindCase {
    enum Flag {
        None,
        Frozen
    }

    uint256[] public stored;
    mapping(address => uint256) public balances;

    function parameterArray(uint256[] calldata values, uint256 i) external pure returns (uint256) {
        return values[i];
    }

    function stateArray(uint256 i) external view returns (uint256) {
        return stored[i];
    }

    function enumGuard(Flag flag) external pure returns (bool) {
        require(flag != Flag.Frozen, "frozen");
        return true;
    }
}

interface AbiView {
    function totalSupply() external view returns (uint256);
}

contract AbiExpressionCase {
    function low(address token, address to, uint256 amount) external returns (bool ok) {
        (ok,) = token.call(abi.encodeWithSignature("transfer(address,uint256)", to, amount));
    }

    function stat(address token) external view returns (bool ok) {
        (ok,) = token.staticcall(abi.encodeWithSelector(AbiView.totalSupply.selector));
    }
}
"""


def overlays(function, kind: str):
    return [item for item in function.semantic_overlays if item.kind == kind]


def effects(function, kind: str):
    return [item for item in function.effects if item.kind == kind]


def semantic_facts(facts, contract: str, function: str, kind: str | None = None):
    return [
        item for item in facts
        if item.get("contract") == contract
        and item.get("function") == function
        and (kind is None or item.get("kind") == kind)
    ]


def assert_guarded_yul_paths(function) -> dict:
    """Check the guard at the final CFG boundary, including its negative path."""
    final = build_function_level_semantic_fact_ir_payload([function])["functions"][0]
    nodes = final["semantic_nodes"]
    predicates = [node for node in nodes if node["kind"] == "BranchCondition"]
    assert len(predicates) == 1
    predicate = predicates[0]
    assert predicate["semantic"]["predicate"] == "(target == FIXED)"
    assignments = [node for node in nodes if node["source_lang"] == "yul" and node.get("lvalue") == "result"]
    assert len(assignments) == 1
    assignment = assignments[0]
    assert assignment["rvalue"] == "1"
    branch_block = predicate["placement"]["anchor_block"]
    assignment_block = assignment["placement"]["anchor_block"]
    edges = final["fact_cfg"]["edges"]
    branch_edges = [edge for edge in edges if edge["from"] == branch_block]
    assert len(branch_edges) == 2
    true_edge = next(edge for edge in branch_edges if edge["kind"] == "true")
    false_edge = next(edge for edge in branch_edges if edge["kind"] == "false")
    assert true_edge["guard"] == "target == FIXED"
    assert false_edge["guard"] == "!(target == FIXED)"

    def reachable(start: str, target: str) -> bool:
        pending, seen = [start], set()
        while pending:
            block = pending.pop()
            if block == target:
                return True
            if block not in seen:
                seen.add(block)
                pending.extend(edge["to"] for edge in edges if edge["from"] == block)
        return False

    assert reachable(true_edge["to"], assignment_block)
    assert not reachable(false_edge["to"], assignment_block)
    returned = next(node for node in nodes if node["kind"] == "Return")
    assert reachable(assignment_block, returned["placement"]["anchor_block"])
    assert reachable(false_edge["to"], returned["placement"]["anchor_block"])
    witness = next(item for item in final["path_witnesses"] if item["semantic_id"] == assignment["semantic_id"])
    assert witness["guards"] == ["target == FIXED"]
    assert true_edge["edge_id"] in witness["control_edge_ids"]
    comparison = next(node for node in nodes if node.get("lvalue") == predicate["rvalue"])
    assert any(edge["kind"] == "data" and edge["from"] == comparison["semantic_id"]
               and edge["to"] == predicate["semantic_id"] for edge in final["semantic_edges"])
    return final


def run() -> None:
    solc = discover_solc(None)
    assert solc, "solc is required"
    with tempfile.TemporaryDirectory(prefix="sseir_slithir_tests_") as directory:
        source = Path(directory) / "Cases.sol"
        source.write_text(SOURCE, encoding="utf-8")
        functions = build_sseir(source, solc_bin=solc, workdir=Path(directory), branch_preprocess=False)
    facts = build_function_level_semantic_fact_payload(functions)["facts"]

    mapping = next(fn for fn in functions if fn.contract == "MappingCase" and fn.function == "update")
    assert not mapping.effects and not mapping.semantic_overlays
    mapping_facts = semantic_facts(facts, "MappingCase", "update")
    assert any(item.get("kind") == "StateWrite" and item.get("lvalue") == "allowances[msg.sender][spender]" for item in mapping_facts)
    assert any(item.get("kind") == "EventEmit" and item.get("semantic", {}).get("event") == "Approval" for item in mapping_facts)
    assert any(item.get("kind") == "Return" and item.get("semantic", {}).get("resolved_operands") == ["allowances[msg.sender][spender]"] for item in mapping_facts)

    branch = next(fn for fn in functions if fn.contract == "BranchCase" and fn.function == "choose")
    writes = semantic_facts(facts, "BranchCase", "choose", "StateWrite")
    assert len(writes) == 2
    assert any("(flag)" in str(item.get("condition")) for item in writes)
    assert any("!(flag)" in str(item.get("condition")) for item in writes)
    assert semantic_facts(facts, "BranchCase", "choose", "Require")

    relay = next(fn for fn in functions if fn.contract == "CallCase" and fn.function == "relay")
    relay_facts = semantic_facts(facts, "CallCase", "relay")
    assert any(item.get("kind") == "ExternalCall" and item.get("semantic", {}).get("function") == "ping" for item in relay_facts)
    assert any(item.get("kind") == "InternalCall" and item.get("semantic", {}).get("function") == "twice" for item in relay_facts)
    assert any(item.get("kind") == "Return" and item.get("semantic", {}).get("resolved_operands") == ["twice(result)"] for item in relay_facts)

    mixed = next(fn for fn in functions if fn.contract == "MixedYulCase" and fn.function == "load")
    assert effects(mixed, "MemoryWrite")
    assert any(item.attrs.get("access") == "balances[who]" for item in overlays(mixed, "MappingRead"))

    constant_context = next(fn for fn in functions if fn.contract == "ConstantContextCase" and fn.function == "guarded")
    assert all(item.attrs.get("language") != "solidity" for item in constant_context.effects)
    assert not any(item.attrs.get("state_variable") == "FIXED" for item in constant_context.effects)
    # ADR P0-T1-001: a guard need not be copied onto every compatibility fact.
    constant_ir = assert_guarded_yul_paths(constant_context)
    assert not any(node["kind"] == "StateRead" for node in constant_ir["semantic_nodes"])
    constant_comparison = next(node for node in constant_ir["semantic_nodes"]
                               if node.get("semantic", {}).get("operator") == "==")
    assert constant_comparison["rvalue"] == "target_1 == FIXED"
    assert constant_comparison["reads"] == ["target_1"]
    assert [read["value"] for read in constant_comparison["fact_ssa"]["reads"]] == ["target_1"]
    assert constant_comparison["semantic"]["constant_operands"] == [{
        "name": "FIXED", "canonical_name": "ConstantContextCase.FIXED",
        "type": "address", "initializer": "address(0x1234)",
    }]
    label = next(fn for fn in functions if fn.contract == "ConstantContextCase" and fn.function == "label")
    label_ir = build_function_level_semantic_fact_ir_payload([label])["functions"][0]
    assert not any(node["kind"] == "StateRead" for node in label_ir["semantic_nodes"])
    label_return = next(node for node in label_ir["semantic_nodes"] if node["kind"] == "Return")
    assert label_return["rvalue"] == "LABEL"  # A declaration reference, not the literal string "LABEL".
    assert label_return["fact_ssa"]["reads"] == []
    assert label_return["semantic"]["constant_operands"][0]["canonical_name"] == "ConstantContextCase.LABEL"

    # The same spelling on a mutable declaration must still read storage.
    state_context = next(fn for fn in functions if fn.contract == "StateContextCase" and fn.function == "guarded")
    state_ir = assert_guarded_yul_paths(state_context)
    state_reads = [node for node in state_ir["semantic_nodes"] if node["kind"] == "StateRead"]
    assert len(state_reads) == 1 and state_reads[0]["rvalue"] == "FIXED"
    state_comparison = next(node for node in state_ir["semantic_nodes"]
                            if node.get("semantic", {}).get("operator") == "==")
    assert "FIXED_1" in state_comparison["reads"]
    assert any(edge["kind"] == "data" and edge["from"] == state_reads[0]["semantic_id"]
               and edge["to"] == state_comparison["semantic_id"] for edge in state_ir["semantic_edges"])

    guarded = next(fn for fn in functions if fn.contract == "BoundaryCase" and fn.function == "guarded")
    boundary = next(iter(guarded.control["assembly_boundaries"].values()))
    reads = {item["name"]: item for item in boundary["external_reads"]}
    assert {"localValue", "stored"}.issubset(reads)
    assert reads["localValue"]["reaching_ssa_versions"]
    assert any(item["name"] == "result" for item in boundary["external_writes"])
    assert [item["predicate"] for item in boundary["control_dependencies"]] == ["flag"]

    nested = next(fn for fn in functions if fn.contract == "BoundaryCase" and fn.function == "nested")
    nested_boundary = next(iter(nested.control["assembly_boundaries"].values()))
    nested_conditions = {item["predicate"] for item in nested_boundary["control_dependencies"]}
    assert nested_conditions == {"outer", "inner"}

    loop_write = next(fn for fn in functions if fn.contract == "BoundaryCase" and fn.function == "loopWrite")
    loop_boundary = next(iter(loop_write.control["assembly_boundaries"].values()))
    assert any("count" in item["predicate"] for item in loop_boundary["control_dependencies"])
    assert any(item["name"] == "value" for item in loop_boundary["external_reads"])
    assert loop_write.control["dominance"]["immediate_dominator"]
    assert loop_write.control["typed_def_use"]["blocks"]

    reference_kind = [fn for fn in functions if fn.contract == "SolidityReferenceKindCase"]
    parameter_array = next(fn for fn in reference_kind if fn.function == "parameterArray")
    assert not parameter_array.effects
    assert any("values[i]" in item.get("semantic", {}).get("resolved_operands", []) for item in semantic_facts(facts, "SolidityReferenceKindCase", "parameterArray"))

    state_array = next(fn for fn in reference_kind if fn.function == "stateArray")
    assert any("stored[i]" in item.get("semantic", {}).get("resolved_operands", []) for item in semantic_facts(facts, "SolidityReferenceKindCase", "stateArray"))

    enum_guard = next(fn for fn in reference_kind if fn.function == "enumGuard")
    assert not enum_guard.effects

    abi_low = next(fn for fn in functions if fn.contract == "AbiExpressionCase" and fn.function == "low")
    abi_low_call = next(item for item in semantic_facts(facts, "AbiExpressionCase", "low") if item.get("kind") == "LowLevelCall")
    assert any("abi.encodeWithSignature" in value for value in abi_low_call.get("semantic", {}).get("resolved_operands", []))

    abi_stat = next(fn for fn in functions if fn.contract == "AbiExpressionCase" and fn.function == "stat")
    abi_stat_call = next(item for item in semantic_facts(facts, "AbiExpressionCase", "stat") if item.get("kind") == "LowLevelCall")
    assert any("abi.encodeWithSelector" in value for value in abi_stat_call.get("semantic", {}).get("resolved_operands", []))

    print("PASS mapping_event: Solidity sol_atom facts record mapping write, event, and return")
    print("PASS branch_state: CFG bridge places atomic state writes under their conditions")
    print("PASS calls: SoliditySemanticLifter projects atomic calls and return")
    print("PASS mixed_yul: existing MemorySSA mapping recovery remains active")
    print("PASS constant_context: Solidity constant guards are context, not storage reads")
    print("PASS guarded_boundary: reaching SSA and state inputs cross into Yul under flag")
    print("PASS nested_boundary: outer and inner control dependencies are preserved")
    print("PASS loop_boundary: Solidity loop control and Yul memory effects remain aligned")
    print("PASS solidity_reference_kind: calldata arrays and enum members are not storage reads")
    print("PASS abi_expression_pretty: Solidity ABI call arguments keep source-level form")


if __name__ == "__main__":
    run()
