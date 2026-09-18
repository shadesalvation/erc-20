# 废弃：Semantic IR

> 此目录未被当前 SFIR pipeline 使用，保留仅供历史参考。请使用 `scripts/s_seir/s_seir_pipeline.py` 输出的 Semantic Fact IR。

Semantic IR is the mutable, program-shaped object model for the unified
function-level Semantic Facts. It is deliberately not another semantic
analysis pass:

```text
Solidity Fact + Yul Fact -- SemanticFactBridge --> unified Semantic Facts
                                                  + function CFG
                                                       -> Semantic IR objects
```

## Contract

- Every input Fact with a `fact_id` becomes exactly one `SemanticOperation`.
- A `SemanticOperation.source_fact` is a preserved snapshot of that Fact;
  `semantic`, `evidence`, `condition`, `reads`, and `writes` are copied onto
  the mutable operation work surface without reinterpretation.
- Support facts are not fused into effects, and evaluation facts are not
  discarded. Any later grouping, simplification, or removal is an explicit
  deobfuscation rewrite with `rewrite_history`.
- CFG blocks, edges, and terminators are copied from the established
  function-level CFG. The builder uses CFG dominance only to place a Fact that
  has several candidate CFG nodes. Ambiguous placement is preserved in
  `unplaced_operations`; source order is never used.
- Locations are object projections of `semantic.location`, with an operation
  index; they do not create new storage semantics.

## In-memory API

```text
SemanticProgram
  -> SemanticFunction
       -> BasicBlock.operations: SemanticOperation[]
       -> CFG edges / direct CFG terminator
       -> location and Fact indexes
```

Useful query indexes are `operation_by_fact_id`, `operations_by_kind`,
`operations_by_location`, and `operations_by_stmt_ref`. They can be exported
with `--semantic-ir-analysis-indexes`.

Later deobfuscation passes mutate operations or CFG only through explicit
rewrite methods (`replace_operation`, `remove_operation`, `redirect_edge`,
`remove_block`). The original Fact remains available in each rewrite record.

## Output

- `semantic_ir.json` is the complete object serialization. Each Fact appears
  once as an operation, either in a block or in `unplaced_operations`.
- `semantic_ir.txt` is an audit view of the same objects. It shows each
  operation's Fact id, condition, location, and full `semantic` payload so
  path-distinct operations remain distinguishable.

The main pipeline writes both outputs by default:

```bash
python scripts/s_seir/s_seir_pipeline.py Contract.sol \
  --semantic-ir-output outputs/semantic_ir.json \
  --semantic-ir-text-output outputs/semantic_ir.txt
```
