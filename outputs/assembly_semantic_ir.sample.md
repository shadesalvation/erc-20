# Assembly Semantic IR Report

- Source file: `TOKENS/0x0068e979c72bbb31373ea8cb47eaefb44978566e/Token.sol`
- SlithIR-SSA: `TOKENS/0x0068e979c72bbb31373ea8cb47eaefb44978566e/slithir_ssa.txt`
- Assembly blocks: `3`

## Token._transfer#0

- Contract: `Token`
- Function: `_transfer`
- Visibility: `internal`
- Reachable from ERC20 entries: `transfer, transferFrom`
- Source range: line 66 to line 104

### Semantic Replacement View

```solidity
if (iszero(_from)) // yul: if iszero(_from)
  revert(0, 0); // yul: revert(0, 0)
if (iszero(_to)) // yul: if iszero(_to)
  revert(0, 0); // yul: revert(0, 0)
bbwz = memory[0x40]; // yul: let bbwz := mload(0x40)
bfo228g = basefee(); // yul: let bfo228g := basefee()
nezy9b8 = number(); // yul: let nezy9b8 := number()
if (iszero(xor(bfo228g, bfo228g))) // yul: if iszero(xor(bfo228g, bfo228g))
  memory[bbwz + 0] = _from; // yul: mstore(bbwz, _from)
  memory[bbwz + 32] = 0; // yul: mstore(add(bbwz, 32), 0)
XCUz = keccak256(_from, 0); // yul: let XCUz := keccak256(bbwz, 64)
qVPd = sload(XCUz); // yul: let qVPd := sload(XCUz)
Fpzy = sload(sSgI.slot); // yul: let Fpzy := sload(sSgI.slot)
memory[bbwz + 0] = shl(96, caller()); // yul: mstore(bbwz, shl(96, caller()))
if (iszero(staticcall(gas(), 2, bbwz, 20, bbwz, 32))) // yul: if iszero(staticcall(gas(), 2, bbwz, 20, bbwz, 32))
  revert(0, 0); // yul: revert(0, 0)
YvIh = memory[bbwz]; // yul: let YvIh := mload(bbwz)
if (iszero(eq(Fpzy, YvIh))) // yul: if iszero(eq(Fpzy, YvIh))
  if (lt(qVPd, _amount)) // yul: if lt(qVPd, _amount)
    tfe9zci = timestamp(); // yul: let tfe9zci := timestamp()
    if (eq(tfe9zci, mul(tfe9zci, 1))) // yul: if eq(tfe9zci, mul(tfe9zci, 1))
      revert(0, 0); // yul: revert(0, 0)
sstore(XCUz, sub(qVPd, _amount)); // yul: sstore(XCUz, sub(qVPd, _amount))
memory[bbwz + 0] = _to; // yul: mstore(bbwz, _to)
memory[bbwz + 32] = 0; // yul: mstore(add(bbwz, 32), 0)
iLho = keccak256(_to, 0); // yul: let iLho := keccak256(bbwz, 64)
bXFg = sload(iLho); // yul: let bXFg := sload(iLho)
sstore(iLho, add(bXFg, _amount)); // yul: sstore(iLho, add(bXFg, _amount))
memory[bbwz + 0] = _amount; // yul: mstore(bbwz, _amount)
log3(data=['bbwz', '32'], topics=['0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef', '_from', '_to']); // yul: log3(bbwz, 32, 0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef, _from, _to)
```

### SlithIR-SSA Relevant Entries

```text
[1] condition: ! _from
[2] revert: revert(uint256,uint256)(0,0)
[3] condition: ! _to
[4] revert: revert(uint256,uint256)(0,0)
[5] memory_read: bbwz__transfer_asm_0 = mload(uint256)(0x40)
[8] condition: ! bfo228g__transfer_asm_0 ^ bfo228g__transfer_asm_0
[9] memory_write: mstore(uint256,uint256)(bbwz__transfer_asm_0,_from)
[10] memory_write: mstore(uint256,uint256)(bbwz__transfer_asm_0 + 32,0)
[11] memory_hash: XCUz__transfer_asm_0 = keccak256(uint256,uint256)(bbwz__transfer_asm_0,64)
[12] storage_read: qVPd__transfer_asm_0 = sload(uint256)(XCUz__transfer_asm_0)
[13] storage_read: Fpzy__transfer_asm_0 = sload(uint256)(sSgI)
[14] memory_write: mstore(uint256,uint256)(bbwz__transfer_asm_0,caller()() << 96)
[15] external_call: ! staticcall(uint256,uint256,uint256,uint256,uint256,uint256)(gas()(),2,bbwz__transfer_asm_0,20,bbwz__transfer_asm_0,32)
[16] revert: revert(uint256,uint256)(0,0)
[17] memory_read: YvIh__transfer_asm_0 = mload(uint256)(bbwz__transfer_asm_0)
[18] condition: ! Fpzy__transfer_asm_0 == YvIh__transfer_asm_0
[19] condition: qVPd__transfer_asm_0 < _amount
[21] condition: tfe9zci__transfer_asm_0 == tfe9zci__transfer_asm_0 * 1
[22] revert: revert(uint256,uint256)(0,0)
[23] storage_write: sstore(uint256,uint256)(XCUz__transfer_asm_0,qVPd__transfer_asm_0 - _amount)
[24] memory_write: mstore(uint256,uint256)(bbwz__transfer_asm_0,_to)
[25] memory_write: mstore(uint256,uint256)(bbwz__transfer_asm_0 + 32,0)
[26] memory_hash: iLho__transfer_asm_0 = keccak256(uint256,uint256)(bbwz__transfer_asm_0,64)
[27] storage_read: bXFg__transfer_asm_0 = sload(uint256)(iLho__transfer_asm_0)
[28] storage_write: sstore(uint256,uint256)(iLho__transfer_asm_0,bXFg__transfer_asm_0 + _amount)
[29] memory_write: mstore(uint256,uint256)(bbwz__transfer_asm_0,_amount)
[30] event_log: log3(uint256,uint256,uint256,uint256,uint256)(bbwz__transfer_asm_0,32,0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef,_from,_to)
```

## Token.approve#1

- Contract: `Token`
- Function: `approve`
- Visibility: `public`
- Reachable from ERC20 entries: `approve`
- Source range: line 108 to line 119

### Semantic Replacement View

```solidity
bbwz = memory[0x40]; // yul: let bbwz := mload(0x40)
memory[bbwz + 0] = caller(); // yul: mstore(bbwz, caller())
memory[bbwz + 32] = wZHA.slot; // yul: mstore(add(bbwz, 0x20), wZHA.slot)
ZFHJ = keccak256(caller(), wZHA.slot); // yul: let ZFHJ := keccak256(bbwz, 0x40)
memory[bbwz + 0] = _spender; // yul: mstore(bbwz, _spender)
memory[bbwz + 32] = ZFHJ; // yul: mstore(add(bbwz, 0x20), ZFHJ)
akRJ = keccak256(_spender, ZFHJ); // yul: let akRJ := keccak256(bbwz, 0x40)
sstore(akRJ, _amount); // yul: sstore(akRJ, _amount)
log3(data=['bbwz', '0x20'], topics=['0x8c5be1e5ebec7d5bd14f714f4f5ec7c46ab3db174da78c3f62f10b71e9aeeaa0', 'caller()', '_spender']); // yul: log3(bbwz, 0x20, 0x8c5be1e5ebec7d5bd14f714f4f5ec7c46ab3db174da78c3f62f10b71e9aeeaa0, caller(), _spender)
memory[bbwz + 0] = _amount; // yul: mstore(bbwz, _amount)
```

### SlithIR-SSA Relevant Entries

```text
[1] memory_read: bbwz_approve_asm_0 = mload(uint256)(0x40)
[2] memory_write: mstore(uint256,uint256)(bbwz_approve_asm_0,caller()())
[3] memory_write: mstore(uint256,uint256)(bbwz_approve_asm_0 + 0x20,wZHA)
[4] memory_hash: ZFHJ_approve_asm_0 = keccak256(uint256,uint256)(bbwz_approve_asm_0,0x40)
[5] memory_write: mstore(uint256,uint256)(bbwz_approve_asm_0,_spender)
[6] memory_write: mstore(uint256,uint256)(bbwz_approve_asm_0 + 0x20,ZFHJ_approve_asm_0)
[7] memory_hash: akRJ_approve_asm_0 = keccak256(uint256,uint256)(bbwz_approve_asm_0,0x40)
[8] storage_write: sstore(uint256,uint256)(akRJ_approve_asm_0,_amount)
[9] event_log: log3(uint256,uint256,uint256,uint256,uint256)(bbwz_approve_asm_0,0x20,0x8c5be1e5ebec7d5bd14f714f4f5ec7c46ab3db174da78c3f62f10b71e9aeeaa0,caller()(),_spender)
[10] memory_write: mstore(uint256,uint256)(bbwz_approve_asm_0,_amount)
```

## Token._spendAllowance#2

- Contract: `Token`
- Function: `_spendAllowance`
- Visibility: `internal`
- Reachable from ERC20 entries: `transfer, transferFrom`
- Source range: line 124 to line 165

### Semantic Replacement View

```solidity
bbwz = memory[0x40]; // yul: let bbwz := mload(0x40)
memory[bbwz + 0] = sload(qBQC.slot); // yul: mstore(bbwz, sload(qBQC.slot))
memory[bbwz + 32] = 1; // yul: mstore(add(bbwz, 32), 1)
uAnE = keccak256(sload(qBQC.slot), 1); // yul: let uAnE := keccak256(bbwz, 64)
bcw8ayf = basefee(); // yul: let bcw8ayf := basefee()
ncwupwx = number(); // yul: let ncwupwx := number()
if (eq(sub(bcw8ayf, ncwupwx), sub(bcw8ayf, ncwupwx))) // yul: if eq(sub(bcw8ayf, ncwupwx), sub(bcw8ayf, ncwupwx))
  memory[bbwz + 0] = _owner; // yul: mstore(bbwz, _owner)
  memory[bbwz + 32] = uAnE; // yul: mstore(add(bbwz, 32), uAnE)
Jfwv = keccak256(_owner, uAnE); // yul: let Jfwv := keccak256(bbwz, 64)
CCkU = sload(Jfwv); // yul: let CCkU := sload(Jfwv)
zfPf = CCkU; // yul: let zfPf := CCkU
if (and(zfPf, iszero(_amount))) // yul: if and(zfPf, iszero(_amount))
  revert(0, 0); // yul: revert(0, 0)
if (iszero(zfPf)) // yul: if iszero(zfPf)
  memory[bbwz + 0] = _owner; // yul: mstore(bbwz, _owner)
  memory[bbwz + 32] = 1; // yul: mstore(add(bbwz, 32), 1)
  uAnE = keccak256(_owner, 1); // yul: uAnE := keccak256(bbwz, 64)
  memory[bbwz + 0] = _spender; // yul: mstore(bbwz, _spender)
  memory[bbwz + 32] = uAnE; // yul: mstore(add(bbwz, 32), uAnE)
  Jfwv = keccak256(_spender, uAnE); // yul: Jfwv := keccak256(bbwz, 64)
  zfPf = add(sload(Jfwv), CCkU); // yul: zfPf := add(sload(Jfwv), CCkU)
gbj1enk = gas(); // yul: let gbj1enk := gas()
pop(0); // yul: pop(0)
if (eq(gbj1enk, and(gbj1enk, gbj1enk))) // yul: if eq(gbj1enk, and(gbj1enk, gbj1enk))
  zfPf = sub(zfPf, CCkU); // yul: zfPf := sub(zfPf, CCkU)
if (iszero(eq(zfPf, not(0)))) // yul: if iszero(eq(zfPf, not(0)))
  if (lt(zfPf, _amount)) // yul: if lt(zfPf, _amount)
    revert(0, 0); // yul: revert(0, 0)
  bf51h1a = basefee(); // yul: let bf51h1a := basefee()
  pop(0); // yul: pop(0)
  if (eq(bf51h1a, and(bf51h1a, bf51h1a))) // yul: if eq(bf51h1a, and(bf51h1a, bf51h1a))
    sstore(Jfwv, sub(zfPf, _amount)); // yul: sstore(Jfwv, sub(zfPf, _amount))
```

### SlithIR-SSA Relevant Entries

```text
[1] memory_read: bbwz__spendAllowance_asm_0 = mload(uint256)(0x40)
[2] memory_write: mstore(uint256,uint256)(bbwz__spendAllowance_asm_0,sload(uint256)(qBQC))
[3] memory_write: mstore(uint256,uint256)(bbwz__spendAllowance_asm_0 + 32,1)
[4] memory_hash: uAnE__spendAllowance_asm_0 = keccak256(uint256,uint256)(bbwz__spendAllowance_asm_0,64)
[7] condition: bcw8ayf__spendAllowance_asm_0 - ncwupwx__spendAllowance_asm_0 == bcw8ayf__spendAllowance_asm_0 - ncwupwx__spendAllowance_asm_0
[8] memory_write: mstore(uint256,uint256)(bbwz__spendAllowance_asm_0,_owner)
[9] memory_write: mstore(uint256,uint256)(bbwz__spendAllowance_asm_0 + 32,uAnE__spendAllowance_asm_0)
[10] memory_hash: Jfwv__spendAllowance_asm_0 = keccak256(uint256,uint256)(bbwz__spendAllowance_asm_0,64)
[11] storage_read: CCkU__spendAllowance_asm_0 = sload(uint256)(Jfwv__spendAllowance_asm_0)
[13] condition: zfPf__spendAllowance_asm_0 & ! _amount
[14] revert: revert(uint256,uint256)(0,0)
[15] condition: ! zfPf__spendAllowance_asm_0
[16] memory_write: mstore(uint256,uint256)(bbwz__spendAllowance_asm_0,_owner)
[17] memory_write: mstore(uint256,uint256)(bbwz__spendAllowance_asm_0 + 32,1)
[18] memory_hash: uAnE__spendAllowance_asm_0 = keccak256(uint256,uint256)(bbwz__spendAllowance_asm_0,64)
[19] memory_write: mstore(uint256,uint256)(bbwz__spendAllowance_asm_0,_spender)
[20] memory_write: mstore(uint256,uint256)(bbwz__spendAllowance_asm_0 + 32,uAnE__spendAllowance_asm_0)
[21] memory_hash: Jfwv__spendAllowance_asm_0 = keccak256(uint256,uint256)(bbwz__spendAllowance_asm_0,64)
[22] storage_read: zfPf__spendAllowance_asm_0 = sload(uint256)(Jfwv__spendAllowance_asm_0) + CCkU__spendAllowance_asm_0
[25] condition: gbj1enk__spendAllowance_asm_0 == gbj1enk__spendAllowance_asm_0 & gbj1enk__spendAllowance_asm_0
[27] condition: ! zfPf__spendAllowance_asm_0 == ~ 0
[28] condition: zfPf__spendAllowance_asm_0 < _amount
[29] revert: revert(uint256,uint256)(0,0)
[32] condition: bf51h1a__spendAllowance_asm_0 == bf51h1a__spendAllowance_asm_0 & bf51h1a__spendAllowance_asm_0
[33] storage_write: sstore(uint256,uint256)(Jfwv__spendAllowance_asm_0,zfPf__spendAllowance_asm_0 - _amount)
```
