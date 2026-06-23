// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract DeepNestedBranch {
    mapping(address => uint256) internal balances;

    function read(
        address user,
        address firstUser,
        address secondUser,
        address thirdUser,
        bool first,
        bool second,
        bool third
    ) external view returns (uint256 result) {
        assembly {
            let p := mload(0x40)
            mstore(p, user)
            mstore(add(p, 32), balances.slot)
            if first {
                mstore(p, firstUser)
                if second {
                    mstore(p, secondUser)
                    if third {
                        mstore(p, thirdUser)
                    }
                }
            }
            let slot := keccak256(p, 64)
            result := sload(slot)
        }
    }
}
