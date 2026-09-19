# Sample 10 — P1-T6R run

Fresh production source → SFIR → P1-T1…P1-T7 run at working HEAD `cd7a13a3405fbf602b9906fb00a7a94f03461a60`, branch `semantic-ir-next`, with uncommitted P1-T6R changes. No sample source changes, no commit/push, no P2 work.

**11 functions; 32 actions; 52 candidates; 6 FEASIBLE; 0 rejected; 46 UNRESOLVED.** Functions remain **0 COMPLETE / 11 PARTIAL**. Main solver outcomes: **39 SAT / 13 UNSUPPORTED**. Scope completeness: **6 COMPLETE / 46 PARTIAL**. Zero flattened regions/propagations remains expected for this run.

The baseline in `../module1_sample10_current/` is unchanged. Baseline counts were 2 FEASIBLE / 50 UNRESOLVED, 35 SAT / 17 UNSUPPORTED. These are observations, not acceptance thresholds.

- `totalSupply`, `balanceOf`, `allowance`: ENTRY→Return closes using explicit StateRead evidence.
- `approve`: ENTRY→first action closes using resolved StorageLocationResolve evidence.
- The old SlithIR-only BranchCondition association error is absent. Existing structured AST/AddressZeroCheck evidence is consumed with exact SSA and preserved Require polarity.
- All ACTION-rooted source/prefix gaps remain. Out-of-carrier definitions, missing exact StateRead result versions, unknown/effectful computations, unsupported `not`/`returndatasize`, and repeated-loop evidence gaps remain explicit.
- All candidates remain; P1-T5/P1-T7 production semantics are unchanged. No SAT+PARTIAL feasibility upgrade.

See [task report](../../docs/task_reports/P1-T6R.md), [machine acceptance with per-candidate node reasons](../../docs/task_reports/P1-T6R_evidence/acceptance.json), [verification](verification.json), [manifest and hashes](run_manifest.json), [summary](summary.json), [control text](module1_control.txt), and [control graph](module1_control.dot).

`p1_t6_refinement.json.gz` persists typed Guards, scope evidence, all queries/digests, solver outcomes and accounting. `p1_t7_module1_results.json.gz` is the unchanged P1-T7 consumer's sealed output. The presentation driver only renders these persisted results.

Reproduce from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/task_reports/P1-T6R_evidence/rerun_sample.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/task_reports/P1-T6R_evidence/verify.py
```

The reusable thin production and presentation drivers remain in the baseline directory and are invoked with this new output directory; they are not modified.
