// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.27;

import {TransparentUpgradeableProxy} from "@openzeppelin/contracts/proxy/transparent/TransparentUpgradeableProxy.sol";

contract XProxy is TransparentUpgradeableProxy {
    constructor(
        address _logic,
        address initialOwner,
        bytes memory _data
    ) TransparentUpgradeableProxy(_logic, initialOwner, _data) {}
}
