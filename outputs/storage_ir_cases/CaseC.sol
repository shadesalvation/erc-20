pragma solidity ^0.8.20;
contract CaseC {
    mapping(uint256 => uint256) private dynamicMap;
    uint256 private control;

    function touch(uint256 off, uint256 value) public {
        assembly {
            let p := mload(0x40)
            mstore(p, 7)
            mstore(add(p, off), value)
            for { let i := 0 } lt(i, 2) { i := add(i, 1) } {
                mstore(add(p, mul(i, 32)), add(value, i))
            }
            mstore(add(p, 32), dynamicMap.slot)
            let dynSlot := keccak256(p, 64)
            let old := sload(dynSlot)
            sstore(dynSlot, add(old, value))
            sstore(control.slot, sload(dynSlot))
        }
    }
}
