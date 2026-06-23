// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract NestedBranch {
    mapping(address => uint256) internal balances;

    function read(
        address user,
        address alternate,
        address overrideUser,
        bool first,
        bool second
    ) external view returns (uint256 result) {
        assembly {
            let p := mload(0x40)
            mstore(p, user)
            mstore(add(p, 32), balances.slot)
            if first {
                mstore(p, alternate)
                if second {
                    mstore(p, overrideUser)
                }
            }
            let slot := keccak256(p, 64)
            result := sload(slot)
        }
    }
}
