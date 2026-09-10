#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from typing import Any

from s_seir_semantic_fact_adapter import SSeirFactAdapter, rough_reads
from s_seir_yul_normalize import normalize_expr


Json = dict[str, Any]


_LOW_LEVEL_YUL_CALL = re.compile(
    r"\b(?:mload|mstore|sload|sstore|calldataload|calldatacopy|codecopy|"
    r"extcodecopy|staticcall|delegatecall|callcode|returndatacopy)\s*\(",
    re.IGNORECASE,
)
_LOW_LEVEL_TRANSPORT = re.compile(r"(?:^|[_:])(?:effect|memoryssa|memory_ssa|path_state|slot_effect)", re.IGNORECASE)
_LOW_LEVEL_IDENTIFIERS = frozenset({
    "mload", "mstore", "sload", "sstore", "calldataload", "calldatacopy",
    "codecopy", "extcodecopy", "staticcall", "delegatecall", "callcode",
    "returndatacopy",
})
_DROP = object()


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
        # The bridge is intentionally not an Effect consumer.  Effects remain
        # internal S-SEIR evidence; this lifter starts with completed overlays
        # and exports only semantic-level provenance derived while projecting
        # those overlays.
        overlays = {
            str(item.get("overlay_id")): item
            for item in fn_dict.get("semantic_overlays") or []
            if isinstance(item, dict) and item.get("overlay_id")
        }
        statement_order = {
            str(statement.get("stmt_id")): index
            for index, statement in enumerate(fn_dict.get("source_statements") or [])
            if isinstance(statement, dict) and statement.get("stmt_id")
        }
        function_id = str(getattr(fn, "function_id", "") or fn_dict.get("function_id") or "")
        out: list[Json] = []
        # semantic_only forbids the legacy adapter's effect-assisted path
        # reconstruction.  SFIR starts with completed overlay semantics.
        for projected in self.adapter.function_facts(fn_dict, semantic_only=True):
            item = projected.to_dict()
            if not self._is_yul_fact(item):
                continue
            item["source_lang"] = "yul"
            item["origin"] = "yul_sseir_semantic_overlay"
            item["function_id"] = function_id
            overlay = overlays.get(str((item.get("evidence") or {}).get("overlay") or ""))
            item["semantic_provenance"] = self._semantic_provenance(
                item, overlay, fn_dict.get("control") or {}
            )
            # Low-level effects and their path-state transport must not cross
            # the Semantic Fact IR boundary.
            evidence = dict(item.get("evidence") or {})
            evidence.pop("effects", None)
            item["evidence"] = evidence
            item["operation_id"] = self._operation_id(item)
            item.pop("depends_on", None)
            item.setdefault("control_predecessors", [])
            item["order"] = {
                "kind": "cfg_partial_order",
                "operation_order": self._local_order(item, statement_order),
            }
            out.append(item)
        # An overlay may expose both a recovered Solidity-level operation and
        # the generic expression/evaluation trace used to derive it.  SFIR is
        # a deobfuscation starting point, so only the recovered operation is
        # canonical downstream; the derivation remains upstream provenance.
        out = self._compose_value_expressions(out, overlays)
        out = self._select_canonical_high_semantics(out, overlays)
        out = self._drop_completed_call_buffer_temporaries(
            out, overlays, fn_dict.get("control") or {}
        )
        out = self._drop_covered_derivation_steps(out, overlays)
        out = self._dedupe_location_facts(out)
        out = self._merge_struct_fragments(out, overlays)
        out = self._drop_cursor_writes_covered_by_struct_fields(out, overlays)
        out = self._canonicalize_path_conditioned_events(out)
        out = self._canonicalize_path_conditioned_calls(out)
        out = self._canonicalize_path_conditioned_values(out)
        # A generic normalization is not automatically a recovered
        # Solidity-like expression.  Its residual Yul primitives are useful
        # upstream evidence, but must never become final SFIR syntax.  This
        # final boundary check runs after canonical replacement, so it cannot
        # suppress a high-level fact that already proved the same operation.
        out = self._sanitize_lower_yul_boundary(out)
        return out

    @classmethod
    def _sanitize_lower_yul_boundary(cls, facts: list[Json]) -> list[Json]:
        """Remove residual opcode/effect transport from final public facts.

        A high-level fact keeps its safe projection after irrelevant upstream
        evidence is removed.  If its executable value itself still contains a
        low-level primitive, S-SEIR has not recovered that operation; model
        the definition as an explicit opaque value rather than pretending the
        opcode is Solidity-like.
        """
        out: list[Json] = []
        for fact in facts:
            rvalue = fact.get("rvalue")
            if cls._contains_lower_yul(rvalue):
                out.append(cls._opaque_unmodeled_fact(fact))
                continue
            item = dict(fact)
            for key in ("semantic", "evidence"):
                cleaned = cls._redact_lower_yul(item.get(key) or {})
                item[key] = {} if cleaned is _DROP else cleaned
            item["reads"] = cls._safe_reads(item.get("reads") or [])
            item["writes"] = cls._safe_values(item.get("writes") or [])
            # A malformed raw value can be nested in a list-valued lvalue or
            # condition even when the main RHS was safe.  Such a node cannot
            # be rendered faithfully and must follow the same explicit path.
            if any(cls._contains_lower_yul(item.get(key)) for key in ("lvalue", "condition", "reads", "writes", "semantic", "evidence")):
                out.append(cls._opaque_unmodeled_fact(fact))
            else:
                out.append(item)
        return out

    @classmethod
    def _opaque_unmodeled_fact(cls, fact: Json) -> Json:
        lvalue = fact.get("lvalue")
        safe_lvalue = lvalue if not cls._contains_lower_yul(lvalue) else None
        reads = cls._safe_reads([*fact.get("reads", []), *rough_reads(str(fact.get("rvalue") or ""))])
        writes = cls._safe_values(fact.get("writes") or [])
        if safe_lvalue and safe_lvalue not in writes:
            writes.append(safe_lvalue)
        evidence = cls._redact_lower_yul(fact.get("evidence") or {})
        item = dict(fact)
        item.update({
            "kind": "UnmodeledSSeirOverlay",
            "lvalue": safe_lvalue,
            "rvalue": "opaqueYulValue()" if safe_lvalue else None,
            "reads": reads,
            "writes": writes,
            "semantic": {
                "operation": "unmodeled_sseir_overlay",
                "status": "unmodeled",
                "overlay_kind": str((fact.get("evidence") or {}).get("overlay_kind") or "unknown"),
                "unmodeled_reason": "residual_low_level_yul_expression",
                "opaque_value": "opaqueYulValue()" if safe_lvalue else None,
            },
            "evidence": {} if evidence is _DROP else evidence,
        })
        return item

    @classmethod
    def _redact_lower_yul(cls, value: Any) -> Any:
        if isinstance(value, dict):
            out: Json = {}
            for key, item in value.items():
                if _LOW_LEVEL_TRANSPORT.search(str(key)):
                    continue
                cleaned = cls._redact_lower_yul(item)
                if cleaned is not _DROP:
                    out[str(key)] = cleaned
            return out
        if isinstance(value, list):
            return [cleaned for item in value if (cleaned := cls._redact_lower_yul(item)) is not _DROP]
        return _DROP if cls._contains_lower_yul(value) else value

    @staticmethod
    def _contains_lower_yul(value: Any) -> bool:
        if isinstance(value, dict):
            return any(YulSemanticLifter._contains_lower_yul(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return any(YulSemanticLifter._contains_lower_yul(item) for item in value)
        return isinstance(value, str) and bool(_LOW_LEVEL_YUL_CALL.search(value))

    @staticmethod
    def _safe_values(values: list[Any]) -> list[Any]:
        return [value for value in values if not YulSemanticLifter._contains_lower_yul(value)]

    @classmethod
    def _safe_reads(cls, values: list[Any]) -> list[Any]:
        out: list[Any] = []
        for value in cls._safe_values(values):
            if isinstance(value, str) and value.strip().lower() in _LOW_LEVEL_IDENTIFIERS:
                continue
            if value not in out:
                out.append(value)
        return out

    @classmethod
    def _compose_value_expressions(cls, facts: list[Json], overlays: dict[str, Json]) -> list[Json]:
        """Replace proven atomic subvalues in a parent expression structurally.

        Only a completed StateRead at the same semantic statement/CFG anchor
        may replace an evaluation temporary.  The reconstructed expression is
        then the one final C-like node; the evaluation steps stay upstream
        evidence.  When this proof is absent the existing parent expression
        remains unchanged.
        """
        high_values: dict[tuple[str | None, tuple[str, ...], str], str] = {}
        for fact in facts:
            if fact.get("kind") != "StateRead" or not fact.get("lvalue") or not fact.get("rvalue"):
                continue
            anchor = (fact.get("semantic_provenance") or {}).get("anchor_cfg_node")
            refs = tuple(str(value) for value in fact.get("stmt_refs") or [])
            high_values[(anchor, refs, str(fact["lvalue"]))] = str(fact["rvalue"])

        for fact in facts:
            overlay = overlays.get(str((fact.get("evidence") or {}).get("overlay") or "")) or {}
            if overlay.get("kind") != "ExpressionNormalization":
                continue
            attrs = overlay.get("attrs") or {}
            atomized = attrs.get("atomized_value") or {}
            steps = atomized.get("steps") if isinstance(atomized, dict) else None
            if not steps:
                continue
            anchor = (fact.get("semantic_provenance") or {}).get("anchor_cfg_node")
            refs = tuple(str(value) for value in fact.get("stmt_refs") or [])
            expressions: dict[str, str] = {}
            substitutions: dict[str, str] = {}
            used_high_value = False
            for step in steps:
                if not isinstance(step, dict) or not step.get("temp"):
                    continue
                temp = str(step["temp"])
                resolved = high_values.get((anchor, refs, temp))
                if resolved:
                    placeholder = f"__sfir_high_value_{len(substitutions) + 1}"
                    expressions[temp] = placeholder
                    substitutions[placeholder] = resolved
                    used_high_value = True
                    continue
                call = str(step.get("call") or "")
                args = [expressions.get(str(arg), str(arg)) for arg in step.get("evaluated_args") or []]
                if not call:
                    continue
                expressions[temp] = normalize_expr(f"{call}({', '.join(args)})", context="value")
            final = str(atomized.get("final") or "")
            rendered = expressions.get(final)
            if not used_high_value or not rendered:
                continue
            for placeholder, resolved in substitutions.items():
                rendered = rendered.replace(placeholder, resolved)
            fact["rvalue"] = rendered
            # The public fact must not retain the implementation-level sload
            # operands after its displayed expression has been replaced by a
            # recovered state access.  This is intentionally only a light
            # free-variable list, not an expression-level dependency graph.
            fact["reads"] = rough_reads(rendered)
            semantic = dict(fact.get("semantic") or {})
            # Preserve one public expression spelling after composition.  The
            # original normalized field may still contain the low-level
            # ``sload`` that was just replaced, so it must not remain as a
            # parallel SFIR representation.
            semantic.pop("expression", None)
            semantic["expression_normalized"] = rendered
            semantic["semantic_resolution"] = "composed_from_sseir_high_value_dependencies"
            fact["semantic"] = semantic
        return facts

    @classmethod
    def _drop_covered_derivation_steps(cls, facts: list[Json], overlays: dict[str, Json]) -> list[Json]:
        """Disconnect evaluation traces after a canonical semantic replacement.

        Parent ExpressionNormalization overlays explicitly carry their
        atomized derivation.  RequireOverlay likewise records the branch
        statement(s) it replaces.  These structural links, plus the semantic
        CFG anchor, are sufficient to discard only covered EvaluationStep and
        branch-normalization facts; no expression-text matching is used.
        """
        parent_keys: set[tuple[str | None, tuple[str, ...]]] = set()
        require_guard_refs: set[str] = set()
        for fact in facts:
            overlay = overlays.get(str((fact.get("evidence") or {}).get("overlay") or "")) or {}
            attrs = overlay.get("attrs") or {}
            anchor = (fact.get("semantic_provenance") or {}).get("anchor_cfg_node")
            refs = tuple(str(value) for value in fact.get("stmt_refs") or [])
            if overlay.get("kind") == "ExpressionNormalization" and (
                isinstance(attrs.get("atomized_value"), dict) or attrs.get("condition_evaluation")
            ):
                parent_keys.add((anchor, refs))
            if overlay.get("kind") == "RequireOverlay":
                require_guard_refs.update(str(value) for value in attrs.get("guard_stmt_refs") or [] if value)

        out: list[Json] = []
        for fact in facts:
            overlay = overlays.get(str((fact.get("evidence") or {}).get("overlay") or "")) or {}
            attrs = overlay.get("attrs") or {}
            anchor = (fact.get("semantic_provenance") or {}).get("anchor_cfg_node")
            refs = tuple(str(value) for value in fact.get("stmt_refs") or [])
            if overlay.get("kind") == "EvaluationStep" and (anchor, refs) in parent_keys:
                continue
            if (
                overlay.get("kind") == "ExpressionNormalization"
                and attrs.get("context") == "condition"
                and refs
                and set(refs).issubset(require_guard_refs)
            ):
                continue
            out.append(fact)
        return out

    @classmethod
    def _dedupe_location_facts(cls, facts: list[Json]) -> list[Json]:
        out: list[Json] = []
        by_identity: dict[str, Json] = {}
        for fact in facts:
            if fact.get("kind") != "StorageLocationResolve":
                out.append(fact)
                continue
            provenance = fact.get("semantic_provenance") or {}
            key = json.dumps({
                "function_id": fact.get("function_id"),
                "kind": fact.get("kind"),
                "condition": fact.get("condition"),
                "stmt_refs": fact.get("stmt_refs") or [],
                "anchor_cfg_node": provenance.get("anchor_cfg_node"),
                "lvalue": fact.get("lvalue"),
                "rvalue": fact.get("rvalue"),
                "location": (fact.get("semantic") or {}).get("location"),
            }, sort_keys=True, ensure_ascii=False)
            existing = by_identity.get(key)
            if existing is not None:
                cls._merge_semantic_sources(existing, fact)
                continue
            by_identity[key] = fact
            out.append(fact)
        return out

    @staticmethod
    def _semantic_source(fact: Json) -> Json:
        provenance = fact.get("semantic_provenance") or {}
        source = provenance.get("semantic_source") or {}
        return dict(source) if isinstance(source, dict) else {}

    @classmethod
    def _merge_semantic_sources(cls, canonical: Json, duplicate: Json) -> None:
        """Retain audit provenance without emitting an alias as another fact.

        This is intentionally based only on the completed semantic overlay
        identity.  Effect ids and path-state transport are not SFIR inputs.
        """
        provenance = canonical.setdefault("semantic_provenance", {})
        sources = list(provenance.get("merged_semantic_sources") or [cls._semantic_source(canonical)])
        duplicate_source = cls._semantic_source(duplicate)
        if duplicate_source and duplicate_source not in sources:
            sources.append(duplicate_source)
        provenance["merged_semantic_sources"] = sources

    @classmethod
    def _select_canonical_high_semantics(
        cls,
        facts: list[Json],
        overlays: dict[str, Json],
    ) -> list[Json]:
        """Remove generic derivations exactly covered by a high-level overlay.

        Recovered address-code, storage-location, calldata, and call-output
        operations may each have a generic ``ExpressionNormalization`` trace.
        Every replacement below requires its own structural identity proof
        (effect, source statement, and/or semantic CFG anchor), rather than
        name-based deduplication of independent assignments.
        """
        covered_generic_ids: set[str] = set()
        covered_evaluation_ids: set[str] = set()
        high_facts: list[tuple[Json, Json]] = []
        for fact in facts:
            overlay = overlays.get(str((fact.get("evidence") or {}).get("overlay") or "")) or {}
            if overlay.get("kind") not in {"AddressHasCode", "AddressCodeSize", "BytesContentHash"}:
                continue
            attrs = overlay.get("attrs") or {}
            if overlay.get("kind") != "BytesContentHash" and (not attrs.get("solidity_equivalent") or not attrs.get("source_expression")):
                continue
            high_facts.append((fact, attrs))

        for high, attrs in high_facts:
            high_target = str(attrs.get("target") or "")
            source_expression = str(attrs.get("source_expression") or attrs.get("expression") or "")
            if not high_target or not source_expression:
                continue
            high_anchor = (high.get("semantic_provenance") or {}).get("anchor_cfg_node")
            high_refs = tuple(str(value) for value in high.get("stmt_refs") or [])
            matched_generic = False
            for candidate in facts:
                overlay = overlays.get(str((candidate.get("evidence") or {}).get("overlay") or "")) or {}
                if overlay.get("kind") != "ExpressionNormalization":
                    continue
                candidate_attrs = overlay.get("attrs") or {}
                if str(candidate_attrs.get("target") or "") != high_target:
                    continue
                candidate_expression = str(
                    candidate_attrs.get("expression_normalized") or candidate_attrs.get("expression") or ""
                )
                if candidate_expression != source_expression:
                    continue
                candidate_anchor = (candidate.get("semantic_provenance") or {}).get("anchor_cfg_node")
                candidate_refs = tuple(str(value) for value in candidate.get("stmt_refs") or [])
                if candidate_anchor != high_anchor or candidate_refs != high_refs:
                    continue
                covered_generic_ids.add(str(candidate.get("operation_id") or ""))
                matched_generic = True
            if not matched_generic:
                continue
            # EvaluationStep nodes from this exact source statement are the
            # derivation of the covered generic RHS, not independent program
            # operations.  They do not define values in the final FactSSA.
            for candidate in facts:
                overlay = overlays.get(str((candidate.get("evidence") or {}).get("overlay") or "")) or {}
                if overlay.get("kind") != "EvaluationStep":
                    continue
                candidate_anchor = (candidate.get("semantic_provenance") or {}).get("anchor_cfg_node")
                candidate_refs = tuple(str(value) for value in candidate.get("stmt_refs") or [])
                if candidate_anchor == high_anchor and candidate_refs == high_refs:
                    covered_evaluation_ids.add(str(candidate.get("operation_id") or ""))

        # A completed memory-array pattern and a local-Yul-function call each
        # replace exactly one generic ValueDef.  The proof is the originating
        # ValueDef effect plus the same semantic CFG anchor; pointer arithmetic
        # and the raw call spelling remain S-SEIR derivation evidence only.
        for high in facts:
            high_overlay = overlays.get(str((high.get("evidence") or {}).get("overlay") or "")) or {}
            if high_overlay.get("kind") not in {"MemoryArrayLengthRead", "MemoryArrayElementRead", "YulLocalFunctionCall", "CalldataSelectorRead"}:
                continue
            high_attrs = high_overlay.get("attrs") or {}
            target = str(high_attrs.get("target") or high.get("lvalue") or "")
            source_effect = str(high_attrs.get("source_value_effect") or "")
            high_effects = {str(value) for value in high_overlay.get("effects") or [] if value}
            high_anchor = (high.get("semantic_provenance") or {}).get("anchor_cfg_node")
            if not target or not high_effects or not high_anchor:
                continue
            for candidate in facts:
                candidate_overlay = overlays.get(str((candidate.get("evidence") or {}).get("overlay") or "")) or {}
                if candidate_overlay.get("kind") != "ExpressionNormalization":
                    continue
                candidate_attrs = candidate_overlay.get("attrs") or {}
                candidate_anchor = (candidate.get("semantic_provenance") or {}).get("anchor_cfg_node")
                candidate_effects = {str(value) for value in candidate_overlay.get("effects") or [] if value}
                same_statement = tuple(str(value) for value in candidate.get("stmt_refs") or []) == tuple(
                    str(value) for value in high.get("stmt_refs") or []
                )
                if (
                    str(candidate_attrs.get("target") or candidate.get("lvalue") or "") == target
                    and candidate_anchor == high_anchor
                    and (
                        (source_effect and source_effect in candidate_effects)
                        or (not source_effect and bool(candidate_effects.intersection(high_effects)))
                        or (high_overlay.get("kind") == "MemoryArrayLengthRead" and same_statement)
                    )
                ):
                    covered_generic_ids.add(str(candidate.get("operation_id") or ""))
                    for evaluation in facts:
                        evaluation_overlay = overlays.get(
                            str((evaluation.get("evidence") or {}).get("overlay") or "")
                        ) or {}
                        if evaluation_overlay.get("kind") != "EvaluationStep":
                            continue
                        evaluation_attrs = evaluation_overlay.get("attrs") or {}
                        evaluation_anchor = (evaluation.get("semantic_provenance") or {}).get("anchor_cfg_node")
                        if (
                            str(evaluation_attrs.get("parent_effect") or "") in (high_effects | ({source_effect} if source_effect else set()))
                            and evaluation_anchor == high_anchor
                        ):
                            covered_evaluation_ids.add(str(evaluation.get("operation_id") or ""))

        # A pointer alias used exclusively to derive a completed array access
        # is memory-layout transport, not a public value operation.  Drop it
        # only when every remaining semantic use is itself already covered.
        for high in facts:
            high_overlay = overlays.get(str((high.get("evidence") or {}).get("overlay") or "")) or {}
            if high_overlay.get("kind") != "MemoryArrayElementRead":
                continue
            high_attrs = high_overlay.get("attrs") or {}
            pointer_effects = {str(value) for value in high_attrs.get("pointer_value_effects") or [] if value}
            high_anchor = (high.get("semantic_provenance") or {}).get("anchor_cfg_node")
            if not pointer_effects or not high_anchor:
                continue
            for candidate in facts:
                candidate_overlay = overlays.get(str((candidate.get("evidence") or {}).get("overlay") or "")) or {}
                if candidate_overlay.get("kind") != "ExpressionNormalization":
                    continue
                candidate_effects = {str(value) for value in candidate_overlay.get("effects") or [] if value}
                candidate_anchor = (candidate.get("semantic_provenance") or {}).get("anchor_cfg_node")
                pointer = str(candidate.get("lvalue") or "")
                pointer_nodes = {str(value) for value in high_attrs.get("pointer_cfg_nodes") or [] if value}
                if not pointer or (candidate_anchor != high_anchor and candidate_anchor not in pointer_nodes):
                    continue
                if not candidate_effects.intersection(pointer_effects):
                    continue
                uncovered_use = any(
                    pointer in {str(value) for value in fact.get("reads") or [] if value}
                    and str(fact.get("operation_id") or "") not in covered_generic_ids
                    and str(fact.get("operation_id") or "") not in covered_evaluation_ids
                    and fact is not candidate
                    for fact in facts
                )
                if uncovered_use:
                    continue
                covered_generic_ids.add(str(candidate.get("operation_id") or ""))
                for evaluation in facts:
                    evaluation_overlay = overlays.get(str((evaluation.get("evidence") or {}).get("overlay") or "")) or {}
                    if (
                        evaluation_overlay.get("kind") == "EvaluationStep"
                        and str((evaluation_overlay.get("attrs") or {}).get("parent_effect") or "") in pointer_effects
                    ):
                        covered_evaluation_ids.add(str(evaluation.get("operation_id") or ""))

        # Mapping-slot and state-read overlays have already abstracted the
        # implementation-level ``keccak256``/``sload`` expression.  Their
        # generic normalization has the same target and CFG anchor, while the
        # high overlay may additionally cite the statement that built the
        # storage reference; therefore candidate refs may be a subset here.
        for high in facts:
            if high.get("kind") not in {"StorageLocationResolve", "StateRead"}:
                continue
            target = str(high.get("lvalue") or "")
            if not target:
                continue
            high_anchor = (high.get("semantic_provenance") or {}).get("anchor_cfg_node")
            high_refs = {str(value) for value in high.get("stmt_refs") or []}
            for candidate in facts:
                overlay = overlays.get(str((candidate.get("evidence") or {}).get("overlay") or "")) or {}
                if overlay.get("kind") != "ExpressionNormalization":
                    continue
                candidate_attrs = overlay.get("attrs") or {}
                candidate_target = str(candidate_attrs.get("target") or candidate.get("lvalue") or "")
                candidate_refs = {str(value) for value in candidate.get("stmt_refs") or []}
                candidate_anchor = (candidate.get("semantic_provenance") or {}).get("anchor_cfg_node")
                if (
                    candidate_target == target
                    and candidate_anchor == high_anchor
                    and candidate_refs
                    and candidate_refs.issubset(high_refs)
                ):
                    covered_generic_ids.add(str(candidate.get("operation_id") or ""))

        # A resolved ``MappingSlot`` is the high-level replacement for the
        # ValueDef that names its physical hash temporary.  The SFIR adapter
        # deliberately removes that temporary from StorageLocationResolve's
        # public lvalue, so recover the identity from the completed overlay
        # itself.  Equality of target, source statement, and semantic CFG
        # anchor identifies the one ValueDef that the MappingSlot was built
        # to replace; it is not a free name-based deduplication.
        for high in facts:
            high_overlay = overlays.get(str((high.get("evidence") or {}).get("overlay") or "")) or {}
            if high.get("kind") != "StorageLocationResolve" or high_overlay.get("kind") != "MappingSlot":
                continue
            high_attrs = high_overlay.get("attrs") or {}
            target = str(high_attrs.get("target") or "")
            high_refs = tuple(str(value) for value in high.get("stmt_refs") or [])
            high_anchor = (high.get("semantic_provenance") or {}).get("anchor_cfg_node")
            if not target or not high_refs:
                continue
            for candidate in facts:
                candidate_overlay = overlays.get(str((candidate.get("evidence") or {}).get("overlay") or "")) or {}
                if candidate_overlay.get("kind") != "ExpressionNormalization":
                    continue
                candidate_attrs = candidate_overlay.get("attrs") or {}
                candidate_refs = tuple(str(value) for value in candidate.get("stmt_refs") or [])
                candidate_anchor = (candidate.get("semantic_provenance") or {}).get("anchor_cfg_node")
                if (
                    str(candidate_attrs.get("target") or candidate.get("lvalue") or "") == target
                    and candidate_refs == high_refs
                    and candidate_anchor == high_anchor
                ):
                    covered_generic_ids.add(str(candidate.get("operation_id") or ""))
                    candidate_effects = {
                        str(value) for value in candidate_overlay.get("effects") or [] if value
                    }
                    for evaluation in facts:
                        evaluation_overlay = overlays.get(
                            str((evaluation.get("evidence") or {}).get("overlay") or "")
                        ) or {}
                        if evaluation_overlay.get("kind") != "EvaluationStep":
                            continue
                        evaluation_attrs = evaluation_overlay.get("attrs") or {}
                        evaluation_anchor = (
                            evaluation.get("semantic_provenance") or {}
                        ).get("anchor_cfg_node")
                        if (
                            str(evaluation_attrs.get("parent_effect") or "") in candidate_effects
                            and evaluation_anchor == high_anchor
                        ):
                            covered_evaluation_ids.add(
                                str(evaluation.get("operation_id") or "")
                            )

        # ``CallOutputRead`` is already a complete S-SEIR semantic overlay:
        # its source is a MemorySSA-proven call output, not a textual mload
        # pattern.  Suppress only the generic value definition at the same
        # source statement and target; this is the exact AST assignment that
        # the high overlay replaces.
        for high in facts:
            high_overlay = overlays.get(str((high.get("evidence") or {}).get("overlay") or "")) or {}
            if high_overlay.get("kind") != "CallOutputRead":
                continue
            target = str(high_overlay.get("attrs", {}).get("target") or high.get("lvalue") or "")
            high_refs = tuple(str(value) for value in high.get("stmt_refs") or [])
            high_anchor = (high.get("semantic_provenance") or {}).get("anchor_cfg_node")
            if not target or not high_refs:
                continue
            for candidate in facts:
                candidate_overlay = overlays.get(str((candidate.get("evidence") or {}).get("overlay") or "")) or {}
                if candidate_overlay.get("kind") != "ExpressionNormalization":
                    continue
                if str(candidate_overlay.get("attrs", {}).get("target") or "") != target:
                    continue
                candidate_refs = tuple(str(value) for value in candidate.get("stmt_refs") or [])
                candidate_anchor = (candidate.get("semantic_provenance") or {}).get("anchor_cfg_node")
                if candidate_refs == high_refs and candidate_anchor == high_anchor:
                    covered_generic_ids.add(str(candidate.get("operation_id") or ""))

        # A calldata-word overlay and its generic normalization cite the same
        # completed ValueDef effect.  Its evaluation steps are structurally
        # tied to that same ValueDef through ``parent_effect``.  That effect
        # identity, together with the CFG anchor and target, is the
        # replacement proof; no expression-text match is used.
        for high in facts:
            high_overlay = overlays.get(str((high.get("evidence") or {}).get("overlay") or "")) or {}
            if high_overlay.get("kind") not in {"CalldataWordRead", "CalldataArrayElementRead"}:
                continue
            high_attrs = high_overlay.get("attrs") or {}
            target = str(high_attrs.get("target") or high.get("lvalue") or "")
            high_effects = {str(value) for value in high_overlay.get("effects") or [] if value}
            high_anchor = (high.get("semantic_provenance") or {}).get("anchor_cfg_node")
            if not target or not high_effects:
                continue
            for candidate in facts:
                candidate_overlay = overlays.get(str((candidate.get("evidence") or {}).get("overlay") or "")) or {}
                if candidate_overlay.get("kind") != "ExpressionNormalization":
                    continue
                candidate_attrs = candidate_overlay.get("attrs") or {}
                candidate_effects = {str(value) for value in candidate_overlay.get("effects") or [] if value}
                candidate_anchor = (candidate.get("semantic_provenance") or {}).get("anchor_cfg_node")
                if (
                    str(candidate_attrs.get("target") or candidate.get("lvalue") or "") == target
                    and candidate_anchor == high_anchor
                    and high_effects == candidate_effects
                ):
                    covered_generic_ids.add(str(candidate.get("operation_id") or ""))
            for candidate in facts:
                candidate_overlay = overlays.get(str((candidate.get("evidence") or {}).get("overlay") or "")) or {}
                if candidate_overlay.get("kind") != "EvaluationStep":
                    continue
                candidate_attrs = candidate_overlay.get("attrs") or {}
                candidate_anchor = (candidate.get("semantic_provenance") or {}).get("anchor_cfg_node")
                if (
                    str(candidate_attrs.get("parent_effect") or "") in high_effects
                    and candidate_anchor == high_anchor
                ):
                    covered_evaluation_ids.add(str(candidate.get("operation_id") or ""))

        # ``AddressZeroCheck`` gives a recovered name/type-aware condition to
        # the very Branch already represented by a generic Predicate.  Keep
        # one BranchCondition by matching only the completed branch evidence
        # and its semantic CFG anchor.
        for high in facts:
            high_overlay = overlays.get(str((high.get("evidence") or {}).get("overlay") or "")) or {}
            if high_overlay.get("kind") != "AddressZeroCheck":
                continue
            high_effects = {str(item) for item in high_overlay.get("effects") or [] if item}
            high_anchor = (high.get("semantic_provenance") or {}).get("anchor_cfg_node")
            if not high_effects or not high_anchor:
                continue
            for candidate in facts:
                candidate_overlay = overlays.get(str((candidate.get("evidence") or {}).get("overlay") or "")) or {}
                if candidate_overlay.get("kind") != "Predicate":
                    continue
                candidate_anchor = (candidate.get("semantic_provenance") or {}).get("anchor_cfg_node")
                candidate_effects = {str(item) for item in candidate_overlay.get("effects") or [] if item}
                if candidate_anchor == high_anchor and candidate_effects == high_effects:
                    covered_generic_ids.add(str(candidate.get("operation_id") or ""))

        # A proved struct-field overlay owns the direct memory read/write at
        # its exact CFG anchor.  The generic ValueDef is only the transport
        # that S-SEIR used to discover the field access.
        for high in facts:
            high_overlay = overlays.get(str((high.get("evidence") or {}).get("overlay") or "")) or {}
            if high_overlay.get("kind") not in {"StructFieldRead", "StructFieldWrite"}:
                continue
            target = str(high.get("lvalue") or "")
            high_anchor = (high.get("semantic_provenance") or {}).get("anchor_cfg_node")
            if not target or not high_anchor:
                continue
            for candidate in facts:
                candidate_overlay = overlays.get(str((candidate.get("evidence") or {}).get("overlay") or "")) or {}
                if candidate_overlay.get("kind") != "ExpressionNormalization":
                    continue
                candidate_target = str((candidate_overlay.get("attrs") or {}).get("target") or candidate.get("lvalue") or "")
                candidate_anchor = (candidate.get("semantic_provenance") or {}).get("anchor_cfg_node")
                if candidate_target == target and candidate_anchor == high_anchor:
                    covered_generic_ids.add(str(candidate.get("operation_id") or ""))

        # Path-sensitive call overlays carry S-SEIR's completed input-memory
        # proof.  A generic direct-call expression at the same semantic CFG
        # endpoint is therefore derivation transport, even when its status
        # target was not separately recovered by the overlay.
        for high in facts:
            high_overlay = overlays.get(str((high.get("evidence") or {}).get("overlay") or "")) or {}
            if high_overlay.get("kind") not in {
                "PathConditionedExternalCall", "PathConditionedLowLevelCall",
                "PathConditionedStaticCallOverlay", "PathConditionedDelegateCallOverlay",
                "PathConditionedPrecompileCall",
            }:
                continue
            high_anchor = (high.get("semantic_provenance") or {}).get("anchor_cfg_node")
            high_refs = tuple(str(value) for value in high.get("stmt_refs") or [])
            if not high_anchor or not high_refs:
                continue
            for candidate in facts:
                candidate_overlay = overlays.get(str((candidate.get("evidence") or {}).get("overlay") or "")) or {}
                if candidate_overlay.get("kind") != "ExpressionNormalization":
                    continue
                candidate_anchor = (candidate.get("semantic_provenance") or {}).get("anchor_cfg_node")
                candidate_refs = tuple(str(value) for value in candidate.get("stmt_refs") or [])
                if candidate_anchor == high_anchor and candidate_refs == high_refs:
                    covered_generic_ids.add(str(candidate.get("operation_id") or ""))

        # A decoded ABI call is the canonical call operation.  The original
        # call overlay remains proof upstream but must not yield a second
        # final call node at the same endpoint.
        for high in facts:
            high_overlay = overlays.get(str((high.get("evidence") or {}).get("overlay") or "")) or {}
            if high_overlay.get("kind") != "AbiEncodedLowLevelCall":
                continue
            high_effects = {str(item) for item in high_overlay.get("effects") or [] if item}
            high_anchor = (high.get("semantic_provenance") or {}).get("anchor_cfg_node")
            if not high_effects or not high_anchor:
                continue
            for candidate in facts:
                candidate_overlay = overlays.get(str((candidate.get("evidence") or {}).get("overlay") or "")) or {}
                if candidate_overlay.get("kind") not in {"LowLevelCall", "StaticCallOverlay", "DelegateCallOverlay"}:
                    continue
                candidate_anchor = (candidate.get("semantic_provenance") or {}).get("anchor_cfg_node")
                candidate_effects = {str(item) for item in candidate_overlay.get("effects") or [] if item}
                if candidate_anchor == high_anchor and candidate_effects == high_effects:
                    covered_generic_ids.add(str(candidate.get("operation_id") or ""))

        # A CALL-family overlay owns its status result when S-SEIR proved that
        # the surrounding Yul assignment binds the direct call expression.
        # Suppress precisely that ValueDef projection; do not use textual call
        # matching and do not suppress unrelated expressions in the block.
        for high in facts:
            high_overlay = overlays.get(str((high.get("evidence") or {}).get("overlay") or "")) or {}
            if high_overlay.get("kind") not in {"ExternalCall", "LowLevelCall", "StaticCallOverlay", "DelegateCallOverlay"}:
                continue
            high_attrs = high_overlay.get("attrs") or {}
            result = str(high_attrs.get("result") or high.get("lvalue") or "")
            result_effect = str(high_attrs.get("result_value_effect") or "")
            high_anchor = (high.get("semantic_provenance") or {}).get("anchor_cfg_node")
            if not result or not result_effect:
                continue
            for candidate in facts:
                candidate_overlay = overlays.get(str((candidate.get("evidence") or {}).get("overlay") or "")) or {}
                if candidate_overlay.get("kind") != "ExpressionNormalization":
                    continue
                candidate_effects = {str(value) for value in candidate_overlay.get("effects") or [] if value}
                candidate_anchor = (candidate.get("semantic_provenance") or {}).get("anchor_cfg_node")
                if (
                    str(candidate_overlay.get("attrs", {}).get("target") or candidate.get("lvalue") or "") == result
                    and candidate_anchor == high_anchor
                    and candidate_effects == {result_effect}
                ):
                    covered_generic_ids.add(str(candidate.get("operation_id") or ""))
        return [
            fact for fact in facts
            if str(fact.get("operation_id") or "") not in covered_generic_ids
            and str(fact.get("operation_id") or "") not in covered_evaluation_ids
        ]

    @classmethod
    def _drop_completed_call_buffer_temporaries(
        cls,
        facts: list[Json],
        overlays: dict[str, Json],
        control: Json,
    ) -> list[Json]:
        """Suppress an internal buffer definition only after a completed call owns it.

        A raw Yul pointer assignment is not a public deobfuscation fact when
        its only observable role is a fully recovered call payload buffer.
        The proof is deliberately structural: S-SEIR's call overlay must own
        the same ``input_ptr`` and have a resolved ``decoded_input``; the
        pointer definition must dominate every such call; and no remaining
        fact may consume that pointer except an output read explicitly linked
        back to one of those calls.  This keeps the raw memory evidence inside
        S-SEIR while preventing it from becoming a duplicate SFIR operation.

        No expression spelling (including ``mload(0x40)``) is used here.
        Unresolved calls, aliased/redefined pointers, and pointers with any
        other semantic use remain visible in SFIR.
        """
        call_kinds = {
            "ExternalCall",
            "LowLevelCall",
            "StaticCallOverlay",
            "DelegateCallOverlay",
            "PathConditionedExternalCall",
            "PathConditionedLowLevelCall",
            "PathConditionedStaticCallOverlay",
            "PathConditionedDelegateCallOverlay",
            "PathConditionedPrecompileCall",
            "AbiEncodedLowLevelCall",
        }
        fact_overlay = {
            str(fact.get("operation_id") or ""): overlays.get(
                str((fact.get("evidence") or {}).get("overlay") or "")
            ) or {}
            for fact in facts
        }
        dominators = cls._control_dominators(control)

        completed_by_pointer: dict[str, list[tuple[Json, Json, str]]] = {}
        for fact in facts:
            overlay = fact_overlay[str(fact.get("operation_id") or "")]
            if overlay.get("kind") not in call_kinds:
                continue
            attrs = overlay.get("attrs") or {}
            pointer = str(attrs.get("input_ptr") or "").strip()
            decoded_input = attrs.get("decoded_input") or any(
                isinstance(candidate, dict) and candidate.get("normalized")
                for candidate in attrs.get("candidates") or []
            )
            overlay_id = str(overlay.get("overlay_id") or "")
            anchor = str((fact.get("semantic_provenance") or {}).get("anchor_cfg_node") or "")
            if not pointer or not decoded_input or not overlay_id or not anchor:
                continue
            completed_by_pointer.setdefault(pointer, []).append((fact, overlay, anchor))

        generic_by_target: dict[str, list[Json]] = {}
        for fact in facts:
            overlay = fact_overlay[str(fact.get("operation_id") or "")]
            if overlay.get("kind") != "ExpressionNormalization":
                continue
            target = str((overlay.get("attrs") or {}).get("target") or fact.get("lvalue") or "").strip()
            if target:
                generic_by_target.setdefault(target, []).append(fact)

        covered_ids: set[str] = set()
        for pointer, calls in completed_by_pointer.items():
            definitions = generic_by_target.get(pointer) or []
            # A single reaching semantic definition is required.  Without
            # value-version evidence at this boundary, multiple definitions
            # remain explicit rather than being guessed equivalent.
            if len(definitions) != 1:
                continue
            definition = definitions[0]
            definition_id = str(definition.get("operation_id") or "")
            definition_anchor = str((definition.get("semantic_provenance") or {}).get("anchor_cfg_node") or "")
            if not definition_id or not definition_anchor:
                continue
            # A completed path-call overlay already carries S-SEIR's proven
            # input-memory relation.  That proof permits a same-block buffer
            # definition; otherwise retain the existing CFG dominance rule.
            if not all(
                definition_anchor == call_anchor or cls._dominates(dominators, definition_anchor, call_anchor)
                for _, _, call_anchor in calls
            ):
                continue

            completed_call_ids = {
                str(call_overlay.get("overlay_id") or "")
                for _, call_overlay, _ in calls
            }
            if cls._has_uncovered_pointer_use(
                pointer, definition_id, facts, fact_overlay, completed_call_ids,
                {(tuple(str(value) for value in fact.get("stmt_refs") or []), anchor) for fact, _, anchor in calls},
            ):
                continue
            covered_ids.add(definition_id)

        return [
            fact for fact in facts
            if str(fact.get("operation_id") or "") not in covered_ids
        ]

    @staticmethod
    def _has_uncovered_pointer_use(
        pointer: str,
        definition_id: str,
        facts: list[Json],
        fact_overlay: dict[str, Json],
        completed_call_ids: set[str],
        completed_call_sources: set[tuple[tuple[str, ...], str]],
    ) -> bool:
        """Return whether a pointer escapes the recovered call abstraction."""
        call_kinds = {
            "ExternalCall",
            "LowLevelCall",
            "StaticCallOverlay",
            "DelegateCallOverlay",
        }
        for fact in facts:
            operation_id = str(fact.get("operation_id") or "")
            if operation_id == definition_id:
                continue
            overlay = fact_overlay.get(operation_id) or {}
            attrs = overlay.get("attrs") or {}
            overlay_id = str(overlay.get("overlay_id") or "")
            if (
                overlay.get("kind") in call_kinds
                and overlay_id in completed_call_ids
                and str(attrs.get("input_ptr") or "").strip() == pointer
            ):
                continue
            if (
                overlay.get("kind") == "CallOutputRead"
                and str(attrs.get("source_call_overlay") or "") in completed_call_ids
            ):
                continue
            # The generic direct-call ValueDef is already replaced by the
            # completed path-call overlay at this exact source endpoint.
            # Its apparent pointer read is derivation transport, not an
            # independent semantic escape.
            anchor = str((fact.get("semantic_provenance") or {}).get("anchor_cfg_node") or "")
            refs = tuple(str(value) for value in fact.get("stmt_refs") or [])
            if overlay.get("kind") == "ExpressionNormalization" and (refs, anchor) in completed_call_sources:
                continue
            if pointer in {str(value) for value in fact.get("reads") or [] if value}:
                return True
        return False

    @staticmethod
    def _control_dominators(control: Json) -> dict[str, set[str]]:
        """Compute CFG dominators for semantic anchors without source ordering."""
        nodes = {
            str(block.get("block_id"))
            for block in control.get("blocks") or []
            if isinstance(block, dict) and block.get("block_id")
        }
        if not nodes:
            return {}
        predecessors: dict[str, set[str]] = {node: set() for node in nodes}
        for edge in control.get("edges") or []:
            if not isinstance(edge, dict):
                continue
            source = str(edge.get("from") or "")
            target = str(edge.get("to") or "")
            if source in nodes and target in nodes:
                predecessors[target].add(source)
        entries = {node for node in nodes if not predecessors[node]}
        if not entries:
            return {}
        dominators = {
            node: {node} if node in entries else set(nodes)
            for node in nodes
        }
        changed = True
        while changed:
            changed = False
            for node in nodes - entries:
                incoming = predecessors[node]
                if not incoming:
                    continue
                common = set.intersection(*(dominators[parent] for parent in incoming))
                updated = common | {node}
                if updated != dominators[node]:
                    dominators[node] = updated
                    changed = True
        return dominators

    @staticmethod
    def _dominates(dominators: dict[str, set[str]], definition: str, use: str) -> bool:
        # Same-block ordering is intentionally not inferred at the SFIR
        # boundary.  It needs S-SEIR value-version evidence, so retain it.
        return definition != use and definition in dominators.get(use, set())

    @classmethod
    def _canonicalize_path_conditioned_events(cls, facts: list[Json]) -> list[Json]:
        """Represent one event operation once, with alternatives as witnesses.

        Candidate paths of one completed event overlay are not distinct event
        executions.  The common guard remains the node condition; the full
        alternatives are retained as semantic-level path witnesses.
        """
        grouped: dict[str, list[Json]] = {}
        remainder: list[Json] = []
        for fact in facts:
            source = cls._semantic_source(fact)
            if fact.get("kind") != "EventEmit" or source.get("overlay_kind") != "PathConditionedEventEmit":
                remainder.append(fact)
                continue
            key = json.dumps({
                "function_id": fact.get("function_id"),
                "overlay_id": source.get("overlay_id"),
                "anchor_cfg_node": (fact.get("semantic_provenance") or {}).get("anchor_cfg_node"),
                "stmt_refs": fact.get("stmt_refs") or [],
                "event": (fact.get("semantic") or {}).get("event"),
                "args": (fact.get("semantic") or {}).get("args"),
            }, sort_keys=True, ensure_ascii=False)
            grouped.setdefault(key, []).append(fact)
        for group in grouped.values():
            canonical = group[0]
            conditions = list(dict.fromkeys(str(item.get("condition")) for item in group if item.get("condition")))
            common = cls._common_conjuncts(conditions)
            if common:
                canonical["condition"] = " && ".join(common)
            else:
                canonical.pop("condition", None)
            provenance = canonical.setdefault("semantic_provenance", {})
            provenance["path_conditions"] = conditions
            semantic = canonical.setdefault("semantic", {})
            semantic["path_conditions"] = conditions
            evidence = dict(canonical.get("evidence") or {})
            evidence.pop("candidate", None)
            canonical["evidence"] = evidence
            canonical["operation_id"] = f"yul_overlay:{cls._semantic_source(canonical).get('overlay_id')}"
            remainder.append(canonical)
        return remainder

    @classmethod
    def _merge_struct_fragments(cls, facts: list[Json], overlays: dict[str, Json]) -> list[Json]:
        """Keep aggregate struct overlays as provenance, not duplicate writes."""
        field_facts: list[tuple[Json, Json]] = []
        fragments: list[tuple[Json, Json]] = []
        for fact in facts:
            overlay = overlays.get(str((fact.get("evidence") or {}).get("overlay") or "")) or {}
            if overlay.get("kind") == "StructFieldWrite":
                field_facts.append((fact, overlay))
            elif overlay.get("kind") in {"StructInitializationFragment", "StructMutationFragment"}:
                fragments.append((fact, overlay))
        covered: set[str] = set()
        for fragment_fact, fragment_overlay in fragments:
            attrs = fragment_overlay.get("attrs") or {}
            mutations = attrs.get("mutations") or attrs.get("fields") or []
            fragment_effects = {str(item) for item in fragment_overlay.get("effects") or [] if item}
            matched = 0
            for field_fact, field_overlay in field_facts:
                field_attrs = field_overlay.get("attrs") or {}
                field_effects = {str(item) for item in field_overlay.get("effects") or [] if item}
                if not field_effects.intersection(fragment_effects):
                    continue
                if field_attrs.get("struct_object") != attrs.get("struct_object"):
                    continue
                cls._merge_semantic_sources(field_fact, fragment_fact)
                matched += 1
            # Do not discard a fragment whose constituent writes were not all
            # independently recovered; it is the only faithful high-level
            # record in that case.
            if matched and matched >= len(mutations):
                covered.add(str(fragment_fact.get("operation_id") or ""))
        return [
            fact for fact in facts
            if str(fact.get("operation_id") or "") not in covered
        ]

    @classmethod
    def _canonicalize_path_conditioned_calls(cls, facts: list[Json]) -> list[Json]:
        """One call endpoint may have several S-SEIR input-memory paths."""
        grouped: dict[str, list[Json]] = {}
        remainder: list[Json] = []
        path_kinds = {
            "PathConditionedExternalCall", "PathConditionedLowLevelCall",
            "PathConditionedStaticCallOverlay", "PathConditionedDelegateCallOverlay",
            "PathConditionedPrecompileCall",
        }
        for fact in facts:
            source = cls._semantic_source(fact)
            if source.get("overlay_kind") not in path_kinds or fact.get("kind") not in {"ExternalCall", "PrecompileCall"}:
                remainder.append(fact)
                continue
            key = json.dumps({
                "function_id": fact.get("function_id"), "overlay_id": source.get("overlay_id"),
                "kind": fact.get("kind"),
                "anchor": (fact.get("semantic_provenance") or {}).get("anchor_cfg_node"),
                "stmt_refs": fact.get("stmt_refs") or [], "lvalue": fact.get("lvalue"),
            }, sort_keys=True, ensure_ascii=False)
            grouped.setdefault(key, []).append(fact)
        for group in grouped.values():
            canonical = group[0]
            conditions = list(dict.fromkeys(str(item.get("condition")) for item in group if item.get("condition")))
            common = cls._common_conjuncts(conditions)
            if common:
                canonical["condition"] = " && ".join(common)
            else:
                canonical.pop("condition", None)
            semantic = canonical.setdefault("semantic", {})
            semantic["path_conditions"] = conditions
            semantic["path_candidates"] = [
                {
                    "condition": item.get("condition"),
                    "target": (item.get("semantic") or {}).get("target"),
                    "call_kind": (item.get("semantic") or {}).get("call_kind"),
                    "selector": (item.get("semantic") or {}).get("selector"),
                    "selector_signature": (item.get("semantic") or {}).get("selector_signature"),
                    "arguments": (item.get("semantic") or {}).get("arguments"),
                    "precompile": (item.get("semantic") or {}).get("precompile"),
                    "call_status_result": (item.get("semantic") or {}).get("call_status_result"),
                }
                for item in group
            ]
            evidence = dict(canonical.get("evidence") or {})
            evidence.pop("candidate", None)
            canonical["evidence"] = evidence
            canonical["operation_id"] = f"yul_overlay:{cls._semantic_source(canonical).get('overlay_id')}"
            remainder.append(canonical)
        return remainder

    @classmethod
    def _drop_cursor_writes_covered_by_struct_fields(cls, facts: list[Json], overlays: dict[str, Json]) -> list[Json]:
        """Remove a cursor-write only when field writes prove the same update.

        ``CursorBasedMemoryWrite`` is a derived summary of the same completed
        struct mutations.  It is not enough that the objects share a name:
        every recovered field/value pair must be present at the same semantic
        CFG anchor.  Otherwise the cursor summary remains the only public
        high-level memory-object operation.
        """
        fields: dict[tuple[str, str, str, str, str], Json] = {}
        for fact in facts:
            if fact.get("kind") != "ValueAssign":
                continue
            semantic = fact.get("semantic") or {}
            if semantic.get("operation") != "struct_field_write":
                continue
            overlay = overlays.get(str((fact.get("evidence") or {}).get("overlay") or "")) or {}
            attrs = overlay.get("attrs") or {}
            write_effect = str(attrs.get("write_effect") or "")
            anchor = str((fact.get("semantic_provenance") or {}).get("anchor_cfg_node") or "")
            fields[(anchor, str(semantic.get("struct_object") or ""), str((semantic.get("field") or {}).get("name") or ""), str(fact.get("rvalue") or ""), write_effect)] = fact

        covered: set[str] = set()
        for fact in facts:
            source = cls._semantic_source(fact)
            if source.get("overlay_kind") != "CursorBasedMemoryWrite":
                continue
            semantic = fact.get("semantic") or {}
            updates = semantic.get("field_updates") or []
            anchor = str((fact.get("semantic_provenance") or {}).get("anchor_cfg_node") or "")
            object_name = str(semantic.get("struct_object") or "")
            if not anchor or not object_name or not updates:
                continue
            exact = True
            for update in updates:
                if not isinstance(update, dict):
                    exact = False; break
                field = str((update.get("field") or {}).get("name") or "")
                value = str(update.get("new_value_normalized") or update.get("new_value") or "")
                write_effect = str(update.get("write_effect") or "")
                if not field or not value or not write_effect or (anchor, object_name, field, value, write_effect) not in fields:
                    exact = False; break
            if exact:
                covered.add(str(fact.get("operation_id") or ""))
        return [fact for fact in facts if str(fact.get("operation_id") or "") not in covered]

    @classmethod
    def _canonicalize_path_conditioned_values(cls, facts: list[Json]) -> list[Json]:
        """Collapse one path-sensitive semantic endpoint to one SFIR node.

        S-SEIR's candidates are alternative reaching definitions of one
        endpoint, not separate source-level executions.  This mirrors the
        existing event rule while retaining every resolved path condition as
        semantic evidence for later deobfuscation.
        """
        grouped: dict[str, list[Json]] = {}
        remainder: list[Json] = []
        supported = {
            "PathConditionedRawReturnData": "Return",
            "PathConditionedPrecompileOutputRead": "ValueCompute",
        }
        for fact in facts:
            source = cls._semantic_source(fact)
            overlay_kind = source.get("overlay_kind")
            if supported.get(overlay_kind) != fact.get("kind"):
                remainder.append(fact)
                continue
            key = json.dumps({
                "function_id": fact.get("function_id"),
                "overlay_id": source.get("overlay_id"),
                "kind": fact.get("kind"),
                "anchor_cfg_node": (fact.get("semantic_provenance") or {}).get("anchor_cfg_node"),
                "stmt_refs": fact.get("stmt_refs") or [],
                "lvalue": fact.get("lvalue"),
            }, sort_keys=True, ensure_ascii=False)
            grouped.setdefault(key, []).append(fact)
        for group in grouped.values():
            canonical = group[0]
            conditions = list(dict.fromkeys(
                str(item.get("condition")) for item in group if item.get("condition")
            ))
            common = cls._common_conjuncts(conditions)
            if common:
                canonical["condition"] = " && ".join(common)
            else:
                canonical.pop("condition", None)
            provenance = canonical.setdefault("semantic_provenance", {})
            provenance["path_conditions"] = conditions
            semantic = canonical.setdefault("semantic", {})
            semantic["path_conditions"] = conditions
            alternatives = [
                {"condition": item.get("condition"), "value": item.get("rvalue")}
                for item in group
            ]
            semantic["path_candidates"] = alternatives
            values = list(dict.fromkeys(str(item.get("rvalue") or "") for item in group))
            if len(values) > 1:
                canonical["rvalue"] = (
                    "pathConditionedReturn()" if canonical.get("kind") == "Return"
                    else "pathConditionedValue()"
                )
            evidence = dict(canonical.get("evidence") or {})
            evidence.pop("candidate", None)
            canonical["evidence"] = evidence
            canonical["operation_id"] = f"yul_overlay:{cls._semantic_source(canonical).get('overlay_id')}"
            remainder.append(canonical)
        return remainder

    @staticmethod
    def _common_conjuncts(conditions: list[str]) -> list[str]:
        if not conditions:
            return []
        parts = [[item.strip() for item in condition.split("&&") if item.strip()] for condition in conditions]
        return [item for item in parts[0] if all(item in candidate for candidate in parts[1:])]

    @staticmethod
    def _is_yul_fact(item: Json) -> bool:
        return item.get("source_lang") == "yul"

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
        return f"yul_fact:{item.get('fact_id') or 'unknown'}"

    @staticmethod
    def _local_order(
        item: Json,
        statement_order: dict[str, int],
    ) -> int:
        statement_positions = [
            statement_order[str(ref)]
            for ref in item.get("stmt_refs") or []
            if str(ref) in statement_order
        ]
        return min(statement_positions) if statement_positions else 0

    @classmethod
    def _semantic_provenance(
        cls,
        item: Json,
        overlay: Json | None,
        control: Json,
    ) -> Json:
        """Project only semantic-level anchor/control information.

        The anchor is derived only from the overlay's source statements and
        high-level control projection.  The returned object contains neither
        low-level effect identity nor effect-derived path state.
        """
        evidence_nodes = list(dict.fromkeys(str(value) for value in item.get("cfg_nodes") or [] if value))
        anchor = cls._overlay_anchor_block(overlay, control, item.get("stmt_refs") or [])
        overlay_attrs = (overlay or {}).get("attrs") or {}
        # An empty Yul revert has been recovered as a Require-like guard.  The
        # overlay is anchored at the recovered terminal effect, while its
        # completed semantic evidence also identifies the guarding branch.
        # SFIR places the Require on that branch so its CFG has normal guard
        # semantics: true continues; false terminates in revert.
        require_failure_anchor = None
        if (overlay or {}).get("kind") == "RequireOverlay":
            require_failure_anchor = anchor
            guard_refs = {str(value) for value in overlay_attrs.get("guard_stmt_refs") or [] if value}
            guard_blocks = [
                str(block.get("block_id"))
                for block in control.get("blocks") or []
                if isinstance(block, dict)
                and block.get("block_id")
                and guard_refs.intersection(str(ref) for ref in block.get("stmts") or [])
                and str((block.get("terminator") or {}).get("kind") or "").lower() == "branch"
            ]
            if len(guard_blocks) == 1:
                anchor = guard_blocks[0]
        semantic_evidence = [str(value) for value in overlay_attrs.get("semantic_evidence_cfg_nodes") or [] if value]
        if semantic_evidence:
            evidence_nodes = list(dict.fromkeys(semantic_evidence + evidence_nodes))
        if anchor and anchor not in evidence_nodes:
            evidence_nodes.append(anchor)
        result: Json = {
            "semantic_source": {
                "overlay_id": (overlay or {}).get("overlay_id"),
                "overlay_kind": (overlay or {}).get("kind"),
            },
            "evidence_cfg_nodes": evidence_nodes,
        }
        if anchor:
            result["anchor_cfg_node"] = anchor
        if require_failure_anchor:
            result["require_failure_cfg_node"] = require_failure_anchor
        if item.get("condition"):
            result["control_conditions"] = [item["condition"]]
        return result

    @staticmethod
    def _overlay_anchor_block(
        overlay: Json | None,
        control: Json,
        stmt_refs: list[Any],
    ) -> str | None:
        if not isinstance(overlay, dict):
            return None
        attrs = overlay.get("attrs") or {}
        explicit = attrs.get("semantic_anchor_cfg_node")
        if explicit:
            return str(explicit)
        explicit_candidates = list(dict.fromkeys(str(value) for value in attrs.get("semantic_evidence_cfg_nodes") or [] if value))
        if len(explicit_candidates) == 1:
            return explicit_candidates[0]
        refs = {str(ref) for ref in stmt_refs if ref}
        matches: list[str] = []
        for block in control.get("blocks") or []:
            if not isinstance(block, dict) or not block.get("block_id"):
                continue
            block_refs = {str(ref) for ref in block.get("stmts") or [] if ref}
            if refs and not refs.intersection(block_refs):
                continue
            matches.append(str(block["block_id"]))
        return matches[0] if len(matches) == 1 else None
