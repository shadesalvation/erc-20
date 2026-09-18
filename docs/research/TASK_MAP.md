# 智能合约语义解混淆项目 Task Map

> 作用：供全新 Codex 对话快速理解“整个研究要做什么、当前 Task 在哪里、前后依赖是什么”。
>
> 本文件是导航层，不是详细工程契约。具体接口、文件落点、schema 和实现约束以 `IMPLEMENTATION_PLAN.md` 为准；当前执行状态以 `PROJECT_STATUS.md` 为准；研究目标与全局不变量以 `docs/research/RESEARCH_FRAMEWORK.md` 为准。

## 1. 最终目标

输入源码级混淆的 Solidity / Yul，恢复 Canonical State Transition IR：

`Guard + Persistent State + State Update + External Effect + Failure + Execution Order`

不以恢复原始 Solidity 源码或 source-identical CFG 为目标。

固定主链：

```text
Solidity / Yul
→ SFIR
→ Module 1: Core Semantic Control Recovery
→ Module 2: Sink-Guided State Dependency Recovery
→ Module 3: Order-Aware State Transition Reconstruction
→ Canonical STIR
→ Paired Benchmark / Oracle
→ Experiment 1 / 2 / 3
→ Ablation / Regression / Reproduction
```

## 2. 新对话如何使用本文件

新 Codex 对话不要从本文件直接开始实现。先按固定顺序读取：

```text
RESEARCH_FRAMEWORK.md
→ TASK_MAP.md
→ PROJECT_STATUS.md
→ IMPLEMENTATION_PLAN.md 中当前 Task / 直接相关接口部分
→ 当前 Task 指定的 upstream task reports / handoffs
→ 相关源码与测试
```

本文件只回答：

- 当前 Task 位于哪里；
- 为什么存在；
- 主要消费什么；
- 留下什么稳定结果；
- 谁会继续消费它。

本文件不替代：详细接口、当前仓库状态、历史 task report、blocking gap handoff。

## 3. Phase 0 — 初始化、能力确认与架构冻结

### P0-T1 — 仓库扫描与 Baseline

- 职责：确认真实仓库结构、frontends、SFIR、测试、工具链与 baseline。
- 输入：当前 Repository。
- 输出：`repository_overview.md`、baseline、`AGENTS.md`、`PROJECT_STATUS.md`。
- 下游：P0-T2。

### P0-T2 — SFIR Capability Map

- 职责：只盘点现有 SFIR 能力与缺口，不实现缺失功能。
- 输入：P0-T1 的仓库事实和 baseline。
- 输出：`sfir_capability_map.md`、支持/缺失矩阵、blocking gap 证据与最小补足边界。
- 下游：P0-T3。
- 当前已确认 blocking gap：现有 SFIR 的 Storage Address Equivalence 不足以保证 ERC-20 状态依赖分析既不漏报也不误报；具体事实以 P0-T2 report / handoff 为准。

### P0-T3 — 接口冻结与 Implementation Plan

- 职责：冻结 Module 1/2/3 contract、目录/schema、完整 Task dependency、条件补充任务和研究框架。
- 输入：P0-T1 + P0-T2。
- 输出：`IMPLEMENTATION_PLAN.md`、`RESEARCH_FRAMEWORK.md`、本 `TASK_MAP.md`、P2-T1A REQUIRED/SKIP 决策。
- 下游：P1-T1。

## 4. Phase 1 — Core Semantic Control Recovery

### P1-T1 — Semantic Action Identification

- 职责：统一识别 StorageWrite / ExternalCall / EtherTransfer / Emit / Revert / Return。
- 输入：SFIR + P0-T3 Module 1 contract。
- 输出：稳定 `SemanticAction` schema / identity / evidence。
- 下游：P1-T2、P1-T5。

### P1-T2 — Dispatcher / Flattened Region Detection

- 职责：识别 dispatcher、flattened region、control-state candidate、case、entry、exit。
- 输入：SFIR CFG + SemanticAction contract。
- 输出：FlattenedRegion / Dispatcher evidence。
- 下游：P1-T3、P1-T4、P1-T5。

### P1-T3 — k-switch Abstract Domain & Context

- 职责：定义 `<Abstract State, Context>`、transfer、join、context update。
- 输入：P1-T2 flattened region / dispatcher state。
- 输出：AbstractState / KSwitchContext / transfer / join。
- 下游：P1-T4。

### P1-T4 — Fixed-Point Propagation Engine

- 职责：在 flattened region 上做 context-sensitive fixed-point propagation。
- 输入：P1-T2 region + P1-T3 abstract domain。
- 输出：node/context abstract states、迭代/收敛 evidence。
- 下游：P1-T5。

### P1-T5 — Candidate Semantic Control Edge Reconstruction

- 职责：生成高召回、允许假边的 candidate semantic control graph。
- 输入：P1-T1 SemanticAction + P1-T2 region + P1-T4 abstract states。
- 输出：Candidate Semantic Control Graph。
- 下游：P1-T6。

### P1-T6 — Local Symbolic Execution + SMT

- 职责：只对 candidate edge 做局部 symbolic refinement，不做生产路径 whole-program symbolic execution。
- 输入：P1-T5 candidate edges + abstract context/states。
- 输出：SAT / UNSAT / UNKNOWN / TIMEOUT / UNSUPPORTED、Guard、opaque-predicate evidence。
- 下游：P1-T7。

### P1-T7 — Guard Canonicalization + Module 1 Evaluation

- 职责：规范化 Semantic Control Edge / Guard，并完成 Module 1 局部评估。
- 输入：P1-T6 refined edges/Guards + SemanticAction identity。
- 输出：`Feasible Semantic Control Edge + Normalized Guard`、Module 1 evaluator/raw results。
- 下游：整个 Module 2、Experiment 1。

## 5. Phase 2 — Sink-Guided State Dependency Recovery

### P2-T1 — Def-Use Infrastructure

- 职责：建立稳定 Definition / Use identity 与 Def→Use / Use→Def 查询。
- 输入：P1-T7 feasible control + SFIR defs/uses。
- 输出：Def-Use infrastructure。
- 下游：P2-T1A（若 REQUIRED）或 P2-T2。

### P2-T1A — Minimal Storage Alias / Address Equivalence（条件任务）

- 当前状态：**REQUIRED**，原因来自 P0-T2 已确认的 Storage Address Equivalence blocking gap；P0-T3 必须最终冻结范围与接口。
- 职责：提供最小、保守、可精化的 storage location relation；不做通用 Alias Analyzer。
- 输入：P0-T2 gap evidence + P0-T3 frozen scope + SFIR storage representation + P2-T1 identity infrastructure。
- 最低输出：`MUST_ALIAS / MAY_ALIAS / NO_ALIAS / UNKNOWN` + evidence/reason + downstream API。
- 关键规则：MAY/UNKNOWN 不得静默当成 NO_ALIAS；后续允许用更稳定的 value/key identity 与 Guard refinement。
- 下游：P2-T2、P2-T4、P2-T6、P2-T8。
- 若 P0-T3 最终证明现有能力足够，应改为 `SKIP / NOT REQUIRED`；后续 Codex 不得自行改变状态。

### P2-T2 — Reaching Definitions

- 职责：在 Module 1 feasible control 上传播 definitions，处理 branch/join/loop/redefinition。
- 输入：P2-T1；若 P2-T1A REQUIRED，则必须消费其 storage relation。
- 输出：Use → reaching definitions。
- 下游：P2-T3、P2-T4。

### P2-T3 — SSA / Explicit Value Identity

- 职责：复用 SSA，或建立等价的 definition identity / merge / phi 表达。
- 输入：P2-T1 + P2-T2。
- 输出：SSA / Explicit Value Identity。
- 下游：P2-T4；可为后续 storage relation refinement 提供更强 key identity，但不得反向重做前序 Task。

### P2-T4 — Value Flow Graph

- 职责：整合 Def-Use / Reaching Definitions / value identity，形成可切片 VFG。
- 输入：P2-T1～P2-T3 + P1-T7；若 REQUIRED，复用 P2-T1A storage relation。
- 输出：Value Flow Graph、dependency edges、state/input/source nodes。
- 下游：P2-T5、P2-T6、P2-T8。

### P2-T5 — Expression Normalization

- 职责：Copy/Constant Propagation、Constant Folding、Algebraic Simplification、Expression Rewriting。
- 输入：P2-T4 VFG。
- 输出：Canonical Expression / normalization engine。
- 下游：P2-T6、P2-T7、P2-T8。

### P2-T6 — Semantic Sink + Backward Slicing

- 职责：第一版以 StorageWrite 为主要 Sink，沿 data + Module 1 control/Guard 做 backward slicing。
- 输入：P2-T4 VFG + P2-T5 expressions + P1-T7 Guard；若 REQUIRED，复用 P2-T1A relation。
- 输出：Relevant Semantic Slice + evidence + optional safe cleanup。
- 下游：P2-T7、P2-T8。

### P2-T7 — Contract State Dependency Lifting + Module 2 Evaluation

- 职责：把 IR-level slice 提升为 Inputs / State Reads / Guard / State Update，并完成 StorageWrite 主路径评估。
- 输入：P2-T6 + P2-T5 + P1-T7。
- 输出：Contract State Dependency schema、Module 2 evaluator/raw results。
- 下游：P2-T8、P3、Experiment 2。

### P2-T8 — Multi-Sink Semantic Dependency Extension

- 职责：把已验证的 slicing/dependency 机制扩展到 ExternalCall / EtherTransfer / Event / Return / Revert。
- 输入：P2-T4～P2-T7 + P1-T7；若 REQUIRED，复用 P2-T1A relation。
- 输出：Multi-Sink / SinkOperand Dependency Summary、call_kind、external/failure dependencies。
- 下游：P3-T1、Experiment 2。

## 6. Phase 3 — Order-Aware State Transition Reconstruction

### P3-T1 — Semantic Event Schema

- 职责：统一生成 StateRead / StateWrite / ExternalCall / EtherTransfer / Emit / Revert / Return 事件。
- 输入：P1-T7 + P2-T7 + P2-T8。
- 输出：SemanticEvent schema / adapter / serialization。
- 下游：P3-T2。

### P3-T2 — Transition Grouping

- 职责：把属于同一逻辑行为的 events 聚合成 TransitionCandidate。
- 输入：P3-T1 + Module 1/2 evidence。
- 输出：TransitionCandidate + grouping evidence。
- 下游：P3-T3。

### P3-T3 — Partial Order Recovery

- 职责：恢复能被证明的 `A ≺ B`，不强行制造 total order。
- 输入：P3-T2 + control/data/state dependencies。
- 输出：OrderConstraint / Partial Order Graph / evidence。
- 下游：P3-T4、P3-T5。

### P3-T4 — Revert / Failure Semantics

- 职责：把 failure/revert 与 commit/rollback 语义显式纳入 transition。
- 输入：P3-T2/P3-T3 + P2-T8 failure dependencies。
- 输出：success/failure transition semantics、rollback/commit facts。
- 下游：P3-T5。

### P3-T5 — Canonical State Transition IR

- 职责：统一 Guard / State Read / Update / External Effect / Failure / Partial Order。
- 输入：P3-T1～P3-T4。
- 输出：Canonical STIR + stable serialization/comparison representation。
- 下游：P3-T6、Phase 4/5。

### P3-T6 — Module 3 Evaluation + Order Benchmark

- 职责：评估 transition/order 恢复，并加入“相同行为、不同关键顺序”专项样例。
- 输入：P3-T5。
- 输出：Module 3 evaluator、order benchmark/raw results。
- 下游：Phase 4、Experiment 3。

## 7. Phase 4 — Paired Benchmark / Oracle

### P4-T1 — Benchmark Schema + Micro Benchmark

- 职责：定义 clean/obfuscated pair schema、oracle/evidence、negative controls。
- 输入：三个 Module 的 canonical outputs/evaluators。
- 输出：benchmark schema + micro benchmark。
- 下游：P4-T2～P4-T4。

### P4-T2 — Controlled Obfuscation + Pair Generation

- 职责：构造 CFF/dispatcher、opaque predicate、fake edge、temp splitting、copy chain、junk、expression decomposition 等可控变换，并保存 profile/seed/log。
- 输入：P4-T1 schema。
- 输出：validated clean/obfuscated pairs、transformation metadata。
- 下游：P4-T3/P4-T4/Phase 5。

### P4-T3 — ERC-20-style Paired Benchmark

- 职责：构建 transfer/approve/transferFrom/mint/burn 等 Solidity/Yul/Mixed paired cases。
- 输入：P4-T1/P4-T2。
- 输出：ERC-20 paired benchmark + reviewed oracle。
- 下游：P4-T4/Phase 5。

### P4-T4 — Larger Contracts + Dataset Freeze

- 职责：加入较完整样例并冻结正式 dataset manifest/fingerprint/coverage matrix。
- 输入：P4-T1～P4-T3。
- 输出：frozen dataset + manifest + fingerprint。
- 下游：P5-T1～P6-T4。

## 8. Phase 5 — Formal Experiments

### P5-T1 — Unified Result Schema + Evaluator Contract

- 职责：统一 Exp1/2/3 raw result schema、environment/version/timeout/seed/fingerprint fields。
- 输入：P1/P2/P3 evaluators + frozen dataset。
- 输出：统一 result schema/evaluator contract。
- 下游：P5-T2～P5-T5。

### P5-T2 — Experiment 1 Runner

- 职责：正式执行 Module 1 control-recovery experiment。
- 输入：P1-T7 evaluator + P4-T4 dataset + P5-T1 schema。
- 输出：Experiment 1 raw results。
- 下游：P5-T5/P6-T1。

### P5-T3 — Experiment 2 Runner

- 职责：正式执行 Module 2 state/sink dependency experiment。
- 输入：P2-T7/P2-T8 evaluator + frozen dataset + P5-T1 schema。
- 输出：Experiment 2 raw results。
- 下游：P5-T5/P6-T2。

### P5-T4 — Experiment 3 Runner

- 职责：正式执行 Module 3 transition/order experiment。
- 输入：P3-T6 evaluator + frozen dataset + P5-T1 schema。
- 输出：Experiment 3 raw results。
- 下游：P5-T5/P6-T3。

### P5-T5 — Aggregation + Export

- 职责：按 overall/category/representation/obfuscation/strength/base-case 聚合，导出统计与 failure distribution。
- 输入：P5-T2～P5-T4 + dataset manifest/profile metadata。
- 输出：aggregate results / tables / robustness slices。
- 下游：Phase 6 / final report。

## 9. Phase 6 — Ablation, Regression, Reproduction

### P6-T1 — Control Recovery Ablation

- 职责：比较 k-switch AI、Whole-Program Symbolic baseline、k-switch + Local Symbolic + SMT；做 k sensitivity。
- 输入：Experiment 1 dataset/evaluator/results。
- 输出：control ablation results。
- 下游：P6-T4。

### P6-T2 — Sink-Guided Ablation

- 职责：验证 sink-guided slicing/semantic relevance 设计贡献。
- 输入：Experiment 2 dataset/evaluator/results。
- 输出：sink-guided ablation results。
- 下游：P6-T4。

### P6-T3 — Order-Aware Ablation

- 职责：验证显式 partial order 对区分状态转移的贡献。
- 输入：Experiment 3 dataset/evaluator/results。
- 输出：order-aware ablation results。
- 下游：P6-T4。

### P6-T4 — Full Regression / Failure Attribution / Reproduction

- 职责：全项目回归、失败归因、最小 failing case、复现封装和最终实验报告。
- 输入：全部模块、dataset、experiments、ablations。
- 输出：`FINAL_EXPERIMENT_REPORT.md`、`REPRODUCE.md`、最终 regression/failure package。
- 下游：项目完成。

## 10. 条件任务与变更规则

1. `P2-T1A` 不计入 37 个固定 Task。
2. 条件任务是否执行只能由 P0-T3 根据 P0-T2 证据在 `IMPLEMENTATION_PLAN.md` / `PROJECT_STATUS.md` 中冻结；后续 Codex 不得自行启用或跳过。
3. 如果通过 `docs/decisions/` 改变 Task 顺序、条件任务或主要输入/输出，必须同步更新本文件。
4. 本文件不得保存当前测试数字、具体源码路径、临时 bug 状态；这些属于 `PROJECT_STATUS.md`、task reports 或 `IMPLEMENTATION_PLAN.md`。
5. 若本文件与 `IMPLEMENTATION_PLAN.md` 冲突，以 `IMPLEMENTATION_PLAN.md` 为准，并把冲突作为文档漂移修正。
