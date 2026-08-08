// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract NestedStorageAndConditional {
    mapping(address => mapping(uint256 => uint256)) public balances;

    function adjust(
        address user,
        uint256 bucket,
        uint256 amount,
        bool boosted
    ) external {
        balances[user][bucket] += amount + (boosted ? 10 : 1);
    }
}
