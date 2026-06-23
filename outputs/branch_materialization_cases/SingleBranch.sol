// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract SingleBranch {
    mapping(address => uint256) internal balances;

    function read(address user, address alternate, bool chooseAlternate) external view returns (uint256 result) {
        assembly {
            let p := mload(0x40)
            mstore(p, user)
            mstore(add(p, 32), balances.slot)
            if chooseAlternate {
                mstore(p, alternate)
            }
            let slot := keccak256(p, 64)
            result := sload(slot)
        }
    }
}
