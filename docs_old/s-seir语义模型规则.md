# S-SEIR 语义模型记录规则

本文说明一份 Solidity 源码经过 S-SEIR 后，语义模型文件如何组织、每个字段表示什么、字段如何生成，以及后续消费者应如何读取。

文档首先说明完整记录规则；第 16 节再使用真实源码和 S-SEIR 输出集中展示逐步推导过程。

本文依据当前实际代码实现整理，主要对应：

```text
scripts/s_seir/s_seir_model.py
scripts/s_seir/s_seir_pipeline.py
scripts/s_seir/s_seir_source_collector.py
scripts/s_seir/s_seir_control_builder.py
scripts/s_seir/s_seir_memory_ssa.py
scripts/s_seir/s_seir_effect_lifter.py
scripts/s_seir/s_seir_sink_resolver.py
scripts/s_seir/s_seir_overlay_builder.py
scripts/s_seir/s_seir_semantic_normalizer.py
scripts/s_seir/s_seir_security_facts.py
```

## 1. 模型的层级关系

S-SEIR 以函数为基本记录单位。模型由低到高分为：

```text
Source Statements
    原始 Solidity/Yul 语句
        ↓ stmt_refs
Control
    函数级 CFG、分支、循环和终止关系
        ↓ path_states / cfg_node_id
Expression Roles
    表达式在当前语义中的用途
        ↓
Effects
    memory/storage/call/log/revert 等底层行为
        ↓ overlay.effects
Semantic Overlays
    从一组 effect 模式恢复出的高级语义
        ↓
Analysis Facts / Security Facts
    分析过程事实与安全摘要
```

核心原则：

```text
1. 高级 overlay 不删除底层 effect。
2. effect 和 overlay 都通过 stmt_refs 回指源码。
3. condition、SSA version、unknown 和 unresolved 都属于证据。
4. attrs 是按 kind 扩展的字段集合；不适用的字段通常不输出。
5. solidity-like 是投影视图，不是核心模型本身。
6. 正式模型不记录 confidence，也没有独立 projection_policies 层。
7. MemorySSA/SinkResolver 是内部推导工具；标准输出只保留它们证明的语义结果。
```

能否无损投影的静态结果保存在 overlay `attrs.solidity_equivalent`、`reason`、`unresolved_reason` 和 `solidity_like` 中。

```text
solidity_equivalent = true/false  表示明确可或不可无损表达；
solidity_equivalent = not_exact   表示存在审计友好表达，但手工布局/分配细节不完全等价；
reason/unresolved_reason           记录不可投影或未解析原因。
```

## 2. 文件最外层组织

### 2.1 标准 Pipeline 输出

直接运行 `s_seir_pipeline.py` 时，标准 JSON 是函数对象数组：

```json
[
  {
    "function_id": "Token.balanceOf(address)",
    "contract": "Token",
    "function": "balanceOf",
    "signature": "balanceOf(address)",
    "source_statements": [],
    "control": {},
    "expr_roles": [],
    "effects": [],
    "semantic_overlays": [],
    "security_facts": [],
    "analysis_facts": []
  }
]
```

默认 `--output` 和 `--text-output` 生成规范语义视图，不包含 MemorySSA/SinkResolver
查询轨迹。如需排查分析器，显式使用：

```bash
python scripts/s_seir/s_seir_pipeline.py Token.sol \
  --output outputs/sseir.json \
  --debug-output outputs/sseir.debug.json
```

`sseir.debug.json` 保留完整 MemorySSA、byte-axis、SinkResolver 和 SlithIR 调试数据；
批处理的默认 `sseir.json` 同样使用规范语义视图。

### 2.2 批处理输出

批量合约脚本会在标准函数模型外增加来源和筛选信息：

```json
{
  "schema": "s-seir-source/v1",
  "source": "/path/Token.sol",
  "result_dir": "/path/output",
  "source_entry": {
    "source": "/path/Token.sol",
    "source_id": "0x...__Token",
    "compile_preparation": {},
    "contracts": [],
    "selected_contracts": ["Token"],
    "functions": [],
    "source_snapshot": {}
  }
}
```

外层字段含义：

| 字段 | 含义 | 生成方式 |
| --- | --- | --- |
| `schema` | 批处理文件格式版本 | 批处理脚本固定写入 |
| `source` | 入口 Solidity 文件 | 来自命令行输入 |
| `result_dir` | 当前合约输出目录 | 由批处理脚本创建 |
| `source_entry` | 一份源码工程的完整分析记录 | 编译准备、合约筛选和 S-SEIR 结果汇总 |
| `compile_preparation` | solc、include path、remapping、依赖安装记录 | 编译预检查阶段生成 |
| `contracts` | AST 中发现的合约、接口和库 | solc AST 扫描生成 |
| `selected_contracts` | 最终分析的完整 ERC-20 实现合约 | 合约种类、abstract 状态、继承和 ERC-20 入口共同筛选 |
| `functions` | `FunctionSSEIR` 列表 | S-SEIR Pipeline 生成 |
| `source_snapshot` | 随结果保存的源码快照 | 批处理导出阶段生成 |

后续语义消费应从 `source_entry.functions` 开始。其余字段用于复现编译环境和解释合约筛选结果。

## 3. FunctionSSEIR 根对象

函数对象固定字段如下：

| 字段 | 类型 | 含义 | 生成方式 |
| --- | --- | --- | --- |
| `function_id` | string | 函数全局标识 | `contract + "." + signature` |
| `contract` | string | 函数所属合约 | solc AST `ContractDefinition.name` |
| `function` | string | 函数名 | AST 函数名；构造函数等使用规范名称 |
| `signature` | string | 带参数类型的函数签名 | 参数 `typeString` 组合 |
| `source_statements` | array | 原始语句表 | `SourceStatementCollector` |
| `control` | object | 函数级 CFG | Slither Solidity CFG 与 Yul CFG 融合 |
| `expr_roles` | array | 表达式用途索引 | `ExpressionRoleAnalyzer` 和 `SemanticNormalizer` |
| `effects` | array | 底层语义节点 | `EffectLifter` 和分支物化阶段 |
| `semantic_overlays` | array | 高级语义节点 | `SemanticOverlayBuilder` |
| `security_facts` | array | 安全行为摘要 | `SecurityFactBuilder` |
| `analysis_facts` | array | 分析过程和辅助表 | Effect、Normalizer 和 Pipeline 汇总 |

批处理结果还可能为函数附加：

| 字段 | 含义 |
| --- | --- |
| `function_source` | assembly 所在函数的完整源码及 `src` |
| `assembly_sources` | 函数内每个 assembly 块的源码、块编号及 `src` |

这两个字段用于人工审计，不属于 `FunctionSSEIR` dataclass 的核心语义层。

## 4. SourceStatement 记录

### 4.1 字段

```json
{
  "stmt_id": "asm_s_3",
  "lang": "yul",
  "text": "aa := sload(keccak256(0, 64))",
  "src": "2200:33:0",
  "function_id": "TKM.balanceOf(address)",
  "block_id": "asm_block_1",
  "origin": {
    "nodeType": "YulAssignment",
    "assembly_block": 1
  }
}
```

| 字段 | 含义 | 生成方式 |
| --- | --- | --- |
| `stmt_id` | 函数内唯一语句编号 | Solidity 使用 `sol_s_N`，Yul 使用 `asm_s_N` |
| `lang` | `solidity` 或 `yul` | 根据 AST 节点所属语言确定 |
| `text` | 语句文本 | Solidity 从 `src` 截取；Yul 由 AST 格式化 |
| `src` | solc source mapping | 原 AST 节点的 `start:length:fileIndex` |
| `function_id` | 所属函数 | 继承 FunctionUnit 的标识 |
| `block_id` | 所属语句块 | Yul 为 `asm_block_N`；Solidity 可为空或为控制块 |
| `origin` | AST 来源信息 | 保存 `nodeType`、assembly block 编号等最小来源信息 |

### 4.2 生成规则

```text
1. SourceStatementCollector 遍历每个 FunctionDefinition/ModifierDefinition。
2. Solidity 控制和语句节点按源位置收集。
3. InlineAssembly 内的 Yul statement 按 Yul AST 收集。
4. 所有语句按 src 起点排序。
5. 后续节点只保存 stmt_refs，不复制完整源码证据。
```

## 5. Control 层

### 5.1 control 根字段

```json
{
  "blocks": [],
  "edges": [],
  "notes": [],
  "loop_contexts": {}
}
```

| 字段 | 含义 | 生成方式 |
| --- | --- | --- |
| `blocks` | CFG basic block 列表 | Solidity CFG block 与 Yul CFG node 融合 |
| `edges` | 控制流边 | Slither edge、Yul edge和拼接边 |
| `notes` | 构建模式和降级说明 | 例如 Slither 不可用时的 fallback |
| `loop_contexts` | assembly 块所在 Solidity loop 上下文 | solc AST 的 For/While/DoWhile 范围匹配 |

### 5.2 block 字段

```json
{
  "block_id": "bb_asm1_n3",
  "kind": "yul",
  "assembly_block": 1,
  "stmts": ["asm_s_3"],
  "terminator": {
    "kind": "Branch",
    "condition": "iszero(check)"
  },
  "attrs": {
    "node_id": 3,
    "node_kind": "if-condition",
    "src": "...",
    "text": "if iszero(check)",
    "function_loop_context": []
  }
}
```

| 字段 | 含义 |
| --- | --- |
| `block_id` | CFG 节点唯一标识 |
| `kind` | `solidity` 或 `yul` |
| `assembly_block` | Yul 节点所属 assembly 编号 |
| `stmts` | 该节点包含的 `stmt_id` |
| `terminator` | 当前 block 的控制终点 |
| `attrs` | node id、node kind、源码位置及循环上下文 |

`block.attrs` 具体字段：

| 字段 | 含义 |
| --- | --- |
| `node_id/node_kind` | 本地 Yul CFG node 编号和类别 |
| `slither_node_id/slither_node_type` | Solidity block 对应的 Slither node |
| `src/text` | block 源码位置和文本 |
| `function_loop_context` | 包含该 assembly 的 Solidity loop |
| `fallback` | 是否由 Slither 不可用时的顺序骨架生成 |

terminator 的 `kind` 支持：

```text
Branch
Return
Revert
Stop
Terminal
Fallthrough
YulNode
```

terminator 的完整可选字段为 `kind`、`condition`、`text`、`node_kind`。`Branch` 额外保存 `condition`；终止节点可以保存 `text` 或原始 node kind。

### 5.3 edge 字段

```json
{
  "from": "bb_asm1_n3",
  "to": "bb_asm1_n4",
  "kind": "true"
}
```

| 字段 | 含义 |
| --- | --- |
| `from` | 前驱 block |
| `to` | 后继 block |
| `kind` | `true/false/fallthrough/back/exit` 等边类型 |

Solidity CFG 优先来自 Slither；Yul CFG 来自 solc Yul AST。ControlBuilder 将 Slither 中 assembly 的占位范围替换为 Yul 子图，并连接 assembly 入口和出口。

### 5.4 path condition 的来源

Effect 的 `path_states` 来自 MemorySSA PathState。若 assembly 入口的直接前驱是 Solidity `Branch`，其条件会作为函数级前缀加入 Yul 本地条件：

```text
Solidity 条件：guard
Yul 条件：iszero(check)
最终 path_states：guard && iszero(check)
```

## 6. ExpressionRole 层

### 6.1 固定字段

```json
{
  "expr_id": "expr_12",
  "text": "add(32, returndata)",
  "normalized": "returndata.data",
  "role": "revert_payload_ptr",
  "type_hint": "memory_ptr",
  "stmt_ref": "asm_s_2",
  "attrs": {
    "object": "returndata",
    "overlay": "ov_1"
  }
}
```

| 字段 | 含义 | 生成方式 |
| --- | --- | --- |
| `expr_id` | 表达式角色唯一编号 | `expr_N` 分配器 |
| `text` | 原始表达式 | Yul AST、effect 或 overlay 字段 |
| `normalized` | 统一语义表达 | `normalize_expr` 或高级 overlay 结果 |
| `role` | 表达式在当前语义中的用途 | 规则分类，不是 Solidity 类型 |
| `type_hint` | 类型提示 | TypeEnv、opcode 参数角色或 overlay 类型 |
| `stmt_ref` | 来源语句 | 对应 SourceStatement |
| `attrs` | 角色特有信息 | object、effect、overlay、index、state variable 等 |

### 6.2 role 分类

| 类别 | 主要 role | 生成规则 |
| --- | --- | --- |
| 控制 | `guard_condition`, `branch_condition`, `loop_bound` | Solidity/Yul branch、loop AST、RequireOverlay |
| memory | `free_memory_pointer`, `memory_slice_start`, `memory_slice_size`, `memory_read_value` | `mload(0x40)` 和 memory effect |
| bytes | `bytes_data_ptr`, `bytes_length_value`, `hash_input` | bytes/string 类型和 `object+32/mload(object)` 模式 |
| storage | `storage_slot_expr`, `mapping_slot_expr`, `mapping_key_material`, `state_access_expr`, `storage_slot_version` | storage effect 和 mapping/state overlay |
| packed slot | `manual_storage_slot_expr`, `packed_hash_material` | manual packed hash slot derivation |
| call | `call_target`, `call_gas`, `call_value`, `call_input_ptr`, `call_input_size`, `call_output_ptr`, `call_output_size` | call-family opcode 参数位置 |
| ABI | `abi_selector`, `abi_argument`, `abi_calldata_ptr`, `abi_calldata_size` | ABI calldata overlay |
| event | `event_topic0`, `event_indexed_argument` | EventLog/EventEmit |
| return/revert | `return_payload_ptr`, `return_payload_size`, `return_payload_value`, `revert_payload_ptr`, `revert_payload_size` | Return/Revert effect 和 overlay |
| struct/array | `struct_field_ptr`, `struct_field_value`, `struct_field_write_value`, `memory_array_*` | TypeEnv + memory layout 模式 |
| address | `address_code_target`, `address_code_size`, `address_has_code_condition`, `address_zero_checked_value` | extcodesize/address zero-check overlay |

同一表达式可以有多个 role。例如 `o` 在 call 中可以是 `call_output_ptr`，在 `mload(o)` 中又是 `memory_slice_start`。这表示用途不同，不是冲突。

当前输出中使用的 role 名称完整列举如下：

```text
abi_argument
abi_calldata_ptr
abi_calldata_size
abi_selector
address_code_size
address_code_target
address_has_code_condition
address_zero_checked_value
branch_condition
bytes_data_ptr
bytes_length_value
call_gas
call_input_ptr
call_input_size
call_output_ptr
call_output_size
call_target
call_value
calldata_read_offset
calldata_word_value
event_indexed_argument
event_topic0
free_memory_pointer
guard_condition
hash_input
loop_bound
manual_storage_slot_expr
mapping_key_material
mapping_slot_expr
memory_array_constructed_base
memory_array_constructed_element
memory_array_constructed_length
memory_array_element_value
memory_array_length_value
memory_read_value
memory_region_ptr
memory_region_value
memory_slice_size
memory_slice_start
packed_hash_material
return_payload_ptr
return_payload_size
return_payload_value
revert_payload_ptr
revert_payload_size
state_access_expr
storage_slot_expr
storage_slot_version
storage_write_value
struct_field_ptr
struct_field_value
struct_field_write_value
```

ExpressionRole `attrs` 常用字段：

| 字段 | 含义 |
| --- | --- |
| `effect/overlay` | 产生该 role 的语义节点 id |
| `object/array/struct_object` | 表达式所属 bytes、array 或 struct 对象 |
| `state_variable/storage_reference/storage_field` | storage 访问上下文 |
| `index/candidate_index/call_input_word` | 参数、候选或 call input word 的位置 |
| `call_kind` | call/staticcall/delegatecall/callcode |
| `memory_slot` | memory read/hash 所使用的位置 |
| `slot_versions` | 该 role 涉及的 storage SSA versions |
| `slot_derivation/slot_derivation_kind` | manual/mapping slot 推导方式 |
| `encoding_hint` | return/call 的 ABI word 提示 |
| `status` | path-conditioned candidate 的解析状态 |
| `pattern/semantic/projection` | 命中的模式和投影类别 |
| `type/array_type/value_versions` | 额外类型或 SSA 信息 |

## 7. Effect 层

Effect 只记录已经发生的低层行为，不负责判断它等价于哪个 Solidity 高级语句。

### 7.1 EffectNode 固定字段

```json
{
  "effect_id": "eff_7",
  "kind": "StorageRead",
  "stmt_refs": ["asm_s_3"],
  "attrs": {}
}
```

| 字段 | 含义 | 生成方式 |
| --- | --- | --- |
| `effect_id` | effect 唯一编号 | `eff_N` 分配器 |
| `kind` | 底层行为种类 | 根据 Yul AST opcode 或控制节点分类 |
| `stmt_refs` | 直接来源语句 | SourceStatement 引用 |
| `attrs` | kind 特有字段 | EffectLifter、MemorySSA 和 SinkResolver 填充 |

### 7.2 Effect 通用 attrs

| 字段 | 含义 |
| --- | --- |
| `cfg_node_id` | 对应 assembly CFG node id |
| `path_states` | 到达该 effect 的条件路径 |
| `nested_in_condition` | 操作是否嵌套在 branch 条件中 |
| `nested_in_value` | 操作是否嵌套在复合右值中 |
| `evaluation_step` | 对应的原子求值步骤编号 |
| `semantic_inputs` | MemorySSA/SinkResolver 已证明的逐路径 sink 输入语义 |
| `memory_semantics` | 非 sink memory read 的规范化来源值 |
| `unresolved_reason` | 确实缺少必要来源时的保守原因 |

### 7.3 ValueDef 与 EvaluationStep

`ValueDef` 记录 Yul 变量定义或赋值：

```json
{
  "kind": "ValueDef",
  "attrs": {
    "targets": ["dhzw"],
    "value": "add(sload(slot), amount)",
    "value_normalized": "storage[slot] + amount",
    "target_versions": {
      "dhzw": ["dhzw__ssa1"]
    },
    "atomized_value": {
      "evaluation_model": "yul_ast_right_to_left_function_call_arguments",
      "steps": [],
      "final": "__sseir_eval_2"
    }
  }
}
```

| attrs 字段 | 含义 |
| --- | --- |
| `targets` | 左值变量列表 |
| `value` | 原始右值 |
| `value_normalized` | 归一化右值 |
| `target_versions` | 左值在当前 CFG 路径上的 SSA version |
| `atomized_value` | 复合右值的原子求值树 |

`EvaluationStep` 记录树中的单次操作：

```json
{
  "kind": "EvaluationStep",
  "attrs": {
    "temp": "__sseir_eval_1",
    "call": "sload",
    "raw_args": ["slot"],
    "evaluated_args": ["slot"],
    "expression": "sload(slot)",
    "expression_normalized": "sload(slot)",
    "order": 1,
    "evaluation_context": "value"
  }
}
```

复合表达式使用 Yul AST 拆分，函数参数按 Yul/EVM 从右到左求值，不按字符串括号硬拆。

### 7.4 MemoryWrite

来源：`mstore/mstore8/copy` 等 memory 写入。

```json
{
  "kind": "MemoryWrite",
  "stmt_refs": ["asm_s_1"],
  "attrs": {
    "address": "add(ptr, 0x20)",
    "value": "amount",
    "write_kind": "mstore",
    "memory_version": "mem_ptr_32__mssa2",
    "origin_node": 4,
    "aliases": [
      {"base": "ptr", "offset": 32, "expression": "ptr + 32"}
    ],
    "path_states": ["entry"]
  }
}
```

| attrs 字段 | 含义 |
| --- | --- |
| `address` | 原始写入地址 |
| `value` | 写入表达式 |
| `write_kind` | `mstore/mstore8/...` |
| `memory_version` | 本次写入产生的 MemorySSA version |
| `origin_node` | 定义所在 CFG node |
| `aliases` | 地址的线性 base+offset 等价形式 |

MemorySSA 还会在查询中按字节记录 source value、source offset、source version 和 known-value 信息，用于处理重叠写入和非对齐 slice。

### 7.5 MemoryRead

来源：`mload(ptr)`，包括嵌套在 condition/value 中的读取。

```json
{
  "kind": "MemoryRead",
  "attrs": {
    "read_from": "ptr",
    "value": "x",
    "value_versions": ["x__ssa1"],
    "memory_semantics": [{
      "role": "memory_read",
      "paths": [{"values": ["amount"]}]
    }]
  }
}
```

| attrs 字段 | 含义 |
| --- | --- |
| `read_from` | mload 指针 |
| `value` | 接收读取结果的变量或临时量 |
| `value_versions` | 读取结果 SSA version |
| `memory_semantics` | 由 MemorySSA/byte-axis 证明的来源值及其执行条件 |
| `reads_after_call_output` | 可到达该读取的前置 call output |
| `value_from_call_output` | 读取值被证明来自调用返回区时的表达式 |
| `returndatasize_pointer_candidates` | 指针来自 `returndatasize()` 时的逐路径候选 |

### 7.6 MemoryHash

来源：`keccak256(ptr, size)`。

```json
{
  "kind": "MemoryHash",
  "attrs": {
    "ptr": "0",
    "size": "64",
    "value": "slotHash",
    "ptr_versions": [],
    "size_versions": [],
    "value_versions": ["slotHash__ssa1"],
    "semantic_inputs": {
      "sink_kind": "MemoryHash",
      "paths": [{
        "arguments": {
          "slot": "keccak256(abi.encode(account, balances.slot))"
        }
      }]
    }
  }
}
```

MemoryHash 本身只说明对 memory range 求哈希。只有后续 storage sink 消费该结果且输入匹配 storage 模式时，才生成 MappingSlot 或 manual packed slot overlay。

### 7.7 MemoryCopy 与 LoopMemorySummary

`MemoryCopy` 记录 copy-family 操作：

```json
{
  "kind": "MemoryCopy",
  "attrs": {
    "op": "calldatacopy",
    "args": ["dst", "src", "size"],
    "cfg_node_id": 5,
    "path_states": ["entry"]
  }
}
```

`op` 可以是：

```text
calldatacopy
codecopy
returndatacopy
mcopy
extcodecopy
```

`LoopMemorySummary` 将 loop-aware lazy MemorySSA 的循环记录放入 Effect 层：

```json
{
  "kind": "LoopMemorySummary",
  "attrs": {
    "kind": "LoopMemoryRecord",
    "loop_id": "loop_1",
    "assembly_block": 1,
    "header_node": 3,
    "condition": "lt(i, count)",
    "bound": {},
    "memory_effects": [],
    "status": "summary_only"
  }
}
```

该 effect 不展开执行循环，只归档循环 memory effect 模板。后续 mload/keccak/log/call/return/revert 查询相关区间时才触发 lazy materialization、MemoryPhi 或 MemoryRangeSummary。

### 7.8 StorageRead / StorageWrite

```json
{
  "kind": "StorageRead",
  "attrs": {
    "slot": "slotHash",
    "slot_versions": ["slotHash__ssa1"],
    "value": "balance",
    "value_versions": ["balance__ssa1"],
    "path_states": ["entry"]
  }
}
```

```json
{
  "kind": "StorageWrite",
  "attrs": {
    "slot": "slotHash",
    "slot_versions": ["slotHash__ssa1"],
    "value": "newBalance",
    "value_versions": ["newBalance__ssa2"]
  }
}
```

| attrs 字段 | 含义 |
| --- | --- |
| `slot` | 原始 slot 表达式 |
| `slot_versions` | 到达 sink 的 slot SSA 候选 |
| `value` | 读取结果或写入值 |
| `value_versions` | value 的 SSA 候选 |
| `parent_*` | 嵌套 sload 所属 memory/storage/call 上下文 |

storage 恢复始终以该 effect 为语义终点，向后追踪 slot 的 ValueDef、MemoryHash 和 MemorySSA 来源。

### 7.9 Call / StaticCall / DelegateCall / CallCode

Call-family effect 记录 Yul 调用的全部参数：

```json
{
  "kind": "StaticCall",
  "attrs": {
    "op": "staticcall",
    "gas": "gas()",
    "target": "2",
    "value": null,
    "input_ptr": "input",
    "input_size": "20",
    "output_ptr": "output",
    "output_size": "32",
    "result": "ok",
    "input_memory": {},
    "input_memory_partial": {},
    "output_memory_query": {},
    "sink_resolution": {},
    "evaluated_args": []
  }
}
```

| attrs 字段 | 含义 |
| --- | --- |
| `op` | call kind |
| `gas` | gas 表达式 |
| `target` | 目标地址表达式 |
| `value` | ETH value；static/delegate call 可为空 |
| `input_ptr/input_size` | calldata memory range |
| `output_ptr/output_size` | returndata memory range |
| `result` | 调用成功标志 SSA 变量 |
| `input_memory` | 普通 MemorySSA 输入查询 |
| `input_memory_partial` | selector/ABI 局部切片 |
| `output_memory_query` | 输出区原 reaching definition |
| `sink_resolution` | 按路径解析的调用输入 |
| `evaluated_args` | 原子化后按实际顺序计算的参数 |

### 7.10 EventLog

来源：`log0-log4`。

```json
{
  "kind": "EventLog",
  "attrs": {
    "op": "log3",
    "data_ptr": "0",
    "data_size": "32",
    "topics": ["topic0", "from", "to"],
    "data_memory": {},
    "topic_memory_reads": [],
    "sink_resolution": {}
  }
}
```

`data_memory` 来自 MemorySSA；`topic_memory_reads` 记录 topic 表达式内部的 mload；`sink_resolution` 保存不同 path 下的数据候选。

### 7.11 Branch、Return、Revert 与 ControlTransfer

Branch：

```json
{
  "kind": "Branch",
  "attrs": {
    "condition": "iszero(check)",
    "condition_normalized": "check == 0",
    "condition_evaluation": {},
    "condition_final_temp": "__sseir_eval_4",
    "language": "yul",
    "path_states": ["entry"]
  }
}
```

Return/Revert：

```json
{
  "kind": "Revert",
  "attrs": {
    "payload_ptr": "0x1c",
    "payload_size": "0x04",
    "payload_memory": {},
    "payload_memory_partial": {},
    "sink_resolution": {},
    "path_states": ["check == 0"]
  }
}
```

`payload_memory` 是完整 memory 查询；`payload_memory_partial` 用于非对齐 selector 等局部恢复。`payload` 只在 Solidity AST 已直接提供错误表达式时出现。

`ControlTransfer` 记录 `break/continue/leave`：

```json
{
  "kind": "ControlTransfer",
  "attrs": {
    "op": "break",
    "language": "yul",
    "cfg_node_id": 10,
    "path_states": ["t == 0"]
  }
}
```

### 7.12 BranchMaterialization

分支物化 effect 记录预处理算法的结构结果：

```json
{
  "kind": "BranchMaterialization",
  "attrs": {
    "assembly_block": 1,
    "sink_node": 8,
    "sink_target": "sload",
    "baseline": {},
    "branches": [],
    "shared_node_ids": [],
    "forced_node_ids": [],
    "discarded_unknown_count": 1,
    "mode": "path_sensitive",
    "policy": "semantic_sink_branch_materialization"
  }
}
```

它记录不同 condition 下哪些 SSA/memory 定义必须随 sink 一起物化，不表示新的 EVM opcode。

## 8. 规范 Memory/Sink 语义输入

标准 S-SEIR 不导出 MemorySSA 和 SinkResolver 的查询状态，而是将查询结果投影为
`semantic_inputs` 或 `memory_semantics`。

### 8.1 semantic_inputs

`semantic_inputs` 用于 `MemoryHash/EventLog/Return/Revert/Call` 等语义终点：

```json
{
  "sink_kind": "MemoryHash",
  "paths": [
    {
      "condition": "condition3",
      "arguments": {
        "slot": "keccak256(abi.encode(from, k.slot))"
      }
    }
  ]
}
```

| 字段 | 含义 |
| --- | --- |
| `sink_kind` | 消费该 memory 输入的语义终点 |
| `paths[].condition` | 该输入成立的 CFG 条件；`entry` 不显式输出 |
| `paths[].arguments` | 按 `slot/data/payload/input` 角色归一化后的语义值 |
| `paths[].unresolved_reason` | 某条路径确实无法确定必要输入时的保守原因 |

已解析的路径不记录 `status=resolved`。只有真正不能确定语义时，才保留
`unresolved_reason`。

### 8.2 memory_semantics

对不属于统一 sink 的 memory read，记录其规范化来源：

```json
{
  "memory_semantics": [
    {
      "role": "memory_read",
      "paths": [
        {
          "condition": "guard",
          "values": ["user", "balances.slot"]
        }
      ]
    }
  ]
}
```

这里不保存 memory version、alias 查找过程、字节单元和查询完整性变化。

### 8.3 Debug-only Memory 查询结构

以下结构只在 `--debug-output` 中保留，不属于默认语义模型。

memory sink 常见查询字段：

```json
{
  "ptr": "ptr",
  "size": "64",
  "complete": true,
  "has_unknown": false,
  "words": [],
  "byte_slice": {},
  "path_states": [],
  "loop_facts": [],
  "top_facts": [],
  "function_memory_bridge": []
}
```

| 字段 | 含义 |
| --- | --- |
| `ptr/size` | 查询的 memory range |
| `complete` | 查询区间是否全部有 reaching definition |
| `has_unknown` | 是否包含未知来源 |
| `words` | 按 32-byte word 组织的结果 |
| `byte_slice` | 按 byte-axis 组织的精确结果 |
| `path_states` | 查询所覆盖的执行路径 |
| `loop_facts` | loop lazy materialization/summary |
| `top_facts` | 无法建模的局部 MemoryTop |
| `function_memory_bridge` | 前一 assembly block 的安全继承记录 |
| `query_kind` | MemoryReadResult/MemoryByteSliceResult 等查询类别 |
| `owner/tracker_scope` | 查询由 S-SEIR 或底层 MemorySSA 的哪一层产生 |
| `assembly_block/node_id` | 查询所在 assembly 和 CFG node |
| `normalized_pointer` | pointer 的线性归一化结果 |
| `address` | base、offset 等地址分解 |
| `role/reason` | sink 参数角色及发起查询的原因 |
| `has_phi` | 结果是否含 MemoryPhi |
| `empty_range` | 查询长度是否为零 |
| `loop_alignment/function_loop_context` | 查询与函数级 loop 的对应关系 |
| `overridden_by_call_output` | 普通 memory reaching definition 是否被更晚的 call output 覆盖 |

#### 8.3.1 word 字段

```json
{
  "offset": 0,
  "address_key": "ptr",
  "value": "account",
  "memory_ssa": "mem_1",
  "value_versions": ["account__ssa1"]
}
```

#### 8.3.2 byte_slice 字段

```json
{
  "query_kind": "MemoryByteSliceResult",
  "pointer": "0x0c",
  "length": "0x20",
  "size": 32,
  "complete": true,
  "slices": [
    {
      "query_offset": 0,
      "size": 20,
      "source_value": "user",
      "source_offset": 12,
      "source_version": "mem_user",
      "extraction": "low_bytes(user, 20)",
      "known_value": {
        "known": true,
        "kind": "symbolic_known",
        "expr": "user"
      }
    }
  ]
}
```

byte-axis 按实际写入顺序逐字节覆盖，因此适合处理 `mstore(0x0c, seed)` 后又执行 `mstore(0x00, user)` 的重叠 memory。

`known=true` 只表示表达式来源可继续追踪，不表示值在编译期可求出。

每个 `byte_slice.slices[]` 的字段：

| 字段 | 含义 |
| --- | --- |
| `query_offset/size` | 该片段在查询区间中的起点和字节数 |
| `source_value` | 原写入值 |
| `source_offset/source_width` | 从原值的哪个字节开始截取及原值宽度 |
| `source_range` | 对应原值的字节范围 |
| `source_version` | 产生这些字节的 MemorySSA version |
| `source_node_id/origin_src` | 原写入 CFG node 和 source mapping |
| `source_kind` | mstore/mstore8/copy 等写入类别 |
| `extraction` | `low_bytes/high_bytes/byte_slice` 等语义表达式 |
| `overlap_count` | 该片段经历的可达覆盖候选数量 |
| `known_value` | 来源可追踪性及 normalized expression |

## 9. Debug-only SinkResolver 子结构

SinkResolver 是 MemorySSA 的补充查询层。它为 sink 的每个参数建立逐路径解析。
以下完整结构只出现在 `--debug-output`，标准输出使用第 8.1 节的 `semantic_inputs`：

```json
{
  "sink_kind": "EventLog",
  "path_sensitive": true,
  "path_resolutions": [
    {
      "condition": "!(cond)",
      "status": "resolved",
      "arg_resolutions": {
        "data": {
          "raw": "0",
          "normalized": "MemorySlice(a)",
          "memory_slice": {}
        }
      }
    },
    {
      "condition": "cond",
      "status": "resolved",
      "arg_resolutions": {
        "data": {
          "raw": "0",
          "normalized": "MemorySlice(b)",
          "memory_slice": {}
        }
      }
    }
  ]
}
```

| 字段 | 含义 |
| --- | --- |
| `sink_kind` | 被解析的 effect 类型 |
| `path_sensitive` | 不同路径是否得到不同语义结果 |
| `path_resolutions` | 每条 condition 下的解析结果 |
| `condition` | 该候选成立的路径 |
| `status` | `resolved/unresolved` |
| `arg_resolutions` | 按参数角色保存解析结果 |
| `memory_slice` | 参数对应的精确 memory 来源 |

MemorySSA 结果完整时优先使用 MemorySSA；SinkResolver 不会覆盖或删除原始查询。

## 10. SemanticOverlay 层

Overlay 将一个或多个 effect 按严格模式解释为更高级语义。

### 10.1 SemanticOverlay 固定字段

```json
{
  "overlay_id": "ov_4",
  "kind": "MappingRead",
  "effects": ["eff_hash", "eff_sload"],
  "stmt_refs": ["asm_s_1", "asm_s_2", "asm_s_3"],
  "attrs": {}
}
```

| 字段 | 含义 | 生成方式 |
| --- | --- | --- |
| `overlay_id` | overlay 唯一编号 | `ov_N` 分配器 |
| `kind` | 高级语义种类 | 模式匹配结果 |
| `effects` | 支撑该语义的 effect ids | 按 backward tracing 证据链收集 |
| `stmt_refs` | 对应原始语句 | 合并 supporting effects 的来源 |
| `attrs` | 高级语义字段 | 各 overlay builder 填充 |

### 10.2 Storage 类 overlay

#### MappingSlot

表示某个 MemoryHash 已被 storage sink 激活并恢复为 mapping slot。

主要字段：

| 字段 | 含义 |
| --- | --- |
| `expression` | 恢复后的 slot 表达式 |
| `key` | 当前 mapping key |
| `base/base_key` | mapping 根 slot 或上一维 slot |
| `state_variable` | storage layout 对应变量 |
| `slot_kind` | `mapping_slot/nested_mapping_slot/storage_ref_mapping_slot` 等 |
| `target/target_keys` | MemoryHash 结果变量及 SSA keys |
| `result_type` | 本维 mapping value 类型 |
| `terminal_storage_value` | 是否已经到达非 mapping value |
| `activation` | 固定为 storage consumer 触发 |

示例：

```json
{
  "kind": "MappingSlot",
  "effects": ["eff_hash"],
  "attrs": {
    "expression": "balances[account]",
    "key": "account",
    "base": "balances.slot",
    "state_variable": "balances",
    "slot_kind": "mapping_slot",
    "target": "slotHash",
    "result_type": "uint256",
    "terminal_storage_value": true,
    "activation": "storage_consumer"
  }
}
```

#### MappingRead / MappingWrite

主要字段：

```text
access                balances[account]
key                   account
slot/slot_key         原始 slot 和 SSA key
slot_versions         所有 reaching slot versions
slot_effect           支撑 slot 的 MemoryHash effect
state_variable        balances
target                读取接收变量
value/value_yul       写入值的 normalized/raw 形式
solidity_like         审计投影
```

示例：

```json
{
  "kind": "MappingRead",
  "attrs": {
    "access": "balances[account]",
    "key": "account",
    "state_variable": "balances",
    "target": "result",
    "slot": "slotHash",
    "slot_versions": ["slotHash__ssa1"],
    "solidity_like": "result = balances[account];"
  }
}
```

Nested mapping 会逐维追踪：

```text
allowances.slot
  -> keccak256(owner, allowances.slot) = allowances[owner]
  -> keccak256(spender, 上一维slot) = allowances[owner][spender]
```

#### StateVariableRead / StateVariableWrite

用于直接 state slot、manual constant slot 和 manual packed hash slot。

| 字段 | 含义 |
| --- | --- |
| `access` | 状态访问表达式 |
| `state_variable` | 已知状态变量名；手工 slot 可为空 |
| `storage_model` | `direct_state_slot_alias/manual_constant_slot/manual_packed_hash_slot` |
| `slot_constant/slot_value` | 固定 slot 名和值 |
| `slot_derivation` | packed hash 等 slot 推导结构 |
| `state_access` | 是否属于合约状态访问 |
| `state_mutation` | 是否修改状态 |
| `mutation_kind` | 写入时为 `state_write` |
| `variable_name_inferred` | 是否推断了变量名；manual slot 通常为 false |

示例：

```json
{
  "kind": "StateVariableWrite",
  "attrs": {
    "access": "storage[_OWNER_SLOT]",
    "value": "newOwner",
    "slot_constant": "_OWNER_SLOT",
    "storage_model": "manual_constant_slot",
    "state_access": true,
    "state_mutation": true,
    "mutation_kind": "state_write",
    "variable_name_inferred": false
  }
}
```

#### PathConditionedStorageRead / Write

同一 storage sink 有多个 slot SSA 来源时，使用候选数组：

```json
{
  "kind": "PathConditionedStorageWrite",
  "attrs": {
    "slot_versions": ["slot__ssa1", "slot__ssa2"],
    "value": "amount",
    "candidates": [
      {
        "status": "resolved",
        "condition": "cond",
        "access": "balances[to]",
        "overlay_kind": "MappingWrite",
        "slot_keys": ["slot__ssa1"]
      },
      {
        "status": "unresolved",
        "condition": "!(cond)",
        "access": "storage[slot__ssa2]",
        "unresolved_reason": "unknown_storage_slot_version"
      }
    ]
  }
}
```

不同 condition 和 unresolved candidate 不合并。只有 condition、access、value 等高级语义完全相同的 resolved candidate 才合并，并保留全部 `slot_keys`。

### 10.3 Revert、Require 与 Return overlay

#### RequireOverlay

来源：Yul condition 下的空 payload `revert(0,0)`。

主要字段：

```text
condition               require 中使用的否定条件
nearest_condition       最近的 Yul condition
control_path            完整外层路径
require_conditions      合并使用的条件
discarded_before_revert revert 前被语义消除的 scratch 操作
revert_payload          原 Revert payload
require_like            solidity-like 文本
```

```json
{
  "kind": "RequireOverlay",
  "attrs": {
    "condition": "check != 0",
    "nearest_condition": "check == 0",
    "path_states": ["check == 0"],
    "require_like": "require(check != 0);"
  }
}
```

#### CustomErrorRevert / PathConditionedCustomErrorRevert

Custom error selector 来自 revert memory slice，并与 selector registry 完整匹配：

```json
{
  "kind": "CustomErrorRevert",
  "attrs": {
    "selector": "0x82b42900",
    "error": "Unauthorized()",
    "revert_like": "revert Unauthorized();",
    "selector_match": {
      "match_status": "matched",
      "preferred_kind": "error"
    }
  }
}
```

不同 path 对应不同 selector 时使用 `candidates[{condition, selector, error, solidity_like}]`。

#### RawRevertBytes

用于识别 bytes memory 数据区被原样作为 REVERT payload 抛出的模式：

```yul
let size := mload(returndata)
revert(add(returndata, 32), size)
```

```json
{
  "kind": "RawRevertBytes",
  "attrs": {
    "source_object": "returndata",
    "payload": "returndata[0:returndata.length]",
    "guard": "returndata.length > 0",
    "sink_resolution": {}
  }
}
```

该语义不会投影为 `revert(string(returndata))`，因为后者会重新 ABI 编码，不能原样转发任意 revert bytes。

#### RawReturnData

表示 Yul `return(ptr,size)` 的原始 EVM 返回语义：

```json
{
  "kind": "RawReturnData",
  "attrs": {
    "payload_ptr": "0",
    "payload_size": "32",
    "payload_memory_complete": true,
    "encoding_hint": "abi_word",
    "values": ["x"],
    "solidity_like": "returnRawAbiWord(x);",
    "solidity_equivalent": false,
    "reason": "yul_return_terminates_current_evm_call_with_raw_return_data"
  }
}
```

它不直接改写为 `return x`，因为 Yul return 会终止当前 EVM call。

### 10.4 Event overlay

#### EventEmit

主要字段：

| 字段 | 含义 |
| --- | --- |
| `event` | 匹配的事件名，未匹配为 `unknownEvent` |
| `signature` | 事件规范签名 |
| `topic0` | 实际/期望 topic0 |
| `topics/raw_topics` | 归一化和原始 topics |
| `args` | 恢复后的 event 参数 |
| `data` | 非 indexed data words |
| `argument_state_reads` | event 参数中的 sload 语义 |
| `argument_memory_reads` | event topic 中的 mload 解析 |
| `topic_constants` | topic 常量名到值的解析 |
| `topic0_near_misses` | 仅前缀相近的诊断，不视为匹配 |
| `emit_like` | solidity-like emit 文本 |

```json
{
  "kind": "EventEmit",
  "attrs": {
    "event": "Transfer",
    "signature": "Transfer(address,address,uint256)",
    "args": ["from", "to", "amount"],
    "emit_like": "emit Transfer(from, to, amount);",
    "topic0_near_misses": []
  }
}
```

非 anonymous event 必须完整匹配 32-byte topic0，且 topic 数量等于 indexed 参数数加一。前 4 bytes 相同不能恢复为已知事件。

#### PathConditionedEventEmit

```json
{
  "kind": "PathConditionedEventEmit",
  "attrs": {
    "event": "Transfer",
    "candidates": [
      {"condition": "cond", "values": ["a"], "solidity_like": "emit Transfer(a);"},
      {"condition": "!(cond)", "values": ["b"], "solidity_like": "emit Transfer(b);"}
    ],
    "notes": ["path_sensitive_sink_resolution"]
  }
}
```

### 10.5 Call overlay

#### LowLevelCall / StaticCallOverlay / DelegateCallOverlay

这些 overlay 保留低层调用形式，不猜测目标合约类型：

```json
{
  "kind": "LowLevelCall",
  "attrs": {
    "target": "token",
    "target_solidity": "token",
    "gas": "gas()",
    "value": "0",
    "input_ptr": "add(ptr, 0x1c)",
    "input_size": "0x44",
    "output_ptr": "0",
    "output_size": "32",
    "result": "ok",
    "selector": "0xa9059cbb",
    "selector_signature": "transfer(address,uint256)",
    "arguments": ["to", "amount"],
    "selector_match": {},
    "solidity_like": "ok = yulCall(...);"
  }
}
```

`selector_match` 结构：

```text
selector
selector_source
preferred_kind
best_match
matches
match_status = matched/unmatched
```

未匹配时保留 selector，不编造签名。

#### AbiCallDataConstruction / AbiEncodedLowLevelCall

`AbiCallDataConstruction` 记录调用前 calldata 的构造过程：

```json
{
  "kind": "AbiCallDataConstruction",
  "attrs": {
    "base": "ptr",
    "input_ptr": "add(ptr, 0x1c)",
    "input_size": "0x44",
    "start_offset": 28,
    "selector": "0xa9059cbb",
    "signature": "transfer(address,uint256)",
    "arguments": [
      {"index": 0, "value": "to", "type": "address"},
      {"index": 1, "value": "amount", "type": "uint256"}
    ],
    "source_writes": ["eff_mstore_selector", "eff_to", "eff_amount"],
    "complete": true,
    "construction_model": "memory_writes_before_call"
  }
}
```

`AbiEncodedLowLevelCall` 在原调用 overlay 上附加 `abi_calldata`、selector 和 arguments，形成可审计的低层 Solidity call 表达式。

#### Path-conditioned call

不同 path 构造不同 calldata 时，使用：

```text
PathConditionedLowLevelCall
PathConditionedStaticCallOverlay
PathConditionedDelegateCallOverlay
PathConditionedPrecompileCall
```

共同字段为调用参数和 `candidates`。每个 candidate 保存 `status`、`condition`、`memory_slice`、selector、arguments、precompile 和 `solidity_like`。

#### PrecompileCall / PrecompileOutputRead

固定地址 staticcall 可识别为 Ethereum precompile：

```json
{
  "kind": "PrecompileCall",
  "attrs": {
    "precompile": "sha256",
    "target": "2",
    "input_words": ["bytes20(msg.sender)"],
    "native_precompile": {
      "solidity_like": "bytes32 hash = sha256(abi.encodePacked(bytes20(msg.sender)));",
      "output_word_expression": "uint256(hash)"
    }
  }
}
```

后续 `mload(output_ptr)` 若满足顺序、路径和无覆盖条件，生成 `PrecompileOutputRead`；多路径则生成 `PathConditionedPrecompileOutputRead`。

### 10.6 Memory object、struct 和 array overlay

#### MemoryRegionAllocate / MemoryRegionWrite

表示从 `mload(0x40)` 出发并更新 free-memory pointer 的手工内存分配：

```json
{
  "kind": "MemoryRegionAllocate",
  "attrs": {
    "base": "mload(0x40)",
    "new_free_pointer": "end",
    "start_node": 1,
    "end_node": 8,
    "write_effect": "eff_free_ptr",
    "stored_values": [],
    "merged_effects": []
  }
}
```

`MemoryRegionWrite` 将某次 mstore 关联到该分配区：`region_base`、`region_allocation_effect`、`address`、`value`。

#### StructInitializationFragment

当分配区内的字段写入与 AST 中某个 memory struct layout 匹配时记录：

```json
{
  "kind": "StructInitializationFragment",
  "attrs": {
    "target": "p",
    "type": "_PackedLogs memory",
    "struct_type": "_PackedLogs",
    "allocation": "ov_alloc",
    "fields": [],
    "solidity_equivalent": "not_exact",
    "reason": "manual_memory_layout_or_custom_allocator"
  }
}
```

#### StructFieldRead / StructFieldWrite / StructMutationFragment

字段必须依据 TypeEnv 中真实 struct 定义和固定 offset 匹配：

```json
{
  "kind": "StructFieldRead",
  "attrs": {
    "struct_object": "p",
    "struct_type": "_PackedLogs",
    "field": {"name": "logs", "type_string": "uint256[]", "offset": 0},
    "read_from": "p",
    "value": "logs",
    "solidity_like": "logs = p.logs;"
  }
}
```

`StructMutationFragment` 汇总一个局部操作区域中对同一 struct 的字段修改，但不要求整个函数只执行 struct mutation。

#### MemoryArrayLengthRead / MemoryArrayElementRead

只在 TypeEnv 已确认参数或 struct field 是 memory array 时恢复：

```text
mload(array)                 -> array.length
mload(add(array, i * 32))    -> array[index]
```

主要字段包括 `array`、`array_type`、`element_type`、`access`、`offset/index`、`memory_read`、`source_expression` 和 `target`。

#### CursorBasedMemoryWrite

表示通过 struct 中保存的 cursor 指针写入数据，并同步推进 cursor：

```json
{
  "kind": "CursorBasedMemoryWrite",
  "attrs": {
    "struct_object": "p",
    "struct_type": "_PackedLogs",
    "cursor_field": "offset",
    "memory_write": {
      "effect": "eff_write",
      "address": "offset",
      "value": "packedValue"
    },
    "field_updates": [],
    "semantic_hint": "write_word_then_advance_struct_cursor"
  }
}
```

该 overlay 依赖已经确认的 struct field layout、写入地址与 cursor 字段来源，不因变量名类似 `ptr/offset` 就进行推断。

#### MemoryArrayConstruction

从返回值和 CFG sink 反向匹配：free-memory pointer、数据起点、元素写入、length 写入、free pointer 更新。

```json
{
  "kind": "MemoryArrayConstruction",
  "attrs": {
    "result": "ordinals",
    "array_type": "uint8[] memory",
    "element_type": "uint8",
    "base": "ordinals",
    "data_start": "ordinals + 32",
    "length_expr": "(ptr - (ordinals + 32)) / 32",
    "element_writes": [],
    "free_memory_pointer_update": "ptr",
    "path_states": [],
    "solidity_equivalent": false,
    "reason": "manual_dynamic_memory_array_construction"
  }
}
```

### 10.7 其他值语义 overlay

| kind | 主要字段 | 模式 |
| --- | --- | --- |
| `BytesContentHash` | object、data pointer、length、hash input、result | `keccak256(object+32, mload(object))` 且类型为 bytes/string |
| `AddressCodeSize` | target、address、code_size | `target := extcodesize(address)` |
| `AddressHasCode` | target、address、condition | bool target 接收 extcodesize |
| `AddressZeroCheck` | variable、check、condition、projection | 标准 address 清洗/零地址 Yul 模式 |
| `CalldataWordRead` | target、offset、width_bytes | `target := calldataload(offset)` |
| `StoragePointerSlotBinding` | pointer、slot、slot_normalized | storage reference 返回值的 `.slot := value` |
| `ExpressionNormalization` | expression、normalized、division_guards | 普通算术/比较/位运算归一化 |
| `EvaluationStep` | temp、call、args、order、solidity_like | effect 原子步骤的高级投影 |

BytesContentHash 示例：

```json
{
  "kind": "BytesContentHash",
  "attrs": {
    "object": "data",
    "object_type": "bytes memory",
    "data_pointer": "add(data, 32)",
    "data_pointer_normalized": "data.data",
    "length_expression": "mload(data)",
    "length_normalized": "data.length",
    "hash_input": "data",
    "hash_algorithm": "keccak256",
    "result_expression": "keccak256(data)",
    "via_value_defs": [],
    "exact_solidity_equivalent": true
  }
}
```

若 pointer/length 有多个不等价 SSA 来源，或 TypeEnv 不能确认对象类型，则不生成该 overlay。

## 11. AnalysisFacts

Analysis fact 是分析过程记录，不等同于合约运行语义。每项至少有 `kind`，其余字段由 kind 决定。

### 11.1 MemorySSAQueryLayer

```json
{
  "kind": "MemorySSAQueryLayer",
  "assembly_block": 1,
  "scope": "s_seir_function_memoryssa_view",
  "function_loop_context": [],
  "loop_alignment": "s_seir_controls_all_loop_contexts_legacy_yul_memoryssa_backend",
  "features": ["path_states", "linear_aliases", "byte_axis_memory_slice"]
}
```

说明当前 assembly 使用的 MemorySSA 能力和外层 loop 上下文。

### 11.2 SemanticSinkMemoryQuery

```json
{
  "kind": "SemanticSinkMemoryQuery",
  "effect": "eff_call",
  "effect_kind": "Call",
  "query_field": "input_memory",
  "memory_query_complete": false,
  "memory_query_has_unknown": true,
  "final_status": "resolved",
  "resolved_by": "sink_resolver",
  "resolution_overlays": ["ov_abi"],
  "resolution_roles": [],
  "path_states": []
}
```

`resolved_by` 可为：

```text
memory_ssa
sink_resolver
semantic_overlay_pattern
expression_role
null
```

即使高级模式成功，`memory_query_has_unknown` 仍保存原始查询状态。

### 11.3 辅助表

| kind | 内容 | 来源 |
| --- | --- | --- |
| `SSEIRStructTable` | struct 名、字段、类型、offset | solc AST |
| `SSEIRConstantTable` | 常量名、类型、表达式和值 | solc AST |
| `SSEIRSelectorRegistry` | selector 到 function/error 签名 | AST + 经哈希校验的注释签名 |
| `SSEIRBranchPreprocess` | 原源码、分析源码、rewrite/import 信息 | 分支预处理阶段 |
| `SSEIRCanonicalization` | 统一规范化过程说明 | SemanticNormalizer |
| `BranchMaterialization` | 分支物化事实 | branch materialization |
| `LoopMemoryRecord` | loop 结构、bound 和 memory effect 模板 | loop-aware lazy MemorySSA |
| `MemoryPhi` | 固定地址 loop-carried reaching definitions | loop lazy materialization |
| `MemoryRangeSummary` | 符号次数 affine memory range | loop lazy materialization |
| `MemoryTop` | 无法可靠建模的局部 memory 区域及原因 | MemorySSA 保守退化 |
| `UnresolvedOverlay` | 未解析 overlay 及原因 | SemanticNormalizer |

`SSEIRBranchPreprocess` 示例：

```json
{
  "kind": "SSEIRBranchPreprocess",
  "original_source": "/src/Token.sol",
  "analysis_source": "/out/branch_preprocessed/Token.sol",
  "rewrite_count": 2,
  "enabled": true,
  "flattened_imports": true,
  "imported_files": ["Base.sol"],
  "preprocessed_files": ["Token.sol", "Base.sol"]
}
```

Analysis fact 的其他字段：

| 字段 | 含义 |
| --- | --- |
| `has_phi` | memory query 是否经过 MemoryPhi |
| `empty_range` | sink 查询区间长度是否为零 |
| `semantic_value` | expression role 已恢复出的语义值 |
| `selector_count/selectors` | registry 中 selector 数量和完整索引 |
| `structs` | AST struct 定义表 |
| `state_variables` | 当前函数可见状态变量及 storage slot |
| `legacy_adapter_used` | canonicalization 是否使用旧结果搬运；当前 native S-SEIR 为 false |
| `text_ir` | BranchMaterialization 的可读文本表示 |

## 12. SecurityFact

Security facts 是从 overlay 提取的审计摘要，不是源码恢复的核心证据。

固定字段：

```json
{
  "fact_id": "fact_1",
  "kind": "StateUpdate",
  "attrs": {},
  "source_overlays": ["ov_write"],
  "source_effects": ["eff_sstore"],
  "stmt_refs": ["asm_s_4"]
}
```

| 字段 | 含义 |
| --- | --- |
| `fact_id` | fact 唯一编号 |
| `kind` | 安全行为种类 |
| `attrs` | 摘要字段 |
| `source_overlays` | 来源高级语义 |
| `source_effects` | 来源底层 effect |
| `stmt_refs` | 来源语句 |

当前主要 kind：

```text
BalanceRead
StateRead
AllowanceUpdate
BalanceUpdate
StateUpdate
TransferEvent
EventEmission
ExternalCall
GuardCondition
TransparentRevertBubble
ExternalCallBeforeStateUpdate
EventStateLink
```

Security fact 常用 attrs：

| 字段 | 含义 |
| --- | --- |
| `access/state_var` | 状态访问表达式及根变量 |
| `account/owner/spender` | ERC-20 行为涉及的账户角色 |
| `value/delta` | 写入值或余额变化量 |
| `event/signature` | 事件名和签名 |
| `call/call_type/target/selector` | 外部调用摘要 |
| `condition/on_fail` | guard 条件及失败行为 |
| `state_update` | 与事件或调用关联的状态更新 fact |

## 13. 字段消费规则

后续 LLM 或审计工具建议按以下顺序读取：

```text
1. 读取 function_id/signature 和 function_source。
2. 用 source_statements 建立 stmt_id 到源码的索引。
3. 用 control/path_states 理解语句的条件和循环归属。
4. 读取 effects，确认真实 memory/storage/call/revert 行为。
5. 读取 semantic_overlays，获得已经完成模式验证的高级语义。
6. 通过 overlay.effects 和 stmt_refs 回查证据。
7. 对 unresolved/unknown 保守保留 assembly，不自行补全缺失事实。
8. security_facts 只作为审计索引，不替代 effect/overlay。
```

必须遵守：

```text
event near-miss 不能视为已知事件；
unmatched selector 不能补成函数签名；
不同 condition 下的 SSA candidate 不能按变量名合并；
manual slot 不应擅自命名为源码状态变量；
RawReturnData/RawRevertBytes 不应强行改成不等价的 Solidity return/revert；
solidity_like 只能作为展示参考。
```

## 14. 字段生成顺序

同一函数字段按以下 Pass 顺序产生：

| 顺序 | Pass | 主要输出 |
| --- | --- | --- |
| 0 | 分支预处理与 solc 编译 | `analysis_source`、AST、`SSEIRBranchPreprocess` |
| 1 | SourceStatementCollector | 函数上下文、变量、`source_statements`、struct/constant 表 |
| 2 | ControlBuilder | `control.blocks/edges/loop_contexts` |
| 3 | build_memory_ssa_views | MemorySSA、alias、byte-axis、loop summary、跨 assembly bridge |
| 4 | ExpressionRoleAnalyzer | 第一批 `expr_roles` |
| 5 | EffectLifter | `effects`、memory query、原子求值步骤 |
| 6 | Branch materialization | `BranchMaterialization` effect/fact |
| 7 | SinkResolver | effect 中的 `sink_resolution` |
| 8 | SemanticOverlayBuilder | `semantic_overlays` |
| 9 | SemanticNormalizer | 补充 roles、`SemanticSinkMemoryQuery`、unresolved facts |
| 10 | SecurityFactBuilder | `security_facts` |
| 11 | Export | 标准 JSON、批处理 JSON、CFG DOT、solidity-like |

因此，读取某个高级 overlay 时，可以稳定地沿以下方向回溯：

```text
overlay
  -> effects
  -> memory query / SSA versions / path_states
  -> stmt_refs
  -> source_statements
  -> 原始 Solidity/Yul 源码
```

## 15. kind 特有可选字段字典

以下字段只在相关 effect/overlay 中出现。它们仍位于节点的 `attrs` 内，不是所有节点的固定字段。

### 15.1 表达式、类型与归一化

| 字段 | 含义 | 主要来源 |
| --- | --- | --- |
| `address_normalized` | 地址表达式的 normalized 形式 | AddressCode overlay |
| `target_type` | 左值或返回目标的 Solidity 类型 | TypeEnv |
| `variable_type` | 被检查变量的 Solidity 类型 | AddressZeroCheck |
| `data_location` | `memory/storage/calldata` | TypeEnv |
| `offset_normalized` | calldata/memory offset 的 normalized 表达式 | CalldataWordRead |
| `hash_expression` | 完整 hash 表达式 | BytesContentHash/ExpressionNormalization |
| `memory_hash` | 与普通表达式关联的 MemoryHash 摘要 | ExpressionNormalization |
| `projection_expression` | address zero-check 等规则投影后的表达式 | AddressZeroCheck |
| `source_pattern` | 命中的底层模式名称 | AddressZeroCheck 等模式 overlay |
| `used_by` | 使用该归一语义的 effect/overlay | 语义关联阶段 |

### 15.2 ABI、call 与 precompile

| 字段 | 含义 |
| --- | --- |
| `call_overlay_kind` | path-conditioned candidate 原本对应的调用 overlay 类型 |
| `input_size_yul` | 原始 Yul input-size 表达式 |
| `input_size_expression` | 归一化或推导后的 input-size 表达式 |
| `output_size_yul` | 原始 Yul output-size 表达式 |
| `head_words` | ABI calldata head 中恢复出的 word 列表 |
| `raw_memory_writes` | 构造 calldata 的原始 MemoryWrite 记录 |
| `selector_write_effect` | 写入 selector 的 MemoryWrite effect id |
| `source_precompile_overlay` | precompile output 的单一来源 overlay |
| `source_precompile_overlays` | 多路径 precompile output 的来源 overlay 列表 |
| `pointer_kind` | 特殊输出指针类别，例如 `returndatasize` |
| `returndatasize_source_calls` | 可产生当前 returndatasize 的前置 call effects |
| `elided_by_native_precompile` | 展示时是否被原生 precompile 语义替代 |

### 15.3 Memory、loop、array 与 struct

| 字段 | 含义 |
| --- | --- |
| `allocation_source` | memory object/array 起始指针的定义来源 |
| `read_effect` | 读取 free-memory pointer 或字段的 effect id |
| `free_memory_pointer_write` | 更新 `memory[0x40]` 的 MemoryWrite effect id |
| `cursor_variables` | 动态 array/struct cursor 变量名集合 |
| `cursor_defs` | cursor 的 ValueDef 及 normalized value |
| `element_write_pattern` | `indexed` 或 `cursor_based` 元素写入模式 |
| `length_write` | 写入动态数组 length 的 MemoryWrite effect id |
| `length_expr_normalized` | 数组 length 的 normalized 表达式 |
| `derived_from_struct_field` | array 语义是否来自已确认的 struct field |
| `mutations` | struct 字段修改列表 |
| `related_memory_writes` | 与 struct cursor 修改关联的数据写入 |
| `packed_semantics` | byte-axis slice 拼接得到的 packed 表达式列表 |
| `resolved_inputs` | MemoryHash/sink 已确认的输入表达式 |
| `partial_slices` | 不完整 return/revert payload 的已知局部 slice |
| `discarded_unknown_paths` | 分支物化时被保守丢弃的 unknown 路径明细 |

### 15.4 Inline storage 与嵌套状态读取

| 字段 | 含义 |
| --- | --- |
| `inline_slot_key` | 为内嵌 `keccak256(...)` 创建的合成 slot SSA key |
| `inline_storage_slot` | MemoryHash 是否直接嵌套在 sload/sstore slot 参数中 |
| `storage_reference` | storage 参数或局部 storage reference 根对象 |
| `storage_reference_type` | storage reference 的 Solidity 类型 |
| `storage_reference_kind` | mapping storage ref 或 struct mapping field 等类别 |
| `storage_field` | storage struct 中匹配的字段定义 |
| `value_state_read` | 写入值内部解析出的 sload 高级语义 |
| `read_expression` | 嵌套状态读取的原始表达式 |
| `consumed_by` | 消费该状态读取结果的父 effect |
| `nested_in_memory_value` | sload 是否嵌套在 mstore value 中 |
| `nested_in_storage_value` | sload 是否嵌套在 sstore value 中 |
| `parent_call` | 嵌套读取所属的父 opcode |
| `parent_effect` | 原子步骤所属的父 effect id |
| `parent_slot` | 父 sstore 的目标 slot |
| `parent_value` | 父表达式的完整 value |
| `parent_memory_address` | 父 MemoryWrite 地址 |
| `parent_memory_value` | 父 MemoryWrite value |
| `parent_memory_version` | 父 MemoryWrite 产生的 MemorySSA version |

### 15.5 Condition、sink 与 payload

| 字段 | 含义 |
| --- | --- |
| `sink_text` | 分支物化语义终点的原始文本 |
| `merged_conditions` | RequireOverlay 合并后的条件集合 |
| `evaluated_require_conditions` | 已按 Yul 求值顺序计算的 require 条件 |
| `path_conditioned_data` | Event data 中存在逐路径来源的 word |
| `payload_ptr_normalized` | return/revert payload 指针的 normalized 形式 |
| `payload_size_normalized` | return/revert payload 长度的 normalized 形式 |
| `source_pointer_expression` | BytesContentHash 追踪前的原始 pointer 表达式 |
| `source_length_expression` | BytesContentHash 追踪前的原始 length 表达式 |

这些字段不能脱离所属 `kind` 单独解释。例如 `target_type` 在 AddressCodeSize 中表示 extcodesize 接收变量类型，在 BytesContentHash 中表示 hash 结果变量类型；消费者应先判断节点 `kind`，再读取 `attrs`。


## 16. 源码与语义模型对照示例

本节使用当前批处理结果中的真实源码和真实 S-SEIR 输出。为突出对应关系，模型片段只保留与当前语义有关的字段；字段值、`stmt_id`、`effect_id` 和 `overlay_id` 均来自实际输出，不是重新编写的伪模型。

阅读一组示例时，应按以下链条理解：

```text
源码语句
  -> SourceStatement.stmt_id
  -> Effect.stmt_refs
  -> SemanticOverlay.effects + SemanticOverlay.stmt_refs
```

所有示例均按当前 Pipeline 的实际顺序生成：

```text
1. SourceStatementCollector
   从 solc AST 收集 Solidity/Yul 语句并分配 stmt_id。

2. ControlBuilder + build_memory_ssa_views
   构造 function-level CFG；为 assembly 内的 value/memory 定义建立 SSA，
   并把 Solidity 分支和循环条件作为 assembly 入口路径前缀。

3. ExpressionRoleAnalyzer + EffectLifter
   原子化表达式，记录 MemoryWrite、MemoryHash、StorageRead、Call、Revert 等 effect。
   这一层只陈述底层行为，不直接声称它是 mapping、event 或 ABI 调用。

4. SinkResolver
   对 MemoryHash、Call、EventLog、Return、Revert 等语义终点统一查询 MemorySSA，
   保留每条 CFG 可达路径的 condition、SSA 版本、字节切片和解析状态。

5. SemanticOverlayBuilder
   只有 effect 链满足既定底层模式时，才生成 MappingRead、EventEmit、
   LowLevelCall、RawReturnData 等高级 overlay。

6. SemanticNormalizer
   整理标准字段和解析状态，但不删除作为证据的底层 effect。
```

因此，“最终语义模型”不是一次字符串替换的结果，而是上述各阶段共同保留下来的 `SourceStatement + Control + Effect + Overlay`。

### 16.1 示例索引

本节后续给出实际输出中的完整关键链：

| 示例 | 源码函数 | 主要记录链 |
| --- | --- | --- |
| A | `TKM.balanceOf(address)` | `MemoryWrite -> MemoryHash -> StorageRead -> MappingSlot/MappingRead` |
| B | `Ownable.requestOwnershipHandover()` | 重叠字节写入、手工 hash 槽位、状态写入和事件 |
| C | `DN404._linkMirrorContract(address)` | calldata 字节切片、Call sink、LowLevelCall 和错误 selector |
| D | `Ownable._checkOwner()` | 原子化 condition、状态读取和路径化自定义错误 |
| E | `DN404._packedLogsMalloc(uint256)` | 自由内存分配和结构体初始化片段 |
| F | `DN404._hasCode(address)` | `extcodesize` 到 `AddressHasCode` |
| G | `DN404._return(uint256)` | 返回区间查询和 `RawReturnData` |

其中示例 A 是 mapping 读取的规范完整示例。`MappingSlot.stmt_refs` 引用 MemoryHash 所在语句，`MappingRead` 通过 `effects` 同时连接 MemoryHash 和 StorageRead；两个 `mstore` 的来源通过 `MemoryHash.attrs.memory_read.words[*].memory_ssa` 回到对应 MemoryWrite。

### 示例 A：mapping 状态读取

实际源码 `TKM.balanceOf(address)`：

```solidity
function balanceOf(address acc) external view returns (uint256 aa) {
    assembly {
        mstore(0, acc)
        mstore(32, l.slot)
        aa := sload(keccak256(0, 64))
    }
}
```

逐步推导：

1. SourceStatementCollector 从 Yul AST 生成 `asm_s_1`、`asm_s_2`、`asm_s_3`。三条语句共享 `function_id = TKM.balanceOf(address)` 和 `block_id = asm_block_5`。
2. MemorySSA 顺 CFG 的 `entry` 路径处理两个 `mstore`：`memory[0:32]` 定义为 `acc`，版本为 `mem_1`；`memory[32:64]` 定义为 `l.slot`，版本为 `mem_2`。
3. EffectLifter 将两个写入提升为 `eff_1/MemoryWrite` 和 `eff_2/MemoryWrite`。在处理 `sload` 参数时，它识别出内联的 `keccak256(0, 64)`，生成 `eff_4/MemoryHash`，随后生成 `eff_5/StorageRead`。
4. `MemoryHash` 是内存语义终点。SinkResolver 在该 CFG 节点查询 `[0, 64)` 的 reaching definitions，得到 `{offset=0, value=acc, memory_ssa=mem_1}` 和 `{offset=32, value=l.slot, memory_ssa=mem_2}`。两段均已知，所以 `complete=true`、`has_unknown=false`。
5. storage overlay 构造器从 `eff_5/StorageRead` 反向激活它实际消费的 hash 版本，而不是按变量文本匹配。hash 输入恰好满足 `key || mapping.slot`，且 storage layout 表明 `l` 是 mapping，因此创建 `ov_1/MappingSlot`。
6. 同一个 storage sink 再与已激活的 MappingSlot 组合，创建 `ov_2/MappingRead`，得到 `access=l[acc]`、`target=aa`。最终模型同时保留包括 ValueDef 在内的全部底层 effects 和两个高级 overlay。

对应的源码语句表：

```json
[
  {
    "stmt_id": "asm_s_1",
    "lang": "yul",
    "text": "mstore(0, acc)",
    "function_id": "TKM.balanceOf(address)",
    "block_id": "asm_block_5"
  },
  {
    "stmt_id": "asm_s_2",
    "lang": "yul",
    "text": "mstore(32, l.slot)",
    "function_id": "TKM.balanceOf(address)",
    "block_id": "asm_block_5"
  },
  {
    "stmt_id": "asm_s_3",
    "lang": "yul",
    "text": "aa := sload(keccak256(0, 64))",
    "function_id": "TKM.balanceOf(address)",
    "block_id": "asm_block_5"
  }
]
```

`mstore` 首先被提升为独立的内存写 effect。`keccak256` 是读取内存的语义终点，因此 `MemoryHash` 从 MemorySSA 查询 `[0, 64)`，得到 key 和 mapping 基槽：

```json
[
  {
    "effect_id": "eff_1",
    "kind": "MemoryWrite",
    "stmt_refs": ["asm_s_1"],
    "attrs": {
      "address": "0",
      "value": "acc",
      "memory_version": "mem_1",
      "write_kind": "mstore",
      "path_states": ["entry"]
    }
  },
  {
    "effect_id": "eff_2",
    "kind": "MemoryWrite",
    "stmt_refs": ["asm_s_2"],
    "attrs": {
      "address": "32",
      "value": "l.slot",
      "memory_version": "mem_2",
      "write_kind": "mstore",
      "path_states": ["entry"]
    }
  },
  {
    "effect_id": "eff_4",
    "kind": "MemoryHash",
    "stmt_refs": ["asm_s_3"],
    "attrs": {
      "ptr": "0",
      "size": "64",
      "value": "keccak256(0, 64)",
      "memory_read": {
        "complete": true,
        "has_unknown": false,
        "words": [
          {"offset": 0, "value": "acc", "memory_ssa": "mem_1"},
          {"offset": 32, "value": "l.slot", "memory_ssa": "mem_2"}
        ]
      }
    }
  },
  {
    "effect_id": "eff_5",
    "kind": "StorageRead",
    "stmt_refs": ["asm_s_3"],
    "attrs": {
      "slot": "keccak256(0, 64)",
      "value": "aa",
      "value_versions": ["aa__ssa1"]
    }
  }
]
```

当且仅当 hash 输入模式完整匹配 `key || stateVariable.slot` 时，overlay 才将其提升为 mapping 访问：

```json
[
  {
    "overlay_id": "ov_1",
    "kind": "MappingSlot",
    "effects": ["eff_4"],
    "stmt_refs": ["asm_s_3"],
    "attrs": {
      "target": "keccak256(0, 64)",
      "expression": "l[acc]",
      "state_variable": "l",
      "key": "acc",
      "base": "l.slot",
      "slot_kind": "mapping_slot",
      "terminal_storage_value": true
    }
  },
  {
    "overlay_id": "ov_2",
    "kind": "MappingRead",
    "effects": ["eff_4", "eff_5"],
    "stmt_refs": ["asm_s_3"],
    "attrs": {
      "access": "l[acc]",
      "target": "aa",
      "state_variable": "l",
      "key": "acc",
      "slot": "keccak256(0, 64)",
      "solidity_like": "aa = l[acc];"
    }
  }
]
```

这里 `MemoryWrite` 和 `MemoryHash` 不会因生成 `MappingRead` 而删除。前者保留底层构造过程，后者记录该高级结论所依赖的实际 hash 输入。

### 示例 B：重叠内存写构造手工 hash 槽位

实际源码 `Ownable.requestOwnershipHandover()`：

```solidity
function requestOwnershipHandover() public payable virtual {
    unchecked {
        uint256 expires = block.timestamp + _ownershipHandoverValidFor();
        assembly {
            mstore(0x0c, _HANDOVER_SLOT_SEED)
            mstore(0x00, caller())
            sstore(keccak256(0x0c, 0x20), expires)
            log2(0, 0, _OWNERSHIP_HANDOVER_REQUESTED_EVENT_SIGNATURE, caller())
        }
    }
}
```

逐步推导：

1. SourceStatementCollector 为两个 `mstore`、`sstore` 和 `log2` 分配 `asm_s_1` 至 `asm_s_4`；外层 Solidity `unchecked` 仍属于同一 function-level Control 上下文。
2. 字节轴 MemorySSA 先把 `_HANDOVER_SLOT_SEED` 的 32 字节写入 `[0x0c, 0x2c)`，再把 `caller()` 的 32 字节写入 `[0x00, 0x20)`。后写入自然成为重叠区域 `[0x0c, 0x20)` 的 reaching definition。
3. EffectLifter 为 `keccak256(0x0c, 0x20)` 创建 `eff_3/MemoryHash`，为其消费者 `sstore` 创建 `eff_4/StorageWrite`，为 `log2` 创建 `eff_5/EventLog`。
4. SinkResolver 查询 hash 区间 `[0x0c, 0x2c)`。按实际覆盖关系，前 20 字节来自 `caller()[12:32]`，后 12 字节来自 `_HANDOVER_SLOT_SEED[20:32]`，所以形成两个连续 byte slice，而不是错误地选择某一个完整 `mstore`。
5. 该 32 字节输入不满足标准 `abi.encode(key, mapping.slot)` 的两个 word 布局，因此 storage overlay 不把它猜成普通 mapping。它匹配手工 packed hash 模式，生成 `StateVariableWrite`，并设置 `storage_model=manual_packed_hash_slot`。
6. Event builder 解析 `_OWNERSHIP_HANDOVER_REQUESTED_EVENT_SIGNATURE` 的常量值，以 topic0 匹配源码 AST 中的 event 定义，再把 `caller()` 规范化为 `msg.sender`，生成 `EventEmit`。

第二次 `mstore` 会覆盖第一次写入区间的一部分。字节轴 MemorySSA 对 `keccak256(0x0c, 0x20)` 的真实 32 字节输入记录为：

```json
{
  "effect_id": "eff_3",
  "kind": "MemoryHash",
  "stmt_refs": ["asm_s_3"],
  "attrs": {
    "ptr": "0x0c",
    "size": "0x20",
    "memory_read": {
      "complete": true,
      "has_unknown": false,
      "byte_slice": {
        "has_overlap": true,
        "slices": [
          {
            "query_offset": 0,
            "size": 20,
            "source_value": "caller()",
            "source_range": [12, 32],
            "source_version": "mem_2",
            "extraction": "bytes20(msg.sender)"
          },
          {
            "query_offset": 20,
            "size": 12,
            "source_value": "_HANDOVER_SLOT_SEED",
            "source_range": [20, 32],
            "source_version": "mem_1",
            "extraction": "low_bytes(_HANDOVER_SLOT_SEED, 12)"
          }
        ],
        "packed_semantics": [
          "bytes20(msg.sender)",
          "low_bytes(_HANDOVER_SLOT_SEED, 12)"
        ]
      }
    }
  }
}
```

该模式不是 Solidity storage layout 中的普通 mapping，因此模型不猜测变量名称，而是记录“手工 packed hash 槽位上的状态写入”：

```json
[
  {
    "overlay_id": "ov_1",
    "kind": "StateVariableWrite",
    "effects": ["eff_3", "eff_4"],
    "stmt_refs": ["asm_s_3"],
    "attrs": {
      "access": "storage[keccak256(abi.encodePacked(bytes20(msg.sender), low_bytes(_HANDOVER_SLOT_SEED, 12)))]",
      "slot": "keccak256(0x0c, 0x20)",
      "storage_model": "manual_packed_hash_slot",
      "value": "expires",
      "solidity_like": "storage[keccak256(abi.encodePacked(bytes20(msg.sender), low_bytes(_HANDOVER_SLOT_SEED, 12)))] = expires;"
    }
  },
  {
    "overlay_id": "ov_2",
    "kind": "EventEmit",
    "effects": ["eff_5"],
    "stmt_refs": ["asm_s_4"],
    "attrs": {
      "event": "OwnershipHandoverRequested",
      "topics": [
        "0xdbf36a107da19e49527a7176a1babf963b4b0ff8cde35ee35d6cd8f1f9ac7e1d",
        "msg.sender"
      ],
      "data": []
    }
  }
]
```

### 示例 C：条件中的低级调用及 calldata 反向恢复

实际源码 `DN404._linkMirrorContract(address)`：

```solidity
function _linkMirrorContract(address mirror) internal virtual {
    assembly {
        mstore(0x00, 0x0f4599e5)
        mstore(0x20, caller())
        if iszero(and(eq(mload(0x00), 1), call(gas(), mirror, 0, 0x1c, 0x24, 0x00, 0x20))) {
            mstore(0x00, 0xd125259c)
            revert(0x1c, 0x04)
        }
    }
}
```

逐步推导：

1. MemorySSA 分别记录 `mstore(0x00, 0x0f4599e5)` 和 `mstore(0x20, caller())`，保留它们的地址、版本和已知值来源。
2. condition 原子化遵守 Yul/EVM dialect 的参数求值顺序。嵌套的 `call(...)` 单独形成 `eff_6/Call`，并标记 `nested_in_condition=true`；随后独立计算 `mload` 和 `eq`，再由 `and` 合并 call 结果与 eq 结果，最后计算 `iszero`。
3. EffectLifter 从 call 参数直接保存 `target=mirror`、`input_ptr=0x1c`、`input_size=0x24`、`output_ptr=0x00` 和 `output_size=0x20`。
4. Call 是内存 sink。SinkResolver 查询 `[0x1c, 0x40)`，把第一个 word 的低 4 字节提取为 `low_bytes(0x0f4599e5, 4)`，再把后一个 write 中的 32 字节提取为 `msg.sender`。查询完整，因此不会产生 `yulMemorySlice` 或伪造未知参数。
5. selector registry 将 `0x0f4599e5` 与 `linkMirrorContract(address)` 匹配。OverlayBuilder 只据此记录 selector/signature/arguments；由于 `mirror` 的具体合约类型无法静态确定，最终生成 `LowLevelCall`，而不是强行恢复成某个类型化接口调用。
6. 失败分支中的 `revert(0x1c, 0x04)` 是另一个 sink。它在该 condition 路径查询到 `mstore(0x00, 0xd125259c)` 的低 4 字节，并通过 selector registry 匹配 `LinkMirrorContractFailed()`，生成路径化错误 overlay。

`call` 是语义终点。它从 `input_ptr = 0x1c` 开始读取 36 字节，而不是从第一次写入的 `0x00` 开始。MemorySSA 按字节切片得到低 4 字节 selector 和后续 32 字节参数：

```json
{
  "effect_id": "eff_6",
  "kind": "Call",
  "stmt_refs": ["asm_s_3"],
  "attrs": {
    "op": "call",
    "gas": "gas()",
    "target": "mirror",
    "value": "0",
    "input_ptr": "0x1c",
    "input_size": "0x24",
    "output_ptr": "0x00",
    "output_size": "0x20",
    "nested_in_condition": true,
    "input_memory": {
      "complete": true,
      "has_unknown": false,
      "byte_slice": {
        "slices": [
          {
            "query_offset": 0,
            "size": 4,
            "source_value": "0x0f4599e5",
            "source_offset": 28,
            "extraction": "low_bytes(0x0f4599e5, 4)"
          },
          {
            "query_offset": 4,
            "size": 32,
            "source_value": "caller()",
            "source_offset": 0,
            "extraction": "msg.sender"
          }
        ]
      }
    }
  }
}
```

selector 匹配和输入恢复成功后，生成低级调用 overlay；它仍然保留 `.call` 语义，不猜测 `mirror` 的静态合约类型：

```json
{
  "overlay_id": "ov_2",
  "kind": "LowLevelCall",
  "effects": ["eff_6"],
  "stmt_refs": ["asm_s_3"],
  "attrs": {
    "op": "call",
    "target_solidity": "mirror",
    "selector": "0x0f4599e5",
    "selector_signature": "linkMirrorContract(address)",
    "arguments": ["caller()"],
    "solidity_like": "__sseir_eval_asm_s_3_2 = yulCall(gas: __sseir_eval_asm_s_3_1, target: mirror, value: 0, input: abi.encodeWithSelector(bytes4(0x0f4599e5) /* linkMirrorContract(address) */, msg.sender), output: memory[0x00:0x20]);"
  }
}
```

同一函数中的 `revert(0x1c, 0x04)` 使用相同的字节切片机制，从条件路径上的 `mstore(0x00, 0xd125259c)` 解析出 `LinkMirrorContractFailed()`，记录为 `PathConditionedCustomErrorRevert`。

### 示例 D：条件、状态读取和自定义错误

实际源码 `Ownable._checkOwner()`：

```solidity
function _checkOwner() internal view virtual {
    assembly {
        if iszero(eq(caller(), sload(_OWNER_SLOT))) {
            mstore(0x00, 0x82b42900)
            revert(0x1c, 0x04)
        }
    }
}
```

逐步推导：

1. ControlBuilder 将 Yul `if` 建成 Branch 节点，true 边携带 `iszero(eq(caller(), sload(_OWNER_SLOT)))`；分支内的 `mstore` 和 `revert` 只在该 path state 下可达。
2. EffectLifter 按表达式树从内到外、按 Yul 参数从右到左原子化 condition：先计算 `sload(_OWNER_SLOT)`，再计算 `caller()`，随后是 `eq`，最后是 `iszero`。
3. `sload(_OWNER_SLOT)` 同时形成 `StorageRead`。TypeEnv 能确认 `_OWNER_SLOT` 是已知手工常量槽，但它不是常规 storage layout 中带变量名的普通 slot，因此 overlay 记录 `StateVariableRead` 和 `storage_model=manual_constant_slot`。
4. `mstore(0x00, 0x82b42900)` 在 true 分支创建 memory 版本；`Revert` effect 保存同一 condition 的 `path_states`，因此不会读取 false 路径或其他分支的内存版本。
5. SinkResolver 对 `revert(0x1c, 0x04)` 查询 4 字节 payload，得到 `low_bytes(0x82b42900, 4)`。selector registry 优先查找 error，匹配到 AST 中的 `Unauthorized()`。
6. 因 sink 带有 condition，OverlayBuilder 生成 `PathConditionedCustomErrorRevert`，把 condition、解析状态、selector 和错误签名放入同一个 candidate 中。

复合 condition 会先原子化。`sload`、`caller()`、`eq` 和 `iszero` 分别形成 EvaluationStep；原始分支仍由 Branch effect 保存：

```json
[
  {
    "effect_id": "eff_3",
    "kind": "StorageRead",
    "stmt_refs": ["asm_s_1"],
    "attrs": {
      "slot": "_OWNER_SLOT",
      "value": "__sseir_eval_asm_s_1_1",
      "path_states": ["entry"]
    }
  },
  {
    "effect_id": "eff_2",
    "kind": "Branch",
    "stmt_refs": ["asm_s_1"],
    "attrs": {
      "condition": "iszero(eq(caller(), sload(_OWNER_SLOT)))",
      "path_states": ["entry"]
    }
  },
  {
    "effect_id": "eff_8",
    "kind": "Revert",
    "stmt_refs": ["asm_s_3"],
    "attrs": {
      "payload_ptr": "0x1c",
      "payload_size": "0x04",
      "path_states": ["iszero(eq(caller(), sload(_OWNER_SLOT)))"]
    }
  }
]
```

高级层分别记录手工常量槽读取与按路径解析出的错误：

```json
[
  {
    "overlay_id": "ov_2",
    "kind": "StateVariableRead",
    "effects": ["eff_3"],
    "stmt_refs": ["asm_s_1"],
    "attrs": {
      "target": "__sseir_eval_asm_s_1_1",
      "access": "storage[_OWNER_SLOT]",
      "storage_model": "manual_constant_slot",
      "solidity_like": "__sseir_eval_asm_s_1_1 = storage[_OWNER_SLOT]; /* state read, manual slot */"
    }
  },
  {
    "overlay_id": "ov_1",
    "kind": "PathConditionedCustomErrorRevert",
    "effects": ["eff_8"],
    "stmt_refs": ["asm_s_3"],
    "attrs": {
      "candidates": [
        {
          "status": "resolved",
          "condition": "iszero(eq(caller(), sload(_OWNER_SLOT)))",
          "selector": "0x82b42900",
          "error": "Unauthorized()",
          "solidity_like": "revert Unauthorized();"
        }
      ],
      "path_states": ["iszero(eq(caller(), sload(_OWNER_SLOT)))"]
    }
  }
]
```

`candidates` 不是猜测列表，而是同一 sink 在不同可达路径下的解析结果。无法完整解析的路径会保留 `status` 和 `reason`，不会套用另一条路径的错误。

### 示例 E：手工内存分配和结构体初始化片段

实际源码 `DN404._packedLogsMalloc(uint256)`：

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

逐步推导：

1. Source collector 从函数 AST 得到返回值 `p` 的类型 `_PackedLogs memory`，TypeEnv 同时读取结构体定义，得到字段 `logs` 位于偏移 0、`offset` 位于偏移 32。
2. EffectLifter 将 `mload(0x40)` 记录为 MemoryRead，将后续 `mstore(0x40, add(offset, shl(5, n)))` 记录为自由内存指针写入；中间表达式先按原子步骤记录 ValueDef/EvaluationStep。
3. `manual_memory_allocations` 在 CFG 顺序上匹配“读取旧 0x40 指针 -> 基于该指针写入对象数据 -> 写回新的 0x40 指针”，由读写 effect 共同生成 `MemoryRegionAllocate`。它不要求整个函数只有这一种操作。
4. `memory_writes_inside_allocation` 使用 CFG 节点范围和线性地址 alias，收集分配区间内的 `mstore(logs, n)`，并排除自由内存指针本身以及直接写返回对象字段的操作。
5. 对返回对象 `p`，结构体匹配器分别检查地址 `p` 与 `add(p, 0x20)`，将其偏移与 AST 结构体布局对齐，得到 `p.logs=logs` 和 `p.offset=offset`。
6. 因为这是手工布局且 Solidity 表达不一定无损，最终记录 `StructInitializationFragment`，保留 `reason=manual_memory_layout_or_custom_allocator`，同时继续保留所有原始 MemoryWrite。

底层首先保留四个 MemoryWrite，以及对 `mload(0x40)` 的 MemoryRead。模式匹配在 CFG 片段中识别旧、自由内存指针和新自由内存指针，形成分配 overlay：

```json
{
  "overlay_id": "ov_1",
  "kind": "MemoryRegionAllocate",
  "effects": ["eff_6", "eff_2"],
  "stmt_refs": ["asm_s_1", "asm_s_4"],
  "attrs": {
    "base": "mload(0x40)",
    "new_free_pointer": "(offset + (n << 5))"
  }
}
```

AST 已知函数返回值 `p` 的类型为 `DN404._PackedLogs`，且 `p` 与 `p + 32` 的写入偏移匹配结构体字段布局，因此记录局部结构体初始化语义：

```json
{
  "overlay_id": "ov_3",
  "kind": "StructInitializationFragment",
  "effects": ["eff_3", "eff_4"],
  "stmt_refs": ["asm_s_5", "asm_s_6"],
  "attrs": {
    "target": "p",
    "struct_type": "DN404._PackedLogs",
    "fields": [
      {
        "field": {
          "name": "logs",
          "type_string": "uint256[]",
          "offset": 0,
          "index": 0
        },
        "value": "logs",
        "write_effect": "eff_3",
        "semantic": "manual_memory_object_pointer"
      },
      {
        "field": {
          "name": "offset",
          "type_string": "uint256",
          "offset": 32,
          "index": 1
        },
        "value": "offset",
        "write_effect": "eff_4",
        "semantic": "memory_value"
      }
    ],
    "solidity_like": "/* construct return memory struct p: struct DN404._PackedLogs */ p.logs = logs; p.offset = offset;",
    "reason": "manual_memory_layout_or_custom_allocator"
  }
}
```

该 overlay 名为 `Fragment`，因为结构体初始化可以只是函数中的一个局部操作；识别不要求整个函数只能包含初始化逻辑。

### 示例 F：`extcodesize` 的高级语义

实际源码 `DN404._hasCode(address)`：

```solidity
function _hasCode(address a) internal view virtual returns (bool result) {
    assembly {
        result := extcodesize(a)
    }
}
```

逐步推导：

1. Yul assignment 被 EffectLifter 记录为 `ValueDef(targets=[result], value=extcodesize(a))`，表达式原子化同时保存 `extcodesize(a)` 的 EvaluationStep。
2. Address code overlay 只遍历 ValueDef，通过结构化 call parser 检查右值函数名必须为 `extcodesize`、参数数量必须为 1，并取得接收变量 `result`。
3. TypeEnv 从函数 AST 得知 `result` 是 `bool`。因此 code size 的非零判断可以无损表示为 `(a.code.length != 0)`，生成 `AddressHasCode`。
4. 如果接收变量不是 bool，同一规则只生成 `AddressCodeSize`，表达式为 `a.code.length`；不会把任意数值接收变量猜成布尔检查。

底层赋值记录为 ValueDef，高级 overlay 根据返回值类型为 `bool`，将非零 code size 解释为地址是否具有代码：

```json
[
  {
    "effect_id": "eff_1",
    "kind": "ValueDef",
    "stmt_refs": ["asm_s_1"],
    "attrs": {
      "targets": ["result"],
      "value": "extcodesize(a)",
      "path_states": ["entry"]
    }
  },
  {
    "overlay_id": "ov_1",
    "kind": "AddressHasCode",
    "effects": ["eff_1"],
    "stmt_refs": ["asm_s_1"],
    "attrs": {
      "target": "result",
      "address": "a",
      "solidity_like": "result = (a.code.length != 0);"
    }
  }
]
```

如果接收值不是布尔语义，底层仍可记录 code size，但不能强行提升为 `AddressHasCode`。

### 示例 G：Yul `return` 作为独立语义终点

实际源码 `DN404._return(uint256)`：

```solidity
function _return(uint256 x) private pure {
    assembly {
        mstore(0x00, x)
        return(0x00, 0x20)
    }
}
```

逐步推导：

1. MemorySSA 将 `mstore(0x00, x)` 记录为 `mem_1`，EffectLifter 同时生成 `eff_1/MemoryWrite`。
2. `return(0x00, 0x20)` 独立生成 `eff_2/Return`。EffectLifter 使用 return 参数查询 `[0x00, 0x20)`，把查询结果写入 `payload_memory`，SinkResolver 再将其统一为 payload 的 sink resolution。
3. 查询在 `entry` 路径找到 `mem_1`，得到一个已知 word `x`，并确认区间完整。该过程依赖的是 return sink 的 ptr/size，而不是硬编码寻找它前面紧邻的 `mstore`。
4. Return overlay 读取 `payload_memory`：`payload_size=32`、只有一个已知值且 `complete=true`，所以设置 `encoding_hint=abi_word`，生成 `RawReturnData(values=[x])`。
5. Overlay 明确保留 `solidity_equivalent=false` 和原因，因为 Yul `return` 直接终止当前 EVM 调用并返回原始字节，不能仅根据局部值把它改写成普通 Solidity `return x;`。

`return` 自身是语义终点。它查询返回区间 `[0x00, 0x20)`，解析出 `x`；高级语义不与某个固定 `mstore` 写法绑定：

```json
[
  {
    "effect_id": "eff_1",
    "kind": "MemoryWrite",
    "stmt_refs": ["asm_s_1"],
    "attrs": {
      "address": "0x00",
      "value": "x",
      "memory_version": "mem_1",
      "write_kind": "mstore"
    }
  },
  {
    "effect_id": "eff_2",
    "kind": "Return",
    "stmt_refs": ["asm_s_2"],
    "attrs": {
      "payload_ptr": "0x00",
      "payload_size": "0x20",
      "path_states": ["entry"]
    }
  },
  {
    "overlay_id": "ov_1",
    "kind": "RawReturnData",
    "effects": ["eff_2"],
    "stmt_refs": ["asm_s_2"],
    "attrs": {
      "values": ["x"],
      "solidity_like": "returnRawAbiWord(x);",
      "reason": "yul_return_terminates_current_evm_call_with_raw_return_data"
    }
  }
]
```

这里没有直接写成 Solidity `return x;`，因为原函数没有 Solidity 返回参数，Yul `return` 表示直接终止当前 EVM 调用并返回原始 ABI 字节。`RawReturnData` 保留了这一区别。
