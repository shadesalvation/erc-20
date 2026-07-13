// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {BaseToken} from "./BaseToken.sol";

contract BscToken is BaseToken {
    uint256 public constant VERSION = 20251104_000000_000;

    constructor() BaseToken("BscToken", "BT") {
        _mint(tx.origin, 100 * 10 ** 18);
    }
}
