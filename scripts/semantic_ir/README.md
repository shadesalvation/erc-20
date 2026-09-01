# Semantic IR v1

`semantic_ir` materializes the existing S-SEIR and unified Semantic Fact
analysis into a mutable, function-level program representation. It does not
redo Yul recovery or Solidity lifting.

## Runtime pipeline

```text
FunctionSSEIR + unified Semantic Facts
                 |
                 v
            Fact grouping
                 |
                 v
SemanticProgram -> SemanticFunction -> BasicBlock -> Instruction/Terminator
                                      -> Expression DAG
                                      -> Location objects
                                      -> Definition/Uses
```

Effect and control facts become instructions or terminators. Support facts,
such as `StorageLocationResolve` and a Yul `sload` computation already covered
by `MappingRead`, are fused into the primary instruction and retained in its
`origin_facts`. Candidates under different path conditions are never fused.

Expressions which fit the supported grammar become typed DAG nodes. An
expression that cannot be parsed is represented as `OpaqueExpression`; its raw
text is retained. Unknown fact kinds similarly become `OpaqueInstruction`.

## Execution and normalized expression views

`semantic-ir/v2` keeps two references for every expression-bearing field:

- `execution_expr`, `execution_arguments`, and `execution_condition` preserve
  Yul atomic temporaries and the operands used by the actual operation;
- `normalized_expr`, `normalized_arguments`, and `normalized_condition`
  preserve the recovered semantic form used for audit and rewriting.

Terminators use the corresponding `execution_values`, `normalized_values`,
`execution_condition`, and `normalized_condition` fields. The v1 fields
`expression`, `arguments`, `condition`, and `values` remain normalized-view
aliases for compatibility.

Definition-use construction reads only the execution view. A normalized state
write such as `total + amount` therefore cannot replace the real dependency on
the SSA result of a preceding `sload`. If no normalization was proven, both
views point to the same expression DAG node instead of inventing a higher-level
equivalent.

## Rewrite surface

`SemanticFunction` provides mutable operations for later deobfuscation passes:

- `replace_expression`
- `replace_instruction`
- `remove_instruction`
- `redirect_edge`
- `remove_block`
- `rebuild_cfg_links`

Every mutation records a `RewriteRecord`. After changing instruction operands,
call `SemanticIRBuilder.rebuild_def_use(function)`.

## Output

The ordinary S-SEIR pipeline now accepts:

```bash
python scripts/s_seir/s_seir_pipeline.py Token.sol \
  --semantic-ir-output outputs/semantic_ir.json
```

The batch pipeline writes `semantic_ir.json` in every per-contract result
directory, together with a human-readable `semantic_ir.txt`. The standalone
entry point is `scripts/semantic_ir/cli.py`:

```bash
python scripts/semantic_ir/cli.py Token.sol \
  --output outputs/semantic_ir.json \
  --text-output outputs/semantic_ir.txt
```

The text renderer is a semantics-preserving audit view of the runtime IR, not
a second analysis pipeline or a replacement for the complete JSON. It prints
functions, basic blocks, semantic instructions, recursive
expression and location forms, terminators, CFG edges, def-use records, and
`origin_facts`. It deliberately keeps `Branch` and `Goto` instead of guessing
structured Solidity control flow.
