// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

contract MultiMappingParity {
    mapping(address => mapping(bytes32 => mapping(uint256 => uint256))) public ledger;

    function solidityRead(address owner, bytes32 bucket, uint256 index)
        external
        view
        returns (uint256 value)
    {
        value = ledger[owner][bucket][index];
    }

    function solidityWrite(address owner, bytes32 bucket, uint256 index, uint256 value) external {
        ledger[owner][bucket][index] = value;
    }

    function solidityUpdate(address owner, bytes32 bucket, uint256 index, uint256 delta) external {
        ledger[owner][bucket][index] = ledger[owner][bucket][index] + delta;
    }

    function yulRead(address owner, bytes32 bucket, uint256 index)
        external
        view
        returns (uint256 value)
    {
        assembly {
            mstore(0x00, owner)
            mstore(0x20, ledger.slot)
            let ownerSlot := keccak256(0x00, 0x40)

            mstore(0x00, bucket)
            mstore(0x20, ownerSlot)
            let bucketSlot := keccak256(0x00, 0x40)

            mstore(0x00, index)
            mstore(0x20, bucketSlot)
            value := sload(keccak256(0x00, 0x40))
        }
    }

    function yulWrite(address owner, bytes32 bucket, uint256 index, uint256 value) external {
        assembly {
            mstore(0x00, owner)
            mstore(0x20, ledger.slot)
            let ownerSlot := keccak256(0x00, 0x40)

            mstore(0x00, bucket)
            mstore(0x20, ownerSlot)
            let bucketSlot := keccak256(0x00, 0x40)

            mstore(0x00, index)
            mstore(0x20, bucketSlot)
            sstore(keccak256(0x00, 0x40), value)
        }
    }

    function yulUpdate(address owner, bytes32 bucket, uint256 index, uint256 delta) external {
        assembly {
            mstore(0x00, owner)
            mstore(0x20, ledger.slot)
            let ownerSlot := keccak256(0x00, 0x40)

            mstore(0x00, bucket)
            mstore(0x20, ownerSlot)
            let bucketSlot := keccak256(0x00, 0x40)

            mstore(0x00, index)
            mstore(0x20, bucketSlot)
            let valueSlot := keccak256(0x00, 0x40)
            sstore(valueSlot, add(sload(valueSlot), delta))
        }
    }
}
