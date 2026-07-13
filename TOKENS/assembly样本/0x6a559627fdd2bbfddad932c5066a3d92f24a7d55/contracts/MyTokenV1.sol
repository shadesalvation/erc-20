// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Initializable} from "../lib/oz-v2/contracts/proxy/utils/Initializable.sol";
import {ERC20Upgradeable} from "../lib/oz-v2/contracts/token/ERC20/ERC20Upgradeable.sol";
import {OwnableUpgradeable} from "../lib/oz-v2/contracts/access/OwnableUpgradeable.sol";
import {UUPSUpgradeable} from "../lib/oz-v2/contracts/proxy/utils/UUPSUpgradeable.sol";

contract MyTokenV1 is
    Initializable,
    ERC20Upgradeable,
    OwnableUpgradeable,
    UUPSUpgradeable
{
    uint8 private constant _DECIMALS = 18;
    uint256[49] private __gap;

    function initialize(
        string memory name_,
        string memory symbol_,
        address initialRecipient,
        uint256 initialSupply
    ) public initializer {
        __ERC20_init(name_, symbol_);
        __Ownable_init(msg.sender);
        __UUPSUpgradeable_init();

        _mint(initialRecipient, initialSupply);
    }

    function decimals() public pure override returns (uint8) {
        return _DECIMALS;
    }

    function _authorizeUpgrade(
        address newImplementation
    ) internal virtual override onlyOwner {}
}
