# Project Status

## Current Task

**P1-T6 = COMPLETE** — Local Symbolic Execution + SMT，2026-09-19。

P0-T1/P0-T2/P0-T3/P1-T1/P1-T2/P1-T3/P1-T4/P1-T5 均为 COMPLETE。
本次只执行 P1-T6；实际 branch `semantic-ir-next`，
`start_head=end_head=c2d7172cb9b44a8daa6f98c795a6970ef2102cbb`，与 prompt 核验快照一致。
未 commit/push。四处既有 OpenZeppelin dependency checkout 改动完整保留。

直接消费 P1-T5 candidate edges、evidence、accounting、unresolved scopes 与
P1-T4 exact node/context query API。新增 bounded typed symbolic closure、Z3
refinement、pre-canonical Guard、逐 query SolverEvidence、opaque prefix classification。
保持原 candidate_id/from/to/region/context 与七字段 schema；没有 mutation 输入、
删除 candidate、生成 transitive candidate 或放宽 P1-T5 candidate validator。
P1-T4/P1-T5/shared contracts/SFIR/MemorySSA/SinkResolver 未修改。

## Baseline / Validation Summary

修改前 runner **31 PASS / 0 FAIL / 0 SKIP / 0 TIMEOUT**，其中 P1-T5 targeted
26/26、P1-T4 targeted 18/18。收尾 P1-T6 **33/33**、P1-T5 **26/26**、P1-T4
**18/18**；machine targeted 共 **77 PASS / 0 FAIL**。最终 runner **32 PASS /
0 FAIL / 0 SKIP / 0 TIMEOUT**。专项验收 **14/14 PASS**；无未解释 regression。

- [Bootstrap / baseline](docs/task_reports/P1-T6_evidence/bootstrap.json)
- [Targeted tests](docs/task_reports/P1-T6_evidence/targeted_tests.json)
- [Acceptance](docs/task_reports/P1-T6_evidence/acceptance.json)
- [Final runner](docs/task_reports/P1-T6_evidence/final_regression/results.json)
- [Frozen producer audit](docs/task_reports/P1-T6_evidence/frozen_boundary.json)
- [Persisted refinement + full queries](docs/task_reports/P1-T6_evidence/refinement_result.json)
- [Accounting](docs/task_reports/P1-T6_evidence/solver_accounting.json)
- [P1-T7 handoff smoke](docs/task_reports/P1-T6_evidence/handoff_smoke.json)
- [Report / handoff](docs/task_reports/P1-T6.md#p1-t7-handoff)
- [Final workspace audit](docs/task_reports/P1-T6_evidence/final_workspace.json)

复现：

```bash
uv pip install --python .venv/bin/python -r scripts/s_seir/requirements-local-refinement.txt
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/s_seir/s_seir_research_local_refinement_tests.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/task_reports/P0-T1_baseline/run_baseline.py docs/task_reports/P1-T6_evidence/final_regression
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/task_reports/P1-T6_evidence/verify.py
```

## Stable Outputs / Interfaces

Owner `scripts/s_seir/s_seir_research_local_refinement.py`：
`refine_candidates / build_candidate_scope / SolverBackend / Z3Backend /
validate_guard / validate_solver_evidence / validate_refined_edge /
query_refinements / refinement_accounting / serialize_refinement_result`。

按 candidate_id 直接取得 refined edge + Guard + SolverEvidence；无需重建 carrier
或重跑 solver。Guard ref 是 `{artifact_ref: guard.id}`，typed term 保持冻结六字段。
operator/type/adapter 版本决策见 [P1-T6-001](docs/decisions/P1-T6-001-local-typed-smt.md)，
IMPLEMENTATION_PLAN 同步链接；无框架/Task dependency/oracle 规则变化。

Backend `z3-solver==4.15.3.0`（runtime 4.15.3），默认 timeout=1000ms/call、
512 expansions、depth=64、128 calls/candidate。保存完整 typed query、SMT-LIB、
side conditions、digest、backend/version/options、scope/assumptions。
不保留 model/proof object，显式 null 与 reason。

SAT/UNSAT 仅在 COMPLETE scope 升级 FEASIBLE/INFEASIBLE；PARTIAL query 的局部
结果仍 UNRESOLVED。UNKNOWN/TIMEOUT/UNSUPPORTED/NOT_RUN 均保留 candidate/reason。
opaque 三个子查询独立，使用各自 prefix base，base UNSAT 不产生 vacuous constant。
上游 PARTIAL/TRUNCATED/UNKNOWN/UNSUPPORTED/ERROR、frontier 与 diagnostics 守恒。

## Known Limitations

R2/R3 `source support insufficient`，只采用结构缩小后局部 refinement 的组织原则，
不声称复现论文算法。当前 COMPLETE 支持 ENTRY-rooted closed typed carrier。
ACTION 起点缺入口前缀证明时只保留 local result；P1-T4 缺逐 contribution path/value
identity，已知 cache 仅作为 exact evidence 保存，不无证据合取或当成可行证明。
因此 flattened/mixed/contextual 路径可消费但通常保持 PARTIAL/UNRESOLVED。

支持 Bool、明确宽度 signed/unsigned BV、address、比较、加减乘、bitwise/logical、
shift/cast、CHECKED side conditions 和 WRAPPING（含 256-bit EVM word）。
raw Yul/switch 字符串条件、Phi 配对、carrier 外 definition、environment/old-state/
effectful result、除余/指数/hash 等明确 unsupported；不扩张为通用 executor。

尚未实现：Guard canonicalization/equivalence、Module1Result/Evaluation、
P2 dataflow/dependency、M3 order/failure/rollback/STIR。

## Blocked Issues

P1-T6 无当前阻塞。所有必要回归、专项验收、machine evidence、typed query replay、
状态/accounting 守恒与 direct handoff 已完成。保守 unsupported 是已记录支持边界。

## Next Allowed Task

P1-T6 已完成并停止；**等待单独授权 P1-T7 — Guard Canonicalization + Module 1 Evaluation**。
P1-T7 直接消费 [P1-T6 handoff](docs/task_reports/P1-T6.md#p1-t7-handoff)，
不得重建候选/carrier 或重跑 solver。不得把 local SAT 或 FIXED_POINT 当成全路径可行。

Bootstrap：RESEARCH_FRAMEWORK → TASK_MAP → PROJECT_STATUS → IMPLEMENTATION_PLAN
当前 Task/直接接口 → direct upstream handoff → 相关代码/测试，同时服从 AGENTS.md。
Repository 是唯一正式状态；重新核验实际 branch/HEAD，不自动开始后续任务。
