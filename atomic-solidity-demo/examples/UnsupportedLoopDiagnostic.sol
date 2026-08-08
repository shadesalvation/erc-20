// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract UnsupportedLoopDiagnostic {
    function sum(uint256 n) external pure returns (uint256 total) {
        for (uint256 i = 0; i < n; i++) {
            total += i;
        }
    }
}
