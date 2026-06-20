pragma solidity ^0.8.20;
contract CaseB {
    mapping(bytes32 => uint256) private claims;
    mapping(address => mapping(bytes32 => uint256)) private used;

    function touch(address user) public {
        assembly {
            let p := mload(0x40)
            calldatacopy(p, 4, 32)
            mstore(add(p, 32), claims.slot)
            let claimSlot := keccak256(p, 64)
            let claim := sload(claimSlot)

            mstore(p, user)
            mstore(add(p, 32), used.slot)
            let userSlot := keccak256(p, 64)
            calldatacopy(p, 36, 32)
            mstore(add(p, 32), userSlot)
            let usedSlot := keccak256(p, 64)
            sstore(usedSlot, add(sload(usedSlot), claim))
        }
    }
}
