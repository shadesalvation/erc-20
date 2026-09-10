// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IControlProbe {
    function probe(uint256 value) external returns (uint256);
}

contract SlitherControlSurface {
    uint256 public total;
    uint256 public marker;

    modifier aboveZero(uint256 value) {
        require(value > 0, "zero");
        marker = 1;
        _;
        marker = 2;
    }

    function addOne(uint256 value) internal pure returns (uint256) {
        return value + 1;
    }

    function addTwo(uint256 value) internal pure returns (uint256) {
        return value + 2;
    }

    function dynamicDispatch(uint256 value, bool chooseFirst) external returns (uint256) {
        function(uint256) internal pure returns (uint256) selected = chooseFirst ? addOne : addTwo;
        uint256 result = selected(value);
        total = result;
        return result;
    }

    function directInternal(uint256 value) external returns (uint256) {
        uint256 result = addOne(value);
        total = result;
        return result;
    }

    function loopControl(uint256 limit) external returns (uint256) {
        for (uint256 i = 0; i < limit; i++) {
            if (i == 2) continue;
            if (i == 5) break;
            total += i;
        }
        return total;
    }

    function guarded(uint256 value) external aboveZero(value) returns (uint256) {
        total = value;
        return total;
    }

    function recover(IControlProbe target, uint256 value) external returns (uint256) {
        try target.probe(value) returns (uint256 received) {
            total = received;
            return received;
        } catch Error(string memory) {
            total = value;
            return value;
        } catch (bytes memory) {
            total = 0;
            return 0;
        }
    }
}
