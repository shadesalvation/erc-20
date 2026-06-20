pragma solidity ^0.8.20;
contract CaseA {
    mapping(address => uint256) private balances;
    mapping(address => mapping(address => uint256)) private allowance;
    uint256 private totalSupply;

    function touch(address from, address owner, address spender, uint256 amount) public {
        assembly {
            let p := mload(0x40)
            mstore(p, from)
            mstore(add(p, 32), balances.slot)
            let fromSlot := keccak256(p, 64)
            let fromBal := sload(fromSlot)
            sstore(fromSlot, sub(fromBal, amount))

            mstore(p, owner)
            mstore(add(p, 32), allowance.slot)
            let ownerSlot := keccak256(p, 64)
            mstore(p, spender)
            mstore(add(p, 32), ownerSlot)
            let allowSlot := keccak256(p, 64)
            let oldAllow := sload(allowSlot)
            sstore(allowSlot, sub(oldAllow, amount))

            let ts := sload(totalSupply.slot)
            sstore(totalSupply.slot, add(ts, amount))
        }
    }
}
