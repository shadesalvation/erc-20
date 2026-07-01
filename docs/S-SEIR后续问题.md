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
