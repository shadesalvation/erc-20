"""Unified Semantic Fact construction for Solidity atoms and Yul S-SEIR."""

from .adapter import (
    SSeirFactAdapter,
    SemanticFact,
    SlitherFactAdapter,
    build_function_level_semantic_fact_payload,
    write_json,
)
from .bridge import FunctionSemanticInput, SemanticFactBridge
from .solidity_atomic_ops import (
    SolidityAtomicOperationExtractor,
    build_solidity_atomic_operation_payload,
    write_solidity_atomic_operation_json,
)
from .solidity_lifter import SoliditySemanticLifter
from .yul_lifter import YulSemanticLifter

__all__ = [
    "FunctionSemanticInput",
    "SSeirFactAdapter",
    "SemanticFact",
    "SemanticFactBridge",
    "SlitherFactAdapter",
    "SolidityAtomicOperationExtractor",
    "SoliditySemanticLifter",
    "YulSemanticLifter",
    "build_function_level_semantic_fact_payload",
    "build_solidity_atomic_operation_payload",
    "write_json",
    "write_solidity_atomic_operation_json",
]
