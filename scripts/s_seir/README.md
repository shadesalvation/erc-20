# S-SEIR

This package owns function-level Yul/inline-assembly semantic analysis:

- Solidity/Yul source statement collection and unified CFG construction;
- AST-based Yul atomic-operation extraction with right-to-left call-argument evaluation;
- Yul MemorySSA, SinkResolver and branch preprocessing;
- expression roles and low-level effects;
- storage, event, revert, call and memory semantic overlays;
- semantic normalization and audit-oriented output.

The package produces `FunctionSSEIR` objects. It no longer owns the unified
Semantic Fact implementation. Fact construction lives in
`scripts/semantic_fact/`, while mutable program materialization lives in
`scripts/semantic_ir/`.

`s_seir_yul_atomic_ops.py` runs before semantic lifting. It keeps one operation
per record, links nested operations through temporary values and dependencies,
and anchors every record to its Yul CFG node and original `asm_s_*` statement.
The table remains available on the runtime function object. Use
`--yul-atomic-output` when an audit JSON snapshot is needed.
