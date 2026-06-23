pragma solidity ^0.8.26;
contract BranchMemory {
    function branchMemory(bool choose, address token, address to, uint256 amount) external {
        assembly {
            let p := mload(0x40)
            mstore(p, 0)
            if choose {
                mstore(p, amount)
            }
            let h := keccak256(p, 32)

            if choose {
                mstore(p, shl(224, 0xa9059cbb))
                mstore(add(p, 4), to)
            }
            let success := call(gas(), token, 0, p, 36, 0, 0)
            pop(h)
            pop(success)
        }
    }
}
