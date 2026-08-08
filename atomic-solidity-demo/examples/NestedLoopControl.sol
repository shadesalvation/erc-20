// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract NestedLoopControl {
    function sum(uint256 n, uint256 limit)
        external
        pure
        returns (uint256 total)
    {
        for (uint256 i = 0; i < n; i++) {
            uint256 j = 0;
            while (j < n) {
                j++;
                if (j == i) {
                    continue;
                }
                total += i + j;
                if (total > limit) {
                    break;
                }
            }
        }
    }
}
