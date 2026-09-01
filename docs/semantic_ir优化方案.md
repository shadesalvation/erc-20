# Semantic IR 优化方案

## 1. 优化目标

本轮优化用于解决 Semantic IR 中执行数据流与高级语义表达混用、Yul 嵌套操作缺少显式 SSA 结果，以及 MemorySSA 分析证据被过早裁剪的问题。

核心原则如下：

1. 区分实际执行表达式和归一化高级表达式；
2. 在 S-SEIR 分析前对 Yul 操作进行原子化；
3. 内部运行模型保留完整 MemorySSA、SinkResolver 和路径信息；
4. 仅在最终公开输出时裁剪分析器内部状态；
5. Def-Use 必须依据 function-level CFG 和 SSA 构建；
6. 无法唯一确定的数据来源保守标记为 unresolved，不进行猜测。

## 2. 区分执行表达式与归一化表达式

每条 `SemanticInstruction` 同时维护两种表达式：

```text
execution_expr
normalized_expr
```

其中：

- `execution_expr` 表示原子操作实际使用的操作数，负责 SSA、Def-Use、执行顺序和路径分析；
- `normalized_expr` 表示恢复后的高级语义，负责审计展示、模式匹配和后续解混淆。

例如 Solidity 源码：

```solidity
total = total + amount;
```

其执行语义应记录为：

```text
total_1 = StateRead(total)
tmp_1 = Add(total_1, amount)
StateWrite(total, tmp_1)
```

最后一条指令同时保留：

```text
normalized_expr = total + amount
```

构建 Def-Use 时只能使用 `execution_expr`，不能用 `normalized_expr` 覆盖实际的 SSA 操作数。

## 3. Yul 原子化

### 3.1 作用

Yul 原子化将嵌套表达式拆分为单一基本操作，使每个中间结果都有明确的 SSA 定义。

输入：

```yul
sstore(balanceSlot, add(sload(balanceSlot), amount))
```

内部原子化结果：

```text
yul_tmp_1 = sload(balanceSlot)
yul_tmp_2 = add(yul_tmp_1, amount)
sstore(balanceSlot, yul_tmp_2)
```

对应 Semantic IR：

```text
yul_tmp_1 = StateRead(balances[msg.sender])
yul_tmp_2 = Add(yul_tmp_1, amount)
StateWrite(balances[msg.sender], yul_tmp_2)
```

这样，嵌套 `sload` 不再是没有结果变量的匿名读取，状态读取与状态写入之间可以建立明确的 Def-Use 关系。

### 3.2 原子节点字段

Yul 原子化结果作为内部结构保存，不要求生成可编译的 Yul 源码。每个原子节点至少记录：

```text
atom_id
op
arguments
result
evaluation_order
cfg_node
condition
stmt_refs
```

### 3.3 处理规则

1. 按 Yul/EVM dialect 的实际参数求值顺序拆分表达式；
2. 每个原子操作只包含一个基本运算或副作用；
3. 同一原始语句拆出的原子操作共同引用原始 `stmt_ref`；
4. 不同 CFG 路径上的定义使用不同 SSA 版本；
5. 合流点存在多个可达定义时构造 Phi；
6. 无法唯一确定来源时保留 unresolved，不强行绑定。

Yul 原子化能够解决嵌套操作显式化问题，但不能取代 CFG、SSA、MemorySSA 和路径分析。

## 4. 调整后的 Yul 处理流程

```text
Yul AST
-> Yul 原子化
-> assembly CFG
-> LocalSSA / MemorySSA
-> SinkResolver
-> Effect
-> Semantic Overlay
-> YulSemanticLifter
-> Semantic Fact
-> Semantic IR
```

S-SEIR 仍然只处理 Yul。Solidity 部分继续由 Solidity 原子操作提取模块和 `SoliditySemanticLifter` 处理，不能使用 S-SEIR 组件分析 Solidity。

## 5. 内部模型与公开输出

### 5.1 内部运行模型

内部运行对象应完整保留：

```text
MemorySSA version
reaching definition
byte-axis 数据来源
source node / source range
known / unknown 属性
base / offset / alias
SinkResolver 查询结果
LoopMemoryRecord
MemoryPhi
MemoryRangeSummary
路径 condition
```

例如 mapping slot 的内部分析可以保留：

```text
memory[0x00:0x20] <- msg.sender
memory[0x20:0x40] <- balances.slot
keccak256(0x00, 0x40)
```

以及每段数据对应的 MemorySSA 版本、定义节点和原始语句。

### 5.2 公开语义模型

最终导出时不输出 MemorySSA 和 SinkResolver 的查询状态，只输出已经恢复的语义与紧凑源码引用：

```text
location = balances[msg.sender]
stmt_refs = [asm_s_1, asm_s_2, asm_s_3, asm_s_4]
```

建议区分：

```text
AnalysisSemanticProgram
PublicSemanticProgram
```

- `AnalysisSemanticProgram` 是运行时完整分析模型；
- `PublicSemanticProgram` 由 exporter 从内部模型投影生成。

裁剪只能发生在最终导出阶段，不能在 S-SEIR、Semantic Fact 或 Semantic IR 构建过程中提前删除分析证据。

## 6. 紧凑证据链

最终模型虽然不公开 MemorySSA 查询细节，但必须保留由这些分析结果推导出的源码引用。

输入：

```yul
mstore(0x00, caller())
mstore(0x20, balances.slot)
let slot := keccak256(0x00, 0x40)
sstore(slot, value)
```

最终 `StateWrite` 应记录：

```json
{
  "kind": "StateWrite",
  "location": "balances[msg.sender]",
  "stmt_refs": ["asm_s_1", "asm_s_2", "asm_s_3", "asm_s_4"]
}
```

MemorySSA 负责在内部生成 `support_stmt_refs`，Semantic Fact 和 Semantic IR 继续传递这些引用，Exporter 只删除查询状态，不删除其推导出的证据引用。

## 7. CFG 感知的 Def-Use

原子化完成后，Def-Use 必须依据 function-level CFG 构建，不能按照指令列表的插入顺序扫描。

处理步骤：

1. 为每个 basic block 计算 reaching definitions 的 `IN` 和 `OUT`；
2. 沿 CFG 边传播 SSA 定义；
3. 存在唯一可达定义时直接建立 use-def 连接；
4. 存在多个可达定义时建立 Phi；
5. 每个 Phi 输入保留其来源路径和 condition；
6. 中间存在同一 Location 的写操作时，旧定义不能越过该写操作；
7. 无法可靠确定来源时保留 unresolved。

跨 Solidity/Yul 边界时：

- Solidity 原子操作由 Solidity 原子化模块提供；
- Yul 原子操作由 Yul 原子化模块提供；
- Semantic IR 只负责在统一 function-level CFG 中组织和连接二者；
- 两种来源使用统一的 Value、Location、Instruction 和 Def-Use 结构。

## 8. 完整示例

输入：

```solidity
total = total + amount;

assembly {
    mstore(0x00, caller())
    mstore(0x20, balances.slot)
    let balanceSlot := keccak256(0x00, 0x40)
    sstore(balanceSlot, add(sload(balanceSlot), amount))
}
```

内部执行 IR：

```text
total_1 = StateRead(total)
sol_tmp_1 = Add(total_1, amount)
StateWrite(total, sol_tmp_1)

balanceSlot = MappingLocation(balances, msg.sender)
balance_1 = StateRead(balanceSlot)
yul_tmp_1 = Add(balance_1, amount)
StateWrite(balanceSlot, yul_tmp_1)
```

归一化语义：

```text
total = total + amount
balances[msg.sender] = balances[msg.sender] + amount
```

MemorySSA 和 SinkResolver 的完整查询状态仍保存在运行时对象中，用于验证、回溯、路径分析和后续解混淆；最终公开的 Semantic IR 文件只保留代码做了什么、在什么条件下执行、执行顺序、状态位置以及相关源码引用。

## 9. 实施优先级

1. 修正 Semantic IR 构建器，分别保存 `execution_expr` 和 `normalized_expr`；
2. 增加 Yul 原子化前置步骤，并保留实际求值顺序；
3. 为没有显式左值的嵌套状态读取生成 SSA 结果；
4. 将 Def-Use 改为基于 function-level CFG 的 reaching-definition 分析；
5. 内部保留完整 MemorySSA、SinkResolver 和路径查询结果；
6. 在最终 exporter 中统一裁剪内部查询状态；
7. 将 MemorySSA 推导出的来源转化为紧凑 `support_stmt_refs`；
8. 为分支合流、Phi、嵌套 `sload`、mapping slot 和跨 Solidity/Yul CFG 编写测试。
