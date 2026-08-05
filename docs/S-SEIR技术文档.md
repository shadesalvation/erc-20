# S-SEIR 技术文档

## 1. 目标

S-SEIR 的目标是把 Solidity 源码与 inline assembly / Yul 的静态分析结果统一记录为函数级语义模型。

当前阶段不做 LLM 代码生成，也不直接替换源码。S-SEIR 只负责确定性语义建模，输出后续可以被人工或 LLM 使用的结构化语义事实。

当前 S-SEIR 输出结构为：

```Plain
FunctionSSEIR:
  source_statements
  control
  expr_roles
  effects
  semantic_overlays
  security_facts
  analysis_facts
```

其中：

- `source_statements` 保存原始 Solidity / Yul 语句。
- `control` 保存函数级 CFG。
- `expr_roles` 保存表达式的语义作用。
- `effects` 保存底层副作用。
- `semantic_overlays` 保存高级语义解释。
- `security_facts` 保存安全行为事实。
- `analysis_facts` 保存分析过程中的辅助事实。

当前已经删除 `ProjectionPolicy` 层。原因是目前还没有足够稳定的系统性规则判断某个语义是否能无损投影为 Solidity 高级语句；本阶段只专注语义恢复与统一建模。

## 2. 总体 Pipeline

主入口：

```Plain
scripts/s_seir/s_seir_pipeline.py
```

处理流程：

```Plain
Solidity source
  -> branch / opaque preprocess
  -> solc AST
  -> event definitions
  -> selector registry
  -> storage layout
  -> SourceStatementCollector
  -> TypeEnv
  -> ControlBuilder
  -> MemorySSA views
  -> ExpressionRoleAnalyzer
  -> EffectLifter
  -> BranchMaterialization
  -> SinkResolver
  -> SemanticOverlayBuilder
  -> SemanticNormalizer
  -> SecurityFactBuilder
  -> FunctionSSEIR
  -> JSON / CFG DOT / solidity-like / LLM assembly views
```

对应代码顺序：

```Python
branch_result = build_branch_preprocessed_analysis_source(...)
ast = compile_source_ast(...)
events = parse_events_from_source(...)
selector_registry = build_selector_registry(ast, source_text)
storage_layouts = extract_storage_layout(...)

for unit in SourceStatementCollector(...).collect():
    apply_storage_layout(unit, storage_layouts)
    type_env = TypeEnv(unit)
    control = ControlBuilder(...).build(unit)
    mem = build_memory_ssa_views(unit, control)
    roles = ExpressionRoleAnalyzer().analyze(unit, type_env, mem)
    effects, facts = EffectLifter().lift(unit, mem, control)
    branch_effects, branch_facts = build_branch_materialization_nodes(unit, mem)
    overlays = SemanticOverlayBuilder(...).build(unit, type_env, roles, effects)
    roles, effects, overlays, normalizer_facts = SemanticNormalizer().normalize(...)
    security_facts = SecurityFactBuilder().build(effects, overlays)
```

其中 `SinkResolver` 不是主入口中的独立一行调用，而是在
`SemanticOverlayBuilder.build()` 开始时通过
`SinkResolver().attach_all(effects)` 执行。它只整理已有 MemorySSA
查询证据，不取代 MemorySSA。

## 3. SourceStatement 层

实现文件：

```Plain
scripts/s_seir/s_seir_source_collector.py
```

### 3.1 作用

SourceStatement 层负责给 Solidity 与 Yul 原始语句分配稳定 `stmt_id`，供后续 effect、overlay、role、fact 引用。

S-SEIR 不构建复杂 provenance graph，只通过 `stmt_refs` 指向原始语句。

### 3.2 处理规则

输入：

- solc AST。
- Solidity 源码文本。

处理规则：

- 遍历 `ContractDefinition`。
- 遍历每个 `FunctionDefinition / ModifierDefinition`。
- 收集函数参数、返回值、局部变量、状态变量。
- 对 Solidity 语句生成 `sol_s_*`。
- 对 InlineAssembly 中的 Yul 语句生成 `asm_s_*`。
- 对每个 InlineAssembly 构造 `AssemblyAstBlock`。

当前识别的 Solidity statement 类型包括：

```Plain
ExpressionStatement
VariableDeclarationStatement
IfStatement
ForStatement
WhileStatement
DoWhileStatement
Return
RevertStatement
EmitStatement
TryStatement
UncheckedBlock
```

当前识别的 Yul statement 类型包括：

```Plain
YulVariableDeclaration
YulAssignment
YulExpressionStatement
YulIf
YulSwitch
YulCase
YulForLoop
YulBreak
YulContinue
YulLeave
```

### 3.3 输出

每条语句记录：

```Plain
stmt_id
lang
text
src
function_id
block_id
origin
```

示例：

```Plain
asm_s_12 [yul] let qVPd := sload(XCUz)
sol_s_3 [solidity] return true
```

## 4. TypeEnv 与 Storage Layout

实现文件：

```Plain
scripts/s_seir/s_seir_type_env.py
scripts/s_seir/s_seir_storage_layout.py
```

### 4.1 TypeEnv

作用：

- 建立函数参数、返回值、局部变量、状态变量的类型查询环境。
- 支持判断变量是否为 `bytes memory`。
- 支持根据 storage slot 查找状态变量。

使用场景：

- `revert(add(32, returndata), size)` 中判断 `returndata` 是否为 bytes memory。
- direct storage slot 恢复时通过 slot 查找状态变量。
- require 恢复时判断地址类型，生成 `address(0)` 风格条件。

### 4.2 Storage Layout

处理方法：

- 调用 `solc --standard-json`。
- 请求 `storageLayout` 输出。
- 解析每个状态变量的 slot、offset、type id。
- 写回 `FunctionUnit.state_variables[*].storage_slot`。

当前不要求用户提前提供 Slither 的 `variables_order.txt`。Storage layout 由脚本运行时从 solc 提取。

## 5. Control 层

实现文件：

```Plain
scripts/s_seir/s_seir_control_builder.py
```

### 5.1 目标

构建函数级 CFG，并把 Solidity CFG 与 Yul CFG 融合。

### 5.2 算法来源

Solidity 层：

```Plain
Slither function CFG
```

Yul 层：

```Plain
原模块 assembly_ast_cfg.build_yul_cfg
```

这里遵循前面确定的原则：

```Plain
Solidity 函数级 CFG 复用 Slither
Yul / inline assembly 内部 CFG 复用原 assembly_ast_cfg
```

### 5.3 融合规则

处理规则：

- 使用 Slither 找到当前函数。
- 使用 Slither node 作为 Solidity CFG 节点。
- 识别 Slither 中处于 InlineAssembly 源码范围内的节点。
- 对 assembly 范围内的 Slither 节点，不直接使用 Slither 展开的 assembly 节点。
- 对每个 InlineAssembly 使用 `build_yul_cfg` 构建本地 Yul CFG 子图。
- 将 Slither 前驱节点连接到 Yul CFG entry。
- 将 Yul CFG exit 连接到 Slither 后继节点。
- 同一 assembly block 内部边使用 Yul CFG 自身边。

边连接策略记录为：

```Plain
assembly_edge_policy = slither_predecessors_to_yul_entry_and_yul_exit_to_slither_successors
```

### 5.4 Loop 上下文

ControlBuilder 还会扫描 Solidity AST 中的：

```Plain
ForStatement
WhileStatement
DoWhileStatement
```

如果某个 InlineAssembly 块位于 Solidity loop 范围内，则在 assembly block 上记录：

```Plain
function_loop_context
```

用途：

- 让 MemorySSA 查询结果知道当前 assembly 是否处于函数级 loop 内。
- 为后续 loop-aware memory summary 提供上下文。

### 5.5 fallback

如果 Slither CFG 不可用，则使用 skeleton 模式：

- Solidity statement 顺序 fallthrough。
- Yul CFG 仍由 `assembly_ast_cfg` 构建。

该模式用于容错，不是主路径。

## 6. MemorySSA 查询层

实现文件：

```Plain
scripts/s_seir/s_seir_memory_ssa.py
```

### 6.1 目标

MemorySSA 是 S-SEIR 中恢复 slot、event、call、return、revert 等语义的基础查询层。

### 6.2 算法来源

S-SEIR 不重写 MemorySSA 算法，而是直接复用原模块：

```Plain
assembly_memory_ssa.analyze_block
assembly_cfg_memory_adapter.resolve_memory_read_with_loops
```

S-SEIR 只增加一层薄包装：

```Plain
SSeirMemorySSAView
```

用于记录：

- assembly block id。
- function loop context。
- query owner。
- tracker scope。

### 6.3 处理规则

对每个 assembly block：

```Plain
backend = analyze_block(block)
view = SSeirMemorySSAView(block_id, backend, function_loop_context)
```

当后续模块需要读取 memory：

```Plain
view.query_memory(node_id, pointer, length, reason)
```

内部调用：

```Plain
legacy_resolve_memory_read(backend, node_id, pointer, length, reason)
```

### 6.4 保留的原模块能力

当前 MemorySSA 保留原模块能力：

- CFG path-sensitive `PathState`。
- Yul value SSA。
- memory reaching definition。
- linear address alias。
- branch candidates。
- loop-aware facts。
- `MemoryPhi`。
- `MemoryRangeSummary`。
- `MemoryTop`。
- semantic sink lazy memory read。

### 6.5 输出

Memory query 输出中会记录：

```Plain
query_kind
pointer
length
normalized_pointer
words
complete
has_unknown
has_phi
loop_facts
top_facts
path_states
owner = S-SEIR
tracker_scope = s_seir_memoryssa_query
function_loop_context
```

## 7. ExpressionRole 初始分析

实现文件：

```Plain
scripts/s_seir/s_seir_expr_roles.py
scripts/s_seir/s_seir_semantic_normalizer.py
```

### 7.1 定位

ExpressionRole 是 S-SEIR 特有的归档层。

它不应该重新发明复杂语义恢复规则，而是在 Effect / Overlay 形成后，把已有语义事实登记为表达式角色。

### 7.2 初始 role 分析

`ExpressionRoleAnalyzer` 会做少量源码级 role 标注：

- Solidity `if` 语句 -> `guard_condition`。
- `mload(0x40)` -> `free_memory_pointer`。
- `mload(bytesMemoryObj)` -> `bytes_length_value`。
- `revert/return(ptr, size)` -> payload ptr / payload size。
- `keccak256(ptr, size)` -> memory slice start / size。
- `call/staticcall/delegatecall/callcode` 参数 -> call gas / target / input / output roles。
- loop record condition -> `loop_bound`。

### 7.3 后置 role 归档

`SemanticNormalizer` 在 Effect / Overlay 之后继续补充 role。

从 Effect 归档：

- `MemoryRead` -> `memory_slice_start`、`memory_read_value`。
- `MemoryHash` -> `memory_slice_start`、`memory_slice_size`。
- `StorageRead/StorageWrite` -> `storage_slot_expr`、`storage_write_value`。
- `Call/StaticCall/DelegateCall/CallCode` -> call target / gas / input / output。
- `EventLog` -> event data range、topic0、indexed argument。
- `Branch` -> `branch_condition`。

从 Overlay 归档：

- `MappingSlot` -> `mapping_slot_expr`、`mapping_key_material`。
- `MappingRead/MappingWrite` -> `mapping_slot_expr`、`state_access_expr`。
- `PathConditionedStorageRead/Write` -> `storage_slot_version`、`state_access_expr`。
- `RequireOverlay` -> `guard_condition`。
- `EventEmit` -> `event_topic0`、`abi_argument`。
- call overlay -> `call_target`、`abi_selector`、`abi_argument`。
- `RawRevertBytes` -> `revert_payload_ptr`。

### 7.4 输出

每个 role 记录：

```Plain
expr_id
text
normalized
role
type_hint
stmt_ref
attrs
```

## 8. Effect 层

实现文件：

```Plain
scripts/s_seir/s_seir_effect_lifter.py
```

### 8.1 目标

Effect 层只记录底层副作用，不负责高级语义解释。

例如：

```Plain
sload(Jfwv)
```

先记录为：

```Plain
StorageRead(slot=Jfwv)
```

是否恢复为：

```Plain
wZHA[_owner][_spender]
```

交给 Overlay 层处理。

### 8.2 算法来源

EffectLifter 复用原模块的 Yul AST 辅助函数：

```Plain
assembly_memory_ssa.direct_call
assembly_memory_ssa.statement_expression
assembly_ast_cfg.yul_expression
assembly_ast_cfg.yul_statement_text
```

Memory 读取通过 S-SEIR MemorySSA query 层，底层仍是原 MemorySSA。

表达式渲染调用：

```Plain
s_seir_yul_normalize.normalize_expr
```

而该函数当前已改为复用原：

```Plain
assembly_arithmetic_compare_ir.render_yul_expression
```

### 8.3 语义调用识别

当前识别的 Yul semantic calls：

```Plain
mload
keccak256
calldatacopy
codecopy
returndatacopy
mcopy
extcodecopy
sload
sstore
call
staticcall
delegatecall
callcode
revert
return
log0-log4
```

### 8.4 生成的 Effect

当前主要 Effect：

```Plain
MemoryWrite
LoopMemorySummary
Branch
ValueDef
MemoryRead
MemoryHash
MemoryCopy
StorageRead
StorageWrite
Call
StaticCall
DelegateCall
CallCode
EventLog
Revert
Return
BranchMaterialization
```

### 8.5 Memory sink 查询规则

以下 effect 会触发 memory query：

- `MemoryRead`
- `MemoryHash`
- `EventLog`
- `Revert`
- `Return`
- `Call / StaticCall / DelegateCall / CallCode`

查询原因分别记录为：

```Plain
mload
keccak256
event_log_data
revert_payload
return_payload
call_input / staticcall_input / delegatecall_input / callcode_input
```

### 8.5.1 SinkResolver 统一语义终点解析层

实现文件：

```Plain
scripts/s_seir/s_seir_sink_resolver.py
```

目的：

```text
把不同 effect 中已有的 MemorySSA / byte-axis 查询结果，统一整理为 path-sensitive sink_resolution。
后续 storage、event、revert、return、call 等 overlay 不再各自发明一套 memory 参数匹配规则，而是统一消费 sink_resolution。
```

当前接入位置：

```text
s_seir_overlay_builder.py

OverlayBuilder.build()
  -> SinkResolver().attach_all(effects)
  -> 后续 overlay builder 读取 effect.attrs.sink_resolution
```

SinkResolver 支持的 effect：

```text
MemoryHash
EventLog
Return
Revert
Call
StaticCall
DelegateCall
CallCode
```

各 effect 对应的 memory range：

```text
MemoryHash   -> memory_read(ptr, size)      -> role = slot/hash_input
EventLog     -> data_memory(data_ptr, size) -> role = data/event_data
Return       -> payload_memory(ptr, size)   -> role = payload/return_payload
Revert       -> payload_memory(ptr, size)   -> role = payload/revert_payload
Call         -> input_memory(ptr, size)     -> role = input/call_input
StaticCall   -> input_memory(ptr, size)     -> role = input/staticcall_input
DelegateCall -> input_memory(ptr, size)     -> role = input/delegatecall_input
CallCode     -> input_memory(ptr, size)     -> role = input/callcode_input
```

核心算法：

```text
1. 对每个 semantic sink 读取已有 memory query 结果。
2. 优先使用 MemoryByteAxis 的 byte_slice / path_slices。
3. 如果 path_slices 不存在，但存在单一路径 slices，则构造 entry path。
4. 对每条 path 单独保留：
   - condition
   - memory slice
   - extraction
   - source_version
   - source_node_id
5. 如果某条 path 的 slice 不完整，标记 unresolved，不强行恢复。
6. 如果不同 path 的 extraction 不同，或 path 带 condition，则标记 path_sensitive = true。
7. 对 keccak256/hash sink，normalized 表示为：
   keccak256(abi.encodePacked(...))
8. 对普通 memory range sink，normalized 表示为：
   MemorySlice(...)
```

`sink_resolution` 结构：

```json
{
  "sink_id": "sink_asm_s_4_Return_12",
  "stmt_refs": ["asm_s_4"],
  "cfg_node_id": 12,
  "sink_kind": "Return",
  "args": ["0", "0x20"],
  "path_sensitive": true,
  "path_resolutions": [
    {
      "path_id": "path_0",
      "condition": "!(cond)",
      "status": "resolved",
      "arg_resolutions": {
        "payload": {
          "expr": "0:0x20",
          "normalized": "MemorySlice(a)",
          "memory_slice": {
            "query_kind": "PathMemoryByteSlice",
            "slices": [
              {
                "query_offset": 0,
                "size": 32,
                "extraction": "a",
                "source_version": "a__ssa1",
                "source_node_id": 2
              }
            ]
          }
        }
      }
    }
  ]
}
```

后续 overlay 消费规则：

```text
PathConditionedStorageRead / PathConditionedStorageWrite
  由 MemoryHash sink_resolution 区分不同 path 下的 keccak 输入。

PathConditionedEventEmit
  由 EventLog.data 的 sink_resolution 生成不同 condition 下的 emit。

PathConditionedCustomErrorRevert
  由 Revert.payload 的 sink_resolution 解析 selector，并匹配源码 error 定义。

PathConditionedRawReturnData
  由 Return.payload 的 sink_resolution 记录不同 condition 下返回的 ABI word / raw memory。

PathConditionedLowLevelCall / PathConditionedStaticCallOverlay / PathConditionedDelegateCallOverlay
  由 call input 的 sink_resolution 解析 selector 与参数；无法匹配 ABI 时保留 yulCall + MemorySlice。
```

示例 1：同一个 EventLog 在不同分支下读取不同 data：

```yul
mstore(0, a)
if cond { mstore(0, b) }
log1(0, 0x20, V_topic0)
```

SinkResolver 输出两条 path：

```text
condition = !(cond)   data = MemorySlice(a)
condition = cond      data = MemorySlice(b)
```

Overlay 输出：

```solidity
if (!(cond)) {
    emit V(a);
}
if (cond) {
    emit V(b);
}
```

示例 2：同一个 revert 在不同分支下抛出不同 error selector：

```yul
mstore(0, 0xaaaaaaaa)
if cond { mstore(0, 0xbbbbbbbb) }
revert(0x1c, 0x04)
```

如果 selector registry 中存在：

```text
0xaaaaaaaa -> A()
0xbbbbbbbb -> B()
```

则恢复为：

```solidity
if (!(cond)) {
    revert A();
}
if (cond) {
    revert B();
}
```

示例 3：同一个低层 call 在不同分支下构造不同 calldata：

```yul
mstore(0, 0xa9059cbb)
mstore(0x20, to)
mstore(0x40, amount)
if cond {
    mstore(0, 0x095ea7b3)
    mstore(0x40, allowance)
}
let ok := call(gas(), token, 0, 0x1c, 0x44, 0, 0)
```

SinkResolver 记录：

```text
condition = !(cond)   input = MemorySlice(selector_transfer, to, amount)
condition = cond      input = MemorySlice(selector_approve, to, allowance)
```

如果 selector 能匹配 ABI，则 overlay 可进一步恢复 selector_signature 与 arguments；否则保守输出：

```solidity
yulCall(... input: MemorySlice(...));
```

### 8.6 SSA 版本记录

EffectLifter 会记录：

- `ValueDef.target_versions`
- `MemoryRead.value_versions`
- `MemoryHash.value_versions`
- `StorageRead.slot_versions`
- `StorageWrite.slot_versions`

规则：

- 新定义变量时，从 MemorySSA 的 `value_definitions` 中找当前 node 产生的 SSA 版本。
- storage slot 参数是简单 identifier 时，从当前 path state 的 reaching value definition 中找 SSA 版本。
- memory query 的 word 如果来自某个 Yul value，也会附加 `value_versions`。

用途：

- 避免同名 Yul 变量多次赋值导致 slot 误合并。
- 例如多个 `Jfwv := keccak256(...)` 不再只按 `Jfwv` 字符串匹配，而按 `Jfwv__ssa*` 区分。

## 9. Branch Materialization

实现文件：

```Plain
scripts/s_seir/s_seir_branch_materialization.py
```

### 9.1 算法来源

S-SEIR 不重写分支物化算法，直接调用原模块：

```Plain
assembly_branch_materialization.find_expansions
assembly_branch_materialization.format_expansion
```

### 9.2 处理规则

对每个 assembly block 的 MemorySSA backend：

```Plain
expansions = find_expansions(backend)
```

每个 expansion 转换为：

```Plain
EffectNode(kind = BranchMaterialization)
AnalysisFact(kind = BranchMaterialization)
```

记录内容：

- `sink_node`
- `sink_target`
- `sink_text`
- `mode`
- `baseline`
- `branches`
- `shared_node_ids`
- `forced_node_ids`
- `discarded_unknown_paths`
- `policy`

当前策略：

```Plain
discard_unknown_memory_paths_and_materialize_known_semantic_sink_paths
```

### 9.3 定位

Branch Materialization 当前作为结构化事实进入 S-SEIR，不直接改写源码。

## 10. SemanticOverlay 层

实现文件：

```Plain
scripts/s_seir/s_seir_overlay_builder.py
```

### 10.1 目标

Overlay 层负责把底层 Effect 解释为更接近 Solidity 语义的结构化节点。

例如：

```Plain
MemoryHash + StorageRead
  -> MappingSlot
  -> MappingRead
```

### 10.2 RequireOverlay

来源 Effect：

```Plain
Revert(payload_ptr=0, payload_size=0)
Branch
```

处理规则：

- 只处理 `revert(0,0)`。
- 从 `path_states` 中找当前 revert 的条件路径。
- 优先使用最近一层 condition。
- 如果上层 condition 块中没有其他有效操作，可以合并上层 condition。
- 如果上层 condition 下存在其他有效 effect，则停止向上合并。
- condition 反转使用 `invert_condition`。

输出：

```Plain
RequireOverlay:
  condition
  nearest_condition
  require_like
  revert_payload = empty
  control_path
  merged_conditions
  require_conditions
  discarded_before_revert
```

补充规则：

- 如果 require 是 native precompile success guard，记录 `elided_by_native_precompile = true`。

### 10.3 RawRevertBytes

来源：

```Plain
Revert(payload_ptr, payload_size)
ExpressionRole(revert_payload_ptr)
MemoryRead
TypeEnv
```

处理规则：

- 判断 payload ptr 是否是 bytes memory object 的 data 指针。
- 判断 size 是否等于该 bytes object 的 length。
- 如果成立，生成 `RawRevertBytes`。

当前仍是基础框架，复杂 error selector / ABI payload 还未完整恢复。

### 10.4 CustomErrorRevert

来源：

```Plain
Solidity Revert effect
```

处理规则：

- 如果 Solidity 源码层 `revert XXX` 已存在 payload 文本，则直接记录为 `CustomErrorRevert`。

### 10.5 Storage Overlay

来源 Effect：

```Plain
MemoryHash
StorageRead
StorageWrite
```

算法原则继承原 storage 模块：

```Plain
keccak256 先只作为 hash candidate
只有被 sload / sstore 消费时才激活为 storage slot
```

### 10.5.1 Hash candidate 收集

对每个 `MemoryHash`：

- 读取 `value_versions`。
- 优先以 SSA version 作为 key。
- 如果没有 version，则 fallback 到原变量名。
- 使用 `memory_read.words` 的前两个 word 判断 mapping slot：
  - word0 = key。
  - word1 = base slot 或上一层 mapping slot。

### 10.5.2 Mapping slot 恢复

规则：

- 如果 word1 能通过 storage layout 解析为状态变量 slot：

```Plain
access = StateVar[key]
```

- 如果 word1 是已知上一层 mapping slot：

```Plain
access = PreviousAccess[key]
```

- 如果 key/base 为 unknown，则不恢复。

输出：

```Plain
MappingSlot
```

### 10.5.3 Storage consumer 激活

对每个 `StorageRead / StorageWrite`：

- 读取 `slot_versions`。
- 对每个 slot version 尝试激活 hash candidate。
- 激活时递归激活 parent mapping slot。

### 10.5.4 Read / Write overlay

如果 slot 能解析为 mapping：

```Plain
MappingRead
MappingWrite
```

如果 slot 是直接状态变量 slot：

```Plain
StateVariableRead
StateVariableWrite
```

如果无法解析：

```Plain
StateVariableRead / StateVariableWrite
unresolved_reason = unknown_storage_slot
```

### 10.5.5 Path-conditioned Storage Overlay

这是 S-SEIR 特有的归档规则。

触发条件：

```Plain
StorageRead / StorageWrite 携带多个 slot_versions
```

输出：

```Plain
PathConditionedStorageRead
PathConditionedStorageWrite
```

记录内容：

- `slot`
- `slot_versions`
- `path_states`
- `target`
- `value`
- `candidates`

每个 candidate 记录：

```Plain
slot_key
status = resolved / unresolved
overlay_kind
access
state_variable
key
slot_effect
solidity_like
unresolved_reason
```

处理策略：

- 如果多个 version 都解析为同一个 access，则仍允许继续输出普通 `MappingRead/MappingWrite` 作为简化视图。
- 如果存在 unresolved 或多个不同 access，则不再压成单一 mapping overlay，只保留 path-conditioned overlay。

用途：

- 避免多个 SSA slot version 被错误合并。
- 保留路径相关 slot 候选，供后续恢复阶段判断。

### 10.6 Event Overlay

来源 Effect：

```Plain
EventLog
```

算法来源：

- event 定义解析复用原 `assembly_event_ir.parse_events_from_source`。
- topic 标准化复用 `assembly_event_ir.normalize_topic_value`。
- 类型判断复用 `assembly_event_ir.is_static_word_type`。

处理规则：

- 读取当前合约及全局 event 定义。
- 对非 anonymous event：
  - topic0 必须等于 `keccak256(EventName(canonicalTypes))`。
  - topic 数必须等于 indexed 参数数 + 1。
- 对 anonymous event：
  - 按 indexed 参数数量匹配。
- indexed 参数来自 topics。
- non-indexed 参数来自 `data_memory` 的 resolved words。
- data 区间通过 MemorySSA 查询得到。

无法匹配时：

```Plain
event = unknownEvent
notes includes unknown_event_topic0
```

附加 notes：

- `data_word_unknown`
- `data_word_has_symbolic_overwrite_candidate`
- `data_length_X_does_not_match_expected_Y`
- `dynamic_non_indexed_data_not_decoded`
- `memory_data_words_incomplete`

### 10.7 External Call / Precompile Overlay

来源 Effect：

```Plain
Call
StaticCall
DelegateCall
CallCode
```

算法来源：

- precompile 表复用原 `assembly_external_call_ir.PRECOMPILES`。
- native precompile 提升复用原 `assembly_external_call_ir.native_precompile_call`。

普通 call overlay：

```Plain
Call -> LowLevelCall
StaticCall -> StaticCallOverlay
DelegateCall -> DelegateCallOverlay
CallCode -> LowLevelCall
```

记录字段：

- `op`
- `target`
- `target_solidity`
- `gas`
- `value`
- `input_ptr`
- `input_size`
- `output_ptr`
- `output_size`
- `input_memory`
- `output_memory_query`

Precompile 处理：

- 如果 target 是已知 precompile 地址，则生成 `PrecompileCall`。
- 如果满足原模块 native precompile 提升条件，则记录：

```Plain
native_precompile:
  solidity_like
  output_word_expression
  result_name
  elides_success_check
```

当前会继续保留低级调用事实，不把未知目标强行恢复为高级 ABI 调用。

### 10.8 PrecompileOutputRead

来源：

```Plain
PrecompileCall
MemoryRead
MemoryWrite
```

处理规则：

- 如果 precompile overlay 有 `output_word_expression`。
- 查找后续读取同一 output pointer 的 `MemoryRead`。
- 要求 read node 在 call node 之后。
- 如果 call 与 read 之间 output pointer 被重写，则不替换。
- 否则生成：

```Plain
PrecompileOutputRead
```

### 10.9 Division Guard Overlay

来源：

```Plain
ValueDef
Branch
StorageWrite
```

算法来源：

```Plain
assembly_arithmetic_compare_ir.division_guards_for_expression
```

处理规则：

- 扫描表达式中的 `div/mod/sdiv/smod`。
- 对除数生成：

```Plain
RequireOverlay(revert_payload = division_by_zero_guard)
```

### 10.10 ExpressionNormalization Overlay

来源：

```Plain
ValueDef
Branch
```

处理规则：

- 对 value definition 生成 Solidity-like 表达式。
- 对 branch condition 生成 normalized condition。
- 表达式渲染复用原 arithmetic parser。

## 11. SemanticNormalizer

实现文件：

```Plain
scripts/s_seir/s_seir_semantic_normalizer.py
```

### 11.1 作用

SemanticNormalizer 是 S-SEIR 的统一归档层。

它不重新做底层语义恢复，只基于已有 Effect / Overlay：

- 补充 ExpressionRole。
- 生成标准 analysis facts。
- 记录 unresolved overlay。
- 记录 semantic sink memory query。

### 11.2 Canonicalization fact

每个函数都会生成：

```Plain
SSEIRCanonicalization:
  mode = native_s_seir_semantic_graph
  legacy_adapter_used = false
  function
  state_variables
```

### 11.3 SemanticSinkMemoryQuery

如果 effect 的 memory query 中存在：

- `has_phi`
- `has_unknown`
- `loop_facts`
- `top_facts`

则生成：

```Plain
SemanticSinkMemoryQuery
```

记录：

```Plain
effect
effect_kind
query_field
has_phi
has_unknown
loop_facts
top_facts
path_states
```

### 11.4 UnresolvedOverlay

如果 overlay attrs 中有：

```Plain
unresolved_reason
```

则生成：

```Plain
UnresolvedOverlay
```

## 12. SecurityFacts

实现文件：

```Plain
scripts/s_seir/s_seir_security_facts.py
```

### 12.1 定位

SecurityFacts 是从 overlay/effect 中提取的安全行为摘要，不参与底层语义恢复。

### 12.2 处理规则

从 overlay 生成：

- `MappingRead / StateVariableRead`
  - 如果能提取 account，则生成 `BalanceRead`。
  - 否则生成 `StateRead`。

- `MappingWrite / StateVariableWrite`
  - 如果 access 有两个 key，则生成 `AllowanceUpdate`。
  - 如果能提取 account，则生成 `BalanceUpdate`。
  - 否则生成 `StateUpdate`。

- `EventEmit`
  - 如果 event 为 `Transfer`，生成 `TransferEvent`。
  - 否则生成 `EventEmission`。

- call overlay
  - 生成 `ExternalCall`。

- `RequireOverlay`
  - 生成 `GuardCondition`。

- `RawRevertBytes`
  - 生成 `TransparentRevertBubble`。

额外规则：

- 如果 external call 的 stmt_ref 排在 storage write 前面，生成：

```Plain
ExternalCallBeforeStateUpdate
```

- 如果存在 TransferEvent 和 BalanceUpdate，生成：

```Plain
EventStateLink
```

## 13. AnalysisFacts

AnalysisFacts 保存分析辅助信息，不直接作为高级语义恢复结果。

当前主要包括：

```Plain
MemorySSAQueryLayer
LoopMemoryRecord
MemoryPhi
MemoryRangeSummary
MemoryTop
BranchMaterialization
SemanticSinkMemoryQuery
UnresolvedOverlay
SSEIRCanonicalization
```

用途：

- 调试 MemorySSA / loop / branch 行为。
- 给后续恢复阶段提供路径、unknown、phi、top 等信息。
- 避免把无法确定的内容伪装成确定性 Solidity 语义。

## 14. 表达式规范化

实现文件：

```Plain
scripts/s_seir/s_seir_yul_normalize.py
```

### 14.1 算法来源

S-SEIR 当前不使用自造轻量字符串表达式算法，而是复用原模块：

```Plain
assembly_arithmetic_compare_ir.render_yul_expression
assembly_arithmetic_compare_ir.division_guards_for_expression
```

### 14.2 处理规则

`normalize_expr(expr)`：

- 去除 SSA 后缀。
- 调用原 parser/render。
- 如果解析失败，则保留原表达式。

`invert_condition(expr)`：

- 对 `iszero(x)`、`lt`、`gt`、`eq` 等常见条件做反转。
- 地址类型使用 `address(0)` 风格。
- call success guard 使用 `(... != 0)`。

`division_guards(expr)`：

- 调用原 division guard 逻辑。

`words_from_memory_query(query)`：

- 读取 MemorySSA query words。
- 如果主 value 为 unknown，但 branch candidate 中存在 known value，则归档 known branch candidate。
- 标记 `discarded_unknown_branch`。

## 15. 当前不处理的内容

### 15.1 ProjectionPolicy

当前已删除，不参与 S-SEIR 输出。

原因：

- 目前没有稳定规则判断是否能无损投影为 Solidity。
- 当前目标是统一语义建模，不做最终源码替换策略。

### 15.2 最终源码替换

当前不做：

- 自动生成可编译 Solidity 函数体。
- 替换原始 assembly 块。
- solc 编译等价验证。

### 15.3 完整 error selector 恢复

当前只具备基础 `RawRevertBytes` 和 `CustomErrorRevert` 框架。

尚未完整处理：

- `Error(string)`。
- `Panic(uint256)`。
- custom error selector。
- revert payload ABI 参数恢复。

## 16. 当前算法来源对照表

| S-SEIR 部分 | 当前处理方式 | 是否复用原模块算法 |
|---|---|---|
| solc AST 编译 | `assembly_ast_cfg.compile_source_ast` | 是 |
| SourceStatement 收集 | S-SEIR 自有归档 | S-SEIR 特有 |
| Solidity CFG | Slither function CFG | 外部工具 |
| Yul CFG | `assembly_ast_cfg.build_yul_cfg` | 是 |
| MemorySSA | `assembly_memory_ssa.analyze_block` | 是 |
| Memory lazy resolve | `assembly_cfg_memory_adapter.resolve_memory_read_with_loops` | 是 |
| Branch materialization | `assembly_branch_materialization.find_expansions` | 是 |
| 表达式规范化 | `assembly_arithmetic_compare_ir.render_yul_expression` | 是 |
| division guard | `assembly_arithmetic_compare_ir.division_guards_for_expression` | 是 |
| event 定义解析 | `assembly_event_ir.parse_events_from_source` | 是 |
| event topic 规范化 | `assembly_event_ir.normalize_topic_value` | 是 |
| precompile 表 | `assembly_external_call_ir.PRECOMPILES` | 是 |
| native precompile 提升 | `assembly_external_call_ir.native_precompile_call` | 是 |
| Effect 层 | S-SEIR 统一归档 | S-SEIR 特有 |
| Overlay 层 | 基于原模块规则归档 | 混合 |
| Path-conditioned storage overlay | S-SEIR 结构化归档 | S-SEIR 特有 |
| ExpressionRole 后置归档 | S-SEIR 结构化归档 | S-SEIR 特有 |
| SecurityFacts | S-SEIR 行为摘要 | S-SEIR 特有 |
| ProjectionPolicy | 当前删除 | 不适用 |

## 17. 当前输出文件

Pipeline 输出两类文件：

```Plain
JSON:
  outputs/*.sseir_check.json

Text:
  outputs/*.sseir_check.txt
```

JSON 用于后续程序处理；Text 用于人工检查。

## 18. 当前原则

1. S-SEIR 是语义模型，不是源码替换器。
2. 凡是原模块已有成熟算法的部分，优先复用原模块算法。
3. S-SEIR 特有部分只负责统一归档、结构化表达和跨模块引用。
4. 不通过 `confidence` 表达不确定性。
5. 不确定内容通过 `unresolved_reason`、`MemoryTop`、`unknown`、`PathConditionedStorage*` 等结构显式表示。
6. `keccak256` 只有被 storage consumer 使用时才作为 slot 激活。
7. event 必须 topic0 精确匹配，不能按名称或 ERC20 习惯强行恢复。
8. external call 未知 ABI 不强行恢复为高级调用。
9. precompile 可以按原模块规则提升为 Solidity 内置语义。
10. ProjectionPolicy 当前不参与输出，后续需要成熟规则后再考虑恢复。

## 19. 近期补充实现

本节记录 6.25 / 7.4 之后继续补充到 S-SEIR 中的实现。核心目标仍然是：

- 不重新臆造轻量算法；
- 保留原模块中 CFG、MemorySSA、语义终点倒推、path-sensitive 恢复等关键思路；
- 将新的修复统一进入 S-SEIR 的 effect / overlay / solidity-like 输出。

### 19.1 Opaque If 预处理

解决的问题：

混淆样例中存在大量恒真或恒假的 Yul `if`，如果直接进入 MemorySSA 和后续 slot 恢复，会产生多余 path，甚至让本来可以恢复的 slot 被保守标记为 unknown。

实现位置：

```Plain
scripts/s_seir/s_seir_opaque_preprocess.py
scripts/s_seir/s_seir_branch_preprocess.py
```

实现方法：

1. 在 branch materialization 之前，先调用 solc AST 定位 Yul `if`。
2. 只识别保守代数恒等式，不做符号证明。
3. 如果条件静态恒真，则去掉 `if` 外壳并保留 body。
4. 如果条件静态恒假，则删除 body。
5. 预处理报告中记录每次 rewrite 的源码位置、条件、判断原因。

当前支持的典型模式：

```Yul
if eq(x, add(x, 0)) { ... }
if eq(mul(a, b), mul(b, a)) { ... }
if iszero(sub(x, x)) { ... }
if iszero(xor(x, x)) { ... }
if eq(x, and(x, x)) { ... }
```

示例：

```Yul
if eq(gcf9eyl, add(gcf9eyl, 0)) {
    mstore(ptr, value)
}
```

预处理后：

```Yul
mstore(ptr, value)
```

在 EUROS 样例中，预处理报告显示：

```Plain
opaque_rewrites 11
branch_rewrites 0
total_rewrites 11
```

含义是：该样例实际只需要剪掉 opaque 条件，不需要进一步做分支物化。

### 19.2 MemorySSA 与 SinkResolver 的职责边界

解决的问题：

引入 SinkResolver 后，需要避免它替代 MemorySSA，导致原有 word-level / path-sensitive / lazy resolve 能力被削弱。

当前规则：

```Plain
MemorySSA 是主查询层。
SinkResolver 是语义终点补充整理层。
```

实现方式：

1. `EffectLifter` 在 `keccak256 / log / return / revert / call` 等语义终点处调用 MemorySSA。
2. MemorySSA 查询结果仍保存在 effect attrs 中，例如：

```Plain
memory_read
data_memory
input_memory
payload_memory
```

3. `SemanticOverlayBuilder.build()` 开头调用：

```Python
effects = SinkResolver().attach_all(effects)
```

4. SinkResolver 只把已有 MemorySSA / byte-axis 查询统一整理为：

```Plain
effect.attrs.sink_resolution
```

5. Overlay 恢复时优先消费 MemorySSA word / value_versions；当 byte slice、packed hash、path-sensitive data 更适合表达时，再使用 `sink_resolution`。

示例：

```Yul
mstore(0, amount)
log3(0, 32, APPROVAL_TOPIC, owner_, spender)
```

MemorySSA 记录：

```Plain
memory[0..32] = amount
```

SinkResolver 补充：

```Plain
sink_kind = EventLog
data path = amount
```

Overlay 生成：

```Solidity
emit Approval(owner_, spender, amount);
```

### 19.3 状态变量读取作为 Mapping Key 的归一

解决的问题：

有些代码先通过 `sload(x.slot)` 读取状态变量，再把读取值写入 memory 参与 mapping slot 计算。旧展示中会出现：

```Solidity
p[sload(d.slot)] = 1;
```

甚至曾出现无目标读取展示为：

```Solidity
d = ;
```

这既不利于审计，也不符合 S-SEIR 对状态变量读写的表达习惯。

实现方法：

1. `sload(d.slot)` 被识别为 `StateVariableRead`。
2. 如果该读取没有显式赋值目标，则 overlay 中展示为：

```Solidity
read d;
```

3. 如果该读取进入 MemorySSA word，并最终被 `keccak256` / `sload` / `sstore` 消费，则在 mapping key 中归一为状态变量名。

示例，TKM constructor：

```Yul
mstore(0, sload(d.slot))
mstore(32, p.slot)
sstore(keccak256(0, 64), 1)
```

恢复前：

```Solidity
p[sload(d.slot)] = 1;
```

恢复后：

```Solidity
read d; // yul: mstore(0, sload(d.slot))
p[d] = 1; // yul: sstore(keccak256(0, 64), 1)
```

语义模型记录：

```Json
{
  "kind": "StateVariableRead",
  "attrs": {
    "access": "d",
    "state_variable": "d",
    "solidity_like": "read d;"
  }
}
```

以及：

```Json
{
  "kind": "MappingWrite",
  "attrs": {
    "access": "p[d]",
    "state_variable": "p",
    "key": "d",
    "value": "1",
    "solidity_like": "p[d] = 1;"
  }
}
```

### 19.4 Mapping Slot 类型与终端值标记

解决的问题：

多维 mapping 的中间 slot 本身不是最终 storage value。例如：

```Solidity
mapping(address => mapping(address => uint256)) m;
```

其中：

```Plain
m[owner_]
```

只是下一层 mapping 的 base slot，不应当被当作最终 `uint256` storage value。

实现方法：

在 `MappingSlot / MappingRead / MappingWrite / PathConditionedStorage*` 的 attrs 中补充：

```Plain
result_type
terminal_storage_value
```

规则：

```Plain
result_type 仍是 mapping(...) -> terminal_storage_value = false
result_type 是 uint/bool/address 等普通类型 -> terminal_storage_value = true
无法判断 -> null
```

示例，TKM allowance：

```Yul
mstore(0, owner_)
mstore(32, m.slot)
let ac := keccak256(0, 64)
mstore(0, spender)
mstore(32, ac)
ab := sload(keccak256(0, 64))
```

恢复：

```Solidity
ac = slot(m[owner_]);
ab = m[owner_][spender];
```

语义模型中：

```Json
{
  "kind": "MappingSlot",
  "attrs": {
    "expression": "m[owner_]",
    "result_type": "mapping(address => uint256)",
    "terminal_storage_value": false
  }
}
```

```Json
{
  "kind": "MappingRead",
  "attrs": {
    "access": "m[owner_][spender]",
    "result_type": "uint256",
    "terminal_storage_value": true,
    "solidity_like": "ab = m[owner_][spender];"
  }
}
```

### 19.5 SSA 版本驱动的 Nested Mapping Base 追踪

解决的问题：

同一个 Yul 变量名可能在不同位置被重新定义。如果只按变量名查找 mapping base，会把不同 SSA 版本错误合并，或者得到 `unknown_storage_slot_version`。

典型场景：

```Yul
let MuuR := keccak256(ptr, 64)   // BVNo[fgMs]
let LjAi := keccak256(ptr, 64)   // BVNo[fgMs][_owner]
...
MuuR := keccak256(ptr, 64)       // BVNo[_owner]
LjAi := keccak256(ptr, 64)       // BVNo[_owner][_spender]
sstore(LjAi, sub(dhzw, _amount))
```

实现方法：

1. MemorySSA query word 中附加 value SSA versions。
2. overlay builder 在恢复 `keccak256(ptr, len)` 时，不只查询文本变量名，也查询对应 SSA version。
3. nested mapping 的 base lookup 使用 `word_value_keys()`，优先匹配当前 reaching definition。
4. 如果同一 storage sink 有多个 path/SSA 候选，则生成 `PathConditionedStorageRead/Write`，而不是猜一个结果。

EUROS `_spendAllowance` 当前恢复：

```Solidity
IUGo = BVNo[fgMs][_owner];
dhzw = BVNo[_owner][_spender];
BVNo[fgMs][_owner] = (dhzw - _amount);
BVNo[_owner][_spender] = (dhzw - _amount);
```

语义模型中最后的写入为：

```Json
{
  "kind": "PathConditionedStorageWrite",
  "attrs": {
    "candidates": [
      {
        "status": "resolved",
        "access": "BVNo[fgMs][_owner]",
        "solidity_like": "BVNo[fgMs][_owner] = (dhzw - _amount);"
      },
      {
        "status": "resolved",
        "access": "BVNo[_owner][_spender]",
        "solidity_like": "BVNo[_owner][_spender] = (dhzw - _amount);"
      }
    ]
  }
}
```

这符合当前规则：同一语义终点在不同 SSA/path 下存在多个可达版本时，全部结构化保留。

### 19.6 单 Word Hash 与 Mapping Hash 的展示区分

解决的问题：

同样是：

```Yul
keccak256(0, 32)
```

它可能不是 mapping slot，而是对单个 word 做 hash。例如 TKM 中用于生成权限 key：

```Yul
mstore(0, caller())
let ad := keccak256(0, 32)
```

实现规则：

```Plain
keccak256(ptr, 64) 且 memory words 形如 key + stateVar.slot
-> mapping slot

keccak256(ptr, 32) 且只有一个完整 word
-> keccak256(abi.encode(value))

packed / overlapping memory
-> keccak256(abi.encodePacked(...))
```

示例：

```Yul
mstore(0, caller())
let ad := keccak256(0, 32)
```

恢复：

```Solidity
ad = keccak256(abi.encode(msg.sender));
```

而：

```Yul
mstore(0, owner_)
mstore(32, m.slot)
let ac := keccak256(0, 64)
```

恢复：

```Solidity
ac = slot(m[owner_]);
```

### 19.7 Path-sensitive 展示层更新

解决的问题：

语义模型中已经能记录 `PathConditionedStorageWrite`、`PathConditionedEventEmit` 等路径敏感结果，但 solidity-like 如果直接线性输出，会丢失“该语句在哪个 condition 下成立”的审计信息。

当前展示规则：

1. 每个 path-conditioned candidate 带有自己的 condition。
2. 如果不同 candidate 恢复为同一语义，语义模型仍可保留多条 path；展示层可以合并或拆分。
3. 对相同语义但不同 condition 的事件、状态读写，会按 condition 展开。
4. 对已知条件树关系，展示层尽量生成嵌套 `if`。

示例，TKM `C()` 中 `Transfer` 事件：

语义模型记录为 `PathConditionedEventEmit`：

```Json
{
  "kind": "PathConditionedEventEmit",
  "attrs": {
    "event": "Transfer",
    "candidates": [
      {
        "condition": "!(condition) && !(lt(fromBalance, amount)) && !(and(shouldTakeFee, isFeeRouter))",
        "args": ["from", "to", "amount"]
      },
      {
        "condition": "condition && !(lt(fromBalance, amount)) && and(shouldTakeFee, isFeeRouter)",
        "args": ["from", "to", "amount"]
      }
    ]
  }
}
```

solidity-like 展示为：

```Solidity
if (!(condition) && !(lt(fromBalance, amount)) && !(and(shouldTakeFee, isFeeRouter))) {
    emit Transfer(from, to, amount);
}
if (condition && !(lt(fromBalance, amount)) && and(shouldTakeFee, isFeeRouter)) {
    emit Transfer(from, to, amount);
}
```

当前边界：

展示层可能比源码更啰嗦，但不会为了美观合并掉必要条件。后续可继续做 condition tree 归并和公共前缀提升。

### 19.8 TKM 样例重跑结果

重跑目标：

```Plain
TOKENS/assembly样本/0x1e0847e537f75e6a983828c7f8ebf5a8108107d6/TKM.sol
```

输出目录：

```Plain
outputs/assembly样本_sseir_by_contract/0x1e0847e537f75e6a983828c7f8ebf5a8108107d6__TKM
```

重跑后语义检查：

```Plain
A:
  require(i == address(0))
  factoryAddr = j
  usdtAddr = h

totalSupply:
  z = k

balanceOf:
  aa = l[acc]

allowance:
  ab = m[owner_][spender]

approve:
  check = o[ad]
  require(check != 0)

setMaxs:
  check = o[ae]
  o[af] = 1

transferFrom:
  currentAllowance = m[from][msg.sender]
  m[from][msg.sender] = ah

B:
  m[owner_][spender] = amount
  emit Approval(owner_, spender, amount)
```

本次重跑相对旧输出的主要变化：

```Plain
1. constructor 中 sload(d.slot) 类 mapping key 已归一为 d/e/f/g。
2. MappingSlot / MappingRead / MappingWrite 增加 result_type 与 terminal_storage_value。
3. D() 的 solidity-like 条件展示更 path-sensitive，但语义模型未回退。
```

### 19.9 当前仍保守保留的情况

1. event topic0 必须完整 32 字节匹配。近似 topic 不强行识别为标准事件。
2. unknownEvent 仍作为保守事件恢复结果保留。
3. `read d;` 这类无目标状态读取是正确语义，但展示形式后续可继续优化。
4. Path-conditioned 展示可能存在重复候选或长 condition，当前优先保证语义不丢失。

## 20. 当前关键算法实现

本节是对当前 `scripts/s_seir/` 实际代码的复核记录。前文中的设计目标或
早期方案与本节不一致时，以本节和当前代码为准。

### 20.1 Function-level CFG 融合

实现位置：

```Plain
scripts/s_seir/s_seir_control_builder.py
scripts/legacy_yul/assembly_ast_cfg.py
```

核心算法：

1. 以 `FunctionUnit` 为分析单位，而不是以单个 assembly block 为最终单位。
2. Solidity 控制流优先使用 Slither function CFG。
3. Yul 节点使用 `assembly_ast_cfg` 从 solc AST 构建的 CFG。
4. 通过 source range 将 Slither 的 assembly 占位节点替换为对应 Yul 子图。
5. 保留 Solidity block 到 assembly entry、assembly exit 到后续 Solidity block 的边。
6. Solidity `if/for/while` 中的 assembly 块会获得 function-level condition/loop context，
   `EffectLifter.assembly_entry_conditions()` 再把这些条件加到 Yul effect 的 `path_states`。
7. Slither 不可用或匹配失败时，使用 solc AST skeleton fallback，并在 `control.notes`
   中保留 fallback 原因。

示例：

```Solidity
if (enabled) {
    assembly { sstore(slot, value) }
}
```

`StorageWrite.path_states` 不仅包含 Yul 内部条件，还包含 `enabled`。后续
`PathConditionedStorageWrite` 可以记录 `condition=enabled`。

### 20.2 Loop-aware Lazy MemorySSA

实现位置：

```Plain
scripts/s_seir/s_seir_memory_ssa.py
scripts/legacy_yul/assembly_memory_ssa.py
scripts/legacy_yul/assembly_cfg_memory_adapter.py
```

S-SEIR 持有 `SSeirMemorySSAView`，底层复用原模块的 CFG MemorySSA 和 loop lazy resolve。
实际流程为：

```Plain
Yul CFG
  -> analyze_block
  -> PathState / value version / memory version
  -> LoopMemoryRecord
  -> sink 查询时 resolve_memory_read_with_loops
  -> MemoryPhi / MemoryRangeSummary / MemoryTop
```

关键规则：

1. 无 memory write 的 loop 不建立 loop memory summary。
2. 存在 memory write 的 loop 在前向阶段记录 `LoopMemoryRecord`，不无限展开回边。
3. `mload/keccak256/log/return/revert/call` 等 memory sink 查询相关区间时才触发懒加载。
4. 固定次数的 affine-range 写入可按需展开。
5. 同一地址的 loop-carried 写入使用 `MemoryPhi`。
6. 符号次数的线性区间写入记录 `MemoryRangeSummary`。
7. 无法确定 alias 时只将局部区间降级为 `MemoryTop`。

```Yul
for { let i := 0 } lt(i, 3) { i := add(i, 1) } {
    mstore(add(ptr, mul(i, 0x20)), i)
}
let h := keccak256(ptr, 0x60)
```

`MemoryHash.memory_read` 可在 sink 查询时获得 `ptr+0 -> 0`、`ptr+32 -> 1`、
`ptr+64 -> 2`。

### 20.3 线性地址 alias、known-value 与字节轴

实现位置：

```Plain
scripts/s_seir/s_seir_memory_ssa.py
```

`linear_aliases_for_text()` 只追踪标识符、整数地址以及 `add/sub(base, constant)`
等线性地址关系。它通过 `PathState.values` 递归跟踪 ValueDef，为同一地址
保留多个 `base + offset` 表示；非线性表达式不强行建立等价 alias。

```Yul
let input := add(ptr, 0x0c)
mstore(input, value)
```

可同时记录 `input+0` 和 `ptr+12`。

`known_value_info()` 将可追踪来源分为 `constant`、`evm_builtin`、
`symbolic_known`、`derived_known` 和 `unknown`。`known=true` 只表示该数据有
可保留的符号来源，不表示数值已被静态求出。

MemoryByteAxis 的规则：

1. 查询长度可静态解析且不超过 4096 字节时启用。
2. `mstore` 按 32 字节写入，`mstore8` 按 1 字节写入。
3. 按 CFG node/version 顺序重放写入，后写字节覆盖早先字节。
4. 连续且来源相同的 byte cell 合并为 slice。
5. slice 保留 `source_version/source_node_id/source_range/extraction/known_value`。
6. 不同 path 分别保留在 `path_slices`。

```Yul
mstore(0x0c, _HANDOVER_SLOT_SEED)
mstore(0x00, caller())
let slot := keccak256(0x0c, 0x20)
```

查询 `0x0c..0x2c` 得到：

```Plain
[0, 20)  = bytes20(msg.sender)
[20, 32) = low_bytes(_HANDOVER_SLOT_SEED, 12)
```

因此 slot 可确定记录为
`keccak256(abi.encodePacked(bytes20(msg.sender), low_bytes(_HANDOVER_SLOT_SEED, 12)))`。

### 20.4 Function-level 多 assembly MemorySSA 桥接

当前并未将多个 assembly CFG 强行并成一个底层 MemorySSA backend，而是在
S-SEIR 查询视图中建立受限桥接：

1. 保存前一 assembly 块的 exit PathState，最多 8 份。
2. 扫描两块之间的 Solidity 源码区间。
3. 中间只有值计算时，将 exit memory clone 到下一块的 inherited state。
4. 遇到 memory allocation、`abi.*`、函数调用、低层调用、`return/revert`
   等可能改变 memory/control 的操作时中断桥接。
5. 只继承函数参数、返回值、Solidity 局部变量和状态变量的 ValueDef 名称。
6. 后块写入覆盖继承 memory，继承 version 改名为 `bridge_bN_*`，并保留
   `inherited_from_version`。

```Solidity
assembly { mstore(0x00, x) }
uint256 y = x + 1;
assembly { let h := keccak256(0x00, 0x20) }
```

第二块可查询到 `x`。如果中间改为 `bytes memory b = abi.encode(x)`，则桥接中断，
不猜测 Solidity 代码后的 memory 状态。

### 20.5 Yul 复合表达式原子化

实现位置：

```Plain
scripts/s_seir/s_seir_yul_eval_order.py
scripts/s_seir/s_seir_effect_lifter.py
```

该算法使用 Yul AST，不用字符串括号硬拆。Yul function-call 参数按实际的
从右到左顺序递归求值，每个 call 生成一个 `EvaluationStep` 与唯一临时变量。

当前覆盖：

1. 所有 Yul `if` condition。
2. 赋值语句中不是单独顶层语义终点的复合右值。
3. 复合表达式中的 `sload/mload/keccak256/call` 额外生成独立 effect。

直接 `x := sload(slot)` 仍保持为单个 StorageRead 语义终点，避免破坏现有
storage overlay 模式。

```Yul
let dhzw := add(sload(LjAi), IUGo)
```

原子化后的语义顺序：

```Plain
tmp1 = sload(LjAi)
tmp2 = add(tmp1, IUGo)
dhzw = tmp2
```

`ValueDef.attrs.atomized_value` 保留：

```Json
{
  "evaluation_model": "yul_ast_right_to_left_function_call_arguments",
  "atomization_model": "rhs_atomic_single_operation_steps",
  "steps": [
    {"temp": "tmp1", "call": "sload", "expression": "sload(LjAi)"},
    {"temp": "tmp2", "call": "add", "expression": "add(tmp1, IUGo)"}
  ],
  "final": "tmp2"
}
```

### 20.6 语义终点查询与 SinkResolver

实现位置：

```Plain
scripts/s_seir/s_seir_effect_lifter.py
scripts/s_seir/s_seir_sink_resolver.py
scripts/s_seir/s_seir_semantic_normalizer.py
```

语义终点包括：

```Plain
MemoryHash
StorageRead / StorageWrite
EventLog
Call / StaticCall / DelegateCall / CallCode
Return / Revert
```

处理顺序：

1. `EffectLifter` 在 sink 当前 CFG node 查询 MemorySSA，挂载 `memory_read`、`input_memory`、
   `data_memory` 或 `payload_memory`。
2. 查询保留 `path_states`、SSA version、word、byte slice、loop fact 和 unknown。
3. `SinkResolver.attach_all()` 不重建内存，只把已有查询按 sink role 统一为
   `sink_resolution.path_resolutions`。
4. 每条 path 单独记录 `condition/status/arg_resolutions`。
5. slice signature 包含 `source_version` 和 `source_node_id`，同名变量的不同 SSA
   来源不会被提前合并。
6. `SemanticNormalizer` 将最终查询状态记为 `SemanticSinkMemoryQuery`，区分
   `memory_ssa`、`sink_resolver`、`semantic_overlay_pattern` 等解析来源。

查询优先级：

```Plain
MemorySSA / loop lazy result
  -> MemoryByteAxis
  -> SinkResolver 逐路径整理
  -> 严格高级语义模式
  -> unresolved
```

SinkResolver 恢复成功不会删除或覆盖原 MemorySSA 证据。

### 20.7 分支化 SSA 候选与语义物化

对于同一源码 sink，如果不同 CFG path 到达时的参数 SSA 不同，当前算法不选择
其中一个，而是生成逐 path candidate：

```Json
{
  "kind": "PathConditionedStorageWrite",
  "attrs": {
    "candidates": [
      {"condition": "!(cond)", "status": "resolved", "access": "balances[a]"},
      {"condition": "cond", "status": "resolved", "access": "balances[b]"}
    ]
  }
}
```

等价候选只在高级语义已恢复后去重。去重 key 包含 `status`、`condition`、
`overlay_kind`、`access/target/value`、`state_variable/key`、`storage_model` 和
`unresolved_reason`。不同 condition、resolved/unresolved 或不同访问对象绝不合并。

预处理阶段还会使用 `s_seir_opaque_preprocess.py` 对少量可静态证明的 Yul
代数恒等式剪枝。无法静态证明的 condition 不剪枝。

### 20.8 Storage sink 驱动的 slot 恢复

当前不对所有 `keccak256` 猜测 mapping。slot 恢复以 `sload/sstore` 为消费终点：

1. `StorageRead/StorageWrite` 记录 slot 表达式及 reaching `slot_versions`。
2. slot 内联 `keccak256` 时，`EffectLifter.inline_keccak_hash_effect()` 先生成独立
   `MemoryHash`。
3. Overlay 按 slot version 回溯 ValueDef/MemoryHash，查找真正到达 sink 的 hash 候选。
4. MemoryHash 通过 MemorySSA word 或 byte-axis 恢复 key/base。
5. base slot 通过 solc storage layout、状态变量信息或 storage-reference `.slot` 校验。
6. 只有 hash 被 storage sink 消费且 key/base 模式完整时，才激活
   `MappingSlot/MappingRead/MappingWrite`。
7. 多维 mapping 递归使用前一层 MappingSlot 作为下一层 base，直到状态
   读写终点。
8. 无法恢复的版本保留 `unknown_storage_slot_version`，不借用另一 path 的 slot。

```Yul
mstore(0, owner)
mstore(32, allowances.slot)
let h1 := keccak256(0, 64)
mstore(0, spender)
mstore(32, h1)
let value := sload(keccak256(0, 64))
```

恢复链：

```Plain
h1    = slot(allowances[owner])
slot2 = slot(allowances[owner][spender])
value = allowances[owner][spender]
```

非常规 packed slot 仍归档为状态读写，但附带
`storage_model=manual_packed_hash_slot`、`slot_derivation.kind` 和 `packed_inputs`。

### 20.9 Call input/output 与 selector 恢复

实现位置：

```Plain
scripts/s_seir/s_seir_effect_lifter.py
scripts/s_seir/s_seir_sink_resolver.py
scripts/s_seir/s_seir_overlay_builder.py
scripts/s_seir/s_seir_selector_registry.py
```

输入恢复：

1. Call effect 先保留 `target/gas/value/input_ptr/input_size/output_ptr/output_size/result`。
2. `input_ptr/input_size` 作为 memory sink 查询 MemorySSA 和 byte-axis。
3. `SinkResolver` 按 path 保留 calldata slice。
4. 只有 selector 字节可定位，且参数 offset/size 与 ABI 布局匹配时，才生成
   `AbiCallDataConstruction/AbiEncodedLowLevelCall`。
5. selector registry 由 Solidity AST 的 function/error 规范签名计算。不匹配时保留
   selector 和 low-level call，不猜测接口。
6. 固定 precompile 地址可按已知 EVM 语义提升，例如 address `2` 的 `staticcall`
   可记录为 SHA-256 precompile。

输出 def-use：

1. 在后续 `mload(output_ptr)` 之前查找最近的 path-compatible call。
2. 指针必须线性等价，两者之间不得存在重写。
3. 匹配后记录 `value_from_call_output=call_output_word(call_temp, 0)` 和
   `reads_after_call_output`。
4. `returndatasize()` 作为指针时保留多路径 candidate，不将不兼容路径合并。

```Yul
let ok := staticcall(gas(), 2, input, 20, output, 32)
let digest := mload(output)
```

可记录：

```Plain
PrecompileCall(precompile=sha256, input=<resolved 20 bytes>)
PrecompileOutputRead(target=digest, source_call=ok, output_index=0)
```

### 20.10 Revert、Return 与 Event 的证据模式

这三类 overlay 都先保留底层 effect，再对 sink 输入做模式匹配。

Revert 模式：

```Plain
revert(0, 0)
  -> 空 payload；可结合最近条件生成 RequireOverlay

mstore(0, selector); revert(0x1c, 4)
  -> byte-axis 取低 4 字节
  -> selector registry 精确匹配
  -> CustomErrorRevert

revert(add(buf, 32), mload(buf)), buf: bytes memory
  -> RawRevertBytes
```

`RequireOverlay` 不会无条件合并所有上层 condition。只有中间条件块没有其他
有效操作时才能向上合并；否则保留最近 guard。

Yul `return(ptr,size)` 始终首先记录 `Return` 语义终点。当 memory payload 可解析时，
记录 `RawReturnData` 或 `PathConditionedRawReturnData`，保留 raw ABI payload。
不会因为 `mstore(0,x); return(0,32)` 就一律强制改写为 Solidity `return x`。

Event 模式：

1. 从 Solidity event 定义计算完整 32-byte topic0。
2. 非 anonymous event 要求 topic0 完全相等，且 topic 数等于 indexed 参数数量加 1。
3. data 区间通过 MemorySSA/SinkResolver 恢复。
4. 不同 path 下参数不同时生成 `PathConditionedEventEmit`。
5. 仅 topic 前缀相似时保留 `unknownEvent` 和 `topic0_near_misses`。

### 20.11 Memory object、struct 与 array 模式

实现位置：

```Plain
scripts/s_seir/s_seir_overlay_builder.py
scripts/s_seir/s_seir_type_env.py
```

这些高级语义不依赖“整个函数只做一件事”，而是从局部 effect 子图做模式匹配。

| 模式 | 必要证据 | 高级 overlay |
|---|---|---|
| 手工 memory allocation | 读 `mload(0x40)`，计算新 free pointer，写回 `mstore(0x40, ...)` | `MemoryRegionAllocate` |
| 分配区域写入 | write address 与 allocation base 线性相关 | `MemoryRegionWrite` |
| struct 字段读写 | TypeEnv 确认 `struct memory`，offset 与 AST struct layout 匹配 | `StructFieldRead/Write` |
| struct 局部初始化 | 局部多个字段绑定到同一 struct object | `StructInitializationFragment` |
| struct 局部修改 | 已知 struct object 的字段被写入 | `StructMutationFragment` |
| array length read | 参数/返回值类型是 memory array，`mload(array)` | `MemoryArrayLengthRead` |
| array element read | 地址匹配 `array + 32 + index*32` | `MemoryArrayElementRead` |
| array construction | 返回值类型是 memory array，且 effect 子图匹配 base、element write、length write、free-pointer update | `MemoryArrayConstruction` |

数组构造以函数返回值类型作为起点，但使用 CFG/effect 关系校验底层模式；
不是看到 `mload(0x40)` 就猜测为数组。

```Yul
ordinals := mload(0x40)
let ptr := add(ordinals, 0x20)
// loop writes elements through ptr
mstore(ordinals, length)
mstore(0x40, ptr)
```

当 `ordinals` 在 TypeEnv 中是 `uint8[] memory` 返回值，且上述底层 effect 子图完整匹配时，
生成 `MemoryArrayConstruction`。缺少 length write 或 free-pointer update 时只保留底层 memory effect。

### 20.12 专用语义模式补充

| 语义 | 触发证据 | 记录 |
|---|---|---|
| 零地址检查 | Branch 外壳 + address projection + TypeEnv address | `AddressZeroCheck` |
| 动态 bytes/string 内容 hash | `obj+32` + `mload(obj)` + bytes/string memory 类型 + MemoryHash sink | `BytesContentHash` |
| 地址代码长度 | ValueDef 右值为 `extcodesize(address)` | `AddressCodeSize` |
| 地址是否有代码 | 同上，左值是 bool | `AddressHasCode` |
| calldata word 读取 | ValueDef 右值为 `calldataload(offset)` | `CalldataWordRead` |
| 除数安全条件 | `div/mod` 的 divisor 与 guard 模式匹配 | `DivisionGuard` |

零地址示例：

```Yul
if iszero(shl(96, newOwner)) { revert(0, 0) }
```

只有 `TypeEnv` 确认 `newOwner` 是 address，且 projection 匹配受支持形式时，才记录
`newOwner == address(0)`。

### 20.13 保守性与无猜测原则

1. 底层 effect 不因 overlay 恢复成功而删除。
2. 高级语义必须由语义终点和完整前置模式共同证明。
3. 不同 SSA version 和 condition 的候选分开保留。
4. 只有高级语义及 condition 都等价时才去重。
5. 信息不足时记录 `unknown/unresolved/MemoryTop/unresolved_reason`，不使用惯用合约写法补齐缺失语义。
6. topic0 和 selector 按完整证据匹配，不按名称、前缀或 ERC-20 惯例猜测。
7. Solidity-like 是展示层，其 condition tree 化简不反向修改 CFG、SSA、effect 或 overlay。

这些约束使 S-SEIR 保留审计需要的不确定性，同时避免将看起来像 Solidity
误当成已经证明等价。
