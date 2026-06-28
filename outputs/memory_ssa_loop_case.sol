// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.27;

contract AssemblyMemorySsaLoopCase {
    function buildAndHash(uint256 words) external pure returns (bytes32 digest, uint256 lastWord) {
        assembly {
            let ptr := mload(0x40)
            let size := mul(words, 0x20)
            for { let i := 0 } lt(i, size) { i := add(i, 0x20) } {
                mstore(add(ptr, i), add(i, 1))
            }
            lastWord := mload(add(ptr, sub(size, 0x20)))
            digest := keccak256(ptr, size)
        }
    }
}
