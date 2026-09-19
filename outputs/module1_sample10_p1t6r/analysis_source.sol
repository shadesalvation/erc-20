// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract YulHeavyERC20 {
    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    string public constant name = "Yul Heavy Token";
    string public constant symbol = "YHT";
    uint8 public constant decimals = 18;

    uint256 private _totalSupply;
    mapping(address => uint256) private _balances;
    mapping(address => mapping(address => uint256)) private _allowances;

    constructor(uint256 initialSupply) {
        assembly {
            sstore(_totalSupply.slot, initialSupply)

            mstore(0x00, caller())
            mstore(0x20, _balances.slot)
            sstore(keccak256(0x00, 0x40), initialSupply)

            mstore(0x00, initialSupply)
            log3(
                0x00,
                0x20,
                0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef,
                0,
                caller()
            )
        }
    }

    function totalSupply() external view returns (uint256 result) {
        assembly {
            result := sload(_totalSupply.slot)
        }
    }

    function balanceOf(address account) public view returns (uint256 result) {
        assembly {
            mstore(0x00, account)
            mstore(0x20, _balances.slot)
            result := sload(keccak256(0x00, 0x40))
        }
    }

    function allowance(address owner, address spender) external view returns (uint256 result) {
        assembly {
            mstore(0x00, owner)
            mstore(0x20, _allowances.slot)
            let ownerSlot := keccak256(0x00, 0x40)
            mstore(0x00, spender)
            mstore(0x20, ownerSlot)
            result := sload(keccak256(0x00, 0x40))
        }
    }

    function approve(address spender, uint256 value) external returns (bool ok) {
        assembly {
            mstore(0x00, caller())
            mstore(0x20, _allowances.slot)
            let ownerSlot := keccak256(0x00, 0x40)
            mstore(0x00, spender)
            mstore(0x20, ownerSlot)
            sstore(keccak256(0x00, 0x40), value)

            mstore(0x00, value)
            log3(
                0x00,
                0x20,
                0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925,
                caller(),
                spender
            )
            ok := 1
        }
    }

    function transfer(address to, uint256 value) external returns (bool ok) {
        assembly {
            if iszero(to) { revert(0x00, 0x00) }

            mstore(0x00, caller())
            mstore(0x20, _balances.slot)
            let fromSlot := keccak256(0x00, 0x40)
            let fromBalance := sload(fromSlot)
            if lt(fromBalance, value) { revert(0x00, 0x00) }

            mstore(0x00, to)
            mstore(0x20, _balances.slot)
            let toSlot := keccak256(0x00, 0x40)

            sstore(fromSlot, sub(fromBalance, value))
            sstore(toSlot, add(sload(toSlot), value))

            mstore(0x00, value)
            log3(
                0x00,
                0x20,
                0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef,
                caller(),
                to
            )
            ok := 1
        }
    }

    function transferFrom(address from, address to, uint256 value) external returns (bool ok) {
        assembly {
            if iszero(to) { revert(0x00, 0x00) }

            mstore(0x00, from)
            mstore(0x20, _allowances.slot)
            let ownerSlot := keccak256(0x00, 0x40)
            mstore(0x00, caller())
            mstore(0x20, ownerSlot)
            let allowanceSlot := keccak256(0x00, 0x40)
            let currentAllowance := sload(allowanceSlot)
            if lt(currentAllowance, value) { revert(0x00, 0x00) }
            if iszero(eq(currentAllowance, not(0))) {
                sstore(allowanceSlot, sub(currentAllowance, value))
            }

            mstore(0x00, from)
            mstore(0x20, _balances.slot)
            let fromSlot := keccak256(0x00, 0x40)
            let fromBalance := sload(fromSlot)
            if lt(fromBalance, value) { revert(0x00, 0x00) }

            mstore(0x00, to)
            mstore(0x20, _balances.slot)
            let toSlot := keccak256(0x00, 0x40)
            sstore(fromSlot, sub(fromBalance, value))
            sstore(toSlot, add(sload(toSlot), value))

            mstore(0x00, value)
            log3(
                0x00,
                0x20,
                0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef,
                from,
                to
            )
            ok := 1
        }
    }

    function burn(uint256 value) external {
        assembly {
            mstore(0x00, caller())
            mstore(0x20, _balances.slot)
            let accountSlot := keccak256(0x00, 0x40)
            let accountBalance := sload(accountSlot)
            if lt(accountBalance, value) { revert(0x00, 0x00) }

            sstore(accountSlot, sub(accountBalance, value))
            sstore(_totalSupply.slot, sub(sload(_totalSupply.slot), value))

            mstore(0x00, value)
            log3(
                0x00,
                0x20,
                0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef,
                caller(),
                0
            )
        }
    }

    function batchBalanceSum(address[] calldata accounts) external view returns (uint256 result) {
        assembly {
            for { let i := 0 } lt(i, accounts.length) { i := add(i, 1) } {
                let account := calldataload(add(accounts.offset, mul(i, 0x20)))
                mstore(0x00, account)
                mstore(0x20, _balances.slot)
                result := add(result, sload(keccak256(0x00, 0x40)))
            }
        }
    }

    function hasCode(address account) external view returns (bool result) {
        assembly {
            result := iszero(iszero(extcodesize(account)))
        }
    }

    function externalBalanceOf(address token, address account) external view returns (uint256 result) {
        assembly {
            let ptr := mload(0x40)
            mstore(ptr, shl(224, 0x70a08231))
            mstore(add(ptr, 0x04), account)
            let success := staticcall(gas(), token, ptr, 0x24, ptr, 0x20)
            if iszero(and(success, eq(returndatasize(), 0x20))) {
                revert(0x00, 0x00)
            }
            result := mload(ptr)
        }
    }
}
