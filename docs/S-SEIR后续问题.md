# S-SEIR 后续问题

## 1. Function-level Assembly 分析单元

当前 S-SEIR 已经是函数级容器，但底层 MemorySSA 仍主要以单个 `assembly {}` block 为单位。

待解决问题：

- 同一函数内多个 assembly block 共享 EVM memory。
- assembly 与 Solidity 局部变量之间的数据流。
- Solidity 语句夹在多个 assembly block 中间时，对 memory、value、state 的影响。
- 不能简单拼接多个 assembly block，需要函数级 CFG 与数据流。

目标：

```text
Function-level CFG
  -> Solidity statements
  -> InlineAssembly CFG subgraph
  -> function-level ValueSSA / MemorySSA
```

## 2. Solidity 函数级 CFG 不够精确

当前 Yul CFG 已比较完整，但 Solidity 层目前仍是 statement skeleton。

待解决问题：

- Solidity `if / while / for / return / revert / break / continue` 的准确控制流。
- Solidity CFG 与 Yul CFG 子图之间的连接。
- 函数级控制依赖、支配关系、可达路径。
- Slither CFG 或 solc AST CFG 的整合选择。

目标是让 S-SEIR 的 `control` 层真正统一 Solidity CFG 与 assembly CFG。

## 3. Function-level MemorySSA / ValueSSA

当前已有 assembly-block 级 CFG MemorySSA、Loop-aware MemorySSA、branch materialization。

待解决问题：

- 跨 assembly block 的 memory reaching definition。
- Solidity 局部变量参与 SSA。
- 命名返回值作为语义终点。
- Solidity 变量赋值、循环更新与 Yul 使用之间的 def-use。
- MemorySSA 与 ValueSSA 的统一查询接口。

典型问题：

```solidity
uint256 ptr;
assembly { ptr := add(buffer, 32) }
ptr--;
assembly { mstore8(ptr, ...) }
```

## 4. Loop-aware Lazy MemorySSA 接入 S-SEIR 查询层

当前 Loop 记录、MemoryPhi、MemoryRangeSummary、MemoryTop 已作为 facts 进入 S-SEIR。

待解决问题：

- 让 `MemoryRangeSummary / MemoryPhi / MemoryTop` 成为 Effect/Overlay 可查询对象。
- 语义终点读取 memory 时，通过统一接口触发 lazy resolve。
- storage/event/call/return/revert overlay 能直接消费 loop summary。
- loop 中嵌套 if、copy、mstore8、动态 alias 的细化处理。

## 5. Branch Materialization 结构化

当前 branch materialization 结果已经进入 `analysis_facts`，但主要仍是文本 lines。

待解决问题：

- 将分支物化结果结构化为 S-SEIR effect/overlay。
- 明确每个物化分支对应的 condition、sink、memory definitions、cloned statements。
- 让后续 storage/event/call overlay 直接引用分支物化节点。
- opaque predicate 分支丢弃策略需要更系统化。

## 6. Storage Overlay 增强

当前已能输出：

- `MappingSlot`
- `MappingRead`
- `MappingWrite`
- `StateVariableRead`
- `StateVariableWrite`

待解决问题：

- nested mapping 的更稳定表达。
- direct state slot、packed storage、array/dynamic array slot 恢复。
- `MemoryRangeSummary` 参与 slot hash 时的保守表达。
- storage read inline substitution 与 overlay 之间的引用关系。
- `slot(...)` 中间表达最终消解。

## 7. Event Overlay 增强

当前 topic0 精确匹配已接入，UnknownEvent 会保守标注。

待解决问题：

- non-indexed 参数从 MemorySSA 中结构化引用，而不只是文本。
- dynamic event data 的 ABI 解码。
- anonymous event 支持。
- topic 参数类型规范化。
- UnknownEvent 的参数展示需要区分 topic/data 来源。

## 8. External Call Overlay 增强

当前低级调用和 precompile 恢复已接入，`sha256` precompile 可提升。

待解决问题：

- 普通 call 的 ABI selector / arguments / returndata 结构化表达。
- return data 的后续使用追踪。
- call success result 与 require/revert 的关系结构化。
- delegatecall/staticcall/callcode 的差异投影策略。
- 常见 ERC20 selector 的可选识别，但不能过度猜测目标合约 ABI。

## 9. Revert / Error 恢复不完整

当前主要处理 `revert(0,0)` -> `RequireOverlay`，RawRevertBytes 有基础框架。

待解决问题：

- `Error(string)`。
- `Panic(uint256)`。
- custom error selector。
- `revert(ptr, size)` 中 memory payload 的 ABI 解码。
- raw revert bytes 与 Solidity 可表达 revert 的区分。
- RawRevertBytes 需要系统测试。

## 10. Expression Role Layer 较初级

当前已有部分 role，例如 memory slice、call 参数、revert payload、free memory pointer。

待补充 role：

- `mapping_slot_expr`
- `abi_selector`
- `abi_argument`
- `return_data_ptr`
- `return_data_size`
- `bytes_data_ptr`
- `guard_condition`
- `loop_bound`
- call target/value/gas/input/output 的统一 role 引用
- role 与 effect/overlay 的双向引用

## 11. ProjectionPolicy 仍是第一版

当前已能区分部分：

- `solidity_statement`
- `solidity_expression`
- `semantic_comment`
- `assembly_preserved`

待解决问题：

- 更严格判断是否为 exact Solidity equivalent。
- 对 helper、unknown、MemoryTop、dynamic alias 的投影降级。
- 对 raw revert payload 标记 `assembly_preserved`。
- 对 low-level call 与 precompile 的不同投影策略。
- 对 branch materialization 后的 block-level projection 判断。
- 对 memory cleanup 后是否可源码替换的判断。

## 12. Memory / Slot 中间表示消解未完成

当前 S-SEIR 能承载恢复语义，但没有完成最终清理。

待解决问题：

- 删除只用于 slot 构造的 memory writes。
- 删除已被 event/call/revert/return 消费的临时 memory 表示。
- 删除 `slot(...)` 临时变量。
- 保留未消解 memory chain 并标注原因。
- 输出更接近 Solidity 的 semantic view。

## 13. 最终源码替换未完成

当前输出是 S-SEIR JSON/TXT 和 Solidity-like semantic view。

待解决问题：

- 根据 S-SEIR overlay 生成 Solidity AST 或源码片段。
- 替换原始 `assembly {}` 块。
- 变量声明、类型、作用域补全。
- 控制流结构重建。
- `solc` 编译验证。
- 行为等价检查或关键语义对比。

## 14. LLM 使用接口未完成

当前 S-SEIR 输出已经适合后续 LLM 使用，但还不是最终输入格式。

待解决问题：

- 按函数输出安全行为切片。
- 按 overlay/effect 生成 compact prompt context。
- 过滤低价值 memory noise。
- 保留 stmt_refs 证据链。
- 明确哪些内容允许 LLM 改写，哪些必须保持确定性分析结果。

## 当前核心结论

当前最大的问题不是旧模块规则没有接入，而是：

```text
S-SEIR 已经开始统一承载旧模块结果，
但底层分析单元还没有彻底升级到 function-level，
旧模块结果也还没有完全结构化为可直接驱动源码恢复的 overlay graph。
```

## 建议优先级

1. Function-level CFG / MemorySSA。
2. Branch materialization 结构化。
3. Loop-aware MemorySSA 查询接口接入 S-SEIR。
4. Revert payload / error selector 恢复。
5. Memory-slot cleanup。
6. ProjectionPolicy 精细化。
7. 最终源码替换与编译验证。

---

## 追加：当前仍待解决的问题（2026-07-04）

本节是在前文基础上的补充总结，用于区分“已经有模块雏形或局部实现”和“仍未真正闭环”的问题。

### 1. Function-level CFG 融合仍未完成闭环

当前思路已经明确：

```text
Solidity 层 CFG 复用 Slither
Yul / inline assembly 内部 CFG 复用现有 assembly_ast_cfg
```

但仍需要完成：

- 将 Slither function CFG block 与 assembly CFG subgraph 精确拼接。
- 给 Solidity statement 与 Yul statement 统一分配 `stmt_id`。
- 明确 assembly block 进入点、退出点、fallthrough、return、revert 与 Solidity 后继节点的关系。
- 保留 Solidity loop / if / return 等控制结构，用于判断 assembly memory write 是否处于 loop 或 branch 中。

### 2. MemorySSA 已有线性 alias 增强，但还未成为统一查询层

目前 MemorySSA 已支持对 `mstore / mstore8 / copy` 地址做线性 alias 记录，例如：

```text
base1 = base0 + 4
mstore(base1 + 4, v)
```

可以同时记录为：

```text
addr = base1 + 4
addr = base0 + 8
```

但仍待解决：

- alias 查询结果还需要稳定接入 storage/event/call/revert/return 等 overlay。
- MemorySSA 的结果仍偏底层 facts，尚未形成 S-SEIR 内统一的 `MemoryReadResult` 查询接口。
- loop 中 memory write 的 `LoopMemoryRecord / MemoryPhi / MemoryRangeSummary / MemoryTop` 需要和 alias 查询统一。
- 当 memory write 位于 Solidity loop 包裹的 assembly block 中时，需要从 function-level CFG 识别 loop 上下文。

### 3. Loop-aware Lazy MemorySSA 需要和 function-level CFG 对齐

当前 loop-aware lazy 的核心规则仍然适用：

```text
前向阶段不无限展开循环
只记录 loop summary
语义终点读取 memory 时再 lazy materialize
```

但还缺少：

- 基于 function-level CFG 识别 Solidity loop 包裹的 assembly write。
- Yul loop 与 Solidity loop 使用统一的 loop 标记。
- 对 loop body 中线性 alias 地址的范围摘要。
- 将 lazy resolve 的结果直接提供给 S-SEIR Effect / Overlay，而不是只保存在调试文本中。

### 4. S-SEIR Adapter 仍偏“结果搬运”，不是完整语义图

当前 S-SEIR 已能承载旧模块输出，但很多内容仍是 adapter 导入的 facts。

待完成：

- 将旧模块文本结果升级为结构化 `EffectNode / OverlayNode`。
- 建立 effect、overlay、expr role、source statement 之间的稳定引用。
- 避免同一语义在多个模块中重复输出不同格式。
- 让后续 LLM 只消费 S-SEIR，而不是再理解各模块自己的输出格式。

### 5. Branch Materialization 尚未与 MemorySSA / Overlay 严格联动

当前分支物化的规则已经明确：

```text
只有语义终点受分支 memory 写入影响时，才物化对应分支
unknown memory 分支可保守丢弃
```

仍待解决：

- 分支物化结果需要以结构化节点进入 S-SEIR。
- 每个物化节点要记录 condition、受影响 sink、需要复制的 memory write、被丢弃分支原因。
- 后续 storage/event/call/revert overlay 应消费物化后的路径结果。
- 嵌套 if、loop 内 if、if 内 loop 的组合场景需要更多测试。

### 6. Solidity 高级语义恢复仍缺少最终 cleanup

当前 memory、slot、event、call 等模块可以恢复一部分高级语义，但最终输出仍会残留：

```text
memory[...]
slot(...)
临时 keccak 变量
低层 helper 表达式
unknown / MemoryTop
```

待完成：

- 从状态变量读写、event emit、external call、return/revert 等语义终点反向追踪。
- 删除只服务于 slot/hash/ABI 编码的 memory 写入。
- 保留真正影响最终语义的局部变量。
- 对无法删除的 memory chain 标注原因，而不是直接混入 Solidity-like 输出。

### 7. Revert / Error 与 External Call 仍需增强

Revert 侧待解决：

- `Error(string)` 识别。
- `Panic(uint256)` 识别。
- custom error selector 识别。
- `revert(ptr, size)` payload 从 MemorySSA 中 ABI 解码。
- RawRevertBytes 与 Solidity 可表达 revert 的投影区分。

External call 侧待解决：

- 普通 call 的 selector、arguments、returndata 结构化恢复。
- call success 与 require/revert 的关系建模。
- low-level call 保守投影策略。
- precompile 可提升为 Solidity 内置函数的规则表继续完善。

### 8. ProjectionPolicy 仍不能驱动最终源码替换

当前 ProjectionPolicy 只能做初步分类，仍需判断：

- 哪些 overlay 可无损恢复为 Solidity statement。
- 哪些只能保留 low-level call。
- 哪些必须保留 assembly。
- 哪些只能输出 semantic comment。
- 哪些因 `unknown / MemoryTop / dynamic alias` 不能源码替换。

这一步完成后，才能可靠进入最终源码替换。

### 9. 测试体系需要从模块样例升级到流水线样例

当前已有若干模块级测试和样例输出，但还需要：

- function-level CFG 融合测试。
- 跨 assembly block 的保守处理测试。
- Solidity loop 包裹 assembly write 的测试。
- 线性 alias 与 slot/event/call 的联动测试。
- branch materialization 后再跑 memory/storage/call/revert 的端到端测试。
- S-SEIR JSON/TXT 输出稳定性测试。

### 当前最关键的下一步

短期最关键的问题不是新增更多恢复规则，而是把已有规则统一接到同一个结构化分析链路中：

```text
Function-level CFG
  -> SourceStatementTable
  -> MemorySSA / LoopSummary / Alias facts
  -> Effect Layer
  -> Overlay Layer
  -> ProjectionPolicy
  -> Solidity-like semantic view
```

只有这一链路稳定后，后续新增规则才不会继续变成分散模块。

