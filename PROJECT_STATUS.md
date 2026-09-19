# Project Status

## Current Task

**P1-T7 = COMPLETE** — Guard Canonicalization + Module 1 Evaluation, 2026-09-19.

P0-T1/P0-T2/P0-T3 and P1-T1 through P1-T6 remain COMPLETE. This task consumed persisted P1-T6 refinement rows and P1-T1 SemanticActions, sealed typed Guards, added scoped five-state Guard comparison, constructed a stable `Module1Result`, and evaluated 11 local micro cases. It did not change P1-T6 feasibility, reconstruct candidates, implement P2/M3, or create P4/P5 datasets or runners.

Actual branch `semantic-ir-next`; `start_head=end_head=5b41fa42f181a44bbed1e3e7d8cbfadc4a46807f`. The earlier P1-T6 report's pre-commit snapshot differs because HEAD is its `p1-t6-completed` commit. No commit/push in P1-T7. Four pre-existing OpenZeppelin dependency checkouts remain untouched.

## Baseline / Validation

Before changes: P1-T6 targeted 33/33 PASS, P1-T1 targeted 19/19 PASS, existing runner 32/32 PASS. Final: P1-T7 targeted **12/12**, evaluator micro **4/4**, P1-T6 **33/33**, P1-T1 **19/19**, runner **33/33** PASS. Machine acceptance **6/6 PASS**, no unexplained regression; `git diff --check` passes. See [P1-T7 report](docs/task_reports/P1-T7.md) and [acceptance evidence](docs/task_reports/P1-T7_evidence/acceptance.json).

## Stable Outputs / Direct Handoff

Production owner: `scripts/s_seir/s_seir_research_guards.py`. Public API: `canonical_term`, `seal_guard`, `validate_sealed_guard`, `guard_comparison_projection`, `compare_guards`, `build_module1_result`, `validate_module1_result`, `query_module1_edges`, `serialize_module1_result`. Canonical rule `p1-t7/guard-canonicalization/v1`; comparison `p1-t7/guard-comparison/v1`. P1-T6 typed terms and feasibility semantics are unchanged.

`Module1Result` schema `erc20-research/module1-result/v1` is an ArtifactEnvelope. Payload contains the P1-T1 actions, feasible/unresolved/rejected edge partitions, sealed Guards, lineage/evidence, unresolved scopes and accounting. The [persisted direct handoff](docs/task_reports/P1-T7_evidence/module1_result.json.gz) contains **1 feasible, 1 rejected, 2 unresolved** candidate IDs and PARTIAL function status. P2-T1 can load it alongside SFIR FactSSA/operands in the [P1-T6 persisted input](docs/task_reports/P1-T6_evidence/handoff_input.json); no P1-T6 rerun or control-edge recovery is required.

Evaluator owner: `experiments/evaluators/module1.py`. The [micro cases](experiments/evaluators/module1_micro_cases.json.gz) and [per-case results](docs/task_reports/P1-T7_evidence/module1_evaluation.json) retain expected claims, recovered refs, comparison outcomes, metric contributions, runtime, config and coverage. Recovery has no expected/oracle/evaluator dependency. Unknown endpoint identity, Guard comparison and flattened/mixed control remain explicit partial coverage.

## Known Limitations

Structural checked/short-circuit Guard equivalence needs joint definedness semantics and returns UNSUPPORTED unless the exact typed projection fast path applies. Cross-sample scope, assumptions and symbols need evidenced mappings. Untyped StorageWrite location strings cannot establish semantic action endpoint equality; join/loop micro cases report non-evaluable coverage. The mixed flattened micro case remains 73 UNRESOLVED candidates. No P2 Def-Use/RD/VFG/slicing/state dependency, M3 transition/order/failure, P4 formal benchmark or P5 experiment runner has been started.

## Next Allowed Task

P1-T7 is complete and stopped. **P2-T1 — Def-Use Infrastructure** requires separate authorization. Bootstrap from the frozen framework, task map, this status, relevant plan sections, [P1-T7 handoff](docs/task_reports/P1-T7.md#module1result-and-p2-t1-direct-handoff), stable artifacts and applicable `AGENTS.md`; do not restart control recovery.
