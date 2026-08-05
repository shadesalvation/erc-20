# S-SEIR 重点样例处理总结

本文总结 `docs/s-seir重点审查样例.md` 中前期反复检查的重点样例。结论依据当前 `outputs/assembly样本_sseir_by_contract/*/sseir.json`，重点关注 Yul inline assembly 的语义模型，不把 solidity-like 展示效果作为正确性的唯一依据。

## 1. 检查范围

本次汇总覆盖 18 个重点输出目录：

```text
6 个反复重点审查样例
12 个逐个重跑审查样例
```

当前输出合计：

| 项目 | 数量 |
| --- | ---: |
| 样例 | 18 |
| 含 assembly 的函数 | 98 |
| Assembly 函数中的 EffectNode | 2015 |
| Assembly 函数中的 SemanticOverlay | 1573 |
| 逐路径 candidate | 112 |
| `status=resolved` candidate | 112 |
| `status=unresolved` candidate | 0 |

高频高级语义包括：

| Overlay | 数量 | 主要用途 |
| --- | ---: | --- |
| `MappingSlot` | 184 | mapping 及多维 mapping slot |
| `MappingRead` | 72 | mapping 状态读取 |
| `MappingWrite` | 56 | mapping 状态写入 |
| `StateVariableRead` | 54 | 普通槽位或手工槽位读取 |
| `StateVariableWrite` | 25 | 普通槽位或手工 hash 槽位写入 |
| `RequireOverlay` | 68 | 空 payload revert 对应的 guard |
| `PathConditionedEventEmit` | 20 | 不同 CFG 路径上的事件参数 |
| `PathConditionedStorageWrite` | 18 | 不同路径对应的 storage SSA |
| `PathConditionedStorageRead` | 10 | 不同路径对应的 storage 读取 |

这些数量表示模型节点数量，不表示源码恢复率。一条 Yul 语句可以同时产生底层 effect、原子求值 overlay 和高级语义 overlay。

## 2. 各样例处理结果

### 2.1 反复重点审查样例

| 样例 | Assembly 函数 | 主要处理内容 | 当前结果 | 估计正确概率 |
| --- | ---: | --- | --- | ---: |
| `0x1e0847...__TKM` | 16 | 单层/多层 mapping、跨 assembly 函数上下文、路径化状态与事件 | `balanceOf`、`allowance`、router flag、`transferFrom` 等形成 MappingRead/Write；复杂分支形成 PathConditionedStorage/Event | **93%**（89%-96%） |
| `0x1bacdd...__contracts__Kof` | 28 | DN404、Ownable、OwnableRoles、struct、array、call、return/revert | 形成结构体片段、内存分配、数组构造/读取、手工槽位、事件、低级调用和原始返回等多类 overlay | **95%**（92%-97%） |
| `0x2bac62...__Token` | 3 | 复杂 condition、sha256 precompile、路径化余额更新 | `_transfer` 的 precompile 输入/输出、mapping 更新和 Transfer 事件均按 condition 分候选记录 | **92%**（88%-95%） |
| `0x3bb48b...__TKM` | 14 | revert payload、TKM mapping 和多路径恢复 | 空 payload 进入 RequireOverlay；状态与事件继续保持 path-sensitive，不再与有数据错误混用 | **92%**（88%-95%） |
| `0x3b9c8f...__contracts__EUROS` | 3 | MemorySSA 与 SinkResolver 协作、opaque 剪枝 | 与复杂 Token 家族相同的 mapping/precompile/path overlay 均已形成；缺失数据时仍保留底层 effect | **92%**（88%-95%） |
| `0x43849f...__Token` | 5 | 已知值、byte-axis、sha256 固定地址 | `ptr + 0x0c` 的 20 字节输入恢复为 `bytes20(msg.sender)`，precompile 输出与后续 mload 建立 def-use | **96%**（93%-98%） |

### 2.2 逐个重跑审查样例

| 样例 | Assembly 函数 | 主要结果 | 估计正确概率 |
| --- | ---: | --- | ---: |
| `0x0003e1...__Token` | 3 | `_transfer` 恢复 mapping、sha256、path state 和 Transfer；`approve` 的非标准 topic 保守为 unknownEvent | **91%**（87%-94%） |
| `0x000505...__Token` | 3 | 与复杂 Token 家族一致，余额/授权状态和 precompile 路径均形成 overlay | **92%**（88%-95%） |
| `0x13c53d...__Token` | 3 | mapping、require、precompile 和逐路径状态恢复稳定 | **92%**（88%-95%） |
| `0x15a96e...__contracts__StellarVault` | 1 | `_loadTransferContext` 的两个地址参数被恢复为 mapping key，形成 MappingRead | **97%**（94%-99%） |
| `0x25ca3e...__ajshhdd` | 1 | `address[] memory` 长度、循环元素读取和循环内事件得到高级语义 | **94%**（90%-97%） |
| `0x28cbe5...__opens` | 1 | 与 airdrop 数组模式一致，形成 MemoryArrayLengthRead、MemoryArrayElementRead 和 PathConditionedEventEmit | **94%**（90%-97%） |
| `0x32b8d4...__sdjsjdl` | 1 | 数组遍历和循环事件模式稳定复现 | **94%**（90%-97%） |
| `0x36e8a6...__contracts__MyTokenV1` | 4 | storage pointer `.slot` 绑定和两次 staticcall calldata 构造已记录；selector 名称未知时保留 selector | **87%**（80%-92%） |
| `0x72cf4c...__NEWYORKCITY` | 3 | 与复杂 Token 家族一致，逐路径 storage、event 和 precompile 均保留 | **92%**（88%-95%） |
| `0x83e3c8...__BABYTOKEN` | 0 | 选中合约及继承实现中没有 Yul inline assembly，作为筛选正确性的负样例 | **N/A** |
| `0x599a69...__contracts__QuantNeural` | 8 | mapping 状态、guard、交互指标更新等形成 MappingRead/Write 和 RequireOverlay | **95%**（92%-97%） |
| `0xdfc541...__contracts__ThetaNet` | 1 | `_loadTransferContext` 的 mapping 恢复与 StellarVault 一致 | **97%**（94%-99%） |

### 2.3 概率口径

这里的“估计正确概率”表示：

> 从该样例已经生成的高级语义 overlay 中随机抽取一个结论，该结论与源码实际 EVM/Yul 行为一致的人工审查估计概率。

它不是模型运行时概率，也不写入正式 S-SEIR 的 `confidence` 字段。当前没有带人工 ground truth 标签的大规模测试集，因此不能把该值解释为统计学准确率。

估计依据包括：

```text
1. Yul 语义终点是否有对应高级 overlay；
2. sink 的 MemorySSA/SinkResolver 参数是否 complete；
3. 不同 condition 下的 candidate 是否分别解析且保留 SSA version；
4. storage layout、参数类型、event topic 和 selector 是否有源码证据；
5. 是否仍存在 unknownEvent、unmatched selector 或手工布局歧义；
6. 前期逐函数人工对照中是否发现语义回退。
```

当前 17 个含 assembly 的样例中，Yul 的 StorageRead/Write、EventLog、Call、Revert、Return sink 均有至少一个非浅层高级 overlay，sink 覆盖率为 100%。但覆盖率只说明“已生成解释”，不说明解释一定正确，所以最终概率低于 100%。

按 Yul sink 数量加权，17 个有效样例的总体估计正确概率约为：

```text
93%（建议按 89%-96% 区间理解）
```

区间差异的主要原因：

| 情形 | 对概率的影响 |
| --- | --- |
| 标准 mapping、直接 state slot、明确 event topic | 区间较窄，通常 94%-99% |
| 多重 condition、path candidate、precompile 输出关联 | 规则明确，但 CFG/SSA 链更长，通常 88%-96% |
| struct/array 手工内存布局 | 高级模式正确，但不一定能无损投影 Solidity，通常 90%-97% |
| `unknownEvent` | 日志结构正确，事件名称与参数类型不完整，降低点估计 |
| unmatched selector | 调用类型与 calldata 正确，但函数身份未知，明显扩大区间 |
| `complete=false` memory query | 若无 AST 类型或其他模式证据，则必须保守降低概率 |

## 3. 典型案例一：多维 mapping 恢复

样例：`0x1e0847...__TKM`，函数 `allowance(address,address)`。

### 3.1 源码

```solidity
function allowance(address owner_, address spender) external view returns (uint256 ab) {
    assembly {
        mstore(0, owner_)
        mstore(32, m.slot)
        let ac := keccak256(0, 64)
        mstore(0, spender)
        mstore(32, ac)
        ab := sload(keccak256(0, 64))
    }
}
```

### 3.2 处理过程

1. MemorySSA 记录第一组 `[owner_, m.slot]` 的两个内存定义及版本。
2. 第一个 `keccak256` 触发内存查询，MemoryHash 恢复 `key=owner_`、`base=m.slot`。
3. Storage layout 确认 `m` 是 mapping，生成第一层 `MappingSlot(access=m[owner_])`。
4. 第二组内存写入把 `spender` 作为 key，把第一层 hash 的 SSA version 作为 base。
5. 第二个 MemoryHash 通过 SSA key 追踪到第一层 MappingSlot，生成 `nested_mapping_slot`。
6. `sload` 是最终 storage sink，它激活被实际消费的第二层 hash，生成 MappingRead。

### 3.3 模型结果

```text
MappingSlot  access=m[owner_]
             slot_kind=mapping_slot

MappingSlot  access=m[owner_][spender]
             slot_kind=nested_mapping_slot

MappingRead  access=m[owner_][spender]
             target=ab
```

最终高级语义为：

```solidity
ab = m[owner_][spender];
```

该结果说明 mapping 恢复不是按 `ac` 变量名猜测，而是通过 MemoryHash 输入、storage layout 和 SSA hash 版本逐层激活。

## 4. 典型案例二：不同 condition 下的状态和事件

样例：`0x2bac62...__Token`，函数 `_transfer(address,address,uint256)`。

### 4.1 相关原始 Yul 语句

```solidity
assembly {
    if iszero(_from) { revert(0, 0) }
    if iszero(_to) { revert(0, 0) }

    let SARKO$ := mload(0x40)
    mstore(SARKO$, _from)
    mstore(add(SARKO$, 32), 0)
    let iVrp := keccak256(SARKO$, 64)
    let JcxQ := sload(iVrp)

    mstore(SARKO$, shl(96, caller()))
    if iszero(staticcall(gas(), 2, SARKO$, 20, SARKO$, 32)) {
        revert(0, 0)
    }
    let SUpo := mload(SARKO$)

    sstore(iVrp, sub(JcxQ, _amount))

    mstore(SARKO$, _to)
    mstore(add(SARKO$, 32), 0)
    let OBse := keccak256(SARKO$, 64)
    let mVat := sload(OBse)
    sstore(OBse, add(mVat, _amount))

    mstore(SARKO$, _amount)
    log3(
        SARKO$,
        32,
        0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef,
        _from,
        _to
    )
}
```

上面省略了 `RuhS/SUpo` 比较和余额不足 revert 的内部语句，但变量名和保留语句均与样例源码一致。

### 4.2 处理过程

1. Control 层保存 `_from != 0`、`_to != 0`、staticcall 成功和余额检查等路径条件。
2. MemorySSA 在每条可达路径上为 `ptr` 区域建立独立版本；SinkResolver 不把同名 `ptr` 的不同版本合并。
3. `iVrp` 和 `OBse` 两次 mapping slot 计算分别恢复为 `wAcL[_from]` 与 `wAcL[_to]`。
4. staticcall 的目标地址为 `2`，匹配 Ethereum `sha256` precompile。
5. byte-axis 查询从 20 字节输入中恢复 `high_bytes((msg.sender << 96),20)`，进一步规范化为 `bytes20(msg.sender)`。
6. `mload(ptr)` 与 precompile 输出区建立 call-output def-use，恢复为 sha256 的输出值。
7. `sstore(toSlot, ...)` 在两条可达路径上有两个 SSA slot version，但二者都解析到 `wAcL[_to]`；模型保留两个 candidate 及各自 condition。
8. EventLog 查询 32 字节 data 区，在两条路径上都解析出 `_amount`，形成两个 Transfer candidate。

### 4.3 模型结果

precompile：

```text
PathConditionedPrecompileCall
  condition = _from != 0 && _to != 0
  precompile = sha256
  input = bytes20(msg.sender)
  solidity_like = sha256(abi.encodePacked(bytes20(msg.sender)))
```

状态写入：

```text
PathConditionedStorageWrite
  candidate 1: wAcL[_to] = mVat + _amount
  candidate 2: wAcL[_to] = mVat + _amount
  两个 candidate 分别保存其 CFG condition 和 slot SSA version
```

事件：

```text
PathConditionedEventEmit
  event = Transfer
  args = [_from, _to, _amount]
  每个 candidate 保留对应 condition
```

该例验证了“同一源码 sink 在不同 condition 下分别解析 SSA”的核心能力。相同高级访问不会抹掉不同路径证据。

## 5. 典型案例三：struct、手工内存与低级调用

样例：`0x1bacdd...__contracts__Kof`。

### 5.1 struct 构造

源码：

```solidity
function _packedLogsMalloc(uint256 n) private pure returns (_PackedLogs memory p) {
    assembly {
        let logs := add(mload(0x40), 0x40)
        mstore(logs, n)
        let offset := add(0x20, logs)
        mstore(0x40, add(offset, shl(5, n)))
        mstore(p, logs)
        mstore(add(0x20, p), offset)
    }
}
```

处理过程：

1. AST 类型环境读取返回值 `p: _PackedLogs memory` 和结构体字段布局。
2. `mload(0x40)` 与后续 `mstore(0x40, newPtr)` 匹配手工内存分配模式。
3. CFG 节点范围和线性 alias 确认 `mstore(logs,n)` 位于该分配区间。
4. `p` 和 `p+32` 的写入偏移分别匹配 `logs`、`offset` 字段。
5. 生成 `MemoryRegionAllocate`、`MemoryRegionWrite`、`StructFieldWrite` 和 `StructInitializationFragment`。

结果：

```text
StructInitializationFragment
  target = p
  struct_type = DN404._PackedLogs
  fields = [p.logs <- logs, p.offset <- offset]
  reason = manual_memory_layout_or_custom_allocator
```

模型将其识别为结构体初始化片段，但仍标明手工内存布局不一定能无损投影为普通 Solidity 构造语句。

### 5.2 calldata 构造和低级调用

`_linkMirrorContract(address)` 中的 `call` 从 `0x1c` 读取 36 字节。byte-axis MemorySSA 将其拆为：

```text
low_bytes(0x0f4599e5, 4)
msg.sender
```

selector registry 匹配到 `linkMirrorContract(address)` 后生成：

```text
LowLevelCall
  target = mirror
  selector = 0x0f4599e5
  selector_signature = linkMirrorContract(address)
  arguments = [caller()]
```

失败路径上的 4 字节 revert payload 同样由 byte-axis 查询解析为 `LinkMirrorContractFailed()`，并记录为 `PathConditionedCustomErrorRevert`。

## 6. 典型案例四：循环中的 memory 数组读取和事件

样例：`0x28cbe5...__opens`，函数 `airdrop(address[])`。

### 6.1 源码

```solidity
function airdrop(address[] memory to) external {
    assembly {
        let len := mload(to)
        let i := 0
        let v := sload(giftAmount.slot)
        for { } lt(i, len) { i := add(i, 1) } {
            let account := mload(add(to, mul(add(i, 1), 0x20)))
            mstore(0x0, v)
            log3(
                0x0,
                0x20,
                0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef,
                address(),
                account
            )
        }
    }
}
```

### 6.2 处理过程

1. 函数参数 AST 明确 `to` 是 `address[] memory`。
2. `mload(to)` 匹配 Solidity memory 动态数组的 length word，生成 `MemoryArrayLengthRead(access=to.length)`。
3. 地址 `to + (i+1)*32` 匹配“长度 word 之后的第 i 个元素”，生成 `MemoryArrayElementRead(access=to[i])`。
4. CFG 将循环体节点标记为 `path_states=[lt(i,len)]`。
5. EventLog sink 从 `[0,32)` 解析出 `v`，topic0 匹配 Transfer，indexed topic 解析为 `address(this)` 和 `account`。
6. 最终生成 `PathConditionedEventEmit`，condition 为 `lt(i,len)`。

### 6.3 模型结果

```text
MemoryArrayLengthRead  len = to.length
MemoryArrayElementRead account = to[i]
StateVariableRead     v = giftAmount
PathConditionedEventEmit
  condition = i < len
  emit Transfer(address(this), account, v)
```

虽然普通 MemorySSA 对外部传入数组内容本身显示 `unknown`，类型和底层地址模式完整匹配，因此高级数组访问可以成立。这不是猜测数组类型，而是使用函数参数 AST 类型作为前提。

## 7. 典型案例五：precompile 输入和输出 def-use

样例：`0x43849f...__Token`，函数 `xjflbfjxthx(address,bool)`。

### 7.1 关键源码

```solidity
assembly {
    let ptr := mload(0x40)
    mstore(ptr, caller())
    let input := add(ptr, 0x0c)
    let inputSize := 0x14
    let output := add(ptr, 0x20)

    if iszero(staticcall(gas(), 2, input, inputSize, output, 0x20)) {
        revert(0, 0)
    }

    let computedHash := mload(output)
    let storedHashSlot := _zpewsnjt.slot
    let storedHash := sload(storedHashSlot)
}
```

### 7.2 处理过程

1. 线性 alias 将 `input` 解析为 `ptr+0x0c`，将 `output` 解析为 `ptr+0x20`。
2. 已知值传播把 `caller()` 标为 EVM builtin known value。
3. byte-axis 按 inputSize=20 从 `mstore(ptr,caller())` 中截取后 20 字节，恢复 `bytes20(msg.sender)`。
4. target=2 匹配 sha256 precompile，生成 `PrecompileCall`。
5. 后续 `mload(output)` 与 call 的 output range 相同，建立输出 def-use，生成 `PrecompileOutputRead`。
6. `_zpewsnjt.slot` 经 storage layout 解析为状态变量读取。

### 7.3 模型结果

```text
PrecompileCall
  precompile = sha256
  input_words = [bytes20(msg.sender)]
  solidity_like = sha256(abi.encodePacked(bytes20(msg.sender)))

PrecompileOutputRead
  target = computedHash
  value = uint256(sha256_result)

StateVariableRead
  target = storedHash
  access = _zpewsnjt
```

该例验证了 SinkResolver 是 MemorySSA 的补充查询层：内存来源仍由 MemorySSA 提供，SinkResolver 负责以 call sink 的 ptr/size 组织查询结果。

## 8. 典型案例六：selector 已知但函数名未知

样例：`0x36e8a6...__contracts__MyTokenV1`，函数 `_iD(address)`。

### 8.1 关键源码

```solidity
assembly {
    let p := mload(0x40)
    mstore(p, 0x0902f1ac00000000000000000000000000000000000000000000000000000000)
    let v2 := staticcall(gas(), t_, p, 0x04, 0, 0)

    mstore(p, 0x3850c7bd00000000000000000000000000000000000000000000000000000000)
    let v3 := staticcall(gas(), t_, p, 0x04, 0, 0)

    iD_ := or(v2, v3)
}
```

### 8.2 处理过程

1. 两次 mstore 的前 4 字节分别恢复为 `0x0902f1ac` 和 `0x3850c7bd`。
2. staticcall 的 input range 为 `[p,p+4)`，因此 calldata 只包含 selector，没有参数。
3. 本地 AST、接口和注释 selector registry 中没有对应签名，`selector_match.status=unmatched`。
4. 模型仍生成 `StaticCallOverlay`、`AbiCallDataConstruction` 和 `AbiEncodedLowLevelCall`，但不伪造函数名。

### 8.3 模型结果

```text
yulStaticcall(
  gas: gasleft(),
  target: t_,
  input: abi.encodeWithSelector(bytes4(0x0902f1ac)),
  output: memory[0:0]
)
```

第二次调用同样保留 `0x3850c7bd`。这是当前“已恢复调用结构，但无法静态命名函数”的标准保守结果。

## 9. 保守残留

### 9.1 unknownEvent

18 个样例中当前有 8 个 event overlay 保留为 `unknownEvent`，主要位于：

```text
TKM.C / TKM._transfer
复杂 Token 家族的 approve
EUROS / NEWYORKCITY 的 approve
```

这些位置仍保留：

```text
EventLog effect
原始 topic
data memory 查询
逐路径 candidate
stmt_refs
```

未匹配 event 名称不会影响底层日志语义。按照此前约定，topic near-miss 暂不强行归并为标准事件。

### 9.2 未命名 selector

当前统计到 6 个 `selector_match=unmatched` overlay，实际来自 MyTokenV1 中两个 selector 在三类 overlay 上的重复记录：

```text
0x0902f1ac
0x3850c7bd
```

输入范围和调用类型已经恢复，只有函数签名无法由当前源码上下文确定。

### 9.3 不完整 memory query

当前有 45 个 effect 内含 `complete=false` 的 memory query。典型原因包括：

```text
函数参数指向的 Solidity memory 对象并非由 assembly 内 mstore 创建；
读取 free-memory pointer 之前没有 assembly 内 reaching definition；
动态地址或动态长度无法逐 word 完全枚举；
源数据来自当前函数之外。
```

这不等价于整个语义失败。例如 `airdrop(address[] memory to)` 的底层 mload 查询可以不完整，但参数 AST 类型和数组布局模式仍足以生成 `to.length` 与 `to[i]`。消费者应同时查看 effect 的完整性和 overlay 的模式证据。

## 10. 最终结论

1. S-SEIR 已能在重点样例中稳定记录 Yul 的底层行为，并将标准 storage、memory、call、event、revert/return 模式提升为统一高级语义。
2. Mapping 恢复已覆盖单层、多层、路径化读取写入和手工 packed hash 槽位；恢复依赖 MemorySSA、slot SSA version 和 storage layout，而不是变量文本猜测。
3. 同一 sink 在不同 condition 下能够保留独立 candidate。当前 18 个样例中的 112 个逐路径 candidate 均为 resolved，但 condition 和 SSA version 没有因此被删除。
4. byte-axis MemorySSA 能处理非 32 字节对齐、重叠写入和 ptr 偏移输入，已经用于 selector、revert payload、event data 和 precompile 输入恢复。
5. struct、动态 memory 数组、手工内存分配和循环内数组读取已能形成局部高级语义；这些 overlay 不要求整个函数只执行一种操作。
6. 无法确定 event 名称、selector 签名或完整 memory 来源时，模型保留底层 effect 和原始值，不用相似值或另一条路径的结果替代。
7. `0x83e3c8...__BABYTOKEN` 没有 Yul inline assembly，说明批处理可以保留 ERC-20 完整实现，同时不凭库代码或普通 Solidity effect 虚构 assembly 结果。

阶段性判断：当前模型已经能够作为后续 LLM 恢复 assembly 的结构化输入。LLM 应优先消费 SourceStatement、Control、Effect、SinkResolution 和 Overlay，并把 unknown/unmatched/complete=false 视为必须保守处理的边界，而不能只读取 solidity-like 文本。
