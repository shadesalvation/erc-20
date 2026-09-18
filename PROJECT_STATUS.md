# Project Status

## Current Task

**P1-T2 = COMPLETE** — Dispatcher / Flattened Region Detection，2026-09-19。

P0-T1/P0-T2/P0-T3/P1-T1 均为 COMPLETE；本次按正式 bootstrap 核验此前 `Next Allowed Task = P1-T2`。当前 branch `semantic-ir-next`，Task `start_head=aff573e557424b00fa0159d3f1cff0da055c3e38`，`end_head=aff573e557424b00fa0159d3f1cff0da055c3e38`（未创建提交）。起始及收尾均保留四处既有 OpenZeppelin dependency checkout，不归入 P1-T2 改动。

本 Task 只实现 SFIR Fact CFG + P1-T1 SemanticAction → `FlattenedRegion` 的 structural candidate detection，没有修改既有 SFIR/P1-T1、MemorySSA/SinkResolver、oracle 或历史证据，没有执行 P1-T3 或后续 abstract interpretation、dependency、order、STIR、benchmark/experiment 工作。

## Baseline / Validation Summary

修改前 P1-T1 targeted：19/19 PASS；修改前 runner：**27 PASS / 0 FAIL / 0 SKIP / 0 TIMEOUT**。P1-T2 targeted：**14/14 PASS**；P1-T1 targeted regression：19/19 PASS；收尾 runner：**28 PASS / 0 FAIL / 0 SKIP / 0 TIMEOUT**。测试入口增加一项仅因新增 `s_seir_research_regions_tests.py`。

- [bootstrap/baseline](docs/task_reports/P1-T2_evidence/bootstrap.json)
- [targeted tests](docs/task_reports/P1-T2_evidence/targeted_tests.json)
- [专项验收 15/15](docs/task_reports/P1-T2_evidence/acceptance.json)
- [final regression](docs/task_reports/P1-T2_evidence/final_regression/results.json)
- [P1-T2 report](docs/task_reports/P1-T2.md)

复现：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/s_seir/s_seir_research_regions_tests.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/s_seir/s_seir_research_actions_tests.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/task_reports/P0-T1_baseline/run_baseline.py /tmp/p1-t2-regression
```

## Stable Outputs / Interfaces

- `scripts/s_seir/s_seir_research_regions.py`：`detect_flattened_regions(sfir_payload, action_result, input_fingerprint=None, config=None)` 与 `validate_flattened_region`。
- 唯一正式 artifact schema：`erc20-research/flattened-region/v1`；payload 严格为 `dispatcher_ref / region_cfg_refs / control_state_candidates / cases / entries / exits / detection_evidence`。
- identity basis 为 `CFG origin set + dispatcher occurrence`；block/node/edge id 仅作 SourceRef locator。遍历重排稳定，不依赖 `state/pc/dispatcher` 等变量名。
- detection 要求 multi-arm dispatch、至少两条结构回返 arm、已有 SFIR condition-read/region-write association 和可划定 entry/exit；这只是 `CANDIDATE`，solver 始终 `NOT_RUN`。
- SemanticAction 直接复用且不修改；关联方式为 `control_anchor → CFG block → region_cfg_refs`。replacement 只作 evidence；unresolved/unanchored 完整保留。
- ordinary acyclic switch 是合法 no-region；no-exit、multi-entry irreducible、ambiguous/insufficient 候选显式 `UNSUPPORTED` 或 `INSUFFICIENT_EVIDENCE/PARTIAL`。

P0-T3 frozen Module contracts、shared contract、P2-T1A REQUIRED 链、G1–G8、schema ownership 与 oracle isolation继续有效。本 Task 未改变 frozen interface，故无新 ADR。

## Known Limitations

- detector 是 structural high-recall candidate stage，不证明 reachability/feasibility/Guard/order 或 case 的真实 successor。
- control-state candidate 只用 SFIR 已有局部 reads/writes/FactSSA/binding/expression/placement evidence；没有跨 CFG value/constant propagation。
- 当前不支持安全划定无出口 cyclic candidate 或 multi-entry irreducible region；显式 PARTIAL，不伪装成 negative。
- standalone Yul object 仍不支持；仅处理已进入统一 SFIR 的 Solidity/Yul/Mixed。
- abstract value、k-switch context、domain/transfer/join、fixed point、Candidate SemanticControlEdge、symbolic/SMT、Guard normalization 均尚未实现。

## Blocked Issues

P1-T2 无当前阻塞。专项验收、必要回归、report、machine evidence 与 P1-T3 handoff 均完成，无未解释 failure。

## Next Allowed Task

P1-T2 已完成并停止；**等待单独授权 P1-T3 — k-switch Abstract Domain and Context**。P1-T3 必须直接消费 [P1-T2 handoff](docs/task_reports/P1-T2.md#p1-t3-handoff) 的 `FlattenedRegion` 与 diagnostics，不得重新检测 dispatcher/region，也不得把 cases 当作 real successor。

后续 bootstrap 仍为：RESEARCH_FRAMEWORK → TASK_MAP → PROJECT_STATUS → IMPLEMENTATION_PLAN 当前 Task/直接接口 → direct upstream report/handoff → 相关源码/测试，同时服从 AGENTS.md。不自动进入 P1-T3。
