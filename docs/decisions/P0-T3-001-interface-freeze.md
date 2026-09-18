# P0-T3-001 — 研究接口、任务链与冻结治理

状态：采纳，2026-09-18；由当前 P0-T3 请求授权的首次冻结，不改变已有 SFIR v1。

## Problem

P0-T1/T2 已完成仓库和能力确认；三个研究模块尚无正式接口。导航草案含可跳过 P2-T1A 的旧措辞与候选 API，不能直接当冻结契约。

## Evidence

[P0-T2 capability map](../architecture/sfir_capability_map.md) 的 G1–G8 与 E0–E3：结构控制不是 feasible control、access 文本不是动态同址、semantic_edges 不是完整 VFG、部分排序只是 fallback。现行 `SemanticFactIRBridge._build_fact_ssa` 的 serial version 和 `_node_order` 也要求区分来源引用、稳定身份与顺序证据。修改前 baseline 见 [P0-T3 evidence](../task_reports/P0-T3_evidence/baseline/results.json)。

## Decision

按 [IMPLEMENTATION_PLAN](../../IMPLEMENTATION_PLAN.md) 首次冻结 research envelope、各 artifact、三模块契约、37 fixed Tasks + REQUIRED supplement、目录/schema/结果 owner 和 oracle 隔离规则。P2-T1A 固定在 P2-T1 与 P2-T2 之间，枚举 SAME/DISTINCT/MAY_OVERLAP/INSUFFICIENT_EVIDENCE；候选 MUST_ALIAS 等不构成旧冻结 API。SFIR 保持主线，研究层为引用式分析结果与语义摘要。

采用分离 proof/completion/solver/diagnostics 的状态协议；局部 SAT 不自动提升全路径可行；unknown/may 不能作无依赖。provenance id 与 canonical comparison identity 分开。Order 为带条件和证据的 partial order；Failure 明确 commit/rollback。冻结正式 RESEARCH_FRAMEWORK，保留候选模板作为历史输入。

## Alternatives

不采用平行 src/新底层 IR/复制 SFIR classes；不沿用 access 文本作为地址证明；不把所有未知强制二值化；不保留默认绕过 P2-T1A 的链。没有充分论文来源时标 source support insufficient，不凭记忆填算法。

## Impact

新增设计承诺但不实现分析算法、不修改生产代码、既有测试、oracle 或历史证据。TASK_MAP 改为简洁导航并与计划一致。后续每个 producer 负责本 artifact schema，各 consumer 验收输入；任何冻结语义变更必须另有 ADR。

## Affected Modules

Module 1、Module 2、Module 3；共享 SFIR 保持现有职责。

## Affected Tasks

P0-T3 以及 P1-T1～P6-T4，含 P2-T1A。具体 direct dependency 与 ownership 见计划，不新增固定 Task。

## Affected Experiments

Experiment 1/2/3 的输入/输出与隔离规则；Phase 6 消融保持独立，不改生产。当前未执行实验或冻结真实 dataset。

## Required Regression

当前：P0-T1 runner 的 26 个既有入口、P0-T3 contract/document consistency 和每项专项验收。后续：身份稳定/状态传播、M1→M2→M3 兼容、四态地址关系、unknown/timeout、partial order/failure、canonical serialization、oracle isolation 的对应 Task 验收；变更时运行被影响 producer/consumer 与实验回归。
