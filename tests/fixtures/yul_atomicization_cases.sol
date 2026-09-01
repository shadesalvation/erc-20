// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract YulAtomicizationCases {
    uint256 private total;

    function update(uint256 amount) external {
        assembly {
            let slot := total.slot
            sstore(slot, add(sload(slot), amount))
        }
    }

    function guardedCall(address target, bytes32 word) external returns (bool ok) {
        assembly {
            mstore(0x00, word)
            if iszero(and(eq(mload(0x00), 1), call(gas(), target, 0, 0x00, 0x20, 0x00, 0x20))) {
                revert(0, 0)
            }
            ok := 1
        }
    }

    function compute(uint256 a, uint256 b, uint256 c, uint256 d) external pure returns (uint256 result) {
        assembly {
            result := add(mul(a, b), sub(c, d))
        }
    }
}
