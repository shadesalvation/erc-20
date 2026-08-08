// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract LoopShortCircuit {
    function scan(uint256 n, bool enabled)
        external
        pure
        returns (uint256 total)
    {
        uint256 i = 0;
        while (enabled && i < n) {
            total += i % 2 == 0 ? i * 2 : i + 1;
            i++;
        }
    }
}
