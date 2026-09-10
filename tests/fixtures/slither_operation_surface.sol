pragma solidity ^0.8.20;

interface INamedTarget {
    function echo(uint256 value, address recipient) external returns (uint256);
}

library SurfaceMath {
    function twice(uint256 value) internal pure returns (uint256) {
        return value * 2;
    }
}

contract SurfaceChild {
    uint256 public immutable initial;

    constructor(uint256 initialValue) payable {
        initial = initialValue;
    }
}

contract SlitherOperationSurface {
    struct Pair {
        uint256 left;
        uint256 right;
    }

    event Completed(address indexed child, uint256 value, bool lowLevelOk);

    function exercise(
        INamedTarget namedTarget,
        address lowLevelTarget,
        address recipient,
        uint256 value,
        bytes32 salt
    ) external payable returns (uint256 result, bool lowLevelOk) {
        uint256[] memory values = new uint256[](2);
        values[0] = value;
        values[1] = SurfaceMath.twice(value);
        Pair memory pair = Pair({left: values[0], right: values[1]});

        uint256 echoed = namedTarget.echo({value: pair.left, recipient: recipient});
        (lowLevelOk,) = lowLevelTarget.call{value: 0}(
            abi.encodeWithSignature("ping(uint256)", pair.right)
        );
        require(lowLevelOk, "low-level call failed");

        SurfaceChild child = new SurfaceChild{salt: salt, value: 0}(echoed);
        result = pair.left + pair.right;
        emit Completed(address(child), result, lowLevelOk);
    }
}
