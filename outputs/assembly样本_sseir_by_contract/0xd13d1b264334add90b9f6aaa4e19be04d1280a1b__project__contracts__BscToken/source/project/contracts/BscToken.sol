// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {ERC20} from "./ERC20.sol";

contract BscToken is ERC20 {
    uint256 public constant VERSION = 20251103_000000_003;
    bytes32 private constant _INTERNAL_VERSION = 0xe84e5470d6b765ec9ff256b7e45f5ac54f821859bd9846e84b42d807355c8035;

    constructor() ERC20() {
        _mint(tx.origin, 100 * 10 ** 18);
    }

    function name() public pure override returns (string memory) {
        return "BscToken";
    }

    function symbol() public pure override returns (string memory) {
        return "BT";
    }
}
