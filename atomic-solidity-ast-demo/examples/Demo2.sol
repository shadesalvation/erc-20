// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Demo2 {
    function calc(uint256 a, uint256 b, uint256 c, uint256 d)
        public
        pure
        returns (uint256)
    {
        return (a + b) * (c - d);
    }
}

