# Project Status

## Current Task

**P1-T1 = COMPLETE** — Semantic Action Identification，2026-09-19。

P0-T1/P0-T2/P0-T3 均为 COMPLETE；本次按正式 bootstrap 核验 `P0-T3 = COMPLETE` 且此前 `Next Allowed Task = P1-T1`。当前 branch `semantic-ir-next`，Task 起始 HEAD `d96e7029113e4385df7665f1fbc4a5898f6a5f01`。起始及收尾均保留四处既有 OpenZeppelin dependency checkout，不归入 P1-T1 改动。

本 Task 只实现 shared frozen-v1 research contract 与 SFIR→SemanticAction 投影，没有修改既有 SFIR/adapter/lifter、MemorySSA/SinkResolver、oracle 或历史证据，没有执行 P1-T2 或后续控制、依赖、顺序、STIR、benchmark/experiment 工作。

## Baseline / Validation Summary

修改前：**26 PASS / 0 FAIL / 0 SKIP / 0 TIMEOUT**。新增 P1-T1 targeted 入口后，收尾 runner：**27 PASS / 0 FAIL / 0 SKIP / 0 TIMEOUT**；P1-T1 内部 19/19 unittest PASS，含真实 Solidity→SFIR 六类 action probe。测试数增加一项仅因新增 `s_seir_research_actions_tests.py`，未硬编码旧数量。

- [bootstrap](docs/task_reports/P1-T1_evidence/bootstrap.json)
- [targeted tests](docs/task_reports/P1-T1_evidence/targeted_tests.json)
- [final regression](docs/task_reports/P1-T1_evidence/final_regression/results.json)
- [专项验收 21/21](docs/task_reports/P1-T1_evidence/acceptance.json)
- [P1-T1 report](docs/task_reports/P1-T1.md)

复现：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/s_seir/s_seir_research_actions_tests.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/task_reports/P0-T1_baseline/run_baseline.py /tmp/p1-t1-regression
```

## Stable Outputs / Interfaces

- `scripts/s_seir/s_seir_research_contracts.py`：`ArtifactEnvelope`、`EvidenceRecord`、`SourceRef`、`AnalysisStatus`、canonical JSON、stable identity、schema/status validation。
- `scripts/s_seir/s_seir_research_actions.py`：`extract_semantic_actions(sfir_payload, input_fingerprint=None, config=None)` 与 `validate_semantic_action`。
- frozen `SemanticAction` 六类：`StorageWrite / ExternalCall / EtherTransfer / Emit / Revert / Return`；required payload 保持 `kind / semantic_ref / operand_refs / control_anchor / replacement_refs`。
- 每个进入范围的 effect occurrence 均为 `EMITTED / REPLACED / UNRESOLVED`；StateRead 明确不是 P1-T1 action。unknown/unsupported/unanchored 与上游 SFIR diagnostics 保持结构化，不静默成功。
- identity basis 为 function + semantic occurrence provenance + action kind；repeat/reorder 稳定，不同 occurrence 不按 payload 合并；证据不足时 `RUN_LOCAL + INSUFFICIENT_EVIDENCE`。
- call-with-value 是一个 ExternalCall + value operand；只有 SFIR `ValueTransferCall` 成为 EtherTransfer。Require/Assert failure 只在 evidence 充分时投影，并与 canonical Revert 去重。
- operand refs 保留 storage location/key/update、call target/arguments/value/result、event args/topics、return values、revert/failure source 与已有 FactSSA refs；没有执行 dependency recovery。

P0-T3 冻结的 Module contracts、P2-T1A REQUIRED 链、G1–G8、目录/schema ownership 与 oracle isolation继续有效。SFIR schema仍为 `s-seir-semantic-fact-ir/v1`；本 Task没有改变 frozen interface，故无新 ADR。

## Known Limitations

- P1-T1 只能投影当前 SFIR 已恢复的 semantic occurrence；独立 Yul object frontend 仍不支持，按 UNSUPPORTED 保留。
- Internal/Library/NewContract/SelfDestruct 等冻结六类之外的 effect-like kind 进入 unresolved coverage，不擅自扩展 action enum。
- 缺 stable provenance 的 action 只能 `RUN_LOCAL`；正式调用方应提供 compilation input fingerprint，fallback 仅是 traversal-neutral SFIR digest。
- unknown call target/arguments、storage location/update 等不表示空/无依赖；action保留 PARTIAL diagnostic。
- 本 Task 未分析 feasibility、Guard、Def-Use/RD、storage equivalence、ValueFlow、slice/dependency、external state impact、order、commit/rollback 或 STIR。
- 既有全局限制仍在：storage identity/phi/environment refs、未知 external effect、复杂循环/order、依赖锁/CI、正式 benchmark/dataset/experiment 尚未完成。
- 独立 STORAGE-ADDR-EQ-001 仍为既有 PARTIAL，缺指定旧模型源码；不属于现行 SFIR/P1-T1，也未被改写为 PASS。

## Blocked Issues

P1-T1 无当前阻塞。专项验收、必要回归、report、machine evidence 与 P1-T2/M2 handoff 均完成，无未解释 failure。

## Next Allowed Task

P1-T1 已完成并停止；**等待单独授权 P1-T2 — Dispatcher / Flattened Region Detection**。不得重新提取 SemanticAction；按 [P1-T1 handoff](docs/task_reports/P1-T1.md#p1-t2-handoff) 直接消费 `result["actions"]`、action id、`semantic_ref`、`control_anchor`、evidence/status、replacement refs 与 unresolved coverage。

后续 bootstrap 仍为：RESEARCH_FRAMEWORK → TASK_MAP → PROJECT_STATUS → IMPLEMENTATION_PLAN 当前 Task/直接接口 → direct upstream report/handoff → 相关源码/测试，同时服从 AGENTS.md。不自动进入 P1-T2。
