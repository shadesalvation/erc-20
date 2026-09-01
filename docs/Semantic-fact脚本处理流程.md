# Semantic Fact 脚本处理流程

## 1. 文档目的

本文按照当前代码的真实调用链，说明 Solidity 源码进入 pipeline 后，如何依次生成 S-SEIR、Solidity 原子操作、Semantic Fact 和 Semantic IR。

本文使用以下人工构造样例说明各阶段的处理结果：

```text
人工构造样例/10_大量Yul_统一语义模型/contracts/YulHeavyERC20.sol
```

该样例包含 11 个函数，覆盖直接状态变量读写、一维和二维 mapping slot 计算、revert、ERC-20 事件、Yul 循环、`extcodesize` 和低层 `staticcall`。

## 2. 总体处理流程

```text
Solidity 源码
  -> 分支预处理
  -> solc AST、事件、selector、storage layout
  -> FunctionUnit + SourceStatementTable
  -> Slither CFG + 本地 Yul CFG 融合
  -> Solidity 原子操作提取
  -> Yul MemorySSA
  -> ExpressionRole + Effect
  -> SinkResolver
  -> SemanticOverlay
  -> FunctionSSEIR
  -> SoliditySemanticLifter / YulSemanticLifter
  -> SemanticFactBridge
  -> Semantic Fact
  -> Semantic IR
```

当前 pipeline 的职责边界是：

```text
Slither          负责 Solidity CFG、SlithIR 和 SlithIR-SSA
S-SEIR           负责 Yul/inline assembly 的静态语义恢复
Semantic Fact    负责统一 Solidity 和 Yul 的事实 schema
Semantic IR      负责将事实重新组织为可修改的函数级程序结构
```

## 3. Pipeline 入口

入口文件为：

```text
scripts/s_seir/s_seir_pipeline.py
```

`main()` 解析源码、solc、Slither、S-SEIR、Semantic Fact、Semantic IR、CFG、原子操作、分支预处理和 Solidity-like 等输出参数，然后调用：

```python
build_sseir(...)
```

`build_sseir()` 是静态分析阶段的总调度器，返回：

```python
list[FunctionSSEIR]
```

运行时应使用项目虚拟环境：

```bash
.venv/bin/python scripts/s_seir/s_seir_pipeline.py ...
```

原因是 `ControlBuilder` 会直接导入 Slither Python 包。若系统 Python 无法导入 Slither，控制流构建会退化为 skeleton CFG，进而缺失 Solidity 原子操作和 Solidity Semantic Fact。

## 4. 分支预处理

实现文件：

```text
scripts/s_seir/s_seir_branch_preprocess.py
scripts/s_seir/s_seir_opaque_preprocess.py
```

预处理先根据已实现的确定性代数规则做保守 opaque-if 剪枝，再利用原分支物化算法检查同一个语义终点在不同路径下是否具有不同的 MemorySSA 输入。

只有出现真实路径分歧，并且分歧会影响语义终点时，才改写分析源码。线性结果或语义相同的多条路径不会改写。

样例 10 的结果为：

```text
opaque_rewrites = 0
branch_rewrites = 0
total_rewrites = 0
```

因此，该样例的预处理源码与原源码相同。

## 5. 全局编译信息提取

预处理后，pipeline 调用 solc 生成 AST，并提取：

```text
Solidity AST
event 定义及 topic0
function/error selector
storage layout
struct 定义
constant 定义
变量类型和数据位置
```

Storage layout 由 `scripts/s_seir/s_seir_storage_layout.py` 调用 solc `--standard-json` 获得，不按源码声明顺序自行猜测。

样例中得到：

```text
_totalSupply -> slot 0
_balances    -> slot 1
_allowances  -> slot 2
```

这些信息写入函数的 `TypeEnv`，供 slot、mapping、struct、bytes 和 array 等语义恢复使用。

## 6. FunctionUnit 与 Source Statement Table

实现文件：

```text
scripts/s_seir/s_seir_source_collector.py
```

`SourceStatementCollector` 遍历 solc AST，为每个函数生成一个 `FunctionUnit`，保存：

```text
function_id、contract、function、signature
function AST
parameters、returns、locals、state_variables
assembly_blocks
source_statements
```

原始语句采用统一 ID：

```text
sol_s_*  Solidity 语句
asm_s_*  Yul 语句
```

例如 `transfer(address,uint256)` 中：

```text
asm_s_1  if iszero(to)
asm_s_2  revert(0x00, 0x00)
asm_s_3  mstore(0x00, caller())
...
asm_s_16 ok := 1
```

这些 ID 是后续 Effect、Overlay 和 Fact 的原始语句引用，不直接表示执行顺序。样例 10 最终生成 11 个函数级处理单元。

## 7. Function-level CFG 构建

实现文件：

```text
scripts/s_seir/s_seir_control_builder.py
```

`ControlBuilder` 融合两个 CFG 来源：

```text
Solidity 部分 -> Slither function CFG
Yul 部分      -> assembly_ast_cfg 构建的本地 Yul CFG
```

Slither 展开的 assembly 节点只作为定位锚点，随后被本地 Yul CFG 子图替换。拼接规则是：

```text
Slither assembly 前驱 -> 本地 Yul entry
本地 Yul exit         -> Slither assembly 后继
```

拼接完成后，在统一 CFG 上重新计算：

```text
dominance
control dependency
control dependency closure
typed def-use
Solidity/Yul boundary context
```

样例中的 `transfer` 最终包含 22 个统一 CFG block 和 23 条 edge。入口与隐式返回来自 Slither，Yul 分支、revert 和普通操作来自本地 CFG。

## 8. Solidity 原子操作提取

实现文件：

```text
scripts/semantic_fact/solidity_atomic_ops.py
```

`SolidityAtomicOperationExtractor` 读取 Solidity CFG block 中归档的 SlithIR-SSA，将其规范化为 `sol_atom`。

主要原子操作包括：

```text
Assignment、Binary、Unary、TypeConversion
Index、Member、StorageRead、StateWrite
Call、EventCall、Condition、Return、Phi
```

每个 atom 补充 `atom_id`、SSA 输入输出、CFG block、block 内顺序、condition、storage access、source span 和 `stmt_refs`。

样例函数主体几乎全部由 Yul 实现，因此 Solidity 侧主要记录 Slither 生成的隐式返回。样例共提取 20 个 Solidity atom，但函数入口 Phi 被标记为：

```text
fact_eligible = false
runtime_operation = false
```

最终只有 9 个 Solidity Semantic Fact。

## 9. Yul MemorySSA

实现文件：

```text
scripts/s_seir/s_seir_memory_ssa.py
```

`build_memory_ssa_views()` 为每个 assembly 块建立 MemorySSA backend，并由 S-SEIR 提供统一查询视图。MemorySSA 记录：

```text
路径 condition
Yul value SSA version
memory SSA version
线性地址 alias
逐字节内存轴
known value
MemoryPhi、loop summary、MemoryTop
跨 assembly 安全继承信息
```

例如：

```yul
mstore(0x00, caller())
mstore(0x20, _balances.slot)
let fromSlot := keccak256(0x00, 0x40)
```

当 `keccak256` 查询 `[0x00, 0x40)` 时，MemorySSA 返回：

```text
memory[0x00:0x20] -> msg.sender, mem_1
memory[0x20:0x40] -> _balances.slot, mem_2
path                 !(iszero(to))
complete             true
```

因此 hash 输入来自 CFG 路径上的 reaching memory definitions，而不是根据相邻源码字符串推测。

样例中的 `batchBalanceSum` 在循环回边上出现不同 memory version，查询结果会形成类似 `phi(mem_1, mem_6)`。若不同 version 最终表达相同的 `account` 和 `_balances.slot`，后续可保守合并为同一 mapping access。

## 10. Yul Effect Layer

实现文件：

```text
scripts/s_seir/s_seir_effect_lifter.py
```

`EffectLifter` 遍历 Yul AST/CFG，将低层操作提升为：

```text
MemoryRead、MemoryWrite、MemoryCopy、MemoryHash
StorageRead、StorageWrite
Call、StaticCall、DelegateCall
EventLog、Branch、Revert、Return
ValueDef、EvaluationStep
```

复杂 Yul 表达式按照 Yul AST 原子化，而不是通过正则硬拆字符串。例如：

```yul
if iszero(and(success, eq(returndatasize(), 0x20))) { ... }
```

会依次产生：

```text
returndatasize()
eq(..., 0x20)
and(success, ...)
iszero(...)
Branch
```

每个 `EvaluationStep` 只包含一个基本操作，并通过临时变量连接。

## 11. Expression Role Layer

实现文件：

```text
scripts/s_seir/s_seir_expr_roles.py
scripts/s_seir/s_seir_semantic_normalizer.py
```

Expression Role 描述表达式在当前语义中的作用，而不仅保存表达式文本。常见角色包括：

```text
memory_slice_start、memory_slice_size、free_memory_pointer
storage_slot_expr、mapping_slot_expr、mapping_key_material
storage_write_value、branch_condition、guard_condition
event_topic0、event_indexed_argument
call_target、call_input_ptr、call_input_size
abi_selector、abi_argument
```

例如 `fromSlot` 在普通表达式层只是变量，恢复后可以记录为：

```text
role       = mapping_slot_expr
normalized = _balances[msg.sender]
```

## 12. SinkResolver

实现文件：

```text
scripts/s_seir/s_seir_sink_resolver.py
```

SinkResolver 以语义终点为触发点，按 CFG 路径解析参数来源。主要终点包括：

```text
mload、keccak256、sload、sstore
log0-log4
call、staticcall、delegatecall
revert、return
```

对于每条可达路径，SinkResolver 记录：

```text
path condition
SSA 参数版本
MemorySSA byte slice
normalized value
resolved/unresolved status
```

SinkResolver 是 MemorySSA 的语义终点查询补充，不会替代 MemorySSA。

## 13. Semantic Overlay

实现文件：

```text
scripts/s_seir/s_seir_overlay_builder.py
```

Overlay Builder 先让 SinkResolver 为相关 Effect 附加查询结果，再通过确定性模式匹配恢复高级语义：

```text
keccak(key, mapping.slot) + sload  -> MappingRead
keccak(key, mapping.slot) + sstore -> MappingWrite
log + topic0 + memory data         -> EventEmit
if condition { revert }            -> RequireOverlay
call + calldata memory             -> ExternalCall
mload(0x40) + memory writes        -> memory object/array construction
```

### 13.1 Storage consumer 激活

普通 `keccak256` 只作为 slot 候选。只有其结果被 `sload/sstore` 消费时，才激活为 mapping/storage overlay，避免将普通 hash 误识别为 storage slot。

### 13.2 transfer 示例

原 Yul：

```yul
sstore(fromSlot, sub(fromBalance, value))
sstore(toSlot, add(sload(toSlot), value))
```

恢复出的核心语义是：

```text
StateRead  _balances[msg.sender]
StateWrite _balances[msg.sender] = fromBalance - value
StateRead  _balances[to]
StateWrite _balances[to] = _balances[to] + value
```

这些操作分别保留自己的 CFG condition。

### 13.3 Event 示例

原 Yul：

```yul
mstore(0x00, value)
log3(0x00, 0x20, TRANSFER_TOPIC0, caller(), to)
```

处理时先将 topic0 与源码 event 定义匹配，indexed 参数取自 topic，非 indexed 参数通过 MemorySSA 查询 data range，最终得到：

```text
EventEmit Transfer(msg.sender, to, value)
```

### 13.4 External call 示例

`externalBalanceOf` 先在 memory 中写入 selector 和 `account`，随后执行：

```yul
staticcall(gas(), token, ptr, 0x24, ptr, 0x20)
```

MemorySSA 和 SinkResolver 恢复出：

```text
call_kind          staticcall
target             token
selector           0x70a08231
selector_signature balanceOf(address)
arguments          [account]
```

## 14. SemanticNormalizer 与 FunctionSSEIR

`SemanticNormalizer` 对 Role、Effect 和 Overlay 做统一补充和去重，并加入 canonicalization、struct table、constant table、selector registry 和 branch preprocess record。

最终封装为：

```python
FunctionSSEIR(
    source_statements,
    control,
    expr_roles,
    effects,
    semantic_overlays,
    security_facts,
    analysis_facts,
)
```

标准 `sseir.json` 通过 `to_semantic_dict()` 输出，隐藏 MemorySSA/SinkResolver 的内部查询轨迹，只保留语义结果。完整查询过程可通过：

```text
--debug-output sseir_debug.json
```

输出。

## 15. Semantic Fact 两路提升

统一 Semantic Fact 的入口是：

```text
scripts/semantic_fact/adapter.py
build_function_level_semantic_fact_payload()
```

处理路线为：

```text
Solidity:
  SolidityAtomicOperationExtractor
    -> SoliditySemanticLifter

Yul:
  S-SEIR
    -> YulSemanticLifter

两路统一 schema
    -> SemanticFactBridge
```

`SoliditySemanticLifter` 只将一个 `sol_atom` 投影为一个 Semantic Fact，不分析 Yul，也不调用 S-SEIR 组件。

`YulSemanticLifter` 只将已经完成恢复的 S-SEIR Overlay 投影为 Semantic Fact，不重新执行 MemorySSA、SinkResolver 或 Overlay 识别。

两种来源最终使用相同字段结构：

```text
fact_id、operation_id、kind
source_lang、origin、function_id
stmt_refs、cfg_nodes、condition
lvalue、rvalue、reads、writes
order、semantic、evidence
control_predecessors
```

## 16. SemanticFactBridge

实现文件：

```text
scripts/semantic_fact/bridge.py
```

Bridge 不重新恢复语义，只负责将已统一 schema 的 Solidity Fact 和 Yul Fact 放回同一函数 CFG：

1. 根据 effect 找到事实对应的语义终点 CFG block；
2. 对统一 CFG 计算 reverse postorder；
3. 使用 block order 和 block 内 operation order 排序；
4. 传播能够支配后续操作的 guard；
5. 连接同 block 和跨 block 的 `control_predecessors`；
6. 统一重编号为 `fact_1...fact_n`。

样例 `transfer` 的核心 Fact 顺序为：

```text
检查 to 是否为零地址
解析 _balances[msg.sender] 的位置
读取 _balances[msg.sender]
检查余额
解析 _balances[to] 的位置
写入 _balances[msg.sender]
读取 _balances[to]
写入 _balances[to]
发出 Transfer
设置 ok
返回 ok
```

## 17. Semantic IR

实现目录：

```text
scripts/semantic_ir/
```

`SemanticIRBuilder` 将 Semantic Fact 放回 CFG block，并构造可在程序运行过程中修改的内存对象：

```text
SemanticProgram
  -> SemanticFunction
    -> BasicBlock
      -> SemanticInstruction
      -> Terminator
    -> ExpressionNode DAG
    -> LocationNode
    -> ValueRecord
    -> FactGroup
```

辅助事实不会全部变成独立运行时指令。例如：

```text
StorageLocationResolve + StateRead
```

会被组织为一个 FactGroup，共同支撑一条 `StateRead` 指令。

该结构提供 `replace_expression`、`replace_instruction`、`remove_instruction`、`redirect_edge`、`rewrite_history` 和 def-use rebuild，适合作为后续 Semantic IR 解混淆 pass 的直接内存起点。

## 18. Solidity-like 输出

`solidity_like.txt` 是独立的审计展示层。它根据 Overlay 和 CFG condition 将 assembly 显示为 Solidity-like 形式，但：

```text
不会修改原源码
不要求能够通过 solc 编译
不会作为 Semantic Fact 的输入
不能替代 S-SEIR 的底层语义记录
```

## 19. 样例 10 输出统计

本次完整处理结果为：

```text
FunctionSSEIR 函数数       11
Solidity atomic operations 20
Solidity Semantic Facts     9
Yul Semantic Facts          108
Semantic Facts 总数         117
Semantic IR instructions    45
Expression nodes            180
Storage locations           14
Fact groups                 45
Semantic IR diagnostics     0
```

主要输出文件为：

```text
sseir.json                       标准 S-SEIR 语义输出
sseir_debug.json                 MemorySSA/SinkResolver 查询轨迹
sseir.txt                        S-SEIR 文本审计输出
solidity_atomic_operations.json  Solidity 原子操作
semantic_facts.json              Solidity/Yul 统一事实
semantic_ir.json                 可修改的函数级 Semantic IR
solidity_like.txt                Solidity-like 审计视图
cfg/*.dot                        函数级统一 CFG
branch_preprocessed.sol          分支预处理后的分析源码
branch_preprocessed.txt          分支预处理报告
```

## 20. 阶段性结论

当前 pipeline 已形成四个层次：

```text
第一层：Slither/solc/Yul AST 提供结构、类型和控制流
第二层：S-SEIR 恢复 Yul 的底层 Effect 与高级 Overlay
第三层：Semantic Fact 统一 Solidity 和 Yul 的原子语义
第四层：Semantic IR 将事实组织为可修改的函数级程序对象
```

MemorySSA、SinkResolver、路径敏感分支追踪和 consumer-gated overlay 只服务于 Yul 语义恢复；Solidity 部分由 SlithIR-SSA 原子化；两者最终在 Semantic Fact schema 和 function-level CFG 顺序上统一。
