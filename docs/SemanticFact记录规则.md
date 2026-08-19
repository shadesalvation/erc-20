# SemanticFact 记录规则

本文说明 S-SEIR 当前输出的 `semantic_facts.json` 如何组织、每个字段表示什么、字段如何生成，以及 Solidity 与 Yul 两条语义来源如何统一到同一层行为事实。

本文依据当前实际代码实现整理，主要对应：

```text
scripts/s_seir/s_seir_semantic_fact_adapter.py
scripts/s_seir/s_seir_solidity_semantic_lifter.py
scripts/s_seir/s_seir_pipeline.py
scripts/s_seir/s_seir_batch_contracts.py
scripts/s_seir/s_seir_control_builder.py
scripts/s_seir/s_seir_effect_lifter.py
scripts/s_seir/s_seir_overlay_builder.py
```

## 1. 目标

`SemanticFact` 是函数级语义模型的统一行为事实层。

当前整体处理结构是：

```text
Solidity 源码
  -> Slither / SlithIR SSA
  -> SolidityAtomicOperationExtractor
  -> SoliditySemanticLifter
  -> Solidity SemanticFact

Yul inline assembly
  -> S-SEIR effects / semantic_overlays
  -> Yul SemanticFact

Solidity SemanticFact + Yul SemanticFact
  -> function-level semantic_facts.json
```

换句话说：

```text
Solidity 部分默认输出高级 semantic fact；
Yul 部分仍在 sseir.json 中保留底层 effect / overlay，同时投影为 semantic fact。
```

`SemanticFact` 不替代 `sseir.json`。它是给后续审计、切片、LLM 源码恢复使用的统一行为事实视图。

## 2. 文件最外层组织

直接运行 `s_seir_pipeline.py` 或批处理脚本后，会生成：

```json
{
  "schema": "s-seir-function-semantic-facts/v3",
  "source": "Token.sol",
  "result_dir": "outputs/Token",
  "model_boundary": {
    "processing_unit": "function",
    "solidity": "SolidityAtomicOperationExtractor produces sol_atom records; SoliditySemanticLifter projects eligible atoms to the common schema.",
    "yul": "S-SEIR analyzes Yul; YulSemanticLifter projects completed Yul semantics to the common schema.",
    "bridge": "SemanticFactBridge restores function-level CFG order and control predecessors."
  },
  "function_count": 9,
  "solidity_fact_count": 38,
  "yul_fact_count": 47,
  "fact_count": 85,
  "solidity_facts": [],
  "yul_facts": [],
  "facts": []
}
```

| 字段 | 含义 | 生成方式 |
| --- | --- | --- |
| `schema` | 文件格式版本 | 固定为 `s-seir-function-semantic-facts/v3` |
| `source` | 分析入口源码 | pipeline / batch 传入 |
| `result_dir` | 当前结果目录 | pipeline / batch 传入 |
| `model_boundary` | 当前模型边界说明 | `build_function_level_semantic_fact_payload` 固定写入 |
| `function_count` | 函数数量 | S-SEIR 函数模型数量 |
| `solidity_fact_count` | Solidity fact 数量 | Slither / SlithIR 投影结果 |
| `yul_fact_count` | Yul fact 数量 | S-SEIR overlay 投影结果 |
| `fact_count` | 总 fact 数量 | `solidity_fact_count + yul_fact_count` |
| `solidity_facts` | Solidity 部分原子行为事实 | 来自 Solidity 原子操作提取器和 SoliditySemanticLifter |
| `yul_facts` | Yul inline assembly 行为事实 | 来自 S-SEIR overlay |
| `facts` | 合并后的完整 fact 列表 | 先 Solidity 后 Yul，最终重新编号 |

## 3. SemanticFact 单条记录

### 3.1 标准字段

当前 dataclass 定义如下：

```json
{
  "fact_id": "fact_12",
  "operation_id": "yul_overlay:ov_12",
  "kind": "StateWrite",
  "source_lang": "yul",
  "origin": "sseir_overlay",
  "function": "_assemblyMove",
  "contract": "AssemblyERC20",
  "signature": "_assemblyMove(address, address, uint256)",
  "stmt_refs": ["asm_s_10", "asm_s_12"],
  "cfg_nodes": ["bb_asm5_n12", "bb_asm5_n14"],
  "anchor_cfg_node": "bb_asm5_n14",
  "condition": "!(lt(fromBalance, value))",
  "lvalue": "balanceOf[to]",
  "rvalue": "(balanceOf[to] + value)",
  "reads": ["balanceOf[to]", "value"],
  "writes": ["balanceOf[to]"],
  "order": {
    "kind": "cfg_partial_order",
    "cfg_block_order": 8,
    "operation_order": 3
  },
  "control_predecessors": ["fact_11"],
  "semantic": {},
  "evidence": {}
}
```

| 字段 | 类型 | 含义 | 生成方式 |
| --- | --- | --- | --- |
| `fact_id` | string | 当前文件内唯一 fact 编号 | 最终合并后由 `renumber_facts` 重新编号 |
| `operation_id` | string | Lifter 输入操作的稳定标识 | Solidity `sol_atom` 或 Yul overlay/effect id |
| `kind` | string | 行为事实类型 | SlithIR operation kind 或 S-SEIR overlay kind 映射得到 |
| `source_lang` | string | 来源语言 | `solidity`、`yul` 或极少数 `mixed` |
| `origin` | string | 来源分析器 | `slither_lifted` 或 `sseir_overlay`；低级 debug 适配器可使用 `slither_ir` |
| `function` | string | 所属函数名 | FunctionSSEIR / Slither function |
| `contract` | string | 所属合约名 | FunctionSSEIR / Slither function |
| `signature` | string | 函数签名 | FunctionSSEIR / Slither function |
| `stmt_refs` | array | 对应原始 SourceStatement id | Slither CFG block 或 S-SEIR overlay/effect 回填 |
| `cfg_nodes` | array | 当前 fact 的完整 CFG 证据节点集合，可同时包含 slot/hash 来源和最终 sink | Slither block id 或 S-SEIR `stmt_refs` 反查 |
| `anchor_cfg_node` | string | 当前 fact 实际发生的 CFG 节点；用于排序、guard 传播和控制前驱 | Bridge 按 fact 类型选择对应的 endpoint effect，例如 `StateWrite -> StorageWrite` |
| `condition` | string/null | 该 fact 成立的路径条件 | CFG control dependency 或 path-conditioned candidate |
| `lvalue` | any | 被赋值、写入或构造的对象 | operation lvalue / overlay attrs |
| `rvalue` | any | 写入值、表达式或调用结果 | operation rvalue / overlay attrs |
| `reads` | array | 该 fact 读取的数据 | SlithIR read 集合或 overlay 值粗略提取 |
| `writes` | array | 该 fact 写入的数据 | SlithIR lvalue/write 或 overlay access |
| `order` | object | 当前函数 CFG 偏序及块内操作顺序 | SemanticFactBridge 根据统一 CFG 回填 |
| `control_predecessors` | array | CFG 上紧邻该 fact 的前序 fact | SemanticFactBridge 沿块内顺序和前驱块回填 |
| `semantic` | object | 结构化行为含义 | 按 `kind` 写入不同字段 |
| `evidence` | object | 证据来源 | Slither operation 或 S-SEIR overlay/effect/candidate |

### 3.2 字段清理规则

`SemanticFact.to_dict()` 会调用 `clean_dict`：

```text
1. None、空数组、空对象通常不输出；
2. 字段不适用时可以缺省；
3. `semantic` 和 `evidence` 应尽量保留非空结构；
4. 最终消费者不应假设每个 fact 都有 lvalue/rvalue/condition。
5. `depends_on` 不属于 v3 正式输出；数据流仍可在临时 `sol_atom.depends_on_atoms` 中调试，正式模型使用原子顺序、读写集合、条件和 CFG 前驱。
6. `cfg_nodes` 不表示线性执行顺序；它是证据集合。当存在 `anchor_cfg_node` 时，`order`、`cfg_predecessor_blocks` 和 `control_predecessors` 一律以该节点为准。
```

例如纯 `Return` 或 raw `Revert` 可能没有 `lvalue/rvalue`，但会保留：

```json
{
  "kind": "Revert",
  "semantic": {
    "operation": "revert"
  }
}
```

## 4. source_lang 与 origin

### 4.1 Solidity fact

Solidity fact 固定为：

```json
{
  "source_lang": "solidity",
  "origin": "slither_lifted"
}
```

生成规则：

```text
1. ControlBuilder 将 Slither CFG block 和 `slithir_ssa` 归档到函数级 control。
2. SolidityAtomicOperationExtractor 将嵌套表达式整理为按 CFG 排序的 `sol_atom`。
3. 提取器根据 Slither `Phi.nodes` 区分函数入口、写版本、跨调用、函数内分支合流和循环携带 Phi。
4. SoliditySemanticLifter 只将 `fact_eligible` 的原子操作投影到统一 schema，不调用 Yul S-SEIR 组件。
5. 函数入口 Phi、单来源写版本 Phi、PhiCallback 和跨函数 Phi只留在原子操作审计表；函数内 CFG 合流和循环携带 Phi可以记录为 `ValuePhi`。
6. StateRead、StateWrite、EventEmit、Require、Call、Return 等运行时行为按一项操作一个 fact 记录。
```

### 4.2 Yul fact

Yul fact 通常为：

```json
{
  "source_lang": "yul",
  "origin": "sseir_overlay"
}
```

生成规则：

```text
1. S-SEIR 先在 sseir.json 中构建 Yul effect 和 semantic_overlay。
2. SSeirFactAdapter 将 overlay 投影为 SemanticFact。
3. 如果 overlay 没有 stmt_refs，会从其 effects 反查 stmt_refs。
4. 如果仍无法判断 source_lang，只有函数内确实存在 Yul statement 时，才归入 yul_facts。
5. 纯 Solidity 函数中的 S-SEIR 辅助 overlay 不进入 yul_facts。
```

## 5. kind 分类

### 5.1 Solidity / SlithIR kind 映射

以下映射仍由 `SlitherFactAdapter` 支持，用于 debug、测试和少量 sink fallback。
当前最终 `solidity_facts` 默认不再逐条输出这些低级 SlithIR 操作。

| SlithIR kind | 可映射 SemanticFact kind | 含义 |
| --- | --- | --- |
| `Assignment` | `ValueAssign` | 普通赋值 |
| `Binary` | `BinaryOperation` | 二元运算 |
| `Unary` | `UnaryOperation` | 一元运算 |
| `TypeConversion` | `TypeConversion` | 类型转换 |
| `Index` | `IndexAccess` | 数组 / mapping 索引引用 |
| `Member` | `MemberAccess` | 成员访问 |
| `Length` | `LengthRead` | `.length` 读取 |
| `Delete` | `Delete` | delete 操作 |
| `InitArray` | `ArrayLiteral` | 数组初始化 |
| `NewArray` | `NewArray` | new array |
| `NewContract` | `NewContract` | new contract / create |
| `NewElementaryType` | `NewElementaryType` | new elementary type |
| `NewStructure` | `NewStructure` | struct 构造 |
| `Phi` | `Phi` | SSA phi |
| `PhiCallback` | `PhiCallback` | Slither callback phi |
| `Unpack` | `TupleUnpack` | tuple 解包 |
| `InternalCall` | `InternalCall` | 内部函数调用 |
| `InternalDynamicCall` | `InternalDynamicCall` | 动态内部调用 |
| `HighLevelCall` | `ExternalCall` | 高级外部调用 |
| `LowLevelCall` | `LowLevelCall` | `.call/.staticcall/.delegatecall` 等低级调用 |
| `LibraryCall` | `LibraryCall` | library 调用 |
| `SolidityCall` | `BuiltinCall` | Solidity 内建函数调用，如 `abi.encode` |
| `Condition` | `BranchCondition` | 分支条件 |
| `Return` | `Return` | 返回 |
| `EventCall` | `EventEmit` | emit event |
| `Send` / `Transfer` | `ValueTransferCall` | ETH send/transfer |
| `Nop` | `Nop` | 空操作 |

注意：

```text
IndexAccess / MemberAccess / LengthRead 首先表示 Solidity 表达式级访问，
不直接等价于 StateRead/StateWrite。
是否产生状态读写，应结合后续 read/write、变量类型、赋值和 Yul slot 恢复结果。
```

### 5.2 Yul / S-SEIR overlay 映射

| S-SEIR overlay kind | SemanticFact kind | 含义 |
| --- | --- | --- |
| `StateVariableRead` | `StateRead` | 状态变量读取 |
| `StateVariableWrite` | `StateWrite` | 状态变量写入 |
| `MappingRead` | `StateRead` | mapping 读取 |
| `MappingWrite` | `StateWrite` | mapping 写入 |
| `PathConditionedStorageRead` | 多条 `StateRead` | 每个 candidate 一条 |
| `PathConditionedStorageWrite` | 多条 `StateWrite` | 每个 candidate 一条 |
| `EventEmit` | `EventEmit` | 事件触发 |
| `PathConditionedEventEmit` | 多条 `EventEmit` | 每个 candidate 一条 |
| `RequireOverlay` | `Require` | require-like 条件 |
| `CustomErrorRevert` / `RawRevertBytes` / `RevertOverlay` | `Revert` | revert 行为 |
| `PathConditionedCustomErrorRevert` / `PathConditionedRevert` | 多条 `Revert` | 每个 candidate 一条 |
| `ExternalCall` / `LowLevelCall` / `StaticCallOverlay` / `DelegateCallOverlay` | `ExternalCall` | 外部/低级调用 |
| `PathConditionedExternalCall` / `PathConditionedLowLevelCall` | 多条 `ExternalCall` | 每个 candidate 一条 |
| `PrecompileCall` | `PrecompileCall` | 预编译合约调用 |
| `PathConditionedPrecompileCall` | 多条 `PrecompileCall` | 每个 candidate 一条 |
| `InternalCall` | `InternalCall` | 内部调用 |
| `ReturnValue` | `Return` | 返回行为 |
| `MemoryRegionAllocate` | `MemoryAllocate` | 手工内存分配 |
| `MemoryArrayConstruction` | `MemoryObjectConstruct` | 内存对象/数组构造 |
| `StructMemoryMutation` / `StructInitializationFragment` | `MemoryObjectWrite` | struct 或内存对象修改 |
| `MemoryRegionWrite` / `CursorBasedMemoryWrite` | `MemoryObjectWrite` | 内存区域写入 |
| `ExpressionNormalization` / `EvaluationStep` | `ValueCompute` | 表达式归一化或原子计算步骤 |

## 6. semantic 子结构

`semantic` 是按 `kind` 扩展的结构化含义。它不是原始源码，也不是完整 AST；它描述“这条 fact 做了什么”。

### 6.1 Solidity lifted semantic

Solidity 最终 fact 优先使用和 Yul 一致的高级语义结构。

例如：

```json
{
  "kind": "StateWrite",
  "origin": "slither_lifted",
  "semantic": {
    "operation": "state_write",
    "access": "balanceOf[msg.sender]",
    "state_variable": "balanceOf",
    "keys": ["msg.sender"]
  },
  "evidence": {
    "overlay": "ov_2",
    "overlay_kind": "MappingWrite",
    "effects": ["eff_4"],
    "lifted_from": "sseir_solidity_overlay"
  }
}
```

这表示：

```text
Solidity 源码中的高级状态写入语义被直接记录为 StateWrite；
函数入口及状态版本 Phi 不作为最终 fact 出现；真正由当前函数 CFG 分支或循环产生的 Phi 可以作为 `ValuePhi` 分析事实保留。
```

### 6.2 Slither operation semantic

Slither operation semantic 仍用于 debug 或 fallback。字段通常包含：

| 字段 | 含义 |
| --- | --- |
| `semantic_category` | 大类，如 `value`、`call`、`control`、`data_access`、`event`、`return`、`ssa` |
| `slithir_kind` | 原 SlithIR operation 类型 |
| `slithir_text` | 原 SlithIR 文本 |
| `operator` | 运算符，适用于 Binary/Unary |
| `arguments` | 调用参数 |
| `values` | Return/Event 等值集合 |
| `reads` | 读取集合 |
| `writes` | 写入集合 |

调用类 fact 额外包含：

| 字段 | 含义 |
| --- | --- |
| `call_target` | 调用目标 |
| `function_name` | 被调函数名 |
| `call_value` | ETH value |
| `call_gas` | gas 参数 |
| `call_type` | call 类型 |

访问类 fact 额外包含：

| 字段 | 含义 |
| --- | --- |
| `base` | 被访问对象 |
| `index_or_member` | 索引或成员 |
| `created_type` | new array / new contract / new structure 创建类型 |

### 6.3 StateRead / StateWrite semantic

Yul 状态读写由 S-SEIR slot 恢复得到：

```json
{
  "operation": "state_write",
  "access": "balanceOf[to]",
  "state_variable": "balanceOf",
  "keys": ["to"],
  "slot": "toSlot",
  "slot_key": "toSlot__ssa3",
  "slot_versions": ["toSlot__ssa3"]
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `operation` | `state_read` 或 `state_write` |
| `access` | 恢复出的状态访问表达式 |
| `state_variable` | 状态变量名 |
| `keys` | mapping key 列表 |
| `storage_model` | 特殊 storage 模型标记 |
| `slot` | 原 slot 表达式 |
| `slot_key` | SSA slot 版本 |
| `slot_versions` | 参与恢复的 slot SSA 版本集合 |
| `candidate_status` | path-conditioned candidate 状态 |

### 6.4 EventEmit semantic

```json
{
  "event": "Transfer",
  "signature": "Transfer(address,address,uint256)",
  "args": ["from", "to", "value"]
}
```

Yul 中如果是 path-conditioned event，则还可能有：

```json
{
  "event": "Transfer",
  "args": ["from", "to", "value"],
  "candidate_status": "resolved",
  "unresolved_reason": null
}
```

### 6.5 Require / Revert semantic

`Require`：

```json
{
  "condition": "check != 0",
  "on_fail": "revert",
  "error": "Unauthorized()"
}
```

`Revert`：

```json
{
  "operation": "revert",
  "error": "Unauthorized()",
  "payload": "0x82b42900",
  "candidate_status": "resolved"
}
```

如果只识别出 raw selector，没有匹配到源码中定义的 error：

```json
{
  "operation": "revert",
  "candidate_status": "unmatched",
  "unresolved_reason": "selector_unmatched"
}
```

### 6.6 Call semantic

Yul call / precompile / low-level call 会记录：

```json
{
  "target": "mirror",
  "call_kind": "call",
  "selector": "0x263c69d6",
  "arguments": ["logs"],
  "value": "0",
  "precompile": null
}
```

path-conditioned call candidate 额外包含：

```json
{
  "candidate_status": "resolved",
  "unresolved_reason": null
}
```

### 6.7 Memory semantic

手工内存分配：

```json
{
  "base": "ordinals",
  "new_free_pointer": "ptr",
  "stored_values": []
}
```

内存对象构造：

```json
{
  "result": "ordinals",
  "array_type": "uint8[] memory",
  "element_type": "uint8",
  "length_expr": "shr(5, sub(ptr, add(ordinals, 0x20)))",
  "element_writes": []
}
```

struct 或内存对象写入：

```json
{
  "target": "p",
  "field": "logs",
  "value": "logs"
}
```

### 6.8 ValueCompute semantic

Yul 表达式原子化和归一化会输出 `ValueCompute`：

```json
{
  "temp": "__sseir_eval_asm_s_1_2",
  "expression": "shr(224, __sseir_eval_asm_s_1_1)",
  "call": "shr",
  "raw_args": ["224", "calldataload(0)"],
  "evaluated_args": ["224", "__sseir_eval_asm_s_1_1"]
}
```

普通归一化赋值：

```json
{
  "target": "h",
  "expression": "keccak256(add(data, 0x20), mload(data))"
}
```

## 7. evidence 子结构

`evidence` 保存这条 fact 如何得到。

### 7.1 Solidity / Slither evidence

```json
{
  "slither": {
    "order": 0,
    "kind": "Assignment",
    "text": "totalSupply_1(uint256) := initialSupply_1(uint256)",
    "ssa": true,
    "source_expression": "totalSupply = initialSupply",
    "lvalue": {},
    "rvalue": {},
    "read": [],
    "cfg_node": "bb_sol_slither_n1",
    "node_id": 1,
    "stmt_refs": ["sol_s_1"]
  }
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `order` | 当前函数内 SlithIR operation 顺序 |
| `kind` | 原 SlithIR operation 类型 |
| `text` | 原 SlithIR 文本 |
| `ssa` | 是否来自 `slithir_ssa` |
| `source_expression` | 对应 Solidity 源表达式 |
| `lvalue/rvalue/read` | Slither 原始变量记录 |
| `cfg_node/node_id` | Slither CFG 节点 |
| `stmt_refs` | S-SEIR SourceStatement 引用 |

### 7.2 Solidity ABI 表达式归一化

Solidity 低级调用的参数经常来自 ABI 内建函数，例如：

```solidity
token.call(abi.encodeWithSignature("transfer(address,uint256)", to, amount));
token.staticcall(abi.encodeWithSelector(IERC20View.totalSupply.selector));
```

SlithIR SSA 中这类表达式可能被拆成临时变量，例如：

```text
TMP_30 = abi.encodeWithSignature()(transfer(address,uint256), to, amount)
TUPLE_1 = token.call(TMP_30)

REF_25 = IERC20View.totalSupply.selector
TMP_36 = abi.encodeWithSelector()(REF_25)
TUPLE_2 = token.staticcall(TMP_36)
```

SemanticFact 不直接记录这些 SlithIR 展示形式。当前规则是：

```text
1. EffectLifter 处理 SolidityCall 时，先根据 stmt_refs 查询 SourceStatementTable。
2. 如果原始 Solidity 语句中存在 abi.* 调用，则按括号平衡提取完整 ABI 子表达式。
3. 该源码级表达式写入 ValueDef。
4. ExternalCall effect / overlay 再通过 value_defs 继承该表达式。
5. 最终 SemanticFact.semantic.arguments 使用源码级 ABI 表达式。
6. 如果 SourceStatementTable 中无法提取，再退回 SlithIR source_expression 或格式化后的 SlithIR 参数。
```

因此，以下旧式表达不会作为最终 semantic argument 输出：

```text
abi.encodeWithSignature()(transfer(address,uint256), to, amount)
abi.encodeWithSelector()(REF_25)
```

而会记录为：

```text
abi.encodeWithSignature("transfer(address,uint256)", to, amount)
abi.encodeWithSelector(IERC20View.totalSupply.selector)
```

同时，`semantic_facts.json` 的最终 fact 视图会递归移除 `solidity_like` 字段。该字段只属于 `sseir.json` 的 overlay/debug 信息或可选展示视图，不能作为精确语义字段传递给后续消费端。

### 7.3 Yul / S-SEIR evidence

```json
{
  "overlay": "ov_11",
  "overlay_kind": "PathConditionedEventEmit",
  "effects": ["eff_19"],
  "candidate": {
    "condition": "!(lt(fromBalance, value))",
    "event": "Transfer",
    "args": ["from", "to", "value"]
  }
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `overlay` | 来源 semantic overlay id |
| `overlay_kind` | 来源 overlay 类型 |
| `effects` | 支撑该 overlay 的底层 effect id |
| `candidate` | path-conditioned overlay 的某个候选语义 |
| `deduped_fact_count` | 多条相同语义 fact 合并后的原始数量 |

## 8. condition 记录规则

`condition` 表示该 fact 成立的路径条件。

### 8.1 Solidity condition

Solidity fact 的 condition 来自 function-level CFG 的 `control_dependencies`：

```text
1. ControlBuilder 通过 Slither CFG 建立 Solidity block。
2. 对每个 Solidity block 记录其控制依赖谓词。
3. SlitherFactAdapter 将 block condition 写入该 block 内每条 SlithIR fact。
```

示例：

```solidity
if (amount > 0) {
    _transfer(to, amount);
}
```

可能产生：

```json
{
  "kind": "InternalCall",
  "condition": "amount > 0",
  "semantic": {
    "call_target": "_transfer"
  }
}
```

### 8.2 Yul condition

Yul fact 的 condition 来自两类来源：

```text
1. PathConditioned overlay candidate 自带 condition；
2. 普通 overlay 若缺少 condition，会从 effect.attrs.path_states 回填单一路径条件。
```

示例：

```yul
if lt(fromBalance, value) {
    revert(0x1c, 0x04)
}
sstore(fromSlot, sub(fromBalance, value))
```

产生：

```json
[
  {
    "kind": "Revert",
    "condition": "lt(fromBalance, value)"
  },
  {
    "kind": "StateWrite",
    "condition": "!(lt(fromBalance, value))"
  }
]
```

### 8.3 多路径保留规则

如果同一条语义终点在不同路径下有不同候选，则保留多条 fact：

```json
[
  {
    "kind": "StateWrite",
    "condition": "flag",
    "lvalue": "balances[a]"
  },
  {
    "kind": "StateWrite",
    "condition": "!flag",
    "lvalue": "balances[b]"
  }
]
```

这里不能合并，因为 `condition` 不同，语义路径不同。

## 9. 去重规则

最终输出会对 `solidity_facts` 和 `yul_facts` 分别去重，然后合并。

去重 key 包含：

```text
kind
source_lang
origin
function
contract
signature
condition
lvalue
rvalue
reads
writes
semantic
```

因此：

```text
1. 不同 condition 的相同语句不会被去重；
2. 同一 condition 下完全相同的语义会被合并；
3. 合并时不会丢证据，stmt_refs/cfg_nodes/reads/writes/evidence 会合并；
4. 合并后的 evidence 会记录 deduped_fact_count。
```

示例：

```json
{
  "kind": "Require",
  "condition": "entry",
  "semantic": {
    "condition": "EXTRACTION_DENOMINATOR != 0"
  },
  "evidence": {
    "overlay": ["ov_10", "ov_11", "ov_12"],
    "effects": ["eff_1", "eff_4", "eff_7"],
    "deduped_fact_count": 3
  }
}
```

## 10. 示例一：Solidity 状态写入

### 10.1 源码

```solidity
constructor(uint256 initialSupply) {
    totalSupply = initialSupply;
}
```

### 10.2 推导过程

内部仍然会用 SlithIR SSA 作为分析材料：

```text
totalSupply_1(uint256) := initialSupply_1(uint256)
```

但最终不输出 `ValueAssign` 低级 fact，而是：

```text
1. EffectLifter 识别 Solidity StorageWrite effect。
2. SemanticOverlayBuilder 生成 StateVariableWrite overlay。
3. SoliditySemanticLifter 将该 overlay 投影为 StateWrite fact。
4. SlithIR / effect / overlay id 作为 evidence 保存。
```

### 10.3 SemanticFact

```json
{
  "fact_id": "fact_1",
  "kind": "StateWrite",
  "source_lang": "solidity",
  "origin": "slither_lifted",
  "function": "constructor",
  "contract": "AssemblyERC20",
  "signature": "constructor(uint256)",
  "stmt_refs": ["sol_s_1"],
  "cfg_nodes": ["bb_sol_slither_n1"],
  "lvalue": "totalSupply",
  "rvalue": "initialSupply",
  "reads": ["initialSupply"],
  "writes": ["totalSupply"],
  "semantic": {
    "operation": "state_write",
    "access": "totalSupply",
    "state_variable": "totalSupply"
  },
  "evidence": {
    "overlay": "ov_1",
    "overlay_kind": "StateVariableWrite",
    "effects": ["eff_1"],
    "lifted_from": "sseir_solidity_overlay"
  }
}
```

推导过程：

```text
Solidity assignment
  -> Slither Assignment
  -> StorageWrite effect
  -> StateVariableWrite overlay
  -> StateWrite SemanticFact
```

## 11. 示例二：Solidity 低级调用

### 11.1 源码

```solidity
(bool ok, bytes memory data) =
    token.call(abi.encodeWithSignature("transfer(address,uint256)", to, amount));
```

### 11.2 SemanticFact

```json
{
  "kind": "ExternalCall",
  "source_lang": "solidity",
  "origin": "slither_lifted",
  "function": "lowLevelTransfer",
  "semantic": {
    "target": "token",
    "call_kind": "LowLevelCall",
    "arguments": ["abi.encodeWithSignature(\"transfer(address,uint256)\", to, amount)"]
  },
  "evidence": {
    "overlay": "ov_3",
    "overlay_kind": "ExternalCall",
    "effects": ["eff_2"],
    "lifted_from": "sseir_solidity_overlay"
  }
}
```

推导过程：

```text
Solidity low-level call
  -> Slither LowLevelCall
  -> ExternalCall effect
  -> ExternalCall overlay
  -> SemanticFact kind = ExternalCall
  -> semantic.target / call_kind / arguments 记录调用行为
```

### 11.3 staticcall selector 示例

源码：

```solidity
(bool ok, bytes memory data) =
    token.staticcall(abi.encodeWithSelector(IERC20View.totalSupply.selector));
```

SemanticFact：

```json
{
  "kind": "ExternalCall",
  "source_lang": "solidity",
  "origin": "slither_lifted",
  "function": "staticSupply",
  "semantic": {
    "target": "token",
    "call_kind": "LowLevelCall",
    "arguments": ["abi.encodeWithSelector(IERC20View.totalSupply.selector)"]
  }
}
```

推导过程：

```text
SourceStatementTable 保存原始 Solidity 语句
  -> EffectLifter 在 SolidityCall 中提取 abi.encodeWithSelector(...)
  -> LowLevelCall 的 TMP 参数解析为该 ABI 表达式
  -> ExternalCall overlay
  -> ExternalCall SemanticFact
```

## 12. 示例三：Yul mapping 读写

### 12.1 源码

```yul
mstore(0x00, from)
mstore(0x20, balanceOf.slot)
let fromSlot := keccak256(0x00, 0x40)
let fromBalance := sload(fromSlot)

mstore(0x00, to)
mstore(0x20, balanceOf.slot)
let toSlot := keccak256(0x00, 0x40)

sstore(fromSlot, sub(fromBalance, value))
sstore(toSlot, add(sload(toSlot), value))
```

### 12.2 S-SEIR 恢复

```text
1. MemorySSA 记录 memory[0x00] = from, memory[0x20] = balanceOf.slot。
2. keccak256(0x00, 0x40) 作为 slot 计算 sink 触发 memory 追踪。
3. fromSlot 恢复为 slot(balanceOf[from])。
4. sload(fromSlot) 恢复为 balanceOf[from]。
5. toSlot 同理恢复为 slot(balanceOf[to])。
6. sstore 作为状态写 sink 恢复为 balanceOf[from] 和 balanceOf[to] 的写入。
7. SemanticOverlayBuilder 生成 MappingRead / MappingWrite。
8. SSeirFactAdapter 投影为 StateRead / StateWrite。
```

### 12.3 SemanticFact

```json
{
  "kind": "StateRead",
  "source_lang": "yul",
  "origin": "sseir_overlay",
  "function": "_assemblyMove",
  "lvalue": "fromBalance",
  "rvalue": "balanceOf[from]",
  "semantic": {
    "operation": "state_read",
    "access": "balanceOf[from]",
    "state_variable": "balanceOf",
    "keys": ["from"],
    "slot": "fromSlot",
    "slot_key": "fromSlot__ssa1"
  },
  "evidence": {
    "overlay": "ov_7",
    "overlay_kind": "MappingRead",
    "effects": ["eff_8", "eff_10"]
  }
}
```

```json
{
  "kind": "StateWrite",
  "source_lang": "yul",
  "origin": "sseir_overlay",
  "function": "_assemblyMove",
  "condition": "!(lt(fromBalance, value))",
  "lvalue": "balanceOf[to]",
  "rvalue": "(balanceOf[to] + value)",
  "semantic": {
    "operation": "state_write",
    "access": "balanceOf[to]",
    "state_variable": "balanceOf",
    "keys": ["to"],
    "slot": "toSlot",
    "slot_key": "toSlot__ssa3"
  },
  "evidence": {
    "overlay": "ov_10",
    "overlay_kind": "MappingWrite",
    "effects": ["eff_15", "eff_18"]
  }
}
```

## 13. 示例四：Yul event

### 13.1 源码

```yul
mstore(0x00, value)
log3(
    0x00,
    0x20,
    0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef,
    from,
    to
)
```

### 13.2 推导过程

```text
1. log3 是事件 sink。
2. topic0 匹配源码 event 定义的 keccak256 签名。
3. topic1/topic2 对应 indexed 参数 from/to。
4. data 区间由 MemorySSA / SinkResolver 解析出 value。
5. overlay 生成 PathConditionedEventEmit。
6. candidate 投影为 EventEmit fact。
```

### 13.3 SemanticFact

```json
{
  "kind": "EventEmit",
  "source_lang": "yul",
  "origin": "sseir_overlay",
  "function": "_assemblyMove",
  "condition": "!(lt(fromBalance, value))",
  "semantic": {
    "event": "Transfer",
    "args": ["from", "to", "value"]
  },
  "evidence": {
    "overlay": "ov_11",
    "overlay_kind": "PathConditionedEventEmit",
    "effects": ["eff_19"],
    "candidate": {
      "condition": "!(lt(fromBalance, value))",
      "event": "Transfer",
      "args": ["from", "to", "value"]
    }
  }
}
```

## 14. 示例五：Yul 原子化计算

### 14.1 源码

```yul
selector := shr(224, calldataload(0))
```

### 14.2 推导过程

```text
1. 右侧是复合表达式。
2. S-SEIR 按实际求值顺序进行原子化：
   __sseir_eval_asm_s_1_1 = calldataload(0)
   __sseir_eval_asm_s_1_2 = shr(224, __sseir_eval_asm_s_1_1)
   selector = __sseir_eval_asm_s_1_2
3. EvaluationStep 记录中间计算。
4. ExpressionNormalization 记录最终赋值。
5. 两类 overlay 都投影为 ValueCompute。
```

### 14.3 SemanticFact

```json
{
  "kind": "ValueCompute",
  "source_lang": "yul",
  "origin": "sseir_overlay",
  "function": "selectorFromCalldata",
  "lvalue": "__sseir_eval_asm_s_1_1",
  "rvalue": "calldataload(0)",
  "semantic": {
    "temp": "__sseir_eval_asm_s_1_1",
    "expression": "calldataload(0)",
    "call": "calldataload",
    "raw_args": ["0"],
    "evaluated_args": ["0"]
  }
}
```

```json
{
  "kind": "ValueCompute",
  "source_lang": "yul",
  "origin": "sseir_overlay",
  "function": "selectorFromCalldata",
  "lvalue": "selector",
  "rvalue": "shr(224, calldataload(0))",
  "semantic": {
    "target": "selector",
    "expression": "shr(224, calldataload(0))"
  }
}
```

## 15. 消费建议

后续审计或 LLM 恢复源码时，建议按以下顺序读取：

```text
1. 先读取 facts。
2. 按 function / contract / signature 分组。
3. 按 condition 分路径。
4. 对每条路径内的 StateWrite、EventEmit、ExternalCall、Revert、Return 优先建模。
5. ValueCompute 用于解释关键表达式和 Yul 原子化过程。
6. 如果需要更细证据，再通过 evidence.overlay/effects 回到 sseir.json。
```

核心原则：

```text
1. SemanticFact 表示行为事实，不是源码替换结果。
2. 不同 condition 下的相同 kind 必须保留。
3. 同一路径下完全相同的语义可以合并，但证据必须保留。
4. Solidity fact 保持 Slither / SlithIR 风格；Yul fact 表达 S-SEIR 恢复后的行为语义。
5. 对于未恢复或不确定语义，记录 unresolved_reason/candidate_status，而不是猜测。
```
