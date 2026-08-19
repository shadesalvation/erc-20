#!/usr/bin/env python3
from __future__ import annotations

import json
from typing import Any

from s_seir_semantic_fact_adapter import SSeirFactAdapter


Json = dict[str, Any]


class YulSemanticLifter:
    """Project completed Yul S-SEIR results to the common fact schema.

    S-SEIR remains the sole owner of Yul analysis and semantic recovery. This
    class only selects Yul-derived rows and normalizes transport fields; it
    does not rerun MemorySSA, SinkResolver, or overlay recognition.
    """

    def __init__(self) -> None:
        self.adapter = SSeirFactAdapter()

    def facts_from_function(self, fn: Any) -> list[Json]:
        fn_dict = fn.to_semantic_dict() if hasattr(fn, "to_semantic_dict") else fn
        if not isinstance(fn_dict, dict):
            return []
        effect_language = {
            str(effect.get("effect_id")): str((effect.get("attrs") or {}).get("language") or "")
            for effect in fn_dict.get("effects") or []
            if isinstance(effect, dict) and effect.get("effect_id")
        }
        statement_order = {
            str(statement.get("stmt_id")): index
            for index, statement in enumerate(fn_dict.get("source_statements") or [])
            if isinstance(statement, dict) and statement.get("stmt_id")
        }
        effect_order = {
            str(effect.get("effect_id")): index
            for index, effect in enumerate(fn_dict.get("effects") or [])
            if isinstance(effect, dict) and effect.get("effect_id")
        }
        function_id = str(getattr(fn, "function_id", "") or fn_dict.get("function_id") or "")
        out: list[Json] = []
        for projected in self.adapter.function_facts(fn_dict):
            item = projected.to_dict()
            if not self._is_yul_fact(item, effect_language):
                continue
            item["source_lang"] = "yul"
            item["origin"] = "yul_sseir"
            item["function_id"] = function_id
            item["operation_id"] = self._operation_id(item)
            item.pop("depends_on", None)
            item.setdefault("control_predecessors", [])
            item["order"] = {
                "kind": "cfg_partial_order",
                "operation_order": self._local_order(item, statement_order, effect_order),
            }
            out.append(item)
        out = self._dedupe_location_facts(out)
        return out

    @staticmethod
    def _dedupe_location_facts(facts: list[Json]) -> list[Json]:
        out: list[Json] = []
        seen: set[str] = set()
        for fact in facts:
            if fact.get("kind") != "StorageLocationResolve":
                out.append(fact)
                continue
            key = json.dumps({
                "function_id": fact.get("function_id"),
                "condition": fact.get("condition"),
                "location": (fact.get("semantic") or {}).get("location"),
                "effects": (fact.get("evidence") or {}).get("effects") or [],
            }, sort_keys=True, ensure_ascii=False)
            if key in seen:
                continue
            seen.add(key)
            out.append(fact)
        return out

    @staticmethod
    def _is_yul_fact(item: Json, effect_language: dict[str, str]) -> bool:
        if item.get("source_lang") == "yul":
            return True
        if item.get("source_lang") not in {None, "", "unknown"}:
            return False
        effect_ids = (item.get("evidence") or {}).get("effects") or []
        languages = {
            effect_language.get(str(effect_id))
            for effect_id in effect_ids
            if effect_language.get(str(effect_id))
        }
        return languages == {"yul"}

    @staticmethod
    def _operation_id(item: Json) -> str:
        evidence = item.get("evidence") or {}
        overlay = evidence.get("overlay")
        candidate = evidence.get("candidate") or {}
        candidate_suffix = ""
        if isinstance(candidate, dict):
            candidate_suffix = str(
                candidate.get("path_id")
                or candidate.get("version")
                or candidate.get("condition")
                or ""
            )
        if overlay:
            return f"yul_overlay:{overlay}:{candidate_suffix}" if candidate_suffix else f"yul_overlay:{overlay}"
        effects = evidence.get("effects") or []
        if effects:
            return f"yul_effect:{effects[0]}"
        return f"yul_fact:{item.get('fact_id') or 'unknown'}"

    @staticmethod
    def _local_order(
        item: Json,
        statement_order: dict[str, int],
        effect_order: dict[str, int],
    ) -> int:
        evidence = item.get("evidence") or {}
        effect_positions = [
            effect_order[str(effect)]
            for effect in evidence.get("effects") or []
            if str(effect) in effect_order
        ]
        if effect_positions:
            if item.get("kind") == "StorageLocationResolve":
                return min(effect_positions)
            return max(effect_positions)
        statement_positions = [
            statement_order[str(ref)]
            for ref in item.get("stmt_refs") or []
            if str(ref) in statement_order
        ]
        return min(statement_positions) if statement_positions else 0
