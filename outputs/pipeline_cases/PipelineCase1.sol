pragma solidity ^0.8.20;
contract PipelineCase1 {
    mapping(address => uint256) private bal;
    function f(address a, uint256 amount) public {
        assembly {
            if iszero(a) {
                revert(0, 0)
            }
            let p := mload(0x40)
            mstore(p, a)
            mstore(add(p, 32), bal.slot)
            let slot := keccak256(p, 64)
            let old := sload(slot)
            if lt(old, amount) {
                revert(0, 0)
            }
            sstore(slot, sub(old, amount))
        }
    }
}
