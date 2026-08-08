// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract UnsupportedDemo {
    function f(bool a, bool b)
        public
        pure
        returns (bool)
    {
        return a && b;
    }
}

