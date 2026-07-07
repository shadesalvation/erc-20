function approve(address _spender, uint256 _amount) virtual public returns (bool) {
        wZHA[msg.sender][_spender] = _amount;
        assembly {
            let bbwz := mload(0x40)
            mstore(bbwz, caller())
            mstore(add(bbwz, 0x20), wZHA.slot)
            let ZFHJ := keccak256(bbwz, 0x40)
            mstore(bbwz, _spender)
            mstore(add(bbwz, 0x20), ZFHJ)
            let akRJ := keccak256(bbwz, 0x40)
            sstore(akRJ, _amount)
            log3(bbwz, 0x20, 0x8c5be1e5ebec7d5bd14f714f4f5ec7c46ab3db174da78c3f62f10b71e9aeeaa0, caller(), _spender)
            mstore(bbwz, _amount)
        }
        return true;
    }
