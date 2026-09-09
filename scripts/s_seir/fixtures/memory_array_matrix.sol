// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract MemoryArrayMatrix {
    function sumMatrix(uint256[][] memory matrix) external pure returns (uint256 total) {
        assembly {
            let outerLength := mload(matrix)
            for { let i := 0 } lt(i, outerLength) { i := add(i, 1) } {
                let row := mload(add(matrix, add(0x20, mul(i, 0x20))))
                let rowLength := mload(row)
                for { let j := 0 } lt(j, rowLength) { j := add(j, 1) } {
                    let value := mload(add(row, add(0x20, mul(j, 0x20))))
                    total := add(total, value)
                }
            }
        }
    }
}
