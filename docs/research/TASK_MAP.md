# 智能合约语义解混淆项目 Task Map — frozen navigation v1

目标：从源码级混淆 Solidity / Yul 恢复 Guard + Persistent State + State Update + External Effect + Failure + Execution Order；不恢复原始源码。

`Solidity / Yul → SFIR → Module 1 控制 → Module 2 依赖 → Module 3 转移 → Canonical STIR → Semantic Validation`。

本文只导航；正式接口与依赖以 [IMPLEMENTATION_PLAN](../../IMPLEMENTATION_PLAN.md) 为准。当前 Task 和执行状态见 [PROJECT_STATUS](../../PROJECT_STATUS.md)。研究目标见 [RESEARCH_FRAMEWORK](RESEARCH_FRAMEWORK.md)。发生冲突须修正文档漂移。

Bootstrap：RESEARCH_FRAMEWORK → TASK_MAP → PROJECT_STATUS → IMPLEMENTATION_PLAN 当前 Task/直接接口 → 指定 upstream handoff → 相关源码/测试。不重新全仓扫描或读取全部历史。

P0 是初始化；P1/P2/P3 分别对应三个研究模块；P4 是配对 benchmark/oracle；P5 是正式实验；P6 是消融/回归/复现。Experiment 1/2/3 对应 Module 1/2/3；Phase 6 不改变生产 pipeline。

固定 Task 共 37 个；补充任务 **P2-T1A = REQUIRED**，不计入 37。固定链 `P2-T1 → P2-T1A → P2-T2`，不得绕过。改变依赖、职责或主要输入输出须记录 ADR 并同步正式计划。

| Task / Phase | Responsibility | Direct input | Stable output | Downstream consumer |
| --- | --- | --- | --- | --- |
| P0-T1 | 仓库扫描与 Baseline | Repository | Repository facts + baseline | P0-T2, P0-T3 |
| P0-T2 | SFIR Capability Map | P0-T1 仓库事实与 baseline | Capability map + G1–G8 evidence | P0-T3 |
| P0-T3 | 接口冻结与 Implementation Plan | P0-T1 facts + P0-T2 gaps | Frozen framework + plan + contracts | P1-T1 |
| P1-T1 | Semantic Action Identification | SFIR + P0-T3 Module 1 contract | SemanticAction | P1-T2, P1-T5, P1-T7 |
| P1-T2 | Dispatcher / Flattened Region Detection | SFIR CFG + SemanticAction | FlattenedRegion | P1-T3, P1-T4, P1-T5 |
| P1-T3 | k-switch Abstract Domain and Context | FlattenedRegion + SFIR typed operands | AbstractDomainContract | P1-T4 |
| P1-T4 | Fixed-Point Propagation Engine | FlattenedRegion + AbstractDomainContract | AbstractPropagation | P1-T5 |
| P1-T5 | Candidate Semantic Control Edge Reconstruction | SemanticAction + FlattenedRegion + AbstractPropagation | Candidate SemanticControlEdge | P1-T6 |
| P1-T6 | Local Symbolic Execution + SMT | Candidate SemanticControlEdge + abstract context + SFIR | Refined SemanticControlEdge + Guard + SolverEvidence | P1-T7 |
| P1-T7 | Guard Canonicalization + Module 1 Evaluation | Refined SemanticControlEdge + Guard + SemanticAction | Module1Result + Module1Evaluation | P2-T1, P2-T1A, P2-T2, P2-T4, P2-T6, P2-T7, P2-T8, P3-T1, P3-T2, P3-T3, P3-T5, P4-T1, P5-T1, P5-T2 |
| P2-T1 | Def-Use Infrastructure | Module1Result + SFIR FactSSA/operands | Definition + Use + DefUseIndex | P2-T1A, P2-T2, P2-T3, P2-T4 |
| P2-T1A | Minimal Storage Alias / Address Equivalence | Definition/Use + SFIR storage/layout + Module1Result | StorageAddressRelation | P2-T2, P2-T4, P2-T6, P2-T8 |
| P2-T2 | Reaching Definitions | DefUseIndex + StorageAddressRelation + Module1Result + existing RD | ReachingDefinitionSet | P2-T3, P2-T4 |
| P2-T3 | SSA / Explicit Value Identity | Definition/Use + ReachingDefinitionSet + FactSSA | ValueIdentity | P2-T4 |
| P2-T4 | Value Flow Graph | DefUseIndex + ReachingDefinitionSet + ValueIdentity + StorageAddressRelation + Module1Result | ValueFlowGraph | P2-T5, P2-T6, P2-T8 |
| P2-T5 | Expression Normalization | ValueFlowGraph + typed SFIR expressions | CanonicalExpression | P2-T6, P2-T7, P2-T8 |
| P2-T6 | Semantic Sink + Backward Slicing | ValueFlowGraph + CanonicalExpression + Module1Result + StorageAddressRelation | SemanticSlice | P2-T7, P2-T8 |
| P2-T7 | Contract State Dependency Lifting + Module 2 Evaluation | SemanticSlice + CanonicalExpression + Module1Result | StateDependency + Module2StorageResult + Module2Evaluation | P2-T8, P3-T1, P5-T3 |
| P2-T8 | Multi-Sink Semantic Dependency Extension | ValueFlowGraph + expressions + slices + StateDependency + Module1Result + StorageAddressRelation | SinkDependency + Module2Result + Module2Evaluation | P3-T1, P3-T2, P3-T3, P3-T4, P3-T5, P4-T1, P5-T1, P5-T3 |
| P3-T1 | Semantic Event Schema | Module1Result + Module2StorageResult + Module2Result + SFIR StateRead | SemanticEvent | P3-T2, P3-T5 |
| P3-T2 | Transition Grouping | SemanticEvent + Module1Result + Module2Result | TransitionCandidate | P3-T3, P3-T4, P3-T5 |
| P3-T3 | Partial Order Recovery | TransitionCandidate + feasible control + data/state/control evidence | OrderConstraint + PartialOrder | P3-T4, P3-T5 |
| P3-T4 | Revert / Failure Semantics | TransitionCandidate + PartialOrder + failure dependencies | FailureSemantics | P3-T5 |
| P3-T5 | Canonical State Transition IR | SemanticEvent + TransitionCandidate + PartialOrder + FailureSemantics + M1/M2 | StateTransitionIR + Module3Result | P3-T6 |
| P3-T6 | Module 3 Evaluation + Order Benchmark | Module3Result + canonical M1/M2 outputs | Module3Evaluation + order micro cases | P4-T1, P5-T1, P5-T4 |
| P4-T1 | Benchmark Schema + Micro Benchmark | 三个 Module canonical outputs/evaluators + micro cases | BenchmarkManifest + micro benchmark + oracle contract | P4-T2, P4-T3, P4-T4 |
| P4-T2 | Controlled Obfuscation + Pair Generation | BenchmarkManifest + approved transformation profiles | Validated pairs + transformation metadata | P4-T3, P4-T4 |
| P4-T3 | ERC-20-style Paired Benchmark | Benchmark schema + validated pairs | ERC-20 pairs + reviewed oracle | P4-T4 |
| P4-T4 | Larger Contracts + Dataset Freeze | Micro/ERC-20 pairs + reviewed oracle + transformation metadata | Frozen dataset + manifest + fingerprint + coverage matrix | P5-T1, P5-T2, P5-T3, P5-T4, P5-T5 |
| P5-T1 | Unified Result Schema + Evaluator Contract | 三个 Module evaluators + frozen dataset | ExperimentResult contract | P5-T2, P5-T3, P5-T4 |
| P5-T2 | Experiment 1 Runner | Module1Evaluation + frozen dataset + ExperimentResult contract | Experiment1 raw results | P5-T5, P6-T1 |
| P5-T3 | Experiment 2 Runner | Module2Evaluation + frozen dataset + ExperimentResult contract | Experiment2 raw results | P5-T5, P6-T2 |
| P5-T4 | Experiment 3 Runner | Module3Evaluation + frozen dataset + ExperimentResult contract | Experiment3 raw results | P5-T5, P6-T3 |
| P5-T5 | Aggregation + Export | Experiment1/2/3 raw results + frozen manifest | AggregateResult + tables + failure distribution | P6-T1, P6-T2, P6-T3, P6-T4 |
| P6-T1 | Control Recovery Ablation | Experiment1 results + evaluator + frozen dataset + aggregate | Control ablation results | P6-T4 |
| P6-T2 | Sink-Guided Ablation | Experiment2 results + evaluator + frozen dataset + aggregate | Sink-guided ablation results | P6-T4 |
| P6-T3 | Order-Aware Ablation | Experiment3 results + evaluator + frozen dataset + aggregate | Order-aware ablation results | P6-T4 |
| P6-T4 | Full Regression / Failure Attribution / Reproduction | 全部模块 + dataset + experiments + ablations | FINAL_EXPERIMENT_REPORT.md + REPRODUCE.md + regression package | 项目研究报告 |
