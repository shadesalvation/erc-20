# Semantic Fact

This package owns the source-neutral Semantic Fact layer.

```text
Solidity source -> SolidityAtomicOperationExtractor -> SoliditySemanticLifter
Yul source      -> S-SEIR                           -> YulSemanticLifter
                                                     |
                                                     v
                                            common Fact schema
                                                     |
                                                     v
                                           SemanticFactBridge
```

Modules:

- `solidity_atomic_ops.py`: SlithIR-SSA to Solidity atomic operations.
- `solidity_lifter.py`: Solidity atoms to the common Fact schema.
- `yul_lifter.py`: completed S-SEIR overlays to the common Fact schema.
- `adapter.py`: Fact model, Yul adapter, payload construction and JSON output.
- `bridge.py`: function-level CFG ordering and control relationships.

This package does not perform Yul MemorySSA, storage recovery, event recovery,
or call recovery. Those remain in `scripts/s_seir/`.
