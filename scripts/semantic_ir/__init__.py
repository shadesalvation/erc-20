"""Mutable Semantic IR materialized from S-SEIR and Semantic Facts."""

from .builder import SemanticIRBuilder, build_semantic_ir_program
from .exporter import render_semantic_ir_text, write_semantic_ir_json, write_semantic_ir_text
from .model import (
    BasicBlock,
    CFGEdge,
    DataObjectNode,
    ExpressionNode,
    LocationNode,
    SemanticFunction,
    SemanticInstruction,
    SemanticProgram,
    Terminator,
)

__all__ = [
    "BasicBlock",
    "CFGEdge",
    "DataObjectNode",
    "ExpressionNode",
    "LocationNode",
    "SemanticFunction",
    "SemanticIRBuilder",
    "SemanticInstruction",
    "SemanticProgram",
    "Terminator",
    "build_semantic_ir_program",
    "render_semantic_ir_text",
    "write_semantic_ir_json",
    "write_semantic_ir_text",
]
