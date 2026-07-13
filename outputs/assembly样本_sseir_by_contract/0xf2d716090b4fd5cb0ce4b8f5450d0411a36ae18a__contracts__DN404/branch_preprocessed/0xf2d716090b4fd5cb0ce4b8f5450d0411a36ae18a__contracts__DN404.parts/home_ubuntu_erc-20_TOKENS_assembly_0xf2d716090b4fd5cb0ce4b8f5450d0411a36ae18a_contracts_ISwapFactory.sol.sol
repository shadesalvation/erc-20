// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;




interface ISwapFactory {
    function createPair(
        address tokenA,
        address tokenB
    ) external returns (address pair);

}