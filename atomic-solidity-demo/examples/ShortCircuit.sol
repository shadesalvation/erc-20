// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract ShortCircuit {
    function checkA() internal pure returns (bool) {
        return false;
    }

    function checkB() internal pure returns (bool) {
        return true;
    }

    function test() external pure returns (bool result) {
        result = checkA() && checkB();
    }
}
