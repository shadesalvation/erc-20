# Project Status

## Current Task

**P1-T5 = COMPLETE** — Candidate Semantic Control Edge Reconstruction，2026-09-19。

P0-T1/P0-T2/P0-T3/P1-T1/P1-T2/P1-T3/P1-T4 均为 COMPLETE；本次按 frozen bootstrap 核验此前等待 P1-T5 授权，并只执行当前 Task。实际 branch `semantic-ir-next`；`start_head=end_head=5636fd5afcda24c1c84a0f9cbc57559d12b210de`，未 commit/push。该 HEAD 是 P1-T4 task-local end_head `58d8cfaa...` 的直接后代 `p1-t4-completed`；ancestry与diff仅含已验收 P1-T4代码/测试/文档/evidence，记录为 post-task sync/documentation drift，未重写或重做 P1-T4。四处既有 OpenZeppelin dependency checkout状态完整保留，不计入本任务。

本 Task 直接组合 supplied SemanticAction、FlattenedRegion、AbstractPropagation node/context cache与现有 Fact CFG，恢复 high-recall first-next candidate `SemanticControlEdge`。normal/flattened/mixed scope显式区分；fake/dispatcher/carrier block压缩；branch/join/loop、context/path alternatives与同块顺序歧义均保留证据。所有 feasibility=`UNRESOLVED`、solver=`NOT_RUN`。没有实现 P1-T6 symbolic/SMT/Guard normalization、edge rejection或 P2+，没有修改上游 frozen producers、SFIR、MemorySSA、SinkResolver或 oracle。

## Baseline / Validation Summary

修改前 P1-T4 targeted：18/18 PASS；P1-T1：19/19 PASS；P1-T2：14/14 PASS；runner：30 PASS / 0 FAIL / 0 SKIP / 0 TIMEOUT。
P1-T5 targeted：**26/26 PASS**；P1-T4/P1-T2/P1-T1 regression：18/18、14/14、19/19 PASS。
收尾 runner：**31 PASS / 0 FAIL / 0 SKIP / 0 TIMEOUT**（仅增加 P1-T5 测试入口）；machine targeted合计 77 PASS / 0 FAIL；专项验收 **21/21 PASS**。

- [bootstrap](docs/task_reports/P1-T5_evidence/bootstrap.json)
- [targeted tests](docs/task_reports/P1-T5_evidence/targeted_tests.json)
- [专项验收](docs/task_reports/P1-T5_evidence/acceptance.json)
- [handoff smoke](docs/task_reports/P1-T5_evidence/handoff_smoke.json)
- [final regression](docs/task_reports/P1-T5_evidence/final_regression/results.json)
- [report / handoff](docs/task_reports/P1-T5.md#p1-t6-handoff)

复现：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/s_seir/s_seir_research_control_edges_tests.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/task_reports/P0-T1_baseline/run_baseline.py docs/task_reports/P1-T5_evidence/final_regression
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/task_reports/P1-T5_evidence/verify.py
```

## Stable Outputs / Interfaces

- `scripts/s_seir/s_seir_research_control_edges.py`：`reconstruct_candidate_control_edges / validate_candidate_edge / validate_candidate_collection / query_candidates / candidate_accounting / serialize_candidate_result`。
- 唯一新增正式 artifact：candidate `erc20-research/semantic-control-edge/v1`；payload恰为 `from / to / candidate_id / guard_ref / context_ref / feasibility / region_ref`。
- endpoint 是 SemanticAction occurrence或 ENTRY/EXIT；identity = endpoints + region/context + path-alternative provenance；只按完整 identity canonical dedupe。
- first-next compression遇下一个 action/EXIT即停止该 alternative，不产生普通 transitive closure；有限 state key保持 loop termination与分支/context alternatives。
- flattened直接复用 P1-T4 validate/cache/query API；normal直接用 Fact CFG；mixed在 region boundary显式切换，region内不跑无 context普通路径。
- FIXED_POINT不等于 feasible；UNKNOWN不删边；RESOURCE_LIMIT保留partial candidates/frontier；UNSUPPORTED/ERROR/upstream PARTIAL显式守恒。
- result accounting是 wrapper machine view，不是第二 graph artifact；edge evidence足以让 P1-T6定位 local carrier/context scope。

P0-T3 frozen interfaces、shared contract、P2-T1A REQUIRED链、G1–G8、oracle isolation继续有效；无冻结接口变更，无新 ADR。

## Known Limitations

R1 source support仍 insufficient；没有声称复现论文 edge algorithm/default k。P1-T4没有逐 contribution transition lineage，observed arm/context匹配仍是 high-recall over-approximation。重复同一 loop arm不按迭代次数复制直接候选；跨多个不同 FlattenedRegion且中间无 semantic boundary的单一 carrier显式 UNSUPPORTED。同块顺序缺完整 SFIR semantic_ids时保留歧义。

尚未实现：SAT/UNSAT feasibility、edge rejection、symbolic/SMT、SolverEvidence、Guard normalization/opaque solving、Def-Use/RD/VFG/StateDependency、order/STIR。

## Blocked Issues

P1-T5 无当前阻塞；必要测试、21项专项验收、machine evidence、candidate accounting/evidence closure与可直接消费 handoff完整，无未解释 regression。

## Next Allowed Task

P1-T5 已完成并停止；**等待单独授权 P1-T6 — Local Symbolic Execution + SMT**。
P1-T6 必须直接使用 [P1-T5 handoff](docs/task_reports/P1-T5.md#p1-t6-handoff) 的 candidate edges、carrier/context evidence与查询 API；不得重新做 endpoint reconstruction，不得提前执行 P1-T7 Guard canonicalization或 Module 2。

后续 bootstrap：RESEARCH_FRAMEWORK → TASK_MAP → PROJECT_STATUS → IMPLEMENTATION_PLAN 当前 Task/直接接口 → direct upstream report/handoff → 相关源码/测试，同时服从 AGENTS.md。重新读取实际 branch/HEAD；不自动进入 P1-T6。
