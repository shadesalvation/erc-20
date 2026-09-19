# Project Status

## Current Task

**P1-T4 = COMPLETE** — Fixed-Point Propagation Engine，2026-09-19。

P0-T1/P0-T2/P0-T3/P1-T1/P1-T2/P1-T3 均为 COMPLETE；本次按 frozen bootstrap 核验此前等待 P1-T4 授权，并只执行当前 Task。实际 branch `semantic-ir-next`；`start_head=end_head=58d8cfaa8e111a71e8aba51b21d7cf8d409e834c`，未 commit/push。当前 HEAD 恰为已验收 P1-T3 post-task sync commit；P1-T3 report 开头残留 IN PROGRESS 与其末尾/status/evidence 的 COMPLETE 不一致，按已知 non-blocking documentation drift 处理，未重做 P1-T3。四处既有 OpenZeppelin dependency checkout 状态完整保留，不计入本任务。

本 Task 只实现 context-sensitive deterministic worklist、node/context cache、existing Fact CFG traversal、P1-T3 transfer/join/context/stabilization 调用、四态 termination 与显式 resource frontier；没有实现 P1-T5 candidate semantic control edges、real successor、P1-T6 symbolic/SMT/Guard 或 P2+，没有修改 P1-T2/P1-T3/shared contract/SFIR/MemorySSA/SinkResolver/oracle。

## Baseline / Validation Summary

修改前 P1-T3 targeted：33/33 PASS；P1-T2 targeted：14/14 PASS；runner：29 PASS / 0 FAIL / 0 SKIP / 0 TIMEOUT。
P1-T4 targeted：**18/18 PASS**；P1-T3 regression：33/33 PASS；P1-T2 regression：14/14 PASS。
收尾 runner：**30 PASS / 0 FAIL / 0 SKIP / 0 TIMEOUT**（仅增加 P1-T4 测试入口）；machine targeted 合计 65 PASS / 0 FAIL / 0 N/A；专项验收 **16/16 PASS**。

- [bootstrap](docs/task_reports/P1-T4_evidence/bootstrap.json)
- [targeted tests](docs/task_reports/P1-T4_evidence/targeted_tests.json)
- [专项验收](docs/task_reports/P1-T4_evidence/acceptance.json)
- [final regression](docs/task_reports/P1-T4_evidence/final_regression/results.json)
- [report / handoff](docs/task_reports/P1-T4.md#p1-t5-handoff)

复现：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/s_seir/s_seir_research_propagation_tests.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/task_reports/P0-T1_baseline/run_baseline.py docs/task_reports/P1-T4_evidence/final_regression
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/task_reports/P1-T4_evidence/verify.py
```

## Stable Outputs / Interfaces

- `scripts/s_seir/s_seir_research_propagation.py`：`normalize_resource_limits / propagate_regions / validate_propagation / node_context_states / query_node_context_state / validate_result_evidence`。
- 唯一新增正式 artifact：`erc20-research/abstract-propagation/v1`；payload 恰为 `region_ref / domain_ref / node_context_states / convergence_evidence / resource_budget / termination`。identity = region + domain + explicit config/scheduling digest。
- node/context entry 恰为 `node_ref / context / state / status / evidence_refs`；state 是 incoming-before-node-transfer final cache state。只创建 reached entry；缺失表示 unreached，UNKNOWN 表示 reached 但缺证据。
- deterministic scheduling = canonical seeds/outgoing edges + FIFO + queued-key coalescing。资源预算显式为 processed items/cache entries/contributions/requeues；截止保留 partial cache/frontier。
- 直接复用 P1-T3 `consume_regions/local_transfer/join_states/update_context/stabilized/state_leq/validate/normalize/status`；ordinary fact identity，same-context join，different contexts 分离；observed case arm 才更新 context。
- FIXED_POINT 仅为 queue drained、无 pending/cutoff/failure、cache stabilized；UNKNOWN 可 FIXED_POINT。RESOURCE_LIMIT/UNSUPPORTED/ERROR 严格分离；upstream PARTIAL 不被局部 fixed point 洗白。
- `node_context_states/query_node_context_state` 是 P1-T5 consumer view，不产生 endpoint、real successor、Guard 或 feasibility。

P0-T3 frozen interfaces、shared contract、P2-T1A REQUIRED 链、G1–G8、oracle isolation 继续有效；无冻结接口变更，无新 ADR。

## Known Limitations

R1 source support仍 insufficient；没有声称复现论文 propagation algorithm/default budget。分析只覆盖 P1-T2 consumable flattened core + explicit exit boundary；缺 typed operands、unsupported local op、checked overflow、不可靠 block fact order保守 UNKNOWN。所有 structural outgoing edges 均作 carrier，不做 Guard/path feasibility，因此 context history 不是真实 successor。

尚未实现：Candidate/Refined SemanticControlEdge、endpoint reconstruction、real successor、symbolic/SMT、Guard normalization、Def-Use/RD/VFG/StateDependency、order/STIR。

## Blocked Issues

P1-T4 无当前阻塞；关键正例正常 FIXED_POINT，必要测试、16 项专项验收、machine evidence、report 与可调用 handoff 完整，无未解释 regression。

## Next Allowed Task

P1-T4 已完成并停止；**等待单独授权 P1-T5 — Candidate Semantic Control Edge Reconstruction**。
P1-T5 直接使用 [P1-T4 handoff](docs/task_reports/P1-T4.md#p1-t5-handoff) 的 `AbstractPropagation` node/context cache 与查询 API，并同时消费既有 SemanticAction/FlattenedRegion；不得重跑 detector/domain，不得把 traversal/context history 当 real successor，不得提前执行 SMT。

后续 bootstrap：RESEARCH_FRAMEWORK → TASK_MAP → PROJECT_STATUS → IMPLEMENTATION_PLAN 当前 Task/直接接口 → direct upstream report/handoff → 相关源码/测试，同时服从 AGENTS.md。重新读取实际 branch/HEAD；不自动进入 P1-T5。
