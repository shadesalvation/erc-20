# Semantic IR 技术文档

## 1. 目标

Semantic IR 的目标是把已经完成语义分析的函数级 S-SEIR 与统一
`SemanticFact`，重新组织成适合程序变换的可变中间表示。

它解决的不是“再次识别语义”，而是以下问题：

1. 将彼此独立的 fact 放回所属函数和 CFG basic block；
2. 将同一高级操作的 effect fact 与 support fact 合并；
3. 将表达式字符串转换为可遍历、可替换的表达式 DAG；
4. 将状态变量、mapping、struct member 和手工 slot 表示为 Location 对象；
5. 显式记录 instruction、terminator、CFG edge 和当前 Def-Use；
6. 为后续解混淆 pass 提供原地改写接口与改写历史；
7. 同时输出完整 JSON 和便于人工审计的文本 IR。

整体关系为：

```text
Solidity source
  -> Solidity atomic operations
  -> SoliditySemanticLifter
  -> Solidity SemanticFact

Yul inline assembly
  -> Yul atomic operations
  -> S-SEIR / MemorySSA / SinkResolver / semantic overlay
  -> YulSemanticLifter
  -> Yul SemanticFact

Solidity SemanticFact + Yul SemanticFact
  -> SemanticFactBridge 按 function-level CFG 组织
  -> SemanticIRBuilder
  -> mutable SemanticProgram
  -> JSON / text IR
  -> 后续解混淆与源码重建
```

当前 schema 为：

```text
semantic-ir/v1
```

## 2. 设计边界

### 2.1 Semantic IR 不重新做语义恢复

Semantic IR 的输入已经包含：

- S-SEIR 构建的 function-level Solidity/Yul 融合 CFG；
- Solidity 原子操作提升后的 Semantic Fact；
- Yul effect、overlay 提升后的 Semantic Fact；
- fact 的 condition、读写集合、CFG 锚点、源码引用与语义字段。

`SemanticIRBuilder` 不重新执行 MemorySSA、slot 恢复、event 匹配或外部调用分析。
这些工作仍由前面的 S-SEIR 与 SemanticFact pipeline 完成。

### 2.2 Semantic Fact 与 Semantic IR 的职责不同

`SemanticFact` 偏向独立的分析事实：

```text
fact_1: StorageLocationResolve
fact_2: StateRead
fact_3: ValueCompute
fact_4: StateWrite
```

Semantic IR 偏向完整的程序结构：

```text
BasicBlock bb_1:
    old = StateRead(location)
    next = Add(old, amount)
    StateWrite(location, next)
    Goto(bb_2)
```

因此不能机械采用“一条 Fact 等于一条 IR instruction”。辅助 fact 可能会被融合成
Location、Expression 或 instruction 的 `origin_facts`。

### 2.3 运行时模型与输出模型

Semantic IR 的核心对象是 Python dataclass，而不是临时 JSON 字典。构建完成后，
`SemanticProgram`、`SemanticFunction`、`BasicBlock`、`SemanticInstruction`、
`ExpressionNode` 和 `LocationNode` 会一直作为内存对象存在，可以直接用于后续 pass。

JSON 是调用 `to_dict()` 后的持久化结果；文本 IR 只是同一内存对象的审计视图，
不会再次分析或改变语义。

## 3. 实现文件

| 文件 | 作用 |
| --- | --- |
| `scripts/semantic_ir/model.py` | 定义 Semantic IR 的运行时数据结构与改写 API |
| `scripts/semantic_ir/expressions.py` | 解析表达式并构建函数内共享的 Expression DAG |
| `scripts/semantic_ir/builder.py` | 从 FunctionSSEIR 和 SemanticFact 构建 SemanticProgram |
| `scripts/semantic_ir/exporter.py` | 输出 JSON 和人类可阅读的文本 IR |
| `scripts/semantic_ir/cli.py` | Semantic IR 独立命令行入口 |
| `scripts/semantic_ir/tests.py` | Fact Fusion、CFG、Def-Use、终结指令和输出测试 |
| `scripts/s_seir/s_seir_pipeline.py` | 主 pipeline 中的集成入口 |
| `scripts/s_seir/s_seir_batch_contracts.py` | 批处理时按合约输出 Semantic IR |

## 4. 输入

构建入口为：

```python
build_semantic_ir_program(functions, fact_payload, source=source)
```

### 4.1 `functions`

`functions` 是 `FunctionSSEIR` 列表。Semantic IR 实际使用其中的：

```text
function_id
contract
function
signature
source_statements
control.blocks
control.edges
```

`control` 已经是 function-level CFG，其中 Solidity 节点来自 Slither，Yul 节点来自
assembly CFG，并已完成 inline assembly 子图与 Solidity CFG 的拼接。

### 4.2 `fact_payload`

`fact_payload` 是统一 Semantic Fact 输出，Semantic IR 读取：

```text
fact_id
function_id
kind
source_lang
stmt_refs
cfg_nodes
anchor_cfg_node
condition
lvalue
rvalue
reads
writes
order
semantic
evidence
```

如果调用者不传 `fact_payload`，`build_semantic_ir_program()` 会调用
`build_function_level_semantic_fact_payload()` 现场生成。

## 5. 总体构建流程

`SemanticIRBuilder._function()` 对每个函数按以下顺序处理：

```text
1. 创建 SemanticFunction
2. 从 function-level control 物化 BasicBlock、CFGEdge、Terminator
3. 对 SemanticFact 执行 Fact Grouping / Fact Fusion
4. 将主 fact 转换为 SemanticInstruction
5. 将 Return / Revert 转换为 Terminator 或显式 instruction
6. 将分支条件辅助 fact 归档到 Branch terminator
7. 固化 ExpressionArena 为函数的 expressions 表
8. 按 fact 的 CFG 块内顺序排列 instruction
9. 构建当前 Def-Use 表
10. 执行结构一致性检查并生成 diagnostics
```

最终返回：

```text
SemanticProgram
  -> SemanticFunction
      -> BasicBlock
          -> SemanticInstruction
          -> Terminator
      -> CFGEdge
      -> ExpressionNode DAG
      -> LocationNode
      -> ValueRecord / Def-Use
      -> FactGroup
      -> RewriteRecord
```

## 6. 运行时数据结构

### 6.1 SemanticProgram

```json
{
  "schema": "semantic-ir/v1",
  "source": "Token.sol",
  "function_count": 1,
  "functions": [],
  "diagnostics": []
}
```

| 字段 | 含义 |
| --- | --- |
| `schema` | Semantic IR 格式版本 |
| `source` | 分析入口源码 |
| `functions` | 函数级 Semantic IR |
| `function_count` | 函数数量，由导出时计算 |
| `diagnostics` | 汇总各函数的一致性检查结果 |

### 6.2 SemanticFunction

主要字段如下：

| 字段 | 含义 |
| --- | --- |
| `function_id` | `Contract.signature` 形式的函数唯一标识 |
| `source_statements` | 原始 Solidity/Yul 语句表 |
| `fact_table` | 输入该函数的完整 fact 表，便于回查 |
| `blocks` | `block_id -> BasicBlock` |
| `edges` | 函数 CFG 边 |
| `expressions` | `expr_id -> ExpressionNode` 表达式 DAG |
| `locations` | `location_id -> LocationNode` 状态位置表 |
| `values` | `value_id -> ValueRecord` Def-Use 表 |
| `fact_groups` | 主 fact 与辅助 fact 的融合关系 |
| `unplaced_facts` | 无法可靠放入 CFG block 的 fact |
| `rewrite_history` | 后续 pass 对函数执行的改写记录 |
| `diagnostics` | 当前函数的结构检查结果 |

### 6.3 BasicBlock 与 CFGEdge

`BasicBlock` 保存：

```text
block_id
kind                 # solidity / yul / synthetic
instructions
terminator
predecessors
successors
stmt_refs
attrs
```

`CFGEdge` 保存：

```text
source
target
kind
predicate
```

`predicate` 只从形如 `true:<condition>` 或 `false:<condition>` 的 edge kind 中提取；
普通 `true`、`false`、`next`、`fallthrough` 边只保留 `kind`。

### 6.4 SemanticInstruction

```json
{
  "instruction_id": "ir_4",
  "op": "StateRead",
  "result": "balance_1",
  "result_value": "value:balance_1#1",
  "expression": "expr_9",
  "location": "loc_2",
  "arguments": [],
  "condition": "expr_12",
  "source_languages": ["yul"],
  "origin_facts": ["fact_6", "fact_5", "fact_4"],
  "origin_effects": ["eff_4"],
  "stmt_refs": ["asm_s_1", "asm_s_2", "asm_s_3", "asm_s_4"],
  "attrs": {}
}
```

字段作用：

| 字段 | 含义 |
| --- | --- |
| `op` | 统一 IR 操作，如 `Assign`、`StateRead`、`StateWrite`、`Require` |
| `result` | 指令产生的变量名 |
| `result_value` | Def-Use 中对应的版本化 ValueRecord id |
| `expression` | 主表达式的 `expr_id` |
| `location` | 状态位置的 `location_id` |
| `arguments` | event、call、Phi 等操作的参数表达式 |
| `condition` | 该 instruction 自带的路径条件 |
| `source_languages` | `solidity` 或 `yul`，仅表示来源，二者使用相同结构 |
| `origin_facts` | 构成该 instruction 的主 fact 与辅助 fact |
| `stmt_refs` | 对应原始源码语句 |
| `attrs.semantic` | 输入 fact 的结构化高级语义 |
| `attrs.order` | CFG 块内 operation order / atomic sequence |

当前 kind 到 instruction op 的特殊映射为：

```text
ValueAssign / ValueCompute / TypeConversion -> Assign
ValuePhi                                  -> Phi
StorageLocationResolve                    -> LocationResolve
AtomicOperation / Unknown                 -> OpaqueInstruction
其他 kind                                 -> 保持原 kind
```

### 6.5 Terminator

Terminator 支持：

```text
Branch
Goto
Return
Revert
Stop
Fallthrough
```

字段包括 `condition`、`targets`、`values`、`origin_facts`、`stmt_refs` 和 `attrs`。
控制流终止行为与普通 instruction 分离，方便后续 CFG 重写。

### 6.6 ExpressionNode

表达式节点保存：

```text
expr_id
kind
operands
value / name
operator / callee
type_hint
raw
origin_facts
rewrite_history
```

常见 `kind`：

```text
Constant
Variable
Add / Sub / Mul / Div / Mod
Eq / Ne / Lt / Gt / Le / Ge
And / Or / BitAnd / BitOr / Xor
Not / BitNot / IsZero
Shl / Shr / Sar
Keccak
Index
Call
Tuple
OpaqueExpression
```

### 6.7 LocationNode

状态位置是一等 IR 对象，而不是只保留字符串。当前归一化类型包括：

```text
StateVariableLocation
MappingLocation
IndexedLocation
StructFieldLocation
RawStorageLocation
```

例如：

```text
balances[msg.sender]
```

记录为：

```json
{
  "kind": "MappingLocation",
  "base": "balances",
  "keys": ["expr_msg_sender"],
  "access": "balances[msg.sender]"
}
```

多维 mapping 的 `keys` 按访问维度依次保存。手工 slot 无法恢复为状态变量时，使用
`RawStorageLocation`，保留 `slot` 与原始 `access`，不进行猜测。

### 6.8 ValueRecord 与 Def-Use

```text
value_id
name
definition
uses
type_hint
```

例如：

```text
value:total_1#1: def=ir_1, uses=[ir_2]
input:amount_1:   def=input, uses=[ir_2]
```

它表示 `ir_1` 定义了 `total_1`，`ir_2` 使用该版本；`amount_1` 在函数内没有定义，
因此作为输入值记录。

## 7. Control 层物化方法

### 7.1 BasicBlock 创建

`_materialize_blocks()` 逐个复制 FunctionSSEIR control block，并保留：

```text
src
text
node_id
node_kind
slither_node_type
function_loop_context
source_terminator
```

这些字段仅作为源码、Slither/Yul CFG 对照信息，不参与新的语义猜测。

### 7.2 CFG edge 创建

构建边时会忽略：

```text
kind 以 terminate: 开头的边
源 block 已经是 Return / Revert / Stop 的边
源或目标不在当前函数 blocks 中的边
```

随后根据边回填每个 block 的 `predecessors` 和 `successors`。

### 7.3 Terminator 创建

规则如下：

1. 源 terminator 是 `Branch`，或存在至少两条 true/false 边：创建 `Branch`；
2. 源 terminator 是 `Return/Revert/Stop`：直接创建同类终结指令；
3. 只有一条后继边：创建 `Goto`；
4. 多条后继但没有可解析分支条件：创建带 `unresolved_condition` 的 `Branch`；
5. 没有后继：创建 `Fallthrough`。

Semantic IR 不尝试把 CFG 猜测性地重构为 Solidity `if/while`，而是保留显式
`Branch/Goto`，为后续结构化 pass 留出空间。

## 8. Fact Grouping 与 Fact Fusion

### 8.1 为什么需要融合

同一状态读取可能在 Semantic Fact 中表现为：

```text
ValueCompute: slot = keccak256(...)
StorageLocationResolve: slot -> balances[user]
ValueCompute: balance = sload(slot)
StateRead: balance = balances[user]
```

如果逐条生成 IR，会重复表示同一操作。Semantic IR 选择 `StateRead` 作为主 fact，
将另外几项作为 support facts 融合进：

```text
balance = StateRead(MappingLocation(balances, [user]))
origin_facts = [StateRead fact, sload fact, slot resolve fact, keccak fact]
```

### 8.2 主 fact

当前 effect/control 主 fact 集合为：

```text
StateRead / StateWrite / Delete
Require / Revert / Return
EventEmit
ExternalCall / LowLevelCall / StaticCall / DelegateCall
PrecompileCall
InternalCall / InternalDynamicCall / LibraryCall / BuiltinCall
ValueTransferCall
```

### 8.3 support fact

当前辅助 fact 包括：

```text
StorageLocationResolve
IndexAccess
MemberAccess
BranchCondition
Yul 来源的 ValueCompute
```

### 8.4 支持关系判断

`_supports()` 综合使用：

1. function id 必须相同；
2. condition 必须兼容；
3. location 结构相同；
4. support lvalue 定义了主 fact 使用的 slot 临时量；
5. 两者共享原始 statement；
6. Yul `sload(...)` 结果与 `StateRead` 结果相同；
7. 主 fact 的表达式实际引用了 support 临时量。

`_support_closure()` 会继续沿 support fact 的 lvalue/read 关系向前收集依赖，直到不再
发现新的定义。

不同 condition 下的候选不会直接融合。当前 condition 兼容判断接受相同条件、空条件，
以及一个条件文本包含另一个条件文本的父子关系。

### 8.5 Branch condition 融合

位于 Branch block 的 `BranchCondition` 和相关 `ValueCompute` 不单独生成普通
instruction，而是作为 Branch terminator 的 `origin_facts`。如果 control 中还没有条件，
则使用最后一个相关 fact 的 `rvalue` 构建条件表达式。

## 9. Expression DAG

### 9.1 解析方法

`ExpressionArena` 使用词法器与 Pratt 风格优先级解析器处理表达式，不依靠简单字符串
切分。它支持：

```text
常量、变量、括号
前缀 ! ~ - +
二元运算 || && | ^ & == != < > <= >= << >> + - * / %
函数调用
数组/mapping 索引
Yul add/sub/mul/iszero/keccak256 等调用到统一 kind 的映射
```

无法解析的表达式不会被丢弃，而是生成：

```text
OpaqueExpression(raw=<原表达式>)
```

### 9.2 DAG 去重

每个函数拥有一个 `ExpressionArena`。节点的结构键由以下内容组成：

```text
kind
operands
value/name/operator/callee
OpaqueExpression.raw
type_hint
```

结构完全相同的子表达式复用同一 `expr_id`，并合并 `origin_facts`，因此表达式表是
函数内 DAG，而不是每条 instruction 各自复制一棵树。

### 9.3 support binding 替换

Fact Fusion 后，support fact 中的：

```text
lvalue -> rvalue
```

会形成 binding。构建主表达式时，`build_with_bindings()` 递归替换临时量，并使用
`active` 集合防止自引用产生无限递归。

## 10. Location 构建

`_location()` 从 fact 的 `semantic.location`、`semantic.state_variable`、`keys`、
`slot` 和 `access` 构建 Location。

归一化规则：

| 输入 location kind | Semantic IR kind |
| --- | --- |
| `state_variable` | `StateVariableLocation` |
| `mapping` | `MappingLocation` |
| `indexed_storage` | `IndexedLocation` |
| `storage_member` | `StructFieldLocation` |
| `manual_slot` / `raw_storage` | `RawStorageLocation` |

key 和 slot 会先进入 ExpressionArena。Location 也按结构 identity 去重；相同位置复用
同一个 `location_id`，并合并 `origin_facts`。

## 11. Instruction 与终结指令生成

### 11.1 instruction 放置

放置优先级为：

```text
anchor_cfg_node
-> cfg_nodes 的最后一个有效节点
-> bb_semantic_unplaced
```

无法放置的 fact 不会被删除，而是进入合成 block `bb_semantic_unplaced`，同时记录到
`unplaced_facts`。

### 11.2 Return / Revert

`Return` 和 `Revert` 优先附加到对应 block 的 terminator：

- 原 terminator 类型相同，或原来是 `Fallthrough/Goto`：升级为真正终结指令；
- 原 block 已有其他冲突终结语义：保留为显式 instruction，不擅自改变 CFG。

升级为终结指令后，会删除该 block 向后的普通 CFG 边，保证 `Return/Revert/Stop` 不再
错误 fallthrough。

### 11.3 instruction 排序

同一 block 内按 fact 中的：

```text
order.operation_order
order.atomic_sequence
```

排序。一个 instruction 融合多个 fact 时，使用其 `origin_facts` 中最早的顺序。

## 12. Def-Use 构建

当前 `_rebuild_def_use()` 的实现步骤为：

1. 清空函数原有 `values`；
2. 按 `function.blocks` 的保存顺序遍历 block；
3. 按 block 内 instruction 顺序遍历；
4. 每遇到一个 `instruction.result`，为同名变量创建新版本：
   `value:<name>#<version>`；
5. 递归遍历 instruction 的 expression、condition、arguments 和 Location key/slot；
6. 变量存在已有定义时，连接到最近创建的同名版本；
7. 函数内没有定义时，记录为 `input:<name>`；
8. 对 terminator 的 condition/value 使用相同方法记录 use。

构建完成后，`result_value` 指向该 instruction 定义的 ValueRecord。

需要明确：当前算法是函数内的顺序式 Def-Use 重建，还不是完整的 CFG reaching-definition
数据流算法。它尚未按照每个 basic block 的 IN/OUT 集合计算路径敏感定义，也不会在
Semantic IR 层自动插入新的 CFG Phi。上游已有的 `ValuePhi` 可以物化为 `Phi`
instruction，但 Semantic IR 自身的 CFG-aware Phi 构造仍属于后续完善内容。

## 13. 一致性检查

`_validate()` 检查：

```text
重复 instruction_id
instruction/terminator 引用了不存在的 expression
expression 引用了不存在的 operand
instruction 引用了不存在的 location
Location key/slot 引用了不存在的 expression
terminator target 不存在
CFG dangling edge
输入 fact 未被 instruction、terminator、fact group 或 unplaced_facts 表示
```

问题不会直接中断输出，而是写入函数与程序的 `diagnostics`。

## 14. 可变 IR 与重写接口

`SemanticFunction` 已提供后续解混淆 pass 所需的基础接口：

```python
replace_expression(...)
replace_instruction(...)
remove_instruction(...)
redirect_edge(...)
remove_block(...)
rebuild_cfg_links()
```

每次改写都会生成 `RewriteRecord`：

```text
pass_name
action
target_id
reason
before
after
```

例如常量折叠 pass：

```python
function.replace_expression(
    expr_id,
    ExpressionNode(expr_id, "Constant", value=7),
    pass_name="constant_fold",
    reason="both operands are constants",
)
builder.rebuild_def_use(function)
```

表达式或指令被替换后，旧节点自身的 rewrite history 会继承到新节点，并追加本次记录。
修改 instruction 的操作数后，需要显式调用 `rebuild_def_use()`。

## 15. 输出

### 15.1 JSON

JSON 完整保留运行时 Semantic IR：

```text
semantic_ir.json
```

写出方法：

```python
write_semantic_ir_json(path, program)
```

### 15.2 人类可阅读文本

文本输出：

```text
semantic_ir.txt
```

它展示：

```text
Function
BasicBlock 与 predecessors
SemanticInstruction
递归表达式与 Location
Terminator
CFGEdges
DefUse
UnplacedFacts
Diagnostics
origin_facts
```

文本 renderer 只读取内存对象，不重新进行 Fact Fusion、表达式恢复或控制流分析。

## 16. 完整示例

### 16.1 源码

```solidity
contract SimpleSemanticIR {
    uint256 total;
    mapping(address => uint256) balances;

    function deposit(uint256 amount) external {
        total = total + amount;
        assembly {
            mstore(0x00, caller())
            mstore(0x20, balances.slot)
            let balanceSlot := keccak256(0x00, 0x40)
            sstore(balanceSlot, add(sload(balanceSlot), amount))
        }
    }
}
```

### 16.2 Solidity 原子操作与 fact

Solidity 原子操作提取器把：

```solidity
total = total + amount;
```

整理为：

```text
total_1 = StateRead(total)
TMP_0 = Add(total_1, amount_1)
StateWrite(total, TMP_0)
```

对应 fact 的核心含义为：

```text
fact_1 StateRead:   total_1 = total
fact_2 ValueCompute TMP_0 = total_1 + amount_1
fact_3 StateWrite:  total = total + amount
```

### 16.3 Yul 原子化与语义恢复

Yul 原子化把最后一条嵌套操作拆为：

```text
yul_tmp_1 = sload(balanceSlot)
yul_tmp_2 = add(yul_tmp_1, amount)
sstore(balanceSlot, yul_tmp_2)
```

MemorySSA 与 slot 恢复同时识别：

```text
memory[0x00:0x20] <- msg.sender
memory[0x20:0x40] <- balances.slot
keccak256(0x00, 0x40) -> balances[msg.sender]
```

Semantic Fact 因此包含：

```text
StorageLocationResolve: balanceSlot -> balances[msg.sender]
StateRead:  yul_tmp_1 = balances[msg.sender]
StateWrite: balances[msg.sender] = balances[msg.sender] + amount
```

### 16.4 Fact Fusion

Semantic IR 以 `StateRead/StateWrite` 为主 fact，把 `keccak256`、`sload` 与
`StorageLocationResolve` 作为 support facts，生成共享的：

```text
MappingLocation(base = balances, keys = [msg.sender])
```

状态读取的 `origin_facts` 同时保留主 fact、slot fact 和底层读取 fact。

### 16.5 文本 IR

当前实际输出的核心部分为：

```text
bb_sol_slither_n1 [solidity]:
    total_1 = StateRead(StateVariableLocation(total))
    TMP_0 = Add(total_1, amount_1)
    StateWrite(StateVariableLocation(total), Add(total, amount))
    Goto(bb_asm1_n0)

bb_asm1_n5 [yul]:
    __sseir_eval_asm_s_4_1 = StateRead(
        MappingLocation(base = balances, keys = [msg.sender])
    )
    StateWrite(
        MappingLocation(base = balances, keys = [msg.sender]),
        Add(Index(balances, msg.sender), amount)
    )
    Goto(bb_asm1_n1)
```

这说明当前模型已经完成：

```text
Solidity/Yul 同一 function-level CFG
统一 StateRead/StateWrite instruction
结构化 MappingLocation
表达式 DAG
源码 fact 归档
```

### 16.6 JSON 片段

```json
{
  "instruction_id": "ir_4",
  "op": "StateRead",
  "result": "__sseir_eval_asm_s_4_1",
  "result_value": "value:__sseir_eval_asm_s_4_1#1",
  "location": "loc_2",
  "source_languages": ["yul"],
  "origin_facts": ["fact_6", "fact_5", "fact_4"]
}
```

对应 Location：

```json
{
  "location_id": "loc_2",
  "kind": "MappingLocation",
  "base": "balances",
  "keys": ["expr_8"],
  "access": "balances[msg.sender]"
}
```

## 17. Yul 原子化的当前接入状态

当前 pipeline 已在 S-SEIR 分析前执行 `YulAtomicOperationExtractor`：

```text
Yul AST
  -> Yul atomic operation table
  -> attach to FunctionUnit
  -> attach to Yul CFG nodes
  -> EffectLifter 用于 condition 与嵌套 sink 求值
  -> S-SEIR overlay
  -> Yul SemanticFact
  -> Semantic IR
```

已解决的内容：

1. Yul 嵌套 `sload` 获得显式临时结果；
2. 复杂 condition 按 Yul/EVM 从右到左参数求值顺序拆分；
3. 原子操作保留 CFG node、source span、stmt refs 和依赖；
4. `StateRead` 可以在 Semantic IR 中具有 `result/result_value`。

尚未完全打通的内容：

1. 不是所有 Yul `ValueCompute` 原子节点都会独立成为 Semantic IR instruction；
2. 嵌套 `sload -> add -> sstore` 中间的 `add` 可能仍只存在于表达式 DAG，而不是独立
   Assign instruction；
3. 当前 StateWrite 可能优先采用归一化高级表达式，而不是实际 SSA 临时量；
4. 因此部分原子临时值在 Def-Use 中仍可能显示为无 use。

## 18. 当前限制与后续优化方向

### 18.1 执行表达式与归一化表达式尚未完全分离

当前 `_fact_expression()` 对 `StateWrite` 优先选择：

```python
semantic.value or fact.rvalue
```

这有利于审计展示，但可能把：

```text
StateWrite(total, TMP_0)
```

重新展开为：

```text
StateWrite(total, total + amount)
```

从而使 `TMP_0` 在 Def-Use 中失去 use。后续建议为 instruction 同时保存：

```text
execution_expr
normalized_expr
```

Def-Use 与执行顺序只使用 `execution_expr`，高级展示与模式匹配使用
`normalized_expr`。

### 18.2 Def-Use 尚未 CFG-aware

当前 Def-Use 是 block 保存顺序上的最近定义匹配。后续需要实现：

```text
每个 block 的 reaching-definition IN/OUT
按 CFG edge 传播版本
多来源时构建 Phi
Phi input 记录来源 block 与 condition
循环使用固定点迭代
无法唯一解析时保留 unresolved
```

### 18.3 内部证据链需继续贯通

MemorySSA、SinkResolver 和 byte-axis 查询状态允许保留在运行时分析对象中，但公开
Semantic IR 不应输出庞大的查询过程。需要继续确保其最终推导出的：

```text
support_stmt_refs
source CFG nodes
resolved location/value
unknown reason
```

能够传到 fact 与 IR 的紧凑字段中。

### 18.4 OpaqueExpression 是保守边界

当前表达式语法不覆盖完整 Solidity/Yul 类型语法、named arguments、复杂 tuple 等形式。
无法解析时保留 `OpaqueExpression(raw=...)`，不会用字符串猜测含义。后续可以扩展 parser，
但不应以错误结构替换原表达式。

## 19. 测试与验收

当前 `scripts/semantic_ir/tests.py` 覆盖：

1. mapping slot/support fact 融合为一个 StateRead；
2. 不同 path condition 下的候选保持独立；
3. Expression DAG、Def-Use 与 rewrite history；
4. Revert 终结 block 删除 fallthrough edge；
5. 文本输出包含 CFG、表达式、Location、Def-Use 与 origin facts。

运行方式：

```bash
PYTHONPATH=scripts uv run python -m semantic_ir.tests
```

独立构建命令：

```bash
uv run python scripts/semantic_ir/cli.py Token.sol \
  --output outputs/semantic_ir.json \
  --text-output outputs/semantic_ir.txt
```

主 pipeline：

```bash
uv run python scripts/s_seir/s_seir_pipeline.py Token.sol \
  --semantic-ir-output outputs/semantic_ir.json \
  --semantic-ir-text-output outputs/semantic_ir.txt
```

批处理时，每个合约结果目录会同时包含：

```text
semantic_ir.json
semantic_ir.txt
semantic_facts.json
solidity_atomic_operations.json
yul_atomic_operations.json
```

## 20. 阶段性结论

当前 Semantic IR 已经是一个真实的、可修改的函数级运行时对象，而不是只存在于计划中的
JSON 格式。它已完成：

```text
FunctionSSEIR + SemanticFact 输入
Solidity/Yul function-level CFG 物化
Fact Fusion
Semantic instruction 与 terminator
Expression DAG
Location IR
基础 Def-Use
源码与 fact 归档
一致性检查
改写 API 与 rewrite history
JSON / 文本输出
```

它已经可以作为后续 Semantic IR 解混淆 pass 的存储载体。当前最重要的后续工作不是重做
Semantic IR，而是在现有模型上继续打通“原子执行表达式 -> CFG-aware Def-Use/Phi ->
归一化高级表达式”的双轨表示，并保持内部 MemorySSA 证据直到最终导出阶段。
