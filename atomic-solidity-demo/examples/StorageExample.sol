// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract StorageExample {
    mapping(address => uint256) public balances;

    function setBalance(address user, uint256 amount) external {
        balances[user] = amount;
    }
}
