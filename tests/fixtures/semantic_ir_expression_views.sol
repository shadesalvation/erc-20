// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract SemanticIRExpressionViews {
    uint256 private total;

    function update(uint256 amount) external {
        assembly {
            let slot := total.slot
            sstore(slot, add(sload(slot), amount))
        }
    }

    function environment() external view returns (address who, uint256 gasRemaining) {
        assembly {
            who := caller()
            gasRemaining := gas()
        }
    }

    function hashBytes(bytes memory data) external pure returns (bytes32 digest) {
        assembly {
            digest := keccak256(add(data, 0x20), mload(data))
        }
    }

    function storeHash(bytes memory data) external {
        assembly {
            sstore(total.slot, keccak256(add(data, 0x20), mload(data)))
        }
    }

    function guardedCall(address target, bytes32 word) external returns (bool ok) {
        assembly {
            mstore(0x00, word)
            if iszero(call(gas(), target, 0, 0x00, 0x20, 0x00, 0x20)) {
                revert(0x00, 0x00)
            }
            ok := 1
        }
    }

    function rawReturn(uint256 value) external pure {
        assembly {
            mstore(0x00, value)
            return(0x00, 0x20)
        }
    }
}
