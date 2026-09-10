# SFIR 技术文档

## 1. 目标

SFIR 是 `Semantic Fact IR` 的简称，schema 为：

```Plain
s-seir-semantic-fact-ir/v1
```

它是后续解混淆的函数级输入语言。它记录的问题是：

```Plain
在什么条件下，按怎样的控制与数据顺序，对什么语义对象做了什么操作？
```

SFIR 不等于 S-SEIR 的标准 JSON，也不等于旧的 `semantic_facts` 兼容产物。它重新以
函数为单位组合两条已经完成的高级语义来源：

```Plain
Solidity 源码
  -> Slither / SlithIR-SSA
  -> SolidityAtomicOperationExtractor
  -> SoliditySemanticLifter
  -> Solidity semantic nodes

Yul inline assembly
  -> S-SEIR recovery CFG / MemorySSA / SinkResolver / effects
  -> completed semantic overlays
  -> YulSemanticLifter
  -> Yul semantic nodes

Solidity semantic nodes + Yul semantic nodes
  -> SemanticFactIRBridge
  -> SFIR: FactCFG + FactSSA + semantic nodes + semantic edges
```

SFIR 的目标是供解混淆和源码重建消费的规范语义表示；它不重跑 Yul 恢复算法，不把
MemorySSA 查询过程改写为下游事实，也不尝试直接生成最终 Solidity 源码。

## 2. 模型边界

### 2.1 上游恢复与 SFIR 的分工

S-SEIR 上游继续拥有以下能力：

```Plain
Yul recovery CFG
MemorySSA / loop-aware lazy resolve
SinkResolver
EffectNode、path_states、slot recovery
低层 memory / storage / call 证据
```

它们用于证明 Yul 高级语义，但不是 SFIR 的输入。SFIR 的 Yul 侧只接受已完成的
`semantic_overlays` 和语义级 CFG provenance：

```Plain
semantic_anchor_cfg_node
semantic_evidence_cfg_nodes
```

以下低层 transport 字段不能出现在 SFIR 的 Yul 节点中：

```Plain
effects / effect_id / effect_kind / path_states / slot_effect
以及由 effect path-state 复制出的 path instance
```

构建器会递归剥离这些字段；若仍检测到泄漏，拒绝该节点并记录
`invalid_yul_effect_transport` 或 `yul_effect_transport_leak` diagnostic。

因此，SFIR 中没有“为了恢复 mapping 而执行的 `mstore/keccak256/sload` effect 链”。若该链
已被证明为 `balances[owner]` 的读取，下游只消费对应的
`StorageLocationResolve/StateRead` 等高级操作；无法证明时则保留明确的 unresolved
高级结果，而不伪造恢复。

### 2.2 不按 path 克隆节点

SFIR 的规范节点按函数唯一记录，不为每条 Solidity–Yul–Solidity 路径复制一份节点。控制
图保存真实的分支和合流；需要条件见证时再生成 `path_witnesses`。这避免路径数随嵌套
分支发生笛卡尔积增长，也确保同一个状态写不会变成多个重复事实。

## 3. Pipeline 与实现位置

主入口：

```Plain
scripts/s_seir/s_seir_pipeline.py
```

SFIR 相关模块：

```Plain
scripts/s_seir/s_seir_solidity_atomic_ops.py
scripts/s_seir/s_seir_solidity_semantic_lifter.py
scripts/s_seir/s_seir_yul_semantic_lifter.py
scripts/s_seir/s_seir_semantic_fact_adapter.py
scripts/s_seir/s_seir_semantic_fact_ir.py
scripts/s_seir/s_seir_semantic_fact_render.py
scripts/s_seir/s_seir_semantic_overlay_provenance.py
```

在每个 `FunctionUnit` 上，pipeline 的相关顺序为：

```Python
control = ControlBuilder(...).build(unit)
solidity_atomic_operations = SolidityAtomicOperationExtractor().extract(unit, control)

# S-SEIR 在此完成 Yul 的 MemorySSA、Effect 和 overlay 恢复。
...
overlays = PredicateLifter().lift(...)
overlays = CalldataArrayLifter().lift(...)
overlays = CalldataSelectorLifter().lift(...)
overlays = MemoryArrayPatternLifter().lift(...)
overlays = YulLocalFunctionLifter().lift(...)
attach_semantic_cfg_provenance(overlays, effects, control)

fn = FunctionSSEIR(...)
fn._sseir_solidity_atomic_operations = solidity_atomic_operations

sfir = build_function_level_semantic_fact_ir_payload(functions, ...)
```

`SolidityAtomicOperationExtractor` 在 S-SEIR 恢复之前完成原子表，但该表只作为附着在
`FunctionSSEIR` 上的 transient 输入。最终桥接阶段才由 `SoliditySemanticLifter` 将其
投影为 SFIR 节点。Yul 一侧的 `YulSemanticLifter` 只读取 completed overlay，绝不重新
调用 MemorySSA、SinkResolver 或 overlay 恢复器。

## 4. 最外层结构

输出的最外层结构如下：

```JSON
{
  "schema": "s-seir-semantic-fact-ir/v1",
  "source": "samples/Example.sol",
  "result_dir": "outputs/example",
  "model_boundary": {
    "processing_unit": "function",
    "name": "Semantic Fact IR",
    "ordering": "Fact CFG partial order plus block-local semantic order",
    "path_policy": "canonical semantic nodes are not path-cloned; path witnesses are derived on demand"
  },
  "function_count": 1,
  "semantic_node_count": 8,
  "functions": []
}
```

| 字段 | 含义 |
| --- | --- |
| `source` | pipeline 接收的入口源码路径 |
| `result_dir` | 该次输出所在目录 |
| `model_boundary` | 固定的输入、排序和路径策略声明 |
| `function_count` | 函数级 SFIR 数量 |
| `semantic_node_count` | 全部函数的规范语义节点数量 |
| `functions` | 每个函数独立的 SFIR |

单个函数包含：

```JSON
{
  "function_id": "Token.transfer(address,uint256)",
  "contract": "Token",
  "function": "transfer",
  "signature": "transfer(address,uint256)",
  "fact_cfg": {},
  "fact_ssa": {},
  "semantic_nodes": [],
  "semantic_edges": [],
  "boundary_links": [],
  "path_witnesses": [],
  "diagnostics": []
}
```

所有关系都限定在所属函数内；不同函数间不共享 FactCFG block、FactSSA version 或
semantic edge。

## 5. 语义节点

### 5.1 公共字段

`semantic_nodes` 是最终程序语义。每个节点由上游事实投影后获得全函数唯一的
`semantic_id`：

```JSON
{
  "semantic_id": "sfir:Token.transfer(address,uint256):yul_overlay:ov_12",
  "origin_id": "fact_12",
  "operation_id": "yul_overlay:ov_12",
  "kind": "StateWrite",
  "source_lang": "yul",
  "origin": "yul_sseir_semantic_overlay",
  "fact_role": "effect",
  "stmt_refs": ["asm_s_8"],
  "cfg_nodes": ["bb_asm1_n12"],
  "condition": "amount <= balances[from]",
  "lvalue": "balances[to]",
  "rvalue": "balances[to] + amount",
  "reads": ["balances[to]", "amount"],
  "writes": ["balances[to]"],
  "order": {"kind": "cfg_partial_order", "operation_order": 3},
  "semantic": {},
  "evidence": {},
  "semantic_provenance": {},
  "placement": {},
  "fact_ssa": {"reads": [], "writes": []}
}
```

| 字段 | 含义 |
| --- | --- |
| `kind` | 操作的统一语义类别 |
| `source_lang` | `solidity` 或 `yul` |
| `origin` | `solidity_atomic_operation` 或 `yul_sseir_semantic_overlay` |
| `fact_role` | `effect`、`control`、`support` 或 `analysis_support` |
| `stmt_refs` | SourceStatement 引用，供回到源码审计 |
| `cfg_nodes` | 语义级 CFG 证据节点，不是执行顺序列表 |
| `condition` | 已完成的节点显式条件；没有时缺省 |
| `lvalue/rvalue/reads/writes` | 可用于重建与 def-use 的语义值接口 |
| `order` | block 内顺序；跨 block 顺序必须由 FactCFG 判断 |
| `semantic` | 按 kind 扩展的结构化含义 |
| `evidence` | 原子操作或 overlay 的可审计来源，不含 Yul effect transport |
| `placement` | 节点在 FactCFG 中的最终位置 |
| `fact_ssa` | 节点对最终 FactSSA version 的读取和写入引用 |

节点可缺少不适用字段。例如 `Return` 通常没有 `lvalue`，`StorageLocationResolve` 是支持
节点而不是一次赋值，`BranchCondition` 在图的 terminator 处使用而不必在 C-like 块体重复
显示。

### 5.2 Solidity 来源

Solidity 端以每个 `sol_atom` 对应一个语义节点。提取器保留稳定 atom id、Slither CFG
位置、源码范围、block 内顺序、SSA 读取/结果、typed reference chain 和路径条件；lifter
只做共同 schema 投影，不对 Yul 语义进行推测。

常见 kind 包括：

```Plain
StateRead / StateWrite / StorageLocationResolve
ValueAssign / ValueCompute / ValuePhi
IndexAccess / MemberAccess / LengthRead / TupleUnpack
Require / Revert / EventEmit / Return
InternalCall / ExternalCall / LibraryCall / LowLevelCall / BuiltinCall
ValueTransferCall / Delete
```

对状态 reference，`Index/Member` 只先建立 location；当 SlithIR 后续消费该 location 时，
extractor 在消费者之前物化 `StorageRead`，从而分开记录“定位”“读取”“计算/写入”。
`Delete` 以被清空的叶子 storage location 为 StateWrite，值为 `0`，不凭空增加旧值读取。

### 5.3 Yul 来源与规范选择

Yul 节点只来自完成的 overlay，例如：

```Plain
StateVariableRead / MappingRead                 -> StateRead
StateVariableWrite / MappingWrite               -> StateWrite
MappingSlot / DynamicArraySlot                  -> StorageLocationResolve
RequireOverlay                                  -> Require
EventEmit                                        -> EventEmit
ReturnValue                                      -> Return
ExternalCall / LowLevelCall / PrecompileCall    -> Call 类节点
Predicate                                        -> BranchCondition 或 ValueCompute
MemoryArrayLengthRead / MemoryArrayElementRead  -> 高级数组读取
CalldataSelectorRead / CalldataArrayElementRead -> msg.sig / calldata 数组访问
YulLocalFunctionDefinition / Call               -> 局部函数定义 / 调用
```

`YulSemanticLifter` 在存在结构化覆盖证据时只保留一个 canonical 高级节点。例如已经恢复的
mapping slot、状态读取、数组元素读取、selector 读取或局部函数调用，会精确替代同一
statement/anchor/effect 关系下的通用 `ExpressionNormalization` 和 evaluator step。若不能
证明这种覆盖关系，原有 `ValueCompute` 保留；不会按变量名或表达式文本进行宽松去重。

局部 Yul 函数定义被适配为 `LocalFunctionDefinition`，具有自己的嵌套 `semantic_cfg`。
它的 `placement.status=nested_definition`，不会作为调用者 FactCFG 中的可执行节点；调用点
则以 `YulLocalFunctionCall` 明确记录调用边界。

### 5.4 放置规则

桥接器只在下列情况放置节点：

1. `semantic_provenance.anchor_cfg_node` 唯一且可映射到 FactCFG；或
2. `semantic_provenance.evidence_cfg_nodes` 映射后恰有一个 FactCFG block。

成功时：

```JSON
{
  "placement": {
    "status": "anchored",
    "anchor_block": "fcfg:Token.transfer(address,uint256):bb_asm1_n12",
    "operation_order": 3,
    "evidence_blocks": ["fcfg:Token.transfer(address,uint256):bb_asm1_n12"]
  }
}
```

有线性块融合或 transport 收缩时，`placement` 会保留 `fused_from_source_blocks` 或
`collapsed_transport_evidence`。无唯一放置点时不使用源码序号猜测，而在函数
`diagnostics` 记录 `unanchored_semantic_node`。

## 6. FactCFG

### 6.1 基本结构

每个函数独立构建 FactCFG。初始 block id 格式为：

```Plain
fcfg:<function-id>:<upstream-block-id>
```

它与 S-SEIR recovery CFG 使用不同 id 命名空间。典型 block 为：

```JSON
{
  "block_id": "fcfg:Token.transfer(address,uint256):bb_asm1_n12",
  "kind": "yul",
  "origin": {
    "engine": "sseir_semantic_projection",
    "source_block_id": "bb_asm1_n12",
    "semantic_source_node_id": 12
  },
  "stmt_refs": ["asm_s_8"],
  "terminator": {"kind": "YulNode", "node_kind": "statement"},
  "semantic_ids": ["sfir:...:ov_12"],
  "predecessors": [],
  "successors": []
}
```

Solidity block 的 `origin.engine` 为 `slither`；Yul block 为
`sseir_semantic_projection`。FactCFG 还保存：

```Plain
blocks / edges / entry_blocks / reverse_postorder
dominance / control_dependencies
```

`semantic_ids` 是 block 内的规范执行顺序。不得按 source offset、节点 id 或 kind 对它重新
排序；融合后的 block 已由唯一 CFG 边证明其前后顺序。

### 6.2 terminator 与 guard

FactCFG 从函数级 ControlBuilder 图建立 block/edge。上游 recovery CFG 中为 assembly
边界分析而保留的 `return/revert/stop` 后结构边不进入 FactCFG：终止操作没有可执行后继。

边的 `guard` 来自完成后的 terminator：

```Plain
true / body / true:<expr>          -> <expr>
false / exit / false:<expr>        -> !(<expr>)
case:<value>                       -> (<discriminant>) == <value>
```

Yul loop condition 从 `true:<condition>` CFG edge 读取真实条件，不把展示用的
`for condition` 标签误作表达式。

### 6.3 Require、Branch 与 Switch 归一

构图后有两项控制语义归一：

1. 对已恢复 `Require`，仅当 CFG 形状确为 `branch -> empty revert / continuation` 时，
   将 guard block terminator 改为 `Require(condition)`；continuation edge 为 `true`，
   revert edge 为 `false`。其他形状保持原图。
2. 对已完成 Predicate，使用其高级 condition 替换 raw Yul branch；switch 依据
   `switch_edges` 同步生成 case/default guard。

每次改写、收缩或融合后都会重新计算 RPO、支配关系和控制依赖。因此
`path_witnesses` 从最终条件而不是旧 Yul 调用拼写派生。

### 6.4 保守收缩与融合

SFIR 用于解混淆，因而不会把无语义的 Yul 准备语句保留成大量只含 `goto` 的块；但收缩
必须由 CFG 证明。

**第一步：trivial Yul relay 收缩。** 只删除同时满足下列条件的 Yul block：

```Plain
无 semantic_ids；恰有一个前驱和一个后继；
入边和出边均为无 guard 的 next/fallthrough/join；
不是 entry/exit/merge/condition/switch/loop/terminal；
不是任何节点的 anchor 或 evidence block。
```

**第二步：线性语义块融合。** 仅在 `P -> S` 为唯一、无 guard 边，`P` 只有该后继、`S`
只有该前驱、两块同语言、都含语义节点且都不是控制/终止边界时，将 `S.semantic_ids` 追加到
`P.semantic_ids`。这是 CFG 证明的执行顺序拼接，不是按源码顺序合并。

**第三步：零语义 transport 收缩。** 最终再删除不带 semantic node 且没有控制 action 的
`YulNode/Fallthrough` transport block。重定向 edge 会保留：

```Plain
contracted_blocks / contracted_edge_ids
collapsed_transport
boundary_transitions
```

如果被删除块承载跨 Solidity/Yul 边界或节点证据，则这些内容迁移到目标块的
`collapsed_entry_transport/entry_boundary_transitions` 或节点 placement provenance。

不会收缩 branch、switch、loop back、merge、entry/exit、return/revert/stop，也不会穿越带
guard 的边或多前驱/多后继控制边界。因此收缩改变的是表示粒度，不删除源码的控制语义。

## 7. FactSSA 与 semantic edges

### 7.1 binding 身份

FactSSA 是最终语义节点的 SSA，不复用 Slither 或 Yul 恢复时的 SSA version。binding 优先
使用 Solidity AST declaration id：

```Plain
decl:<declaration-id>:value
```

Yul 可见的 `.slot/.offset/.address/.selector/.length` 作为同一声明的 facet，例如：

```Plain
decl:28:value
decl:28:storage_slot
decl:28:storage_offset
```

没有 declaration id 时才使用函数作用域内 `name:<name>:value`，并以
`identity_status` 标记该降级身份。

### 7.2 reaching-definition 固定点

`fact_ssa` 包含：

```Plain
bindings
definitions
phis
reaching_definitions_in
reaching_definitions_out
```

参数、命名返回值、状态变量及其 facet 在 entry block 产生 definition；每个语义节点的
`writes` 产生：

```Plain
fssa:<binding-id>:vN
```

构建器按 FactCFG RPO 对 reaching definitions 迭代至固定点。一个合流 block 对同一 binding
收到多个版本时，生成：

```Plain
fssa:<binding-id>:phi:<N>
```

Phi 版本继续流向后继，而非把所有旧输入直接传播到后续块。节点的
`fact_ssa.reads/writes` 只引用最终版本；无法找到定义的读取记为
`unresolved_fact_ssa_read`，不绑定到同名变量。

### 7.3 数据边与跨语言边

`semantic_edges` 描述节点间的值流：

| `kind` | 含义 |
| --- | --- |
| `data` | 普通同语言或非特殊语义定义到读取 |
| `data_phi` | Phi 的每个 incoming definition 到读取 |
| `bridge_input` | Solidity 原子定义被 Yul 高级节点读取 |
| `bridge_output` | Yul 高级节点定义被后续 Solidity 节点读取 |

`bridge_output` 同时会写入 `boundary_links`，以便单独审计 Yul 到 Solidity 的函数边界输出。
它的源必须是实际 Yul 高级节点的 FactSSA definition；不能因 assembly boundary 的
`external_writes` 再额外虚构同一命名返回值定义。

## 8. 路径见证与诊断

`path_witnesses` 按已放置的语义节点生成：

```JSON
{
  "witness_id": "pw:sfir:...",
  "semantic_id": "sfir:...",
  "anchor_block": "fcfg:...",
  "guards": ["flag", "amount <= balances[from]"],
  "control_edge_ids": ["e3"],
  "status": "symbolic"
}
```

它是 symbolic guard 摘要，不是一次具体执行轨迹，也不生成新的语义节点。

当前构建器会至少检查：

1. 每个 anchored node 恰好位于一个 FactCFG block；
2. FactSSA definition version 没有重复；
3. 每条 semantic edge 的两端都属于本函数 semantic node 集合；
4. Yul 节点没有低层 effect transport；
5. terminal FactCFG block 没有 successor。

失败信息记录在函数级 `diagnostics`，应由后续工具保留，而不是静默忽略。

## 9. 人类可读投影

实现位置：

```Plain
scripts/s_seir/s_seir_semantic_fact_render.py
```

渲染器只接受最终 SFIR payload，不读取 S-SEIR SourceStatement、Effect 或 MemorySSA 作为
fallback。这防止人类可读版本把 SFIR 已排除的低层推导过程重新带回下游。

| 输出 | 内容 |
| --- | --- |
| SFIR text | function、FactCFG block、semantic id/kind、FactPhi 摘要 |
| C-like | 基本块标签、高级语义语句、条件/`switch` 和必要的 `goto` |
| FactCFG text | block、terminator、SFIR 语句、edge guard、收缩记录 |
| FactCFG DOT | 每函数一张颜色区分 Solidity/Yul/分支/终止块的图 |

C-like 中仍出现的 `goto` 是 FactCFG 中未被证明可合并的控制边，不是从 Yul 重新泄漏的
低层跳转。常见原因是 branch、switch、loop back 或多前驱合流；已经安全收缩的 transport
会以注释和 provenance 表示。

## 10. 运行与产物

默认 SFIR 输出为：

```Plain
outputs/semantic_fact_ir.json
```

完整的人工审阅输出可通过：

```bash
python scripts/s_seir/s_seir_pipeline.py samples/example.sol \
  --semantic-fact-ir-output outputs/example.sfir.json \
  --semantic-fact-ir-text-output outputs/example.sfir.txt \
  --semantic-fact-c-output outputs/example.sfir.c \
  --semantic-fact-cfg-text-output outputs/example.factcfg.txt \
  --semantic-fact-cfg-dot-dir outputs/example.factcfg_dot \
  --solidity-atomic-output outputs/example.sol_atoms.json
```

兼容别名：

```Plain
--semantic-facts-output / --semantic-ir-output
  -> --semantic-fact-ir-output

--semantic-ir-text-output
  -> --semantic-fact-ir-text-output

--sfir-c-output
  -> --semantic-fact-c-output
```

`--solidity-atomic-output` 用于审计 transient `sol_atom` 表，不是 SFIR 本身；
`--debug-output` 输出完整 FunctionSSEIR 查询证据，同样不应作为后续解混淆输入。

## 11. 验证与维护原则

相关回归：

```Plain
scripts/s_seir/s_seir_semantic_fact_ir_tests.py
scripts/s_seir/s_seir_semantic_fact_render_tests.py
scripts/s_seir/s_seir_semantic_fact_adapter_tests.py
scripts/s_seir/s_seir_memory_array_local_function_tests.py
scripts/s_seir/s_seir_predicate_lifter_tests.py
```

修改 SFIR 相关逻辑时应同时验证：

1. 新的高阶语义确实进入最终 `semantic_nodes`；
2. 被替代的通用语义不会重复进入 SFIR；
3. CFG 放置、边 guard、FactSSA def-use 均由 CFG/SSA 证据决定，而非源码顺序；
4. 未证明的匹配保留 unresolved 或通用高级表达，不能伪装为 Solidity 等价操作；
5. effect、MemorySSA、SinkResolver 和 path-state 不穿透 SFIR 边界；
6. 控制块收缩后，边界 provenance 与 block 内操作顺序仍可审计；
7. C-like、FactCFG text 和 DOT 均只渲染最终 SFIR。
