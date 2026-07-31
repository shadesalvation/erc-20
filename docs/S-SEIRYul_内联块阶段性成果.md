# S-SEIR Yul 内联块阶段性成果

## 1. 检查对象

本次检查对象为：

```text
outputs/assembly样本_sseir_by_contract/0x3bb48be44d06b6a1f5272a64de4839a13a62590b__TKM/sseir.json
```

对应源码样例：

```text
TOKENS/assembly样本/0x3bb48be44d06b6a1f5272a64de4839a13a62590b/TKM.sol
```

本次只关注 S-SEIR 语义模型本身，不以 `solidity_like.txt` 展示层作为主要判断依据。

## 2. 总体结论

当前 S-SEIR 对该样例中的 Yul inline assembly 语义恢复已经可以作为阶段性成果。

主要能力已经覆盖：

- 直接状态变量读写；
- mapping / nested mapping slot 恢复；
- storage read / write 语义提取；
- path-conditioned storage sink 记录；
- `require` / `revert(0, 0)` 空 payload 语义记录；
- ERC20 标准事件 `Transfer`、`Approval` 识别；
- 未知事件的保守记录；
- 嵌套 `sload(...)` 作为写入右值时的状态读取归一化。

## 3. 关键恢复结果

### 3.1 Constructor

源码中的：

```yul
sstore(g.slot, sload(c.slot))
sstore(r, sload(c.slot))
```

当前语义模型中恢复为：

```text
StateVariableRead: c
StateVariableWrite: g = c
MappingWrite: h[q] = c
```

同时保留原始证据：

```text
value_yul = sload(c.slot)
value_state_read = c
```

说明嵌套在 `sstore` 右值中的 `sload(c.slot)` 已经被正确提升为状态读取语义。

### 3.2 ERC20 查询入口

`balanceOf(address)` 中：

```yul
mstore(0, account)
mstore(32, h.slot)
v := sload(keccak256(0, 64))
```

恢复为：

```text
MappingRead: h[account]
```

`allowance(address,address)` 中二维 mapping 恢复为：

```text
MappingRead: i[owner][spender]
```

### 3.3 Allowance 更新

`transferFrom(address,address,uint256)` 中恢复出：

```text
MappingRead:  i[from][msg.sender]
MappingWrite: i[from][msg.sender] = ac
```

说明 nested mapping 的多维 slot 追踪链路可用。

### 3.4 Approve 事件

`_approve(address,address,uint256)` 中恢复出：

```text
MappingWrite: i[owner_][spender] = amount
PathConditionedEventEmit: Approval(owner_, spender, amount)
```

事件 topic0 匹配：

```text
Approval(address,address,uint256)
0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925
```

### 3.5 Transfer 主流程

`_transfer(address,address,uint256)` 中恢复出主要余额语义：

```text
MappingRead:  h[from]
MappingWrite: h[from] = newFromBalance
MappingRead:  h[to]
MappingWrite: h[to] = toBalance + finalAmount
```

手续费分支中恢复出：

```text
StateVariableRead: p
MappingRead:  h[feeToAddr]
MappingWrite: h[feeToAddr] = feeToBalance + feeAmount
```

同时恢复出：

```text
PathConditionedEventEmit: Transfer(from, to, amount)
```

事件 topic0 匹配：

```text
Transfer(address,address,uint256)
0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef
```

## 4. Revert / Require 处理结果

当前 `revert(0, 0)` 已不再被误判为 incomplete memory slice。

语义模型中记录为：

```text
status = resolved
normalized = empty
notes = ["revert_payload", "empty_payload", "zero_length_memory_range"]
```

对应恢复出的 `RequireOverlay` 包括：

```text
require(e == address(0))
require(isToRouterVal != 0)
require(isFromRouterVal != 0)
require(fromBalance >= amount)
require(!(owner_ == 0 || spender == 0))
```

说明空 payload revert 已经先被识别为无数据 revert，再进入 require 语义恢复；有数据 revert 仍保留原 memory slice / selector 解析流程。

## 5. 保守残留

当前仅发现一个合理保守残留：

```text
PathConditionedEventEmit: unknownEvent
topic0 = 0x87fdc57bf3d4fa5e58b9d7b91e09258d7f6d3b939f5d3a7a9f5f5f5f5f5f5f5f
```

该 topic0 不匹配样例源码中声明的：

```text
Transfer(address,address,uint256)
Approval(address,address,uint256)
Reduce(address,uint256)
```

因此当前将其标记为 `unknownEvent` 是保守正确的。

## 6. 当前仍可优化的问题

语义模型中 `_transfer` 的部分 path-conditioned storage/event overlay 仍存在候选冗余。

例如同一语义可能同时以：

```text
PathConditionedStorageWrite
MappingWrite
```

形式出现，或在多个 path candidate 中表达相同的访问结果。

这不影响当前语义判断，但会影响后续给 LLM 的输入简洁性。后续可以补充 overlay 去重、候选合并、路径条件压缩等优化。

## 7. 阶段性判断

当前 S-SEIR 对该样例中 Yul inline assembly 的核心状态语义、事件语义、异常语义恢复基本正确。

该结果已经适合作为后续 LLM 源码恢复前的结构化语义输入，但在大规模批量样例中仍需要继续观察：

- path-conditioned overlay 是否过度冗余；
- unknown event 是否确实为源码未声明事件；
- 复杂 opaque 分支是否需要更多预处理规则；
- 展示层是否需要进一步合并条件结构。
