# Sample 10 — current Module 1 run

- Branch/HEAD: `semantic-ir-next` / `2530c87303dc9bb6e06b541a3c7c95af5373930e`.
- Source: `人工构造样例/10_大量Yul_统一语义模型/contracts/YulHeavyERC20.sol`; SHA-256 `2a307c7fd2f6cab220de93b208010deaf7f1f4c74266f54940b70c3089c1db46`.
- Current SFIR was generated from this source by `scripts/s_seir/s_seir_pipeline.py`. The branch-preprocessed analysis source has the same SHA-256 as the original source.
- The existing production P1-T1 through P1-T7 APIs ran without an interface failure. This is a recovery result, not an expected/oracle comparison.

## Overall

| Functions | SemanticActions | Candidates | FEASIBLE | INFEASIBLE | UNRESOLVED | COMPLETE functions | PARTIAL functions |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 11 | 32 | 52 | 2 | 0 | 50 | 0 | 11 |

P1-T2 found **0 FlattenedRegions** for this input; P1-T3 built the k-switch domain and P1-T4 returned **0 propagations**. P1-T5 still recovered 52 normal-region candidates. P1-T6 recorded 35 SAT and 17 UNSUPPORTED solver outcomes; only two SAT rows had COMPLETE scope. The remaining 33 SAT rows stayed UNRESOLVED because scope coverage was PARTIAL. No UNKNOWN or TIMEOUT solver outcome occurred.

## Per function

| Function | Actions | Candidates | FEASIBLE | INFEASIBLE | UNRESOLVED | Status |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| constructor(uint256) | 3 | 4 | 1 | 0 | 3 | PARTIAL |
| totalSupply() | 1 | 2 | 0 | 0 | 2 | PARTIAL |
| balanceOf(address) | 1 | 2 | 0 | 0 | 2 | PARTIAL |
| allowance(address, address) | 1 | 2 | 0 | 0 | 2 | PARTIAL |
| approve(address, uint256) | 3 | 4 | 0 | 0 | 4 | PARTIAL |
| transfer(address, uint256) | 6 | 9 | 0 | 0 | 9 | PARTIAL |
| transferFrom(address, address, uint256) | 8 | 13 | 0 | 0 | 13 | PARTIAL |
| burn(uint256) | 4 | 6 | 0 | 0 | 6 | PARTIAL |
| batchBalanceSum(address[]) | 1 | 3 | 0 | 0 | 3 | PARTIAL |
| hasCode(address) | 1 | 2 | 0 | 0 | 2 | PARTIAL |
| externalBalanceOf(address, address) | 3 | 5 | 1 | 0 | 4 | PARTIAL |

## Important recovered control edges

- `constructor(uint256)`: `ENTRY --[true]--> StorageWrite(_totalSupply := initialSupply)` is **FEASIBLE**, SAT with COMPLETE scope. Candidate `semantic-control-edge:503114889888cb21af4cf64620b90bece973d3bfc069d27f260983e16020fd7f`. The subsequent StorageWrite and Emit links remain UNRESOLVED despite local SAT because their source/prefix reachability is not established.
- `externalBalanceOf(address,address)`: `ENTRY --[true]--> ExternalCall(staticcall token.balanceOf(account))` is **FEASIBLE**, SAT with COMPLETE scope. Candidate `semantic-control-edge:98d9af7dc0dc47a49e6c84990c2c7ebf29621efc9e4f427ea5af732b8309233b`. Edges involving the Revert and Return remain UNRESOLVED.
- `transfer` and `transferFrom`: all candidate edges remain **UNRESOLVED**. Several Revert-related carriers have solver outcome UNSUPPORTED, with explicit evidence that operation/failure semantics or exact typed BranchCondition association are outside P1-T6's local predicate closure. This does not prove those candidates feasible or infeasible.
- There are **no INFEASIBLE/rejected edges** in this run.

Every SemanticAction ID, all 52 candidate IDs, canonical typed Guards, scope completeness, solver outcome and unresolved reason are listed by function in [summary.txt](summary.txt) and [summary.json](summary.json). [module1_control.dot](module1_control.dot) and [module1_control.txt](module1_control.txt) are presentation views of the final Module1Result; they do not add or remove edges.

## Artifacts and checks

- [run_manifest.json](run_manifest.json): exact commands, HEAD, source/compiler versions, stage config and artifact hashes.
- [sfir.json](sfir.json): newly generated SFIR; [sfir_stdout.log](sfir_stdout.log) and [sfir_stderr.log](sfir_stderr.log): pipeline logs.
- `p1_t1_actions.json.gz` through `p1_t7_module1_results.json.gz`: persisted production stage outputs; `p1_t3_domain.json.gz` includes the abstract-domain contract.
- [module1_stdout.log](module1_stdout.log), [module1_stderr.log](module1_stderr.log): Module 1 driver logs.
- [verification.json](verification.json): 16/16 source/artifact/lineage/view/production-boundary checks passed.
- [regression/results.json](regression/results.json): existing runner 33 PASS, 0 FAIL/SKIP/TIMEOUT. `git diff --check` passed; no production recovery file changed.

The observed gaps are current producer support limits, not fixes made in this run. No P2/M3 analysis was started and `PROJECT_STATUS.md` was not modified.
