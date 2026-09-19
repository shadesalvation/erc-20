# P1-T6-001 — Local typed terms and reproducible SMT adapter

Status: adopted within the explicitly authorized P1-T6 scope, 2026-09-19.

## Problem / Evidence

P0-T3 freezes the six-field typed term vocabulary but leaves operator semantics
to its first producer. P1-T5's seven-field edge and validator are candidate-stage
only. P1-T4 exports incoming-before-transfer cache entries without per-contribution
path/value lineage. PredicateLifter renders strings; these are not typed ASTs.
The existing `.venv` has no SMT backend or pip; `uv` is available.

## Decision

Keep shared schemas and P1-T5 validators unchanged. P1-T6 owns its refined-edge
validator and wrapper. The refined edge keeps its candidate ID, endpoints,
region and context; `guard_ref = {artifact_ref: Guard.id}`. The referenced Guard
is pre-canonical. Wrapper fields carry completeness, accounting and local scope;
they are not extra SemanticControlEdge payload fields or a second control graph.

Typed term fields remain exactly `op/type/operands/literal/symbol_ref/arithmetic_mode`.
The first implemented operator version is `p1-t6/typed-atomic-lowering/v1`:

| Operators | Semantics |
| --- | --- |
| `literal`, `symbol` | Bool or explicitly sized signed/unsigned BV; decimal string literals, provenance-bound symbol refs |
| `== !=` | Same-type equality/inequality |
| `< <= > >=` | BV comparison using operand signedness |
| `+ - *` | Width-preserving `WRAPPING`, or `CHECKED` with an exact double-width equality side condition |
| `& \| ^ ~ << >>` | Fixed-width BV operations; right shift arithmetic for signed, logical for unsigned; equal-width operands |
| `! && \|\|` | Bool; logical binary terms declare `EAGER` or `SHORT_CIRCUIT`; the latter gates RHS side conditions |
| `cast` | BV narrowing extracts low bits; widening sign/zero extends according to source signedness; same-width preserves bits |
| `unknown` | Non-lowerable term; `type=null`, literal object preserves reason/source refs |

Type records use `name/bit_width/signed`: reuse P1-T3's explicit `uint8..uint256`
and `int8..int256`; add Bool (`null/null`) and address/address payable (`160/false`).
Missing type, width, signedness or arithmetic checkedness is unsupported. No
implicit infinite-precision integers. Division, remainder, exponentiation,
memory/storage aliasing, hashing, effectful results and Phi pairing remain unsupported.
This is an implementation subset, not a claim that all SFIR expressions are typed.

The SFIR adapter only resolves existing typed atomic evidence and exact FactSSA
reads. It does not parse raw predicate text. Text equality is used only to check
the producer's CFG-condition/typed-Condition association. Input symbols use entry
declaration refs; mutable locations and display names never become value identities.
P1-T4 exact query API is used for every node/context ref. Known cache values are
retained as evidence, but not asserted without an exact path/value association.
Missing associations explicitly make that scope PARTIAL; FIXED_POINT alone is
not a reachability proof. This deliberately leaves flattened refinement conservative.

Use `z3-solver==4.15.3.0`, installed via
`uv pip install --python .venv/bin/python -r scripts/s_seir/requirements-local-refinement.txt`.
The adapter records actual backend version, timeout, random seed 0, full SMT-LIB,
typed assertions and derived side conditions. See the [official Z3 Python API](https://z3prover.github.io/api/html/z3.z3.html).
Default per-candidate limits: 512 expansions, depth 64 (hard maximum 128), 128
solver calls, 1000 ms per call. No whole-function path enumeration.

SolverEvidence has the frozen envelope and required payload. Each call is separate.
`query:<digest>` resolves to that call's complete inline query artifact. Opaque
classification refs use these query IDs to avoid cyclic content-addressed evidence
IDs. Final classifications also cite the three enclosing SolverEvidence IDs in
the wrapper and shared EvidenceRecord. No model or proof object is retained;
`model_or_proof_ref=null`, with an explicit reason. SMT-LIB retains BV literals
losslessly and permits replay. No claimed proof certificate or canonical Guard.

## Alternatives

Rejected: relaxing P1-T5 validation; introducing a second graph; treating strings
as typed ASTs; assuming arbitrary Phi input or storage-name equality; asserting
all cache values together; home-made SAT rules; unpinned backend dependency.

## Impact / Affected Modules / Tasks / Experiments

Module 1 only, P1-T6 producer and P1-T7 direct consumer; Experiment 1 future input.
No change to framework, task dependency, oracle rules, SFIR, P1-T4 or P1-T5.
No semantic extension field is added. R2/R3 source support remains insufficient;
only the frozen structural narrowing followed by local refinement principle is used.

## Required Regression

P1-T6 typed/scope/solver/status/evidence tests, P1-T5/P1-T4 direct regressions,
existing runner, deterministic replay and frozen-producer byte comparison.
Evidence: `docs/task_reports/P1-T6_evidence/`.
