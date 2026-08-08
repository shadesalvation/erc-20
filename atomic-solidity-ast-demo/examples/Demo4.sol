// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Demo4 {
    function test(uint256 a, uint256 b, uint256 c)
        public
        pure
        returns (uint256)
    {
        if ((a + b) > c) {
            return a;
        }

        return b;
    }
}

