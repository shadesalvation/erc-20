# Project Status

## Current Task

**P1-T3 = COMPLETE** — k-switch Abstract Domain and Context，2026-09-19。

P0-T1/P0-T2/P0-T3/P1-T1/P1-T2 均为 COMPLETE；本次按 frozen bootstrap 核验此前等待 P1-T3 授权，并只执行当前 Task。实际 branch `semantic-ir-next`；`start_head=end_head=1cb1d117c7c395cbf78464d7ff985a00441f767d`，未 commit/push。P1-T2 report 的旧 task-local SHA 与当前 post-task commit 差异已审查为 documentation drift，见 [bootstrap/report](docs/task_reports/P1-T3.md#framework-alignment--bootstrap)。四处既有 OpenZeppelin dependency checkout 改动完整保留，不计入本任务。

本 Task 只定义域、单个候选的局部 transfer/join、bounded observed-arm context 与 stabilization API；没有执行 P1-T4 worklist/fixed point 或后续 Tasks，没有修改 P1-T2/P1-T1/shared contract/SFIR/MemorySSA/SinkResolver/oracle。

## Baseline / Validation Summary

修改前 P1-T2 targeted：14/14 PASS；runner：28 PASS / 0 FAIL / 0 SKIP / 0 TIMEOUT。
P1-T3 targeted：**33/33 PASS**；P1-T2 regression：14/14 PASS；P1-T1 regression：19/19 PASS。
收尾 runner：**29 PASS / 0 FAIL / 0 SKIP / 0 TIMEOUT**（仅增加 P1-T3 测试入口）；专项验收 **17/17 PASS**。

- [bootstrap](docs/task_reports/P1-T3_evidence/bootstrap.json)
- [targeted tests](docs/task_reports/P1-T3_evidence/targeted_tests.json)
- [专项验收](docs/task_reports/P1-T3_evidence/acceptance.json)
- [final regression](docs/task_reports/P1-T3_evidence/final_regression/results.json)
- [report / handoff](docs/task_reports/P1-T3.md#p1-t4-handoff)

复现：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/s_seir/s_seir_research_abstract_domain_tests.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/task_reports/P0-T1_baseline/run_baseline.py /tmp/p1-t3-regression
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/task_reports/P1-T3_evidence/verify.py
```

## Stable Outputs / Interfaces

- `scripts/s_seir/s_seir_research_abstract_domain.py`：`build_contract / validate_contract / consume_regions`；值构造与规范化、`local_transfer / pure_operation / join_values / join_states / update_context / stabilized / value_leq / state_leq`。
- 唯一正式 artifact：`erc20-research/abstract-domain-contract/v1`；payload 恰为 `domain_version / value_domain / context_policy / k / transfer_rules / join_rules / context_update / stabilization_policy`。identity = canonical 规则/config digest。
- AbstractValue/AbstractState/KSwitchContext 仅内部 dict views；无新顶层 schema。BOTTOM/typed FINITE/TOP/UNKNOWN 严格区分，UNKNOWN 保留 status/reason/SourceRefs。
- config 必须显式传 k（0..32）与 finite_cap（1..256）；不是论文默认值。finite distinct values > cap 才升 typed TOP；k 是最近 observed dispatcher arms 的历史长度。
- 直接消费 P1-T2 region.id/payload/status/evidence，保留 candidate_diagnostics/action_input，不重新提取 actions。合法 no-region 与 upstream incomplete 分别返回 NO_REGION 与 NO_CONSUMABLE_REGION；均不伪造 per-region state。
- typed uintN/intN 值、literal/copy、局部 `+ - * & | ^`、显式 typed literal Phi alternatives、identity cast；缺类型/模式/operand 或非支持操作显式 UNKNOWN。checked overflow 不等于 wrapping，不推导 failure path。
- join/stabilization 用 canonical state/evidence/diagnostics + Context；不使用 object identity 或偶然 list order。SourceRef/FactSSA 只作已有局部证据，不重做 RD/CFG/SSA。

P0-T3 frozen interfaces、shared contract、P2-T1A REQUIRED 链、G1–G8、oracle isolation 继续有效；无冻结接口变更，无新 ADR。

## Known Limitations

R1 source support 仍 insufficient；没有声称复现论文 lattice/default k/算法。主线 atomic SFIR 未保留足够 typed operand 的复杂表达式、缺宽度/算术模式、非 identity cast、standalone Yul/non-SFIR 等仍为显式 UNKNOWN/UNSUPPORTED。checked 溢出保守 UNKNOWN。合法 initial unknown 并不表示 P1-T3 实现失败，也不作为 proven program value。

尚未实现：worklist、whole-region fixed point、node/context propagated states、real successor、Candidate/Refined SemanticControlEdge、symbolic/SMT、Guard normalization、Def-Use/RD/VFG/StateDependency、order/STIR。

## Blocked Issues

P1-T3 无当前阻塞；必要测试、17 项专项验收、machine evidence、report 与可调用 handoff 完整，无未解释 regression。

## Next Allowed Task

P1-T3 已完成并停止；**等待单独授权 P1-T4 — Fixed-Point Propagation Engine**。
P1-T4 直接使用 [P1-T3 handoff](docs/task_reports/P1-T3.md#p1-t4-handoff) 的域/state/context/transfer/join/stabilization API，不得复制第二套域逻辑。P1-T2 cases/RETURNS_TO_DISPATCHER 仍仅为 observed structural evidence，不是真实 successor。

后续 bootstrap：RESEARCH_FRAMEWORK → TASK_MAP → PROJECT_STATUS → IMPLEMENTATION_PLAN 当前 Task/直接接口 → direct upstream report/handoff → 相关源码/测试，同时服从 AGENTS.md。重新读取实际 branch/HEAD；不自动进入 P1-T4。
