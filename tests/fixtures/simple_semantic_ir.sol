// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract SimpleSemanticIR {
    uint256 public total;
    mapping(address => uint256) public balances;

    function deposit(uint256 amount) external {
        total = total + amount;

        assembly {
            mstore(0x00, caller())
            mstore(0x20, balances.slot)
            let balanceSlot := keccak256(0x00, 0x40)
            sstore(balanceSlot, add(sload(balanceSlot), amount))
        }
    }
}
