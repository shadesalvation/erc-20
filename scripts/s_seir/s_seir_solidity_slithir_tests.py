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

    function guarded(address target) external pure returns (bool result) {
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


def run() -> None:
    solc = discover_solc(None)
    assert solc, "solc is required"
    with tempfile.TemporaryDirectory(prefix="sseir_slithir_tests_") as directory:
        source = Path(directory) / "Cases.sol"
        source.write_text(SOURCE, encoding="utf-8")
        functions = build_sseir(source, solc_bin=solc, workdir=Path(directory), branch_preprocess=False)

    mapping = next(fn for fn in functions if fn.contract == "MappingCase" and fn.function == "update")
    mapping_writes = overlays(mapping, "MappingWrite")
    mapping_reads = overlays(mapping, "MappingRead")
    event_emits = overlays(mapping, "EventEmit")
    assert any(item.attrs.get("access") == "allowances[msg.sender][spender]" for item in mapping_writes)
    assert any(item.attrs.get("access") == "allowances[msg.sender][spender]" for item in mapping_reads)
    assert any(item.attrs.get("event") == "Approval" and item.attrs.get("args") == ["msg.sender", "spender", "amount"] for item in event_emits)
    assert overlays(mapping, "ReturnValue")

    branch = next(fn for fn in functions if fn.contract == "BranchCase" and fn.function == "choose")
    writes = [item for item in effects(branch, "StorageWrite") if item.attrs.get("typed_access")]
    assert len(writes) == 2
    assert any(any("flag" in path and not path.startswith("!(") for path in item.attrs.get("path_states") or []) for item in writes)
    assert any(any("!(flag)" in path for path in item.attrs.get("path_states") or []) for item in writes)
    assert overlays(branch, "RequireOverlay")

    relay = next(fn for fn in functions if fn.contract == "CallCase" and fn.function == "relay")
    external_calls = overlays(relay, "ExternalCall")
    internal_calls = overlays(relay, "InternalCall")
    assert any(item.attrs.get("function") == "ping" and item.attrs.get("arguments") == ["value"] for item in external_calls)
    assert any(item.attrs.get("function") == "twice" for item in internal_calls)
    assert any(item.attrs.get("values") == ["twice(result)"] for item in overlays(relay, "ReturnValue"))

    mixed = next(fn for fn in functions if fn.contract == "MixedYulCase" and fn.function == "load")
    assert effects(mixed, "MemoryWrite")
    assert any(item.attrs.get("access") == "balances[who]" for item in overlays(mixed, "MappingRead"))

    constant_context = next(fn for fn in functions if fn.contract == "ConstantContextCase" and fn.function == "guarded")
    assert not any(item.attrs.get("state_variable") == "FIXED" for item in effects(constant_context, "StorageRead"))
    assert any("FIXED" in path for item in constant_context.effects for path in item.attrs.get("path_states") or [])

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
    assert not any(item.attrs.get("access") == "values[i]" for item in effects(parameter_array, "StorageRead"))

    state_array = next(fn for fn in reference_kind if fn.function == "stateArray")
    assert any(item.attrs.get("access") == "stored[i]" and item.attrs.get("state_variable") == "stored" for item in effects(state_array, "StorageRead"))

    enum_guard = next(fn for fn in reference_kind if fn.function == "enumGuard")
    assert not any(item.attrs.get("access") == "Flag.Frozen" for item in effects(enum_guard, "StorageRead"))

    abi_low = next(fn for fn in functions if fn.contract == "AbiExpressionCase" and fn.function == "low")
    abi_low_call = next(item for item in overlays(abi_low, "ExternalCall"))
    assert abi_low_call.attrs.get("arguments") == ['abi.encodeWithSignature("transfer(address,uint256)", to, amount)'], abi_low_call.attrs

    abi_stat = next(fn for fn in functions if fn.contract == "AbiExpressionCase" and fn.function == "stat")
    abi_stat_call = next(item for item in overlays(abi_stat, "ExternalCall"))
    assert abi_stat_call.attrs.get("arguments") == ["abi.encodeWithSelector(AbiView.totalSupply.selector)"], abi_stat_call.attrs

    print("PASS mapping_event: typed nested mapping read/write and EventEmit")
    print("PASS branch_state: path-conditioned state writes and RequireOverlay")
    print("PASS calls: structured external/internal calls and ReturnValue")
    print("PASS mixed_yul: existing MemorySSA mapping recovery remains active")
    print("PASS constant_context: Solidity constant guards are context, not storage reads")
    print("PASS guarded_boundary: reaching SSA and state inputs cross into Yul under flag")
    print("PASS nested_boundary: outer and inner control dependencies are preserved")
    print("PASS loop_boundary: Solidity loop control and Yul memory effects remain aligned")
    print("PASS solidity_reference_kind: calldata arrays and enum members are not storage reads")
    print("PASS abi_expression_pretty: Solidity ABI call arguments keep source-level form")


if __name__ == "__main__":
    run()
