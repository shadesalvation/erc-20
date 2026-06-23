pragma solidity ^0.8.26;
contract ExternalCallCases {
    function tokenCall(address token, address to, uint256 amount) external {
        assembly {
            let p := mload(0x40)
            mstore(p, shl(224, 0xa9059cbb))
            mstore(add(p, 4), to)
            mstore(add(p, 36), amount)
            let success := call(gas(), token, 0, p, 68, 0, 0)
            if iszero(success) { revert(0, 0) }
        }
    }

    function hashCall(bytes32 input) external {
        assembly {
            let p := mload(0x40)
            let out := add(p, 32)
            mstore(p, input)
            if iszero(staticcall(gas(), 2, p, 32, out, 32)) { revert(0, 0) }
        }
    }

    function proxyCall(address implementation, address to) external {
        assembly {
            let p := mload(0x40)
            mstore(p, shl(224, 0x12345678))
            mstore(add(p, 4), to)
            let ok := delegatecall(gas(), implementation, p, 36, 0, 0)
            if iszero(ok) { revert(0, 0) }
        }
    }
}
