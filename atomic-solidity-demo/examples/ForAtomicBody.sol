// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract ForAtomicBody {
    function accumulate(uint256 n, uint256 seed)
        external
        pure
        returns (uint256 total)
    {
        total = seed;
        for (uint256 i = 0; i < n; i++) {
            uint256 term = (i + 1) * (seed + 2);
            total += term;
        }
    }
}
