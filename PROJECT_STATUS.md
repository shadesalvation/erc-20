# Project Status

## Current Task

**P0-T3 = COMPLETE** — 接口冻结与 Implementation Plan，2026-09-18。

P0-T1 = COMPLETE；P0-T2 = COMPLETE。两者 latest handoff 与当前 SFIR 源码/测试一致，123 个来源文件哈希核对无冲突。当前 branch `semantic-ir-next`，HEAD `b6f02567eed4fb6057003124435b3f19576f826c`。旧报告描述其当时的未提交迁移，不代表本次工作树仍有那些改动；本次起始只有四处依赖 checkout 的既有未跟踪内容，全部保留。

本 Task 仅冻结文档、接口/ownership/依赖和一致性证据；没有实现 Phase 1、P2-T1A 或任何后续分析算法，没有修改生产源码、既有测试、oracle 或历史证据。

## Baseline / Validation Summary

修改前及收尾均 **26 PASS / 0 FAIL / 0 SKIP / 0 TIMEOUT**：25 个主线脚本 + 1 个归档模块（内部 5 个 unittest），计数单位为入口。归档只维持旧回归，不作为新主线能力。

- [baseline](docs/task_reports/P0-T3_evidence/baseline/results.json)、[final](docs/task_reports/P0-T3_evidence/final/results.json)
- [upstream handoff](docs/task_reports/P0-T3_evidence/upstream_handoff.json)
- [consistency](docs/task_reports/P0-T3_evidence/consistency.json)、[专项验收](docs/task_reports/P0-T3_evidence/acceptance.json)
- [Git/change summary](docs/task_reports/P0-T3_evidence/change_summary.json)

复现：

```bash
.venv/bin/python docs/task_reports/P0-T1_baseline/run_baseline.py /tmp/p0-t3-regression
.venv/bin/python docs/task_reports/P0-T3_evidence/validate_contracts.py --final
```

一致性校验 PASS；7/7 负向文档变异被拒绝，57/57 架构专项验收 PASS。校验覆盖 37 fixed + 1 supplement、无环依赖、G1–G8 ownership、关键 artifact producer/consumer、TASK_MAP 与计划、状态/四态 storage relation、源码未变、Git 范围；专项验收区分人工架构判断与机器结构检查。未来算法/solver/正式 benchmark/实验未执行，不计作 PASS。

## Frozen Artifacts / Interfaces

- [RESEARCH_FRAMEWORK](docs/research/RESEARCH_FRAMEWORK.md)：冻结研究问题、三个 Module、评价原则、全局不变量和研究人员变更决策边界。
- [IMPLEMENTATION_PLAN](IMPLEMENTATION_PLAN.md)：Module contracts、Interface Freeze Table、Gap Resolution Map、Research Reference Map、directory/schema/result ownership、全部 Task dependency 和逐 Task 工程验收。
- [TASK_MAP](docs/research/TASK_MAP.md)：精简导航，与正式计划一致。
- [ADR P0-T3-001](docs/decisions/P0-T3-001-interface-freeze.md)、[P0-T3 report](docs/task_reports/P0-T3.md)。

冻结 SemanticAction、SemanticControlEdge、Guard、StorageAddressRelation、Definition/Use、ValueIdentity、ValueFlow、CanonicalExpression、SemanticSlice、统一 State/Sink Dependency、SemanticEvent、OrderConstraint、TransitionCandidate、STIR；共享 identity/evidence/status/serialization 和 module result 契约见计划。M2 直接消费 M1；M3 直接消费 M1+M2。SFIR v1 不变；MemorySSA/SinkResolver 职责不变。

**P2-T1A = REQUIRED**，固定 `P2-T1 → P2-T1A → P2-T2`；冻结 `SAME / DISTINCT / MAY_OVERLAP / INSUFFICIENT_EVIDENCE`，覆盖 ERC-20 named state、单层/嵌套 mapping、已恢复 Solidity/inline Yul/Mixed 路径、访问时点 key/Guard、unknown write/call clobber。不做通用 alias analyzer。

## Known Limitations

- 新接口是设计承诺，当前代码尚无完整 feasible control/SMT、VFG/slicing、Canonical STIR。P2-T1A REQUIRED 不表示算法完成。
- 现有 storage identity 依赖 access 文本；key copy/redefinition/同值参数/unknown/clobber 缺口保持，owner 已分配。
- msg.sender/msg.value/compound refs 与 Phi predecessor 配对存在边界；缺 refs 不表示无依赖。普通 FactSSA serial version 只作来源引用，不用作跨运行稳定身份。
- 部分块内 source/id/list order、无出口 postdom 是 fallback；不构成最终顺序证明。循环 occurrence 无充分证据时保留 unknown transition。
- 独立 Yul object→SFIR 入口缺失；只将已恢复 Solidity/inline Yul/Mixed 纳入当前基础。不支持情形须显式报告，未授权新增通用 frontend。
- 研究参考缺原文支持，计划明确 `source support insufficient`；后续 Task 不得把任务指定思想冒称论文复现。
- 依赖锁/统一构建/CI 仍缺；没有冻结真实 benchmark dataset 或运行正式实验，冻结的是 ownership/契约。未知 external effects、gas 投影缺失、完整跨合约/代理/重入仍有范围限制。

## Blocked Issues

P0-T3 无当前阻塞。必要回归、一致性校验、57 项专项验收与交接全部完成，无未解释 regression。

独立 STORAGE-ADDR-EQ-001 [review](docs/task_reports/STORAGE-ADDR-EQ-001_review.md) / [修复准备](docs/task_reports/STORAGE-ADDR-EQ-001.md) 保持 PARTIAL：指定旧模型源码缺失、原目标测试 ModuleNotFoundError 未解决；不属于现行 SFIR，也未被本 Task 豁免为 PASS。继续该事项需正确源码及单独授权。

## Next Allowed Task

P0-T3 已完成并停止；**等待单独授权 P1-T1 — Semantic Action Identification**。不自动进入下一 Task。

后续 Bootstrap：RESEARCH_FRAMEWORK → TASK_MAP → PROJECT_STATUS → IMPLEMENTATION_PLAN 当前 Task/直接接口 → 当前 Task direct upstream handoff → 相关源码与测试，同时服从 AGENTS.md。P1-T1 先读 P0-T3 report 与计划 §1–§4/§10/§12 的 P1-T1，不重做 P0 扫描和 capability review。
