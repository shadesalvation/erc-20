// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract SlitherBaseSurface {
    uint256 internal baseValue;

    constructor(uint256 initialValue) {
        baseValue = initialValue;
    }

    function describe(uint256 value) public virtual returns (uint256) {
        return baseValue + value;
    }
}

contract SlitherInheritanceSurface is SlitherBaseSurface {
    uint256 public receivedValue;
    uint256 public fallbackCount;

    constructor(uint256 initialReceived) SlitherBaseSurface(7) {
        receivedValue = initialReceived;
    }

    function describe(uint256 value) public override returns (uint256) {
        return baseValue * value;
    }

    receive() external payable {
        receivedValue += msg.value;
    }

    fallback() external payable {
        fallbackCount += 1;
    }
}
