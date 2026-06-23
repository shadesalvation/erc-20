pragma solidity ^0.8.26;
contract DivisionCase {
    uint256 private result;

    function divide(uint256 a, uint256 b) external {
        assembly {
            let quotient := div(a, b)
            if lt(quotient, 2) {
                revert(0, 0)
            }
            sstore(result.slot, mod(a, b))
        }
    }
}
