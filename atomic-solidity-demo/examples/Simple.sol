// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract Simple {
    function calculate(
        uint256 a,
        uint256 b,
        uint256 c,
        uint256 d
    ) external pure returns (uint256 result) {
        result = (a + b) * (c - d);
    }
}
