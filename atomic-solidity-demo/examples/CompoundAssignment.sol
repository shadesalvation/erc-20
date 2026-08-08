// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract CompoundAssignment {
    mapping(address => uint256) public balances;

    function addBalance(address user, uint256 amount) external {
        balances[user] += amount;
    }
}
