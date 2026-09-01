// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract SemanticIRMemoryDefUseComplex {
    mapping(address => mapping(uint256 => uint256)) public ledger;

    function update(
        address user,
        uint256 bucket,
        uint256 amount,
        bool discount,
        bool normalizeBucket
    ) external returns (uint256 result) {
        uint256 delta = amount;
        if (discount) {
            delta = amount - 1;
        }

        assembly {
            let free := mload(0x40)
            let ptr := add(free, 0x20)

            // Resolve ledger[user].
            mstore(ptr, user)
            mstore(add(ptr, 0x20), ledger.slot)
            let outerSlot := keccak256(ptr, 0x40)

            // Resolve ledger[user][bucket], with a path-sensitive overwrite.
            mstore(ptr, bucket)
            if normalizeBucket {
                mstore(ptr, 1)
            }
            mstore(add(ptr, 0x20), outerSlot)
            let leafSlot := keccak256(ptr, 0x40)

            let oldValue := sload(leafSlot)
            let newValue := add(oldValue, delta)
            sstore(leafSlot, newValue)
            result := newValue
        }
    }
}
