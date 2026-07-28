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
  -> solc AST
  -> event definitions
  -> storage layout
  -> SourceStatementCollector
  -> TypeEnv
  -> ControlBuilder
  -> MemorySSA views
  -> ExpressionRoleAnalyzer
  -> EffectLifter
  -> BranchMaterialization
  -> SemanticOverlayBuilder
  -> SemanticNormalizer
  -> SecurityFactBuilder
  -> FunctionSSEIR
```

对应代码顺序：

```Python
ast = compile_source_ast(...)
events = parse_events_from_source(...)
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
