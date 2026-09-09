#!/usr/bin/env python3
"""Recover the canonical ABI selector extraction from Yul calldata code.

The EVM exposes calldata as 32-byte words, while Solidity's ``msg.sig`` is
the high four bytes of calldata.  This pass recognizes that exact, typed
def-use shape without treating arbitrary shifts or arbitrary calldata reads as
selectors.
"""
from __future__ import annotations

from typing import Any

from assembly_semantic_ir import parse_int_literal
from s_seir_id import IdAllocator
from s_seir_model import EffectNode, SemanticOverlay
from s_seir_yul_normalize import call_parts


class CalldataSelectorLifter:
    """Lift ``shr(224, calldataload(0))`` assigned to ``bytes4`` to ``msg.sig``."""

    def __init__(self) -> None:
        self.ids = IdAllocator()

    def lift(
        self,
        type_env: Any,
        effects: list[EffectNode],
        overlays: list[SemanticOverlay],
    ) -> list[SemanticOverlay]:
        out = list(overlays)
        completed_effects = {
            str(item.attrs.get("source_value_effect") or "")
            for item in out
            if item.kind == "CalldataSelectorRead"
        }
        for effect in effects:
            if effect.kind != "ValueDef" or effect.effect_id in completed_effects:
                continue
            targets = [str(item) for item in effect.attrs.get("targets") or [] if str(item)]
            if len(targets) != 1:
                continue
            target = targets[0]
            target_info = getattr(type_env, "lookup", lambda _name: None)(target)
            target_type = str(getattr(target_info, "type_string", "") or "").strip()
            if target_type != "bytes4":
                continue
            expression = str(effect.attrs.get("value") or "")
            if not self._is_selector_extraction(expression):
                continue
            out.append(SemanticOverlay(
                self.ids.new("ov_calldata_selector"), "CalldataSelectorRead",
                [effect.effect_id], list(effect.stmt_refs), {
                    "overlay_kind": "CalldataSelectorRead",
                    "target": target,
                    "target_type": target_type,
                    "source": "msg.data",
                    "access": "msg.sig",
                    "source_expression": expression,
                    "source_value_effect": effect.effect_id,
                    "semantic_model": "calldata_selector_word_shift",
                    "solidity_like": f"{target} = msg.sig;",
                    "solidity_equivalent": True,
                },
            ))
        return out

    @staticmethod
    def _is_selector_extraction(expression: str) -> bool:
        shift, shift_args = call_parts(expression)
        if shift != "shr" or len(shift_args) != 2 or parse_int_literal(shift_args[0]) != 224:
            return False
        load, load_args = call_parts(shift_args[1])
        return load == "calldataload" and len(load_args) == 1 and parse_int_literal(load_args[0]) == 0
