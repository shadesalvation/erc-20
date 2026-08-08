// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Demo3 {
    function check(uint256 a, uint256 b)
        public
        pure
        returns (bool)
    {
        return !(a + 1 > b);
    }
}

