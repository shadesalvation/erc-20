# P1-T6R — local typed evidence correction

Status: COMPLETE (correction implemented and verified; awaiting human acceptance). This is a posterior correction to P1-T6, not a new research stage. No commit or push. `PROJECT_STATUS.md` and the authorized task progression are unchanged.

Branch: `semantic-ir-next`. Base HEAD and final working HEAD: `cd7a13a3405fbf602b9906fb00a7a94f03461a60`. HEAD matches the supplied snapshot; implementation is in the uncommitted working tree. The four pre-existing OpenZeppelin dependency checkout statuses are preserved. Historical P1-T6/P1-T7 reports/evidence and `outputs/module1_sample10_current/` are unchanged.

## Confirmed causes and corrections

1. Yul BranchCondition lacked SlithIR operand evidence. The existing AST evaluation DAG retained calls and evaluated operands, but their structural identities were not carried through Predicate/SFIR. `YulEvaluationOrder` now preserves AST argument descriptors; Predicate adds `yul-typed-predicate/v1` with operator, explicit word/Bool types, AST operand/evaluation identities and resolution status. SFIR preserves it. AddressZeroCheck consumes its existing structured variable/type/check plus exact FactSSA. Source/normalized strings remain association checks, never a parsed typed AST.
2. Resolved StateRead lost its explicit result type. The adapter preserves `result_type`; direct StateVariableRead saves the existing type-environment type, just as MappingRead already does. P1-T6 creates an unknown typed value at an exact read occurrence only with resolved scalar/mapping location and closed read/write/version/owner evidence. Identity includes semantic occurrence, location SourceRef, result definition and storage version. Reads are not unified by display/access text. Post-write/Phi/effect ambiguity remains unsupported.
3. Unused high-level support nodes were blanket reachability gaps. P1-T6 now records `reachability_transparent_evidence` with explicit rules for resolved StorageLocationResolve, exact typed StateRead, and supported total typed computations. It checks semantic kind/role, input versions, declared roots, term validity and checkedness. Unknown/effectful nodes and unproven checked arithmetic remain partial. An unsupported unused cast is also rejected.
4. Existing Require projection reverses failure/continuation polarity. SFIR now additionally preserves the unique original predicate occurrence and original CFG edge polarity before that existing projection. P1-T6 validates this binding and records `condition_polarity`, including opaque results. This adds evidence without changing CFG reconstruction or inferring negation from text.

The implementation version is `p1-t6r-v1`; artifact schemas and the frozen seven-field SemanticControlEdge remain unchanged. Existing public APIs (`refine_candidates`, `build_candidate_scope`, `query_refinements`, validation/serialization APIs) remain the handoff. P1-T7 consumes persisted results unchanged, without carrier reconstruction or solver reruns.

## Modified files

All code paths are under `scripts/s_seir/`:

- `s_seir_research_local_refinement.py`, `s_seir_research_local_refinement_tests.py`
- `s_seir_predicate_lifter.py`, `s_seir_predicate_lifter_tests.py`
- `s_seir_semantic_fact_adapter.py`, `s_seir_semantic_fact_adapter_tests.py`
- `s_seir_semantic_fact_ir.py`, `s_seir_semantic_fact_ir_tests.py`
- `s_seir_yul_eval_order.py`, `s_seir_overlay_builder.py`

Additional artifacts: this report, [P1-T6R_evidence](P1-T6R_evidence/acceptance.json), and [fresh Sample 10 run](../../outputs/module1_sample10_p1t6r/RUN_SUMMARY.md).

## Validation

Before modifications, existing runner: **33 PASS** ([baseline](P1-T6R_evidence/baseline/results.json)). Final targeted: **P1-T6 43/43**, **Predicate 5/5**, **adapter 26/26**, **SFIR 29/29**, **P1-T5 26/26**, **P1-T7 12/12**, **Module1 micro 4/4**. Final full runner: **33 PASS / 0 FAIL / 0 SKIP / 0 TIMEOUT** ([results](P1-T6R_evidence/final_regression/results.json)). `git diff --check` passes.

All original soundness tests remain, including missing typed predicate, source-prefix coverage, UNKNOWN/TIMEOUT, checked/wrapping arithmetic, resource limits and upstream status conservation. Added tests cover structured AST evidence deletion, explicit StateRead closure failures, distinct read identities, prior effects, repeated carriers, support transparency, unused checked/unsupported casts, structural Require polarity, traversal reorder and P1-T7 consumption.

The interrupted final runner had completed only 27/33; it was rerun to completion. Early development failures are retained in `local_refinement_initial.log` and `correction_initial.log`; their fixes are exercised by final passing tests. No failures were waived.

[Machine acceptance](P1-T6R_evidence/acceptance.json) recomputes scopes/accounting/query digests, replays persisted solver calls, verifies candidate partition/lineage and graph witnesses, reseals P1-T7 without solving, checks protected files and baseline hashes, and records per-candidate reasons with affected semantic nodes. Z3 **4.15.3**, timeout **1000 ms**, random seed **0**; proof/model retention remains disabled with explicit reasons. Full query artifacts remain persisted.

## Sample 10 observations

Fresh source → SFIR → existing Module 1 public APIs, using unchanged thin drivers. No recovery logic or sample special case was added to the driver. New output: `outputs/module1_sample10_p1t6r/`. The original sample source is unchanged.

| Metric | Before | After |
|---|---:|---:|
| Functions | 11 | 11 |
| SemanticActions | 32 | 32 |
| Candidates | 52 | 52 |
| FEASIBLE | 2 | 6 |
| INFEASIBLE / rejected | 0 | 0 |
| UNRESOLVED | 50 | 46 |
| COMPLETE / PARTIAL functions | 0 / 11 | 0 / 11 |
| Solver SAT / UNSUPPORTED | 35 / 17 | 39 / 13 |
| Solver UNSAT / UNKNOWN / TIMEOUT / NOT_RUN | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |
| COMPLETE / PARTIAL scopes | 2 / 50 | 6 / 46 |

Reason counts below count distinct affected candidates per reason; categories overlap and are not an aggregate oracle.

| Unresolved reason | Before | After |
|---|---:|---:|
| SlithIR-only typed predicate association | 17 | 0 |
| Carrier operation/failure not covered | 50 | 46 |
| ACTION source/prefix not established | 40 | 40 |
| Missing control-edge guard | 1 | 1 |
| Repeated carrier block | 1 | 1 |
| Definition outside local carrier | 0 | 9 |
| StateRead missing exact result/read versions | 0 | 9 |
| StateRead after storage/effectful operation | 0 | 1 |
| StateRead dynamic occurrence unavailable | 0 | 1 |
| Yul typed representation unavailable | 0 | 2 |
| Unsupported Yul `not` | 0 | 2 |
| Unsupported `returndatasize` | 0 | 2 |

`totalSupply`, `balanceOf`, `allowance` ENTRY→Return now have explicit StateRead transparency evidence and COMPLETE scopes. `approve` ENTRY→first action has resolved StorageLocationResolve transparency evidence. Neither kind contributes the old blanket carrier reason anywhere in the new run. The remaining blanket reasons reference other nodes, including source actions/returns and unsupported value computations; they were not deleted.

For `transfer`, `burn` and `transferFrom`, structured branch evidence is consumed without the old SlithIR-only association failure. Some definitions lie outside the emitted local carrier and remain unsupported; the implementation does not extend carriers to fetch them. All 40 ACTION-rooted candidates retain the source/prefix limitation. No incoming-FEASIBLE propagation or synthetic Require/Revert reachability shortcut was introduced.

## Retained boundaries and limitations

- Typed Yul subset: `eq`, `lt`, `gt`, `slt`, `sgt`, `iszero`, and Boolean-valued `and`/`or`; numeric AST literals and exact word operands. A mismatched Solidity declaration type is rejected rather than silently cast. Other builtins/operators and loop predicates without structured evidence remain unsupported.
- StateRead support is deliberately restricted to resolved scalar/mapping reads with one exact storage input and result definition, entry storage version, unique carrier occurrence, and no preceding storage/effectful operation. No alias inference, cross-read equality, persistent RD or missing-result synthesis.
- ACTION-rooted reachability, synthetic Require/Revert provenance, external-call/returndata semantics, Phi and dynamic iteration remain unresolved where evidence is insufficient. SAT+PARTIAL stays UNRESOLVED; no candidates are removed.
- P1-T1 through P1-T5 and P1-T7 production semantics are unchanged. No P2 Def-Use/RD/VFG/slicing, Module 2, Module 3, state transition reconstruction or STIR. R2/R3 source support remains insufficient; no paper algorithm reproduction claim.

Reproduce the new run and audit (historical outputs are read-only inputs):

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/task_reports/P1-T6R_evidence/rerun_sample.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/task_reports/P1-T6R_evidence/verify.py
```
