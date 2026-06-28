pragma solidity ^0.8.26;

contract LoopMemorySSA {
    function probe(uint256 count) external pure returns (uint256 result) {
        assembly {
            let p := 0x80
            mstore(p, 0)

            for { let i := 0 } lt(i, count) { i := add(i, 1) } {
                mstore(p, add(mload(p), 1))
            }

            result := mload(p)
        }
    }
}
