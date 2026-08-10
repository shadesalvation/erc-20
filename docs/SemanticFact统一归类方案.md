# SemanticFact 统一归类方案

## 1. 目标

当前项目中存在两类语义来源：

- Solidity 源码经 Slither / SlithIR 得到的低级语义事实；
- Yul inline assembly 经 S-SEIR 恢复得到的高级语义 overlay。

二者本质上都在回答同一个问题：

```text
这段代码在什么条件下，对哪些对象做了什么行为？
```

因此后续可以统一收敛到一层：

```text
SemanticFact = 代码行为事实
```

其中：

```text
Solidity -> Slither / SlithIR -> SemanticFact
Yul      -> S-SEIR Overlay   -> SemanticFact
```

换句话说，Slither 负责把 Solidity 降到行为事实层；S-SEIR 负责把 Yul 提升到行为事实层。

## 2. SemanticFact 顶层分类

第一版建议分为 8 类：

```text
1. ValueFact    值计算
2. StateFact    状态变量读写
3. MemoryFact   内存读写 / 内存对象构造
4. ControlFact  条件、循环、路径约束
5. RevertFact   require / revert / error
6. EventFact    emit event
7. CallFact     外部调用 / 内部调用 / precompile
8. ReturnFact   返回值
```

这些类别同时覆盖 Solidity SlithIR 语义和 Yul S-SEIR overlay 语义。

## 3. 统一记录格式

每条 `SemanticFact` 建议统一记录为：

```json
{
  "fact_id": "fact_12",
  "kind": "StateWrite",
  "source_lang": "yul",
  "origin": "sseir_overlay",
  "function": "transfer",
  "stmt_refs": ["asm_s_8"],
  "cfg_nodes": ["bb_4:n8"],
  "condition": "fromBalance >= amount",
  "lvalue": "balances[to]",
  "rvalue": "balances[to] + amount",
  "reads": ["balances[to]", "amount"],
  "writes": ["balances[to]"],
  "semantic": {
    "state_variable": "balances",
    "keys": ["to"],
    "operation": "mapping_write"
  },
  "evidence": {
    "effects": ["eff_3", "eff_4"],
    "overlay": "ov_9"
  }
}
```

核心字段含义：

| 字段 | 含义 |
| --- | --- |
| `fact_id` | SemanticFact 的唯一编号 |
| `kind` | 行为类型，例如 `StateWrite`、`EventEmit`、`ExternalCall` |
| `source_lang` | 来源语言，`solidity` 或 `yul` |
| `origin` | 语义来源，例如 `slither_ir`、`sseir_overlay` |
| `function` | 所属函数 |
| `stmt_refs` | 对应原始 SourceStatement 引用 |
| `cfg_nodes` | 对应 CFG 节点 |
| `condition` | 该事实成立的路径条件 |
| `lvalue` | 被写入或赋值对象 |
| `rvalue` | 写入值或计算表达式 |
| `reads` | 该事实读取的对象 |
| `writes` | 该事实写入的对象 |
| `semantic` | 结构化语义字段 |
| `evidence` | 来源 effect / overlay 证据 |

## 4. S-SEIR 当前语义到 SemanticFact 的映射

| S-SEIR 当前 Effect / Overlay | SemanticFact |
| --- | --- |
| `StateVariableRead` | `StateRead` |
| `StateVariableWrite` | `StateWrite` |
| `MappingRead` | `StateRead` |
| `MappingWrite` | `StateWrite` |
| `PathConditionedStorageRead` | 多条 `StateRead`，每个 candidate 一条 |
| `PathConditionedStorageWrite` | 多条 `StateWrite`，每个 candidate 一条 |
| `EventEmit` | `EventEmit` |
| `PathConditionedEventEmit` | 多条 `EventEmit`，每个 candidate 一条 |
| `RequireOverlay` | `Require` |
| `CustomErrorRevert` | `Revert` |
| `PathConditionedCustomErrorRevert` | 多条 `Revert`，每个 candidate 一条 |
| `ExternalCall` / `LowLevelCall` | `ExternalCall` |
| `PrecompileCall` | `PrecompileCall` |
| `InternalCall` | `InternalCall` |
| `ReturnValue` | `Return` |
| `MemoryRegionAllocate` | `MemoryAllocate` |
| `MemoryArrayConstruction` | `MemoryObjectConstruct` |
| `StructMemoryMutation` | `MemoryObjectWrite` |
| `ExpressionNormalization` | `ValueCompute` |
| `EvaluationStep` | `ValueCompute` |

## 5. Slither / SlithIR 到 SemanticFact 的映射

| SlithIR 类型 / 信息 | SemanticFact |
| --- | --- |
| `Assignment` | `ValueAssign` |
| `Binary` | `BinaryOperation` |
| `Unary` | `UnaryOperation` |
| `TypeConversion` | `TypeConversion` |
| `Index` | `IndexAccess` |
| `Member` | `MemberAccess` |
| `Length` | `LengthRead` |
| `Delete` | `Delete` |
| `InitArray` | `ArrayLiteral` |
| `NewArray` | `NewArray` |
| `NewStructure` | `NewStructure` |
| `NewElementaryType` | `NewElementaryType` |
| `NewContract` | `NewContract` |
| `Phi` | `Phi` |
| `PhiCallback` | `PhiCallback` |
| `Unpack` | `TupleUnpack` |
| `InternalCall` | `InternalCall` |
| `InternalDynamicCall` | `InternalDynamicCall` |
| `HighLevelCall` | `ExternalCall` |
| `LowLevelCall` | `LowLevelCall` |
| `LibraryCall` | `LibraryCall` |
| `SolidityCall` | `BuiltinCall` |
| `Condition` | `BranchCondition` |
| `Return` | `Return` |
| `EventCall` | `EventEmit` |
| `Send` / `Transfer` | `ValueTransferCall` |
| `Nop` | `Nop` |
| state variable read | `StateRead` |
| state variable write | `StateWrite` |

注意：`Index`、`Member`、`Length` 在 SlithIR 中首先表示访问表达式或引用构造，不应过早猜成状态读写。是否形成 `StateRead` / `StateWrite`，应结合 Slither 的 `read/write` 集合、变量类型、后续赋值，以及 S-SEIR 对 Yul 的 slot 恢复结果。当前 `SlitherFactAdapter` 会保留 Slither-like 的 operation fact，并在 `semantic.semantic_category` 中归类为 `value`、`data_access`、`call`、`control`、`event`、`return`、`ssa` 等大类。

## 6. 示例：Solidity 到 SemanticFact

Solidity 源码：

```solidity
balances[to] += amount;
```

Slither / SlithIR 可抽象出：

```text
read balances[to]
tmp = balances[to] + amount
write balances[to] = tmp
```

转为 SemanticFact：

```json
{
  "kind": "StateWrite",
  "source_lang": "solidity",
  "origin": "slither_ir",
  "lvalue": "balances[to]",
  "rvalue": "balances[to] + amount",
  "reads": ["balances[to]", "amount"],
  "writes": ["balances[to]"],
  "semantic": {
    "state_variable": "balances",
    "keys": ["to"],
    "operation": "mapping_write"
  }
}
```

## 7. 示例：Yul 到 SemanticFact

Yul 源码：

```yul
mstore(0x00, to)
mstore(0x20, balances.slot)
sstore(keccak256(0x00, 0x40), amount)
```

S-SEIR 恢复过程：

```text
mstore 记录 memory[0x00] = to
mstore 记录 memory[0x20] = balances.slot
sstore 作为语义终点触发 MemorySSA / SinkResolver
keccak256(0x00, 0x40) 被解析为 mapping slot
最终恢复为 balances[to] = amount
```

转为 SemanticFact：

```json
{
  "kind": "StateWrite",
  "source_lang": "yul",
  "origin": "sseir_overlay",
  "lvalue": "balances[to]",
  "rvalue": "amount",
  "reads": ["to", "amount"],
  "writes": ["balances[to]"],
  "semantic": {
    "state_variable": "balances",
    "keys": ["to"],
    "operation": "mapping_write"
  },
  "evidence": {
    "effects": ["MemoryWrite", "MemoryWrite", "StorageWrite"],
    "overlay": "MappingWrite"
  }
}
```

## 8. 路径条件处理

S-SEIR 中的 path-conditioned overlay 不建议在 SemanticFact 层继续保留为一个复杂对象，而应展开成多条事实。

S-SEIR overlay：

```json
{
  "kind": "PathConditionedStorageWrite",
  "candidates": [
    {
      "condition": "flag",
      "access": "balances[a]",
      "value": "x"
    },
    {
      "condition": "!flag",
      "access": "balances[b]",
      "value": "x"
    }
  ]
}
```

展开为 SemanticFact：

```json
[
  {
    "kind": "StateWrite",
    "condition": "flag",
    "lvalue": "balances[a]",
    "rvalue": "x",
    "writes": ["balances[a]"]
  },
  {
    "kind": "StateWrite",
    "condition": "!flag",
    "lvalue": "balances[b]",
    "rvalue": "x",
    "writes": ["balances[b]"]
  }
]
```

这样更适合后续 LLM 和审计规则消费。

## 9. 后续落地架构

建议新增两个 adapter：

```text
SlitherFactAdapter
  输入：Solidity AST / Slither CFG / SlithIR
  输出：SemanticFact

SSeirFactAdapter
  输入：S-SEIR Effect / SemanticOverlay
  输出：SemanticFact
```

最终结构：

```text
Solidity source
  -> Slither / SlithIR
  -> SlitherFactAdapter
  -> SemanticFact Store

Yul inline assembly
  -> S-SEIR pipeline
  -> SSeirFactAdapter
  -> SemanticFact Store
```

## 10. 关键原则

1. SemanticFact 只记录“代码行为事实”，不区分其来自 Solidity 还是 Yul。
2. `origin` 用于说明事实来源，例如 `slither_ir` 或 `sseir_overlay`。
3. S-SEIR 的 Yul 专属恢复逻辑仍然保留，包括 MemorySSA、SinkResolver、slot/event/revert/call 模式恢复。
4. Slither 的 CFG、SSA、变量读写信息可作为 Solidity 部分事实生成的基础。
5. Path-conditioned overlay 应优先展开为多条带 `condition` 的 SemanticFact。
6. 后续 LLM 和审计规则应主要消费 SemanticFact，而不是直接消费 SlithIR 或 S-SEIR overlay。
