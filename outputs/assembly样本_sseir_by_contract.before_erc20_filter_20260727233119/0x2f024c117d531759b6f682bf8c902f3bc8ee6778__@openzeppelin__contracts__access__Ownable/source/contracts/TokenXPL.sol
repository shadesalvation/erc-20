// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

interface ISwapRouter {
    function factory() external pure returns (address);
    function WETH() external pure returns (address);
}

interface ISwapFactory {
    function createPair(
        address tokenA,
        address tokenB
    ) external returns (address pair);
    function getPair(
        address tokenA,
        address tokenB
    ) external view returns (address pair);
}

contract TOKEN is ERC20, Ownable {

    address private constant _WETH = 0x6100E367285b01F48D07953803A2d8dCA5D19873;
    address private constant _ROUTER = 0xfc9869eF6E04e8dcF09234Ad0bC48a6f78a493cC;  
  
    constructor(
        string memory name_,
        string memory symbol_,
        uint256 number,
        address toAddress
    ) ERC20(name_, symbol_) Ownable(msg.sender) {

        _mint(toAddress, number * (10 ** 18));

        ISwapRouter swapRouter = ISwapRouter(_ROUTER);
        ISwapFactory swapFactory = ISwapFactory(swapRouter.factory());
        swapFactory.createPair(address(this), _WETH);

        renounceOwnership();
    }

}
