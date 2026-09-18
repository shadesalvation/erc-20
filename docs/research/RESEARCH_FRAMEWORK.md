# Research Framework — frozen v1

P0-T3 冻结的研究约束。研究框架限定目标与主张，不代表各模块已实现。具体实现接口见 [IMPLEMENTATION_PLAN](../../IMPLEMENTATION_PLAN.md)。本文件取代候选模板作为正式约束，模板保留为历史输入。

## Research Question 与 Final Semantic Target

给定源码级混淆 Solidity / Yul，恢复真实状态转移语义，而非恢复原始源码：

`Guard + Persistent State / State Read + State Update + External Effect + Failure + Execution Order`。

## End-to-End Pipeline 与模块职责

```text
Solidity / Yul → SFIR → Module 1 → Module 2 → Module 3 → Canonical STIR → Semantic Validation
```

| Module | 研究问题 / 唯一职责 | Stable output |
| --- | --- | --- |
| 1 — Core Semantic Control Recovery | 哪些关键行为能够发生，在什么条件下发生；候选结构恢复、局部 feasibility refinement、Guard 规范化 | Feasible Semantic Control Edge + Normalized Guard |
| 2 — Sink-Guided State Dependency Recovery | 行为由哪些输入、旧持久状态和数据/控制依赖决定；先 StorageWrite，再共享机制扩展 Multi-Sink | State / Sink Dependency Summary |
| 3 — Order-Aware State Transition Reconstruction | 将前两模块事实按可证执行偏序组成状态转移，显式保留失败/提交/回滚 | Canonical State Transition IR |

Module 1 的稳定输出同时保留 unresolved/rejected 记录，不能使下游将未证明边当作不存在。Module 2 直接消费 Module 1 的 feasible control / normalized Guard，不重新恢复控制。Module 3 直接消费 Module 1 + Module 2，不重新分析它们。SFIR 是三者共享基础设施；STIR 是语义摘要，不是替代 SFIR 的另一套底层 IR。

## Experiment Mapping

Experiment 1 ↔ Module 1；Experiment 2 ↔ Module 2；Experiment 3 ↔ Module 3。Phase 6 ablation 只验证设计选择，不改变生产 pipeline。Whole-program symbolic execution 只允许作为受预算约束的独立实验 baseline，生产仅对候选结构作局部 refinement。

## Evaluation Principle

clean / obfuscated 两侧分别经过相同版本与配置的 recovery pipeline，在 canonical semantic representation 比较。源码文本相似度不是主指标；原始 CFG 不是最终语义答案；原始源码、clean 内部事实、expected transition 不得用来帮助另一侧 recovery。

Recovery pipeline 永远不能读取 expected / frozen oracle。只有 evaluator、benchmark validation、test layer 可以读取 oracle。未知结果、失败、超时及不支持样例必须显式报告，不得修改 oracle、删除失败样例或放宽判定制造通过。语义证据和比较的完备性限制须与结果一同保留。

## Scope

主体是 ERC-20 风格合约及核心 persistent-state semantics。Solidity / Yul / Mixed 是表示鲁棒性维度，支持范围以已验证 frontend 与显式诊断为准，不由研究目标推定所有表示已获支持。较完整合约只提供补充外部有效性证据。

不追求覆盖所有 Solidity/Yul 合约、所有混淆模式、通用源码恢复、通用 alias analyzer、任意地址算术、完整跨合约/代理/重入分析。新增能力须直接服务三个模块、对应实验或必要工程支撑。

## Global Invariants

- Module 2 复用 Module 1；Module 3 消费 Module 1 + Module 2。
- Order 是最终语义，使用 partial order；不能强制 total order。无法证明时保留 incomparable / unknown。
- CFG、SSA、已验证的支配关系及数据/状态/控制依赖提供顺序证据；source position、semantic id、偶然 list order 不能代替最终 order evidence。
- Failure / revert condition / commit / rollback 必须显式；发生过的写与最终提交写区分。
- UNKNOWN / TIMEOUT / UNSUPPORTED / TRUNCATED / FALLBACK / OPAQUE / INSUFFICIENT_EVIDENCE 不得静默成为成功、不可行或无依赖。
- SFIR 共享；复用 Fact CFG、FactSSA、RD、storage、MemorySSA、SinkResolver；不建立平行底层 IR。
- 替代语义进入下游后，旧表示仅保留为 evidence，不能重复计效。
- Task 不得为局部实现便利反向修改研究目标或实验约束。

## Authority / Change Control

Repository 内设计文档权威层级为：`RESEARCH_FRAMEWORK.md → IMPLEMENTATION_PLAN.md → Current Task Prompt`。该顺序约束仓库设计冲突的处理，不覆盖明确的新研究人员指令。TASK_MAP 只导航；PROJECT_STATUS 只记当前状态；task report 只记录已执行事实与 handoff。

后续 Task 与框架冲突时不得自行改框架或继续冲突实现：记录冲突，无法安全继续则 PARTIAL/BLOCKED，通过 `docs/decisions/` 提交 Problem、Evidence、Decision、Alternatives、Impact、Affected Modules、Affected Tasks、Affected Experiments、Required Regression，由研究人员决定。采纳后同步计划、导航、schema 和受影响回归；不允许用局部报告覆盖冻结约束。

## 后续新对话 Bootstrap

从 P1-T1 起：本文件 → [TASK_MAP](TASK_MAP.md) → [PROJECT_STATUS](../../PROJECT_STATUS.md) → IMPLEMENTATION_PLAN 当前 Task 与直接相关接口 → 该 Task 直接 upstream reports/handoff → 相关源码/测试。同时服从适用 AGENTS.md。

不重新扫描整个仓库、不一次读取所有历史报告、不凭 Task ID 猜职责。只有当前 Task 验收与交接完成后停止，后续 Task 需单独授权。
