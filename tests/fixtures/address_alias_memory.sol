// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract AddressAliasMemory {
    function simpleAlias() external pure returns (uint256 x) {
        assembly {
            let base0 := mload(0x40)
            let base1 := add(base0, 4)
            mstore(add(base1, 4), 0x11)
            x := mload(add(base0, 8))
        }
    }

    function loopAlias() external pure returns (uint256 x) {
        assembly {
            let base0 := mload(0x40)
            let i := 0
            for { } lt(i, 3) { i := add(i, 1) } {
                let base1 := add(base0, 4)
                mstore(add(base1, 4), i)
            }
            x := mload(add(base0, 8))
        }
    }

    function subAlias() external pure returns (uint256 x) {
        assembly {
            let base0 := mload(0x40)
            let base1 := add(base0, 0x20)
            let addr := sub(base1, 4)
            mstore(add(addr, 8), 0x22)
            x := mload(add(base0, 36))
        }
    }
}
