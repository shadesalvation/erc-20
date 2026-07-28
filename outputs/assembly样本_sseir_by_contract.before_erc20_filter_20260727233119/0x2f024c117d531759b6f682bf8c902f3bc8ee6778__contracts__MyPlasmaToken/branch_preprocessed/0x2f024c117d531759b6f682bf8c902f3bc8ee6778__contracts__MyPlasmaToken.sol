// // SPDX-License-Identifier: MIT
// pragma solidity ^0.8.27;

// import "./PlasmaToken.sol";
// import "@openzeppelin/contracts-upgradeable/access/OwnableUpgradeable.sol";
// import "@openzeppelin/contracts-upgradeable/proxy/utils/UUPSUpgradeable.sol";
// import "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";

// contract MyPlasmaToken is Initializable, OwnableUpgradeable, UUPSUpgradeable,PlasmaToken {
//     function initialize(
//         string memory name,
//         string memory symbol,
//         uint256 initialSupply,
//         address receiver
//     ) public initializer {
//         __Token_init(name, symbol, initialSupply, receiver);
//         __Ownable_init(msg.sender);
//         __UUPSUpgradeable_init();
//     }

//     function _authorizeUpgrade(
//         address newImplementation
//     ) internal override onlyOwner {}
// }
