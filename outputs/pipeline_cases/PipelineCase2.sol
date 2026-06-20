pragma solidity ^0.8.20;
contract PipelineCase2 {
    mapping(address => mapping(address => uint256)) private allow;
    function f(address owner, address spender, uint256 amount) public {
        assembly {
            let p := mload(0x40)
            mstore(p, owner)
            mstore(add(p, 32), allow.slot)
            let ownerSlot := keccak256(p, 64)
            mstore(p, spender)
            mstore(add(p, 32), ownerSlot)
            let allowSlot := keccak256(p, 64)
            let current := sload(allowSlot)
            if iszero(eq(current, not(0))) {
                if lt(current, amount) {
                    revert(0, 0)
                }
                sstore(allowSlot, sub(current, amount))
            }
        }
    }
}
