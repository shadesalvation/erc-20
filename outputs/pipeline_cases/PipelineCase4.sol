pragma solidity ^0.8.20;
contract PipelineCase4 {
    mapping(bytes32 => uint256) private claims;
    uint256 private marker;
    function f() public {
        assembly {
            let p := mload(0x40)
            calldatacopy(p, 4, 32)
            mstore(add(p, 32), claims.slot)
            let slot := keccak256(p, 64)
            if iszero(sload(slot)) {
                revert(0, 0)
            }
        }
        assembly {
            let p := mload(0x40)
            let h := keccak256(p, 32)
            sstore(marker.slot, h)
        }
    }
}
