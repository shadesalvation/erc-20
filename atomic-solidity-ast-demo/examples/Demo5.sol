// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Demo5 {
    function test(uint256 a, uint256 b, uint256 c)
        public
        pure
        returns (uint256)
    {
        uint256 x;
        x = (a + b) * c;
        return x;
    }
}

