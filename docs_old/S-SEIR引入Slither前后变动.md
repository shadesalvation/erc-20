# S-SEIR 引入 Slither 前后变动

## 1. 修改目的

本次修改主要补强 S-SEIR 对普通 Solidity 代码的底层语义记录。

修改前，S-SEIR 对 Yul 已具备 CFG、MemorySSA、storage、event、call、revert 等较完整的语义分析，但对普通 Solidity 语句主要依赖 AST 文本，只能浅层记录 `Return`、`Revert` 和 `Branch`。

修改后，函数级 CFG 继续由 Slither Solidity CFG 与项目原有 Yul CFG 拼接，同时把 SlithIR 和 SlithIR-SSA 原子操作归档到 Solidity CFG block，再提升为统一的 S-SEIR Effect 和 Semantic Overlay。

整体流程由：

```text
Solidity AST 文本
-> 少量 Return / Revert / Branch
```

升级为：

```text
Solidity CFG
-> SlithIR-SSA 原子操作
-> ValueDef / StorageRead / StorageWrite / Call / EventLog
   / Branch / Require / Revert / Return
-> Semantic Overlay
```

Yul 部分原有的 CFG、MemorySSA、SinkResolver、slot 恢复和语义终点倒推算法保持不变。

## 2. CFG 与原子操作记录

### 2.1 修改前

Solidity CFG block 主要记录节点标识、节点类型和源码文本：

```json
{
  "slither_node_id": 1,
  "slither_node_type": "NodeType.RETURN",
  "text": "wZHA[_owner][_spender]"
}
```

### 2.2 修改后

每个 Solidity CFG block 新增：

```text
slithir
slithir_ssa
state_variables_read
state_variables_written
```

每条 SlithIR-SSA 操作记录操作顺序、操作类型、左右值、参数、类型和 SSA 信息。例如：

```json
{
  "order": 0,
  "kind": "Index",
  "text": "REF_2 -> wZHA_1[_owner_1]",
  "ssa": true,
  "lvalue": {
    "text": "REF_2",
    "type": "mapping(address => uint256)",
    "is_reference": true
  },
  "variable_left": {
    "text": "wZHA_1",
    "base_name": "wZHA",
    "is_state": true
  },
  "variable_right": {
    "text": "_owner_1",
    "base_name": "_owner"
  }
}
```

同时修复了 CRLF 源文件下 Slither 原始文件偏移与 solc standard-json 归一化源码偏移不一致的问题。

## 3. 普通赋值与计算

### 3.1 修改前

普通 Solidity 计算通常不会形成独立的底层 Effect：

```solidity
uint256 x = amount + 1;
```

### 3.2 修改后

SlithIR-SSA 中的计算和赋值被分别记录为原子 `ValueDef`：

```json
{
  "kind": "ValueDef",
  "attrs": {
    "targets": ["TMP_1"],
    "value": "(amount + 1)",
    "atomic_operation": "Binary",
    "source": "slithir_ssa"
  }
}
```

```json
{
  "kind": "ValueDef",
  "attrs": {
    "targets": ["x"],
    "value": "(amount + 1)",
    "atomic_operation": "Assignment",
    "source": "slithir_ssa"
  }
}
```

目前支持的主要 SlithIR 原子类型包括：

```text
Assignment
Binary
Unary
TypeConversion
Length
Index
Member
Phi
Unpack
InitArray
NewArray
NewStructure
NewContract
```

## 4. SSA 与 Phi

### 4.1 修改前

普通 Solidity 变量没有统一的 SSA 来源记录，不同控制路径上的变量版本主要依赖源码文本表达。

### 4.2 修改后

Phi 被记录为 `ValueDef`：

```json
{
  "kind": "ValueDef",
  "attrs": {
    "targets": ["value"],
    "target_versions": {
      "value": ["value_3"]
    },
    "value": "phi(value_1, value_2)",
    "atomic_operation": "Phi",
    "phi_inputs": ["value_1", "value_2"]
  }
}
```

如果 Phi 输入只是同一源码变量的不同 SSA 版本，则折叠回源码变量。例如：

```text
phi(wZHA_0, wZHA_1) -> wZHA
```

从而避免产生 `phi(wZHA, wZHA)[owner]` 一类无意义展示。

## 5. 状态读取与写入

### 5.1 修改前

完整的 `StorageRead` 和 `StorageWrite` 主要来自 Yul `sload`、`sstore`。普通 Solidity 状态访问通常只保留在源码文本中。

### 5.2 修改后：状态读取

源码：

```solidity
return balances[user];
```

底层 Effect：

```json
{
  "kind": "StorageRead",
  "attrs": {
    "typed_access": true,
    "access": "balances[user]",
    "state_variable": "balances",
    "keys": ["user"],
    "reference_kind": "index",
    "access_version": "REF_1",
    "source": "slithir_ssa",
    "path_states": ["entry"]
  }
}
```

### 5.3 修改后：状态写入

源码：

```solidity
balances[user] = amount;
```

底层 Effect：

```json
{
  "kind": "StorageWrite",
  "attrs": {
    "typed_access": true,
    "access": "balances[user]",
    "state_variable": "balances",
    "keys": ["user"],
    "value": "amount",
    "value_ssa": "amount_1",
    "source": "slithir_ssa"
  }
}
```

简单状态变量写入记录为：

```json
{
  "kind": "StorageWrite",
  "attrs": {
    "typed_access": true,
    "access": "totalSupply",
    "state_variable": "totalSupply",
    "keys": [],
    "value": "amount"
  }
}
```

`delete balances[user]` 被记录为值为 `0` 的 `StorageWrite`，并带有：

```json
{
  "delete": true
}
```

## 6. 多维 mapping 与成员引用

连续的 SlithIR `Index` 和 `Member` 操作会构建完整的类型化引用链。

例如：

```text
REF_1 = allowances[owner]
REF_2 = REF_1[spender]
```

最终记录为：

```json
{
  "access": "allowances[owner][spender]",
  "state_variable": "allowances",
  "keys": ["owner", "spender"]
}
```

结构体成员访问同样可以通过 `Member` 和 `Index` 组合表示，例如：

```text
account.info.balance
```

普通 Solidity 的类型化状态引用不需要经过 slot 反推；Yul 状态访问仍通过 MemorySSA、SinkResolver 和 slot 恢复算法处理。

## 7. 条件分支

### 7.1 修改前

```json
{
  "kind": "Branch",
  "attrs": {
    "condition": "if (flag)",
    "language": "solidity"
  }
}
```

### 7.2 修改后

```json
{
  "kind": "Branch",
  "attrs": {
    "condition": "(amount != 0)",
    "condition_ssa": "TMP_2",
    "source": "slithir_ssa",
    "path_states": ["entry"]
  }
}
```

分支中的状态写入分别携带对应路径：

```json
{
  "path_states": ["flag"]
}
```

或：

```json
{
  "path_states": ["!(flag)"]
}
```

条件表达式中的状态读取也会独立生成 `StorageRead`。

## 8. Event

### 8.1 修改前

普通 Solidity `emit` 没有形成完整的统一 Event Effect；事件恢复主要针对 Yul `log0-log4`，需要匹配 `topic0` 并解析 memory data。

### 8.2 修改后

源码：

```solidity
emit Approval(owner, spender, amount);
```

底层 Effect：

```json
{
  "kind": "EventLog",
  "attrs": {
    "source_event": true,
    "event_name": "Approval",
    "arguments": ["owner", "spender", "amount"],
    "argument_versions": ["owner_1", "spender_1", "amount_1"],
    "source": "slithir_ssa"
  }
}
```

随后直接提升为 `EventEmit`。事件名称来自 Slither `EventCall`，不需要执行 Yul topic 匹配。

## 9. 函数调用

### 9.1 修改前

普通 Solidity 调用大多没有进入统一 Effect；外部调用恢复主要针对 Yul `call`、`staticcall` 和 `delegatecall`。

### 9.2 修改后

Solidity 调用根据 SlithIR 类型记录为：

```text
InternalCall
LibraryCall
ExternalCall
```

源码：

```solidity
uint256 result = target.ping(value);
```

底层 Effect：

```json
{
  "kind": "ExternalCall",
  "attrs": {
    "typed_call": true,
    "target": "target",
    "function": "ping",
    "function_signature": "ping(uint256)",
    "arguments": ["value"],
    "result": "TMP_1",
    "result_version": "TMP_1",
    "source": "slithir_ssa"
  }
}
```

内部调用：

```solidity
twice(result);
```

记录为：

```json
{
  "kind": "InternalCall",
  "attrs": {
    "typed_call": true,
    "function": "twice",
    "arguments": ["result"]
  }
}
```

Yul 低层调用继续使用原 MemorySSA 和 SinkResolver，从调用语义终点倒推 calldata、selector 和参数来源。

## 10. Require 与 Revert

### 10.1 修改前

Solidity `revert` 主要保存源码文本，`require` 没有统一的结构化 Effect。

### 10.2 修改后

源码：

```solidity
require(amount != 0, "zero");
```

记录为：

```json
{
  "kind": "Require",
  "attrs": {
    "condition": "(amount != 0)",
    "arguments": ["(amount != 0)", "\"zero\""],
    "builtin": "require",
    "source": "slithir_ssa"
  }
}
```

随后形成 `RequireOverlay`，并保留失败参数。

Solidity `revert` 记录 payload、参数和来源。Yul 的空 payload、Custom Error selector、RawRevertBytes 等恢复规则保持不变。

## 11. Return

### 11.1 修改前

```json
{
  "kind": "Return",
  "attrs": {
    "text": "return true",
    "language": "solidity"
  }
}
```

### 11.2 修改后

```json
{
  "kind": "Return",
  "attrs": {
    "values": ["true"],
    "value": "true",
    "value_versions": [""],
    "source": "slithir_ssa",
    "path_states": ["entry"]
  }
}
```

返回 mapping 时可以同时得到：

```text
StorageRead: wZHA[_owner][_spender]
ReturnValue: return wZHA[_owner][_spender];
```

## 12. Overlay 与 Expression Role 适配

新增底层 Effect 会继续进入统一高级语义层：

| 底层 Effect | Semantic Overlay |
| --- | --- |
| 类型化 `StorageRead` | `MappingRead` / `StateVariableRead` |
| 类型化 `StorageWrite` | `MappingWrite` / `StateVariableWrite` |
| 源码 `EventLog` | `EventEmit` |
| `ExternalCall` | `ExternalCall` |
| `InternalCall` | `InternalCall` |
| `LibraryCall` | `LibraryCall` |
| `Require` | `RequireOverlay` |
| Solidity `Return` | `ReturnValue` |

Expression Role 同步补充：

```text
state_access_expr
mapping_key_material
storage_write_value
call_target
abi_argument
branch_condition
guard_condition
return_value
```

## 13. 与原 Yul 算法的关系

本次修改没有使用 SlithIR 替换原有 Yul 算法。

继续保留：

1. 项目自建 Yul CFG；
2. Function-level assembly MemorySSA；
3. byte-axis memory 记录；
4. Loop-aware Lazy MemorySSA；
5. SinkResolver 语义终点查询；
6. 分支候选和路径条件；
7. slot、event、external call、revert 的模式匹配；
8. 无法确认时保守记录为 unknown。

Slither 主要用于其更成熟的 Solidity CFG、SlithIR、SSA、类型和引用关系。普通 Solidity 使用 Slither 的类型化结果；Yul 使用项目原有的专用恢复算法。

## 14. 测试结果

新增四类测试：

1. 多维 mapping 读写和 `EventEmit`；
2. 条件路径下的状态写入和 `RequireOverlay`；
3. 外部调用、内部调用和 `ReturnValue`；
4. Solidity/Yul 混合函数，验证原 MemorySSA mapping 恢复未被影响。

真实 Token 样例得到：

```text
MappingRead: wZHA[_owner][_spender]
ReturnValue: return wZHA[_owner][_spender];
ReturnValue: return true;
```

同时通过原有 control builder、storage/revert、SinkResolver、跨 assembly MemorySSA、event、memory array 和展示层回归测试。面向 assembly 的 LLM 导出没有混入新增的普通 Solidity SlithIR Effect。

## 15. 结论

引入 Slither 后，S-SEIR 对普通 Solidity 的记录由 AST 文本级浅层描述升级为基于 CFG 和 SlithIR-SSA 的原子语义模型。普通 Solidity 与 Yul 最终使用相同的 Effect、Overlay 和 Expression Role 框架，但各自采用更合适的底层分析方法：

```text
Solidity
-> Slither CFG / SlithIR-SSA / 类型化引用

Yul
-> 自建 CFG / MemorySSA / SinkResolver / 模式匹配

二者
-> 统一 S-SEIR Effect / Semantic Overlay / Expression Role
```
