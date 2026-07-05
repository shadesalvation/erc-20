#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path as _SSEIRPath
import sys as _sseir_sys
_SSEIR_ROOT = _SSEIRPath(__file__).resolve().parents[1]
for _sseir_path in (_SSEIR_ROOT / "legacy_yul", _SSEIR_ROOT / "s_seir"):
    _sseir_text = str(_sseir_path)
    if _sseir_text not in _sseir_sys.path:
        _sseir_sys.path.insert(0, _sseir_text)
from dataclasses import asdict, dataclass, field
from typing import Any

@dataclass
class SourceStatement:
    stmt_id: str; lang: str; text: str; src: str; function_id: str; block_id: str|None=None; origin: dict[str,Any]=field(default_factory=dict)
    def to_dict(self): return asdict(self)
@dataclass
class ExpressionRole:
    expr_id: str; text: str; normalized: str|None; role: str; type_hint: str|None; stmt_ref: str; attrs: dict[str,Any]=field(default_factory=dict)
    def to_dict(self): return asdict(self)
@dataclass
class EffectNode:
    effect_id: str; kind: str; stmt_refs: list[str]; attrs: dict[str,Any]=field(default_factory=dict)
    def to_dict(self): return asdict(self)
@dataclass
class SemanticOverlay:
    overlay_id: str; kind: str; effects: list[str]; stmt_refs: list[str]; attrs: dict[str,Any]=field(default_factory=dict)
    def to_dict(self): return asdict(self)
@dataclass
class ProjectionPolicy:
    policy_id: str; target_overlay: str; exact_solidity_equivalent: bool; output_kind: str; reason: str; stmt_refs: list[str]
    def to_dict(self): return asdict(self)
@dataclass
class SecurityFact:
    fact_id: str; kind: str; attrs: dict[str,Any]; source_overlays: list[str]; source_effects: list[str]; stmt_refs: list[str]
    def to_dict(self): return asdict(self)
@dataclass
class VariableInfo:
    name: str; kind: str; type_string: str; data_location: str|None=None; src: str=""; storage_slot: int|None=None
    def to_dict(self): return asdict(self)
@dataclass
class FunctionUnit:
    function_id: str; contract: str; function: str; signature: str; ast_node: dict[str,Any]
    parameters: list[VariableInfo]=field(default_factory=list); returns: list[VariableInfo]=field(default_factory=list); locals: list[VariableInfo]=field(default_factory=list); state_variables: list[VariableInfo]=field(default_factory=list)
    assembly_blocks: list[Any]=field(default_factory=list); source_statements: list[SourceStatement]=field(default_factory=list)
@dataclass
class FunctionSSEIR:
    function_id: str; contract: str; function: str; signature: str; source_statements: list[SourceStatement]; control: dict[str,Any]
    expr_roles: list[ExpressionRole]; effects: list[EffectNode]; semantic_overlays: list[SemanticOverlay]; projection_policies: list[ProjectionPolicy]
    security_facts: list[SecurityFact]=field(default_factory=list); analysis_facts: list[dict[str,Any]]=field(default_factory=list)
    def to_dict(self):
        return {"function_id":self.function_id,"contract":self.contract,"function":self.function,"signature":self.signature,
        "source_statements":[x.to_dict() for x in self.source_statements],"control":self.control,
        "expr_roles":[x.to_dict() for x in self.expr_roles],"effects":[x.to_dict() for x in self.effects],
        "semantic_overlays":[x.to_dict() for x in self.semantic_overlays],"projection_policies":[x.to_dict() for x in self.projection_policies],
        "security_facts":[x.to_dict() for x in self.security_facts],"analysis_facts":self.analysis_facts}
