// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

import "../@openzeppelin/contracts/token/ERC20/ERC20.sol";

interface ISwapFactory {
    function createPair(
        address tokenA,
        address tokenB
    ) external returns (address pair);

    function getPair(
        address tokenA,
        address tokenB
    ) external view returns (address pair);
    function feeTo() external view returns (address);
}

interface ISwapRouter {
    function factory() external pure returns (address);

    function WETH() external pure returns (address);
}

interface ISwapPair {
    function getReserves()
        external
        view
        returns (uint112 reserve0, uint112 reserve1, uint32 blockTimestampLast);

    function token0() external view returns (address);

    function balanceOf(address account) external view returns (uint256);

    function kLast() external view returns (uint);

    function totalSupply() external view returns (uint256);
}

contract TOKEN is ERC20 {

    address private WETH = 0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c;
    address private ROUTER = 0x10ED43C718714eb63d5aA57B78B54704E256024E;


    constructor(
        string memory _name,
        string memory _symbol,
        uint256 _number,
        address _toAddress
    ) ERC20(_name, _symbol) {

        _mint(_toAddress, _number * (10 ** 18));

        ISwapRouter swapRouter = ISwapRouter(ROUTER);
        ISwapFactory swapFactory = ISwapFactory(swapRouter.factory());
        address swapPair = swapFactory.createPair(address(this), WETH);

    }

}
