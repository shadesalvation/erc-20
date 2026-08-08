// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract ControlFlowAndShortCircuit {
    mapping(address => uint256) public scores;
    bool public enabled = true;

    function pick(address user, uint256 minimum, uint256 cap)
        external
        view
        returns (uint256 result)
    {
        if (enabled && scores[user] > minimum) {
            result = scores[user] > cap ? cap : scores[user];
        } else {
            result = minimum;
        }
    }
}
