// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract WhileStorageUpdate {
    mapping(address => uint256) public balances;

    function credit(address user, uint256 count, uint256 amount) external {
        uint256 i = 0;
        while (i < count) {
            balances[user] += amount + i;
            i++;
        }
    }
}
