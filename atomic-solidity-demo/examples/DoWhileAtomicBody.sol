// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract DoWhileAtomicBody {
    function halve(uint256 value)
        external
        pure
        returns (uint256 result, uint256 steps)
    {
        result = value;
        do {
            result = result / 2;
            steps += 1;
        } while (result > 1);
    }
}
