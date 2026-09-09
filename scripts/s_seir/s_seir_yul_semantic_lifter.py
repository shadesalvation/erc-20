#!/usr/bin/env python3
from __future__ import annotations

import json
from typing import Any

from s_seir_semantic_fact_adapter import SSeirFactAdapter, rough_reads
from s_seir_yul_normalize import normalize_expr


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
        out = self._canonicalize_path_conditioned_events(out)
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
            if overlay.get("kind") not in {"AddressHasCode", "AddressCodeSize"}:
                continue
            attrs = overlay.get("attrs") or {}
            if not attrs.get("solidity_equivalent") or not attrs.get("source_expression"):
                continue
            high_facts.append((fact, attrs))

        for high, attrs in high_facts:
            high_target = str(attrs.get("target") or "")
            source_expression = str(attrs.get("source_expression") or "")
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
                if str(candidate_attrs.get("expression") or "") != source_expression:
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
            decoded_input = attrs.get("decoded_input")
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
            if not all(cls._dominates(dominators, definition_anchor, call_anchor) for _, _, call_anchor in calls):
                continue

            completed_call_ids = {
                str(call_overlay.get("overlay_id") or "")
                for _, call_overlay, _ in calls
            }
            if cls._has_uncovered_pointer_use(
                pointer, definition_id, facts, fact_overlay, completed_call_ids
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
