pragma solidity ^0.8.20;
contract PipelineCase3 {
    uint256 private gate;
    mapping(uint256 => uint256) private data;
    function f(uint256 x, uint256 amount) public {
        assembly {
            let p := mload(0x40)
            if gt(amount, 10) {
                let t := timestamp()
                if eq(t, mul(t, 1)) {
                    revert(0, 0)
                }
            }
            mstore(p, x)
            mstore(add(p, 32), data.slot)
            let slot := keccak256(p, 64)
            sstore(slot, add(sload(slot), amount))
            if iszero(sload(gate.slot)) {
                revert(0, 0)
            }
        }
    }
}
