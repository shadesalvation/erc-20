# Semantic IR 工作开始前上下文

## 1. 当前仓库与分支

- 实际工作目录：`/home/ubuntu/erc-20`（小写目录）。
- 旧的大写目录已废弃，不应再作为修改来源。
- 当前分支：`semantic-ir-next`。
- 当前基线提交：`f7fe4a4 sf v0.4.2`。
- 新分支从该提交创建，用于继续 Semantic IR 相关工作。

当前工作区有运行产生的 `__pycache__` 改动、依赖目录和未跟踪输出；它们不是本次功能代码改动，不应清理或提交，除非后续任务明确要求。

## 2. 已回退到的状态

此前曾加入独立的 Yul 原子化模块及其对接逻辑。它会将一个 Yul 操作拆成多个操作，但后续部分仍同时消费源操作与拆分操作，导致 Semantic Fact 中出现重复结果。

用户要求回到 **未接入该独立 Yul 原子化模块** 的版本，因此当前基线是 `f7fe4a4`。已确认当前版本中不存在：

- `s_seir_yul_atomic_ops.py`；
- `YulAtomicOperationExtractor`；
- 独立 Yul 原子操作表及其 pipeline 接入；
- 以该表替换原 Yul 输入的适配逻辑。

注意：当前 S-SEIR 仍保留早期、内置的 RHS 求值步骤记录机制（`EvaluationStep`），用于条件和复合 Yul 右值的求值顺序表达；这不是已回退的独立 Yul 原子化模块。

## 3. 当前真实 pipeline

当前入口是：

`scripts/s_seir/s_seir_pipeline.py`

当前主流程为：

```text
Solidity source
  -> 可选 branch preprocess
  -> solc AST / Slither CFG / SourceStatementCollector
  -> function-level ControlBuilder
  -> SolidityAtomicOperationExtractor（仅 Solidity 部分）
  -> MemorySSA
  -> Expression Role Analyzer
  -> EffectLifter（include_solidity=False，即只提升 Yul effect）
  -> branch materialization
  -> SemanticOverlayBuilder
  -> SemanticNormalizer
  -> S-SEIR Function model
  -> Semantic Fact adapter + bridge
```

关键边界：

- S-SEIR 只处理 inline Yul/assembly；不要用 S-SEIR 的组件去分析 Solidity。
- Solidity 侧由 Slither / `SolidityAtomicOperationExtractor` 得到原子操作，再由 `SoliditySemanticLifter` 组织为与 Yul 相同 schema 的 Semantic Fact。
- Yul 侧先经 S-SEIR 恢复 effect、overlay，再由 `YulSemanticLifter` 组织为同一 schema 的 Semantic Fact。
- `SemanticFactBridge` 按 function-level CFG 将两侧 fact 重新组织。

当前 pipeline 输出：

- S-SEIR JSON/text；
- Semantic Facts JSON；
- debug JSON；
- function-level CFG DOT；
- Solidity-like 输出；
- Solidity 原子操作审计输出（传入对应命令行参数时）。

## 4. Semantic IR 的当前事实

当前提交 **没有** 把 Semantic IR 接入 pipeline。

已核对 `scripts/s_seir/s_seir_pipeline.py`：CLI 与 `main()` 中没有 Semantic IR builder、exporter 或 Semantic IR 输出参数。

工作目录中可能仍出现：

```text
outputs/_sample10_current_recheck/semantic_ir.json
outputs/_sample10_current_recheck/semantic_ir.txt
```

这些文件是回退前在更晚版本生成的遗留输出：其时间早于当前重跑的 `sseir.json` 与 `facts.json`，不能作为当前 `f7fe4a4` 的 Semantic IR 结果审查，也不能被视为当前 pipeline 的产物。

历史上 Semantic IR 实现位于后续提交（例如 `6c8ccc4 s-ir v0.1.0` 及之后的版本）。是否、以及如何将它恢复或重新设计并接入 `semantic-ir-next`，是接下来的工作内容。

## 5. 已确认的项目规则

所有后续设计、修改、集成和测试遵循以下约束：

1. 尽量保留并扩展既有组件，特别是 MemorySSA、SinkResolver；不要为局部问题做大规模重写。
2. 若某个既有功能不完整，可补充规则、数据结构或查询行为，但不能破坏无关功能。
3. 所有执行顺序、reaching definition、数据追踪和 backward tracing 都必须依据 function-level/Yul CFG，不能以源码顺序或线性扫描代替 CFG。
4. 使用 CFG reachability、支配关系、SSA/Phi、def-use、liveness、fixed-point 等常规编译原理/静态分析方法；证据不足时保留 unresolved/unknown，不猜测语义。
5. 一个替换 pass 只能有一个规范化的下游输入。新表示接入 pipeline 后，旧表示只能作为 source evidence，不能再生成重复 effect、overlay、fact 或 IR instruction。

## 6. Semantic Fact 的目标与当前情况

最终 Semantic Fact 的目标是统一记录 Solidity 与 Yul 两部分：

```text
在何种 condition 下，按何种 CFG 顺序，对哪个状态位置，执行何种原子操作。
```

它服务于两个后续任务：

- 基于统一 IR 的解混淆，再交由 LLM 重建源码；
- 基于原子操作和状态变更模式的逻辑漏洞检测/模式匹配。

当前 Semantic Fact 对 Yul 的状态读写、mapping slot、多维 mapping、事件、revert/require、外部调用等高级语义已有较好的恢复能力；但复合表达式仍可能同时保留求值步骤与原完整 `ValueCompute`，尚未形成严格的单一原子化下游表示。

## 7. 样例 10 的最新重跑

样例源码：

`人工构造样例/10_大量Yul_统一语义模型/contracts/YulHeavyERC20.sol`

当前版本重跑输出目录：

`outputs/_sample10_current_recheck/`

其中当前有效输出：

- `sseir.json` / `sseir.txt`；
- `facts.json`；
- `debug.json`；
- `solidity_like.txt`；
- `cfg/` 下的 11 个 function CFG DOT 文件。

结果概览：

- 处理了 11 个函数；
- 生成 117 条 Semantic Fact：Solidity 来源 9 条、Yul 来源 108 条；
- `_totalSupply`、`_balances`、`_allowances` 及二维 mapping 均正确恢复；
- `Transfer` / `Approval` topic 正确恢复；
- `staticcall` 正确识别为 `balanceOf(address)`，并恢复 selector `0x70a08231` 与参数 `account`；
- named return 通过 Slither SSA 名称和 `resolved_operands` 对接回 Yul 变量；
- `batchBalanceSum` 的循环 CFG 正确，但 `i`/`result` 尚无显式 loop-carried ValuePhi，且 loop header 存在保守的 `unknown(loop-carried-memory)` 记录；
- `transferFrom` 在无限 allowance 与普通 allowance 的互补路径下记录了两个参数相同的 `Transfer` fact：语义未错，但可在未来展示/归档层合并。

## 8. 已讨论但尚未实施的 Semantic IR 方案

用户提出：既然独立 Yul 原子化已去除，后续可以从 Semantic Fact 出发，在进入 Semantic IR 前进行原子化。

已达成的设计结论：可以采用：

```text
Semantic Fact
  -> Fact-level canonical atomization pass
  -> Semantic IR
```

该 pass 应遵守：

1. 只拆 fact 内部已经确认的复合表达式，不重新猜测 slot、event、call 或 storage 含义。
2. 保留 `StateWrite`、`StateRead`、`EventEmit`、`ExternalCall`、`Require` 等语义终点；仅将其 operand 的复合计算拆成 SSA 临时值。
3. 使用结构化 AST/effect/overlay 数据，不可仅靠 JSON 中的表达式字符串硬拆。
4. 新生成的原子 fact 继承原 fact 的 CFG block、condition、source reference 与路径语义。
5. 在 CFG 中把计算步骤放在终点之前；合流和循环处使用现有 SSA/Phi 规则。
6. 被替换的原复合 fact 不得再进入 Semantic IR，以符合“单一规范化下游输出”规则。

例子：

```text
原 Semantic Fact:
  StateWrite(_balances[to], _balances[to] + value)

原子化后:
  StateRead(tmp1, _balances[to])
  ValueCompute(tmp2, tmp1 + value)
  StateWrite(_balances[to], tmp2)
```

三条新 fact 必须位于原状态写入所在 CFG block，继承相同路径 condition。若原模型中已有同一 `StateRead`，应按 operation identity、SSA version、CFG node 复用，而不是重复生成。

## 9. 接下来建议的起点

1. 从历史 Semantic IR 提交中只阅读其模型、builder、exporter 与测试，确认哪些部分可复用；不要直接假定旧模块与当前 `f7fe4a4` 的 Semantic Fact schema 兼容。
2. 定义当前分支的 Semantic IR 输入边界：使用 pipeline 内存中的 Function Semantic Fact 图，而不是仅消费输出 JSON 字符串。
3. 实现或恢复 Fact-level canonical atomization pass，并明确它与现有内置 Yul `EvaluationStep` 的去重和替换关系。
4. 接入 Semantic IR builder/exporter，新增 CLI 输出，并使用样例 10 与至少若干控制流/多维 mapping 回归样例验证。
5. 每次接入都确认新结果已到最终输出、旧表示不再重复出现。

