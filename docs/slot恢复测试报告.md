# Slot 恢复测试报告

## 测试目标

验证 `assembly_storage_ir.py` 对以下场景的恢复能力：

```text
1. 普通 mapping slot 恢复
2. nested mapping slot 恢复
3. 直接状态变量 sload/sstore 恢复
4. calldatacopy 数据参与 slot 计算
5. 动态 offset / 循环 memory 写入下的保守恢复
```

测试输出文件位于：

```text
outputs/storage_ir_cases/
```

---

## CaseA：mapping、nested mapping、直接状态变量

### 原 Solidity 源码

```solidity
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
```

### 恢复后的关键 IR

```text
MAPPING_SLOT: fromSlot := balances[from]
MAPPING_READ: fromBal := balances[from]
MAPPING_WRITE: balances[from] := fromBal - amount

MAPPING_SLOT: ownerSlot := allowance[owner]
NESTED_MAPPING_SLOT: allowSlot := allowance[owner][spender]
NESTED_MAPPING_READ: oldAllow := allowance[owner][spender]
NESTED_MAPPING_WRITE: allowance[owner][spender] := oldAllow - amount

STATE_VARIABLE_READ: ts := totalSupply
STATE_VARIABLE_WRITE: totalSupply := ts + amount
```

### 结论

普通 mapping、nested mapping 和直接状态变量读写均可恢复。

---

## CaseB：calldatacopy 数据参与 slot 计算

### 原 Solidity 源码

```solidity
pragma solidity ^0.8.20;
contract CaseB {
    mapping(bytes32 => uint256) private claims;
    mapping(address => mapping(bytes32 => uint256)) private used;

    function touch(address user) public {
        assembly {
            let p := mload(0x40)
            calldatacopy(p, 4, 32)
            mstore(add(p, 32), claims.slot)
            let claimSlot := keccak256(p, 64)
            let claim := sload(claimSlot)

            mstore(p, user)
            mstore(add(p, 32), used.slot)
            let userSlot := keccak256(p, 64)
            calldatacopy(p, 36, 32)
            mstore(add(p, 32), userSlot)
            let usedSlot := keccak256(p, 64)
            sstore(usedSlot, add(sload(usedSlot), claim))
        }
    }
}
```

### 恢复后的关键 IR

```text
MAPPING_SLOT: claimSlot := claims[calldata[4:4+32]]
MAPPING_READ: claim := claims[calldata[4:4+32]]

MAPPING_SLOT: userSlot := used[user]
NESTED_MAPPING_SLOT: usedSlot := used[user][calldata[36:36+32]]
INLINE_NESTED_MAPPING_READ: _ := used[user][calldata[36:36+32]]
NESTED_MAPPING_WRITE: used[user][calldata[36:36+32]] := sload(usedSlot) + claim
```

### 结论

`calldatacopy` 写入 memory 后，后续 `keccak256` 可正确读取 calldata 来源，并用于 mapping slot 恢复。

---

## CaseC：动态 offset 与循环写入

### 原 Solidity 源码

```solidity
pragma solidity ^0.8.20;
contract CaseC {
    mapping(uint256 => uint256) private dynamicMap;
    uint256 private control;

    function touch(uint256 off, uint256 value) public {
        assembly {
            let p := mload(0x40)
            mstore(p, 7)
            mstore(add(p, off), value)
            for { let i := 0 } lt(i, 2) { i := add(i, 1) } {
                mstore(add(p, mul(i, 32)), add(value, i))
            }
            mstore(add(p, 32), dynamicMap.slot)
            let dynSlot := keccak256(p, 64)
            let old := sload(dynSlot)
            sstore(dynSlot, add(old, value))
            sstore(control.slot, sload(dynSlot))
        }
    }
}
```

### 恢复后的关键 IR

```text
MAPPING_SLOT: dynSlot := dynamicMap[unknown]
ResolvedInputs: [0: unknown candidates=['mul(i, 32)/size 32: add(value, i)', 'off/size 32: value'], 32: dynamicMap.slot]

MAPPING_READ: old := dynamicMap[unknown]
MAPPING_WRITE: dynamicMap[unknown] := old + value
INLINE_MAPPING_READ: _ := dynamicMap[unknown]
STATE_VARIABLE_WRITE: control := sload(dynSlot)
```

### 结论

当动态 offset 或循环写入可能覆盖固定 memory 位置时，模块不会强行恢复为确定 key，而是输出 `unknown` 并保留候选来源，避免误判。

---

## 总结

当前 slot 恢复模块已覆盖：

```text
mapping[key]
mapping[key1][key2]
stateVariable
copy 数据参与 slot
inline sload
unchecked arithmetic 标注
动态/循环 memory 写入下的保守恢复
```

其中动态 offset 和循环写入不会做过度具体化，只保留候选影响源。

---

## 当前真实样例：0x0068e979c72bbb31373ea8cb47eaefb44978566e

### 测试输入

```text
TOKENS/0x0068e979c72bbb31373ea8cb47eaefb44978566e/Token.sol
TOKENS/0x0068e979c72bbb31373ea8cb47eaefb44978566e/variables_order.txt
TOKENS/0x0068e979c72bbb31373ea8cb47eaefb44978566e/slithir_ssa.txt
```

测试输出：

```text
outputs/assembly_storage_ir.sample.txt
```

该样例包含 3 个 assembly block：

```text
Token._transfer
Token.approve
Token._spendAllowance
```

### 原 Solidity 关键源码

```solidity
function _transfer(address _from, address _to, uint256 _amount) virtual internal {
    assembly {
        let bbwz := mload(0x40)
        mstore(bbwz, _from)
        mstore(add(bbwz, 32), 0)
        let XCUz := keccak256(bbwz, 64)
        let qVPd := sload(XCUz)
        let Fpzy := sload(sSgI.slot)
        sstore(XCUz, sub(qVPd, _amount))

        mstore(bbwz, _to)
        mstore(add(bbwz, 32), 0)
        let iLho := keccak256(bbwz, 64)
        let bXFg := sload(iLho)
        sstore(iLho, add(bXFg, _amount))
    }
}

function approve(address _spender, uint256 _amount) virtual public returns (bool) {
    assembly {
        let bbwz := mload(0x40)
        mstore(bbwz, caller())
        mstore(add(bbwz, 0x20), wZHA.slot)
        let ZFHJ := keccak256(bbwz, 0x40)
        mstore(bbwz, _spender)
        mstore(add(bbwz, 0x20), ZFHJ)
        let akRJ := keccak256(bbwz, 0x40)
        sstore(akRJ, _amount)
    }
    return true;
}

function _spendAllowance(address _owner, address _spender, uint256 _amount) virtual internal {
    assembly {
        let bbwz := mload(0x40)
        mstore(bbwz, sload(qBQC.slot))
        mstore(add(bbwz, 32), 1)
        let uAnE := keccak256(bbwz, 64)
        mstore(bbwz, _owner)
        mstore(add(bbwz, 32), uAnE)
        let Jfwv := keccak256(bbwz, 64)
        let CCkU := sload(Jfwv)

        if iszero(zfPf) {
            mstore(bbwz, _owner)
            mstore(add(bbwz, 32), 1)
            uAnE := keccak256(bbwz, 64)
            mstore(bbwz, _spender)
            mstore(add(bbwz, 32), uAnE)
            Jfwv := keccak256(bbwz, 64)
            zfPf := add(sload(Jfwv), CCkU)
        }
        sstore(Jfwv, sub(zfPf, _amount))
    }
}
```

### 恢复后的关键 IR：_transfer

```text
MAPPING_SLOT: XCUz := UbLX[_from]
MAPPING_READ: qVPd := UbLX[_from]
STATE_VARIABLE_READ: Fpzy := sSgI
MAPPING_WRITE: UbLX[_from] := qVPd - _amount

MAPPING_SLOT: iLho := UbLX[_to]
MAPPING_READ: bXFg := UbLX[_to]
MAPPING_WRITE: UbLX[_to] := bXFg + _amount
```

恢复出的 Solidity-like 视图：

```solidity
qVPd = UbLX[_from];
Fpzy = sSgI;
unchecked { UbLX[_from] = qVPd - _amount; }
bXFg = UbLX[_to];
unchecked { UbLX[_to] = bXFg + _amount; }
```

### 恢复后的关键 IR：approve

```text
MAPPING_SLOT: ZFHJ := wZHA[caller()]
NESTED_MAPPING_SLOT: akRJ := wZHA[caller()][_spender]
NESTED_MAPPING_WRITE: wZHA[caller()][_spender] := _amount
```

恢复出的 Solidity-like 视图：

```solidity
wZHA[caller()][_spender] = _amount;
```

### 恢复后的关键 IR：_spendAllowance

```text
INLINE_STATE_VARIABLE_READ: _ := qBQC
MAPPING_SLOT: uAnE := wZHA[sload(qBQC.slot)]
NESTED_MAPPING_SLOT: Jfwv := wZHA[sload(qBQC.slot)][_owner]
NESTED_MAPPING_READ: CCkU := wZHA[sload(qBQC.slot)][_owner]

MAPPING_SLOT: uAnE := wZHA[_owner]
NESTED_MAPPING_SLOT: Jfwv := wZHA[_owner][_spender]
INLINE_NESTED_MAPPING_READ: _ := wZHA[_owner][_spender]
NESTED_MAPPING_WRITE: wZHA[_owner][_spender] := zfPf - _amount
```

恢复出的 Solidity-like 视图：

```solidity
read qBQC;
CCkU = wZHA[sload(qBQC.slot)][_owner];
read wZHA[_owner][_spender];
unchecked { wZHA[_owner][_spender] = zfPf - _amount; }
```

### 结论

真实样例中：

```text
UbLX slot 0 -> balance mapping
wZHA slot 1 -> allowance nested mapping
sSgI slot 5 -> direct state variable
qBQC slot 6 -> inline state variable read
```

均可根据 `variables_order.txt` 恢复为状态变量名。模块能够恢复 transfer 中的余额读写、approve 中的授权写入，以及 _spendAllowance 中较复杂的 nested mapping 读写和内嵌 `sload`。
