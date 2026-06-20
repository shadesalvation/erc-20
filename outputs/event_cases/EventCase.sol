pragma solidity ^0.8.26;
contract EventCase {
    event Approval(address indexed owner, address indexed spender, uint256 value);
    function approveLike(address _spender, uint256 _amount) public {
        assembly {
            let p := mload(0x40)
            mstore(p, _amount)
            log3(p, 0x20, 0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925, caller(), _spender)
        }
    }
}
