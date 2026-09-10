#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any


@dataclass
class SemanticFact:
    fact_id: str
    kind: str
    source_lang: str
    origin: str
    function: str
    fact_role: str | None = None
    contract: str | None = None
    signature: str | None = None
    stmt_refs: list[str] = field(default_factory=list)
    cfg_nodes: list[str] = field(default_factory=list)
    condition: str | None = None
    lvalue: Any = None
    rvalue: Any = None
    reads: list[Any] = field(default_factory=list)
    writes: list[Any] = field(default_factory=list)
    semantic: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


class FactIdAllocator:
    def __init__(self, prefix: str = "fact") -> None:
        self.prefix = prefix
        self.counter = 0

    def new(self) -> str:
        self.counter += 1
        return f"{self.prefix}_{self.counter}"


class SSeirFactAdapter:
    """Convert existing S-SEIR semantic output into unified SemanticFact rows.

    This adapter is intentionally a projection layer. It does not redo
    MemorySSA, SinkResolver, slot recovery, event recovery, or call lifting.
    """

    # The completed pipeline may only expose these semantic overlays at the
    # Yul -> SFIR boundary.  Candidates used by completion passes are
    # intentionally absent: they must be replaced by a proved overlay or
    # discarded before reaching this adapter.
    FINAL_OVERLAY_KINDS = frozenset({
        "AbiCallDataConstruction", "AbiEncodedLowLevelCall",
        "AddressCodeSize", "AddressHasCode", "AddressZeroCheck",
        "BytesContentHash", "CallOutputRead", "CalldataArrayElementRead",
        "CalldataSelectorRead", "CalldataWordRead", "CursorBasedMemoryWrite",
        "CustomErrorRevert", "DelegateCallOverlay", "DynamicArraySlot",
        "EvaluationStep", "EventEmit", "ExpressionNormalization", "ExternalCall",
        "InternalCall", "LibraryCall", "LowLevelCall", "MappingRead", "MappingSlot", "MappingWrite",
        "MemoryArrayConstruction", "MemoryArrayElementRead", "MemoryArrayLengthRead",
        "MemoryRegionAllocate", "MemoryRegionWrite", "PathConditionedCustomErrorRevert",
        "PathConditionedDelegateCallOverlay", "PathConditionedEventEmit",
        "PathConditionedExternalCall", "PathConditionedLowLevelCall",
        "PathConditionedPrecompileCall", "PathConditionedPrecompileOutputRead",
        "PathConditionedRawReturnData", "PathConditionedRevert",
        "PathConditionedStaticCallOverlay", "PathConditionedStorageRead",
        "PathConditionedStorageWrite", "PrecompileCall", "PrecompileOutputRead",
        "Predicate", "RawReturnData", "RawRevertBytes", "RequireOverlay",
        "ReturnValue", "RevertOverlay", "StateVariableRead", "StateVariableWrite",
        "StaticCallOverlay", "StoragePointerSlotBinding", "StructFieldRead",
        "StructFieldWrite", "StructInitializationFragment", "StructMemoryMutation",
        "StructMutationFragment", "YulLocalFunctionCall", "YulLocalFunctionDefinition",
    })
    # This table is the executable boundary contract for completed S-SEIR
    # overlays.  ``project`` rows have a direct SFIR fact rule below;
    # ``derivation`` rows are allowed to reach the lifter only until a
    # structurally-proved high-level replacement removes them; and
    # ``candidate`` rows must have been completed by an S-SEIR lifter before
    # this boundary.  A candidate that escapes is made visible as unmodeled,
    # never silently interpreted as a source-level operation.
    DERIVATION_OVERLAY_KINDS = frozenset({
        "EvaluationStep", "ExpressionNormalization", "StructInitializationFragment",
        "StructMutationFragment", "MemoryRegionWrite", "CursorBasedMemoryWrite",
    })
    FINAL_OVERLAY_POLICY = {
        **{kind: "project" for kind in FINAL_OVERLAY_KINDS},
        **{kind: "derivation" for kind in DERIVATION_OVERLAY_KINDS},
        "CalldataArrayElementCandidate": "candidate",
    }

    def __init__(self) -> None:
        self.ids = FactIdAllocator()

    def build_from_payload(self, payload: Any) -> dict[str, Any]:
        functions = extract_function_dicts(payload)
        facts = [fact.to_dict() for fn in functions for fact in self.function_facts(fn)]
        return {
            "schema": "s-seir-semantic-facts/v1",
            "source": payload.get("source") if isinstance(payload, dict) else None,
            "function_count": len(functions),
            "facts": facts,
        }

    def function_facts(self, fn: dict[str, Any], *, semantic_only: bool = False) -> list[SemanticFact]:
        """Project completed overlays to facts.

        ``semantic_only`` is the SFIR boundary mode: the projection starts
        solely from the completed high-level overlays.  Existing callers keep
        the historical effect-assisted evidence mode by default.
        """
        out: list[SemanticFact] = []
        stmt_lang = self.statement_languages(fn)
        effect_by_id = {} if semantic_only else {
            item.get("effect_id"): item for item in fn.get("effects") or [] if isinstance(item, dict)
        }
        for overlay in fn.get("semantic_overlays") or []:
            if not isinstance(overlay, dict):
                continue
            out.extend(self.overlay_facts(fn, overlay, stmt_lang, effect_by_id))
        return out

    def overlay_facts(
        self,
        fn: dict[str, Any],
        overlay: dict[str, Any],
        stmt_lang: dict[str, str],
        effect_by_id: dict[str, dict[str, Any]],
    ) -> list[SemanticFact]:
        overlay = self.overlay_with_effect_refs(overlay, effect_by_id)
        kind = str(overlay.get("kind") or "")
        attrs = overlay.get("attrs") or {}
        policy = self.FINAL_OVERLAY_POLICY.get(kind)
        if policy is None:
            return [self.unmodeled_overlay_fact(fn, overlay, stmt_lang, reason="unknown_final_overlay_kind")]
        if kind in {"PathConditionedStorageRead", "PathConditionedStorageWrite"}:
            facts = [
                self.storage_candidate_fact(fn, overlay, candidate, stmt_lang, effect_by_id)
                for candidate in attrs.get("candidates") or []
                if isinstance(candidate, dict)
            ]
            return facts or [self.unmodeled_overlay_fact(fn, overlay, stmt_lang, reason="no_structured_path_candidates")]
        if kind == "PathConditionedEventEmit":
            facts = [
                self.event_candidate_fact(fn, overlay, candidate, stmt_lang)
                for candidate in attrs.get("candidates") or []
                if isinstance(candidate, dict)
            ]
            return facts or [self.unmodeled_overlay_fact(fn, overlay, stmt_lang, reason="no_structured_path_candidates")]
        if kind in {"PathConditionedCustomErrorRevert", "PathConditionedRevert"}:
            facts = [
                self.revert_candidate_fact(fn, overlay, candidate, stmt_lang)
                for candidate in attrs.get("candidates") or []
                if isinstance(candidate, dict)
            ]
            return facts or [self.unmodeled_overlay_fact(fn, overlay, stmt_lang, reason="no_structured_path_candidates")]
        if kind in {
            "PathConditionedPrecompileCall", "PathConditionedExternalCall",
            "PathConditionedLowLevelCall", "PathConditionedStaticCallOverlay",
            "PathConditionedDelegateCallOverlay",
        }:
            facts = [
                self.call_candidate_fact(fn, overlay, candidate, stmt_lang)
                for candidate in attrs.get("candidates") or []
                if isinstance(candidate, dict)
            ]
            return facts or [self.unmodeled_overlay_fact(fn, overlay, stmt_lang, reason="no_structured_path_candidates")]
        if kind == "PathConditionedRawReturnData":
            facts = [
                self.raw_return_candidate_fact(fn, overlay, candidate, stmt_lang)
                for candidate in attrs.get("candidates") or []
                if isinstance(candidate, dict)
            ]
            return facts or [self.unmodeled_overlay_fact(fn, overlay, stmt_lang, reason="no_structured_path_candidates")]
        if kind == "PathConditionedPrecompileOutputRead":
            facts = [
                self.precompile_output_candidate_fact(fn, overlay, candidate, stmt_lang)
                for candidate in attrs.get("candidates") or []
                if isinstance(candidate, dict)
            ]
            return facts or [self.unmodeled_overlay_fact(fn, overlay, stmt_lang, reason="no_structured_path_candidates")]
        if policy == "candidate":
            return [self.unmodeled_overlay_fact(fn, overlay, stmt_lang, reason="completion_candidate_reached_sfir_boundary")]
        fact = self.plain_overlay_fact(fn, overlay, stmt_lang, effect_by_id)
        if fact and policy in {"project", "derivation"}:
            return [fact]
        # A silent omission would make an upgraded S-SEIR appear complete to
        # downstream deobfuscation.  Keep unknown future overlays structured
        # and anchored, but never invent their execution semantics.
        return [self.unmodeled_overlay_fact(fn, overlay, stmt_lang)]

    def plain_overlay_fact(
        self,
        fn: dict[str, Any],
        overlay: dict[str, Any],
        stmt_lang: dict[str, str],
        effect_by_id: dict[str, dict[str, Any]],
    ) -> SemanticFact | None:
        kind = str(overlay.get("kind") or "")
        attrs = overlay.get("attrs") or {}
        if kind in {"MappingSlot", "DynamicArraySlot"}:
            location = self.storage_location(attrs)
            if not self.is_resolved_storage_location(location):
                # S-SEIR retains the physical slot construction upstream as
                # audit evidence.  SFIR must not pretend that an arbitrary
                # slot expression is a Solidity storage location, nor leak
                # its keccak/slot transport into the reconstruction input.
                return self.fact(
                    fn,
                    overlay,
                    stmt_lang,
                    "UnresolvedStorageLocation",
                    rvalue="opaqueStorageLocation",
                    semantic={
                        "operation": "unresolved_storage_location",
                        "status": "unresolved",
                        "reason": "storage_location_not_semantically_recovered",
                        "candidate_kind": location.get("kind"),
                    },
                )
            access = location["access"]
            return self.fact(
                fn,
                overlay,
                stmt_lang,
                "StorageLocationResolve",
                # A location resolve is a support declaration, not an
                # assignment to the physical ``keccak256`` slot temporary.
                # Semantic IR groups it with StateRead/StateWrite through the
                # structured location object below.
                rvalue=access,
                reads=self.storage_location_reads(location),
                writes=[],
                semantic={
                    "operation": "storage_location_resolve",
                    "location": location,
                    "resolution_status": "resolved",
                },
            )
        if kind in {"StateVariableRead", "MappingRead"}:
            access = attrs.get("access") or attrs.get("slot")
            target = attrs.get("target")
            return self.fact(
                fn,
                overlay,
                stmt_lang,
                "StateRead",
                lvalue=target,
                rvalue=access,
                reads=[access],
                # A recovered storage read defines its local target.  The
                # generic ``sload`` normalization used to mask this omission,
                # but SFIR correctly keeps only the high-level StateRead.
                writes=clean_list([target]),
                semantic=self.state_semantic(attrs, "state_read"),
                extra_evidence={"storage_resolution": self.storage_resolution_evidence(attrs)},
            )
        if kind in {"StateVariableWrite", "MappingWrite"}:
            access = attrs.get("access") or attrs.get("slot")
            value = attrs.get("value") or attrs.get("value_yul")
            return self.fact(
                fn,
                overlay,
                stmt_lang,
                "StateWrite",
                lvalue=access,
                rvalue=value,
                reads=self.reads_for_value(value, attrs),
                writes=[access],
                semantic={
                    **self.state_semantic(attrs, "state_write"),
                    "value": value,
                },
                extra_evidence={"storage_resolution": self.storage_resolution_evidence(attrs)},
            )
        if kind in {"AddressHasCode", "AddressCodeSize"}:
            is_check = kind == "AddressHasCode"
            value = attrs.get("condition") if is_check else attrs.get("code_size")
            return self.fact(
                fn,
                overlay,
                stmt_lang,
                "ValueCompute",
                lvalue=attrs.get("target"),
                rvalue=value,
                reads=clean_list([attrs.get("address")]),
                writes=clean_list([attrs.get("target")]),
                semantic={
                    "operation": "address_has_code" if is_check else "address_code_size",
                    "address": attrs.get("address_normalized") or attrs.get("address"),
                    "code_size": attrs.get("code_size"),
                    "predicate": attrs.get("condition") if is_check else None,
                    "check_kind": attrs.get("check_kind"),
                },
            )
        if kind == "AddressZeroCheck":
            expression = attrs.get("condition") or "addressZeroCheck(unknown)"
            return self.fact(
                fn, overlay, stmt_lang, "BranchCondition",
                rvalue=expression,
                reads=clean_list([attrs.get("variable")]),
                semantic=pick(attrs, (
                    "variable", "variable_type", "check", "condition", "projection",
                    "projection_expression", "source_pattern",
                )) | {
                    "operation": "address_zero_check",
                    "expression": expression,
                    "context": "condition",
                    "status": "resolved",
                },
            )
        if kind == "BytesContentHash":
            target = attrs.get("target")
            expression = attrs.get("result_expression") or attrs.get("expression") or "keccak256(bytes)"
            object_name = attrs.get("object") or attrs.get("hash_input")
            return self.fact(
                fn, overlay, stmt_lang, "HashCompute",
                lvalue=target, rvalue=expression,
                reads=clean_list([object_name]), writes=clean_list([target]),
                semantic=pick(attrs, (
                    "hash_algorithm", "object", "object_type", "target_type", "expression",
                    "result_expression", "pattern", "exact_solidity_equivalent",
                )) | {
                    "builtin_signature": "keccak256(bytes)",
                    "arguments": clean_list([object_name]),
                    "operation": "bytes_content_hash",
                },
            )
        if kind == "EventEmit":
            return self.fact(fn, overlay, stmt_lang, "EventEmit", reads=flat_list(attrs.get("args")), semantic={
                "event": attrs.get("event") or attrs.get("event_name"),
                "signature": attrs.get("signature"),
                "args": attrs.get("args"),
                **self.event_topic_semantics(attrs),
            })
        if kind == "RequireOverlay":
            condition = attrs.get("condition") or attrs.get("nearest_condition") or attrs.get("require_like")
            failure_predicates = attrs.get("semantic_require_conditions") or attrs.get("require_conditions")
            return self.fact(fn, overlay, stmt_lang, "Require", condition=condition, reads=rough_reads(condition), semantic={
                "condition": condition,
                "failure_predicates": failure_predicates,
                "guard_stmt_refs": attrs.get("guard_stmt_refs"),
                "on_fail": "revert",
                "error": attrs.get("error") or attrs.get("custom_error"),
            })
        if kind == "Predicate":
            expression = attrs.get("expression")
            predicate_id = attrs.get("predicate_id")
            return self.fact(fn, overlay, stmt_lang, "BranchCondition", lvalue=predicate_id, rvalue=expression,
                             reads=clean_list(attrs.get("dependencies") or rough_reads(expression)), writes=clean_list([predicate_id]), semantic={
                                 "operation": "control_predicate",
                                 "predicate_id": predicate_id,
                                 "expression": expression,
                                 "context": attrs.get("context") or "condition",
                                 "status": attrs.get("status"),
                                 "semantic_model": attrs.get("semantic_model"),
                                 "switch_edges": attrs.get("switch_edges"),
                             })
        if kind in {"CustomErrorRevert", "RawRevertBytes", "RevertOverlay"}:
            condition = attrs.get("guard") or attrs.get("condition") or attrs.get("nearest_condition")
            return self.fact(fn, overlay, stmt_lang, "Revert", condition=condition, reads=flat_list(attrs.get("args")), semantic={
                "operation": "revert",
                "error": attrs.get("error") or attrs.get("custom_error"),
                "payload": attrs.get("payload") or attrs.get("revert_payload"),
                "source_object": attrs.get("source_object"),
                "arguments": attrs.get("args"),
                "guard": condition,
            })
        if kind in {"ExternalCall", "LowLevelCall", "StaticCallOverlay", "DelegateCallOverlay"}:
            return self.call_fact(fn, overlay, stmt_lang, "ExternalCall")
        if kind == "LibraryCall":
            return self.call_fact(fn, overlay, stmt_lang, "LibraryCall")
        if kind == "AbiCallDataConstruction":
            payload = self.abi_payload_name(overlay)
            arguments = [
                item.get("value_semantic") or item.get("value_normalized") or item.get("value")
                for item in attrs.get("arguments") or [] if isinstance(item, dict)
            ]
            signature = attrs.get("signature")
            return self.fact(
                fn, overlay, stmt_lang, "AbiEncode",
                lvalue=payload,
                rvalue=f"abi.encodeWithSelector({signature or attrs.get('selector') or 'unknown'})",
                reads=clean_list(arguments), writes=[payload],
                semantic=pick(attrs, ("selector", "signature", "selector_match", "arguments")) | {
                    "builtin_signature": "abi.encodeWithSelector(bytes4,...)",
                    "arguments": clean_list([attrs.get("selector"), *arguments]),
                    "encoded_payload": payload,
                    "operation": "abi_calldata_construction",
                },
            )
        if kind == "AbiEncodedLowLevelCall":
            fact = self.call_fact(fn, overlay, stmt_lang, "LowLevelCall")
            semantic = dict(fact.semantic)
            calldata = attrs.get("abi_calldata") or {}
            payload_overlay = calldata.get("overlay") if isinstance(calldata, dict) else None
            semantic.update({
                "operation": "abi_encoded_low_level_call",
                "encoded_payload": self.abi_payload_name_from_id(payload_overlay) if payload_overlay else None,
                "abi_encoding": self.abi_encoding_semantics(calldata) if isinstance(calldata, dict) else {},
            })
            fact.semantic = clean_dict(semantic)
            fact.reads = clean_list([*fact.reads, semantic.get("encoded_payload")])
            return fact
        if kind == "CallOutputRead":
            target = attrs.get("target")
            value = attrs.get("value")
            return self.fact(
                fn,
                overlay,
                stmt_lang,
                "ValueCompute",
                lvalue=target,
                rvalue=value,
                writes=clean_list([target]),
                semantic=pick(attrs, (
                    "operation", "source_call_overlay", "call_kind", "target_address",
                    "selector", "selector_signature", "arguments", "word_index",
                    "value", "solidity_like", "resolution",
                )) | {"operation": "external_call_return_word"},
            )
        if kind == "PrecompileOutputRead":
            target = attrs.get("target")
            value = attrs.get("value") or "precompileOutput(unknown)"
            return self.fact(
                fn, overlay, stmt_lang, "ValueCompute",
                lvalue=target, rvalue=value, writes=clean_list([target]),
                semantic=pick(attrs, ("source_precompile_overlay", "value")) | {
                    "operation": "precompile_output_read",
                    "value": value,
                },
            )
        if kind == "CalldataWordRead":
            target = attrs.get("target")
            offset = attrs.get("offset_normalized") or attrs.get("offset")
            value = f"calldataWord({offset})" if offset else "calldataWord(unknown)"
            # This is a completed S-SEIR overlay.  Do not lower it back to
            # the original Yul ``calldataload`` spelling in final SFIR.
            return self.fact(
                fn,
                overlay,
                stmt_lang,
                "ValueCompute",
                lvalue=target,
                rvalue=value,
                reads=rough_reads(offset),
                writes=clean_list([target]),
                semantic=pick(attrs, (
                    "source", "offset", "offset_normalized", "width_bytes",
                    "target_type", "source_expression", "reason",
                )) | {"operation": "calldata_word_read", "value": value},
            )
        if kind == "CalldataSelectorRead":
            target = attrs.get("target")
            return self.fact(
                fn,
                overlay,
                stmt_lang,
                "ValueCompute",
                lvalue=target,
                rvalue="msg.sig",
                reads=["msg.data"],
                writes=clean_list([target]),
                semantic=pick(attrs, (
                    "source", "access", "target_type", "semantic_model", "solidity_like",
                )) | {"operation": "calldata_selector_read", "value": "msg.sig"},
            )
        if kind == "CalldataArrayElementRead":
            target = attrs.get("target")
            access = attrs.get("access") or "calldataArrayElement(unknown)"
            return self.fact(
                fn,
                overlay,
                stmt_lang,
                "ValueCompute",
                lvalue=target,
                rvalue=access,
                reads=clean_list([attrs.get("array"), attrs.get("index")]),
                writes=clean_list([target]),
                # The source ``calldataload`` expression is recovery evidence
                # only.  Final SFIR receives the typed array operation and its
                # control proof, never the low-level calldata spelling.
                semantic=pick(attrs, (
                    "array", "array_type", "element_type", "data_location",
                    "index", "access", "layout", "element_encoding",
                    "bounds_proof", "semantic_model", "solidity_like",
                )) | {"operation": "calldata_array_element_read", "value": access},
            )
        if kind in {"MemoryArrayLengthRead", "MemoryArrayElementRead"}:
            target = attrs.get("target")
            access = attrs.get("access") or (
                "memoryArrayElement(unknown)" if kind == "MemoryArrayElementRead" else "memoryArrayLength(unknown)"
            )
            operation = "memory_array_element_read" if kind == "MemoryArrayElementRead" else "memory_array_length_read"
            reads = [attrs.get("array")]
            if kind == "MemoryArrayElementRead":
                reads.append(attrs.get("index"))
            return self.fact(
                fn,
                overlay,
                stmt_lang,
                "ValueCompute",
                lvalue=target,
                rvalue=access,
                reads=clean_list(reads),
                writes=clean_list([target]),
                semantic=pick(attrs, (
                    "array", "array_type", "element_type", "data_location", "index", "access",
                    "layout", "bounds_proof", "pattern_model", "solidity_like",
                )) | {"operation": operation, "value": access},
            )
        if kind == "YulLocalFunctionDefinition":
            return self.fact(
                fn,
                overlay,
                stmt_lang,
                "LocalFunctionDefinition",
                lvalue=attrs.get("name"),
                semantic=pick(attrs, (
                    "name", "parameters", "returns", "body", "semantic_cfg", "solidity_like",
                    "semantic_model", "assembly_block",
                )),
            )
        if kind == "YulLocalFunctionCall":
            target = attrs.get("target")
            arguments = clean_list(attrs.get("arguments"))
            return self.fact(
                fn,
                overlay,
                stmt_lang,
                "InternalCall",
                lvalue=target,
                rvalue=f"{attrs.get('function')}({', '.join(str(item) for item in arguments)})",
                reads=arguments,
                writes=clean_list([target]),
                semantic=pick(attrs, (
                    "function", "arguments", "definition_overlay", "semantic_model", "assembly_block",
                )) | {"operation": "yul_local_function_call"},
            )
        if kind == "PrecompileCall":
            return self.call_fact(fn, overlay, stmt_lang, "PrecompileCall")
        if kind == "InternalCall":
            return self.call_fact(fn, overlay, stmt_lang, "InternalCall")
        if kind == "ReturnValue":
            values = clean_list(attrs.get("values"))
            # ``ReturnValue`` is emitted by the actual S-SEIR return builder
            # as a list.  Preserve that source-level arity rather than
            # accidentally projecting it as an empty return.
            value = values[0] if len(values) == 1 else values
            return self.fact(fn, overlay, stmt_lang, "Return", rvalue=value, reads=values, semantic={
                "operation": "return",
                "value": value,
                "values": values,
                "target": attrs.get("target"),
            })
        if kind == "RawReturnData":
            values = clean_list(attrs.get("values"))
            expression = self.raw_return_expression(attrs)
            return self.fact(
                fn, overlay, stmt_lang, "Return",
                rvalue=expression, reads=values,
                semantic=pick(attrs, ("encoding_hint", "payload_memory_complete", "reason")) | {
                    "operation": "raw_return_data",
                    "values": values,
                    "return_expression": expression,
                },
            )
        if kind == "MemoryRegionAllocate":
            base = attrs.get("base") or attrs.get("region_base")
            return self.fact(fn, overlay, stmt_lang, "MemoryAllocate", lvalue=base, rvalue="memoryRegion", writes=clean_list([base]), semantic={
                "operation": "memory_region_allocate",
                "stored_value_count": len(attrs.get("stored_values") or []),
                "region": base,
                "semantic_hint": attrs.get("semantic_hint"),
            })
        if kind == "MemoryArrayConstruction":
            result = attrs.get("result")
            length = attrs.get("length_expr_normalized") or attrs.get("length_expr") or "unknown"
            # S-SEIR has proved these writes are part of one array-object
            # construction, but it deliberately has not necessarily proved
            # every raw memory address is a source-level array index.  Retain
            # the recovered values and pattern without inventing indices.
            elements = clean_list([
                item.get("value_normalized") or item.get("value")
                for item in attrs.get("element_writes") or [] if isinstance(item, dict)
            ])
            return self.fact(fn, overlay, stmt_lang, "MemoryObjectConstruct", lvalue=result,
                             rvalue=f"new {attrs.get('array_type') or 'array'}({length})",
                             reads=clean_list([length, *elements]), writes=[result], semantic={
                                 "operation": "memory_array_construction",
                                 "result": result,
                                 "array_type": attrs.get("array_type"),
                                 "element_type": attrs.get("element_type"),
                                 "length": length,
                                 "elements": elements,
                                 "element_write_pattern": attrs.get("element_write_pattern"),
                                 "recovered_element_write_count": len(elements),
                             })
        if kind == "StructFieldRead":
            field = attrs.get("field") or {}
            target = attrs.get("value")
            access = self.struct_field_access(attrs)
            return self.fact(
                fn, overlay, stmt_lang, "ValueCompute",
                lvalue=target, rvalue=access, reads=clean_list([attrs.get("struct_object")]), writes=clean_list([target]),
                semantic=pick(attrs, ("struct_object", "struct_type", "field")) | {
                    "operation": "struct_field_read", "access": access,
                    "field_type": field.get("type_string"),
                },
            )
        if kind == "StructFieldWrite":
            value = attrs.get("value_normalized") or attrs.get("value")
            access = self.struct_field_access(attrs)
            return self.fact(
                fn, overlay, stmt_lang, "ValueAssign",
                lvalue=access, rvalue=value,
                reads=self.reads_for_value(value, attrs), writes=[access],
                semantic=pick(attrs, ("struct_object", "struct_type", "field")) | {
                    "operation": "struct_field_write", "access": access,
                },
            )
        if kind == "StoragePointerSlotBinding":
            pointer = attrs.get("pointer") or "storagePointer"
            return self.fact(
                fn, overlay, stmt_lang, "ValueCompute",
                lvalue=pointer, rvalue="opaqueStorageLocation",
                writes=[pointer],
                semantic={
                    "operation": "storage_pointer_binding",
                    "status": "unresolved",
                    "reason": attrs.get("reason") or "storage_pointer_location_not_semantically_recovered",
                    "value": "opaqueStorageLocation",
                },
            )
        if kind in {"StructMemoryMutation", "StructInitializationFragment", "StructMutationFragment", "MemoryRegionWrite", "CursorBasedMemoryWrite"}:
            target = attrs.get("target") or attrs.get("region_base") or attrs.get("object")
            value = attrs.get("value") or attrs.get("value_normalized") or attrs.get("fields") or attrs.get("field_updates")
            return self.fact(fn, overlay, stmt_lang, "MemoryObjectWrite", lvalue=target, rvalue=value, reads=flat_list(value), writes=[target], semantic=pick(attrs, (
                "target", "region_base", "object", "struct_object", "struct_type", "fields", "mutations", "semantic_hint",
                "cursor_field", "field_updates", "value", "value_normalized",
            )) | {"operation": "memory_object_write"})
        if kind in {"ExpressionNormalization", "EvaluationStep"}:
            target = attrs.get("target") or attrs.get("temp")
            is_condition = attrs.get("context") == "condition"
            value = (
                attrs.get("condition_normalized") if is_condition else None
            ) or attrs.get("expression_normalized") or attrs.get("value") or attrs.get("expression") or attrs.get("solidity_like")
            return self.fact(fn, overlay, stmt_lang, "ValueCompute", lvalue=target, rvalue=value, reads=rough_reads(value), writes=[target], semantic=pick(attrs, (
                "target", "temp", "value", "expression_normalized", "condition_normalized", "context", "solidity_like", "call", "raw_args", "evaluated_args",
            )))
        return None

    def storage_candidate_fact(
        self,
        fn: dict[str, Any],
        overlay: dict[str, Any],
        candidate: dict[str, Any],
        stmt_lang: dict[str, str],
        effect_by_id: dict[str, dict[str, Any]],
    ) -> SemanticFact:
        attrs = overlay.get("attrs") or {}
        is_write = str(overlay.get("kind")) == "PathConditionedStorageWrite"
        access = candidate.get("access") or attrs.get("slot")
        value = candidate.get("value") or attrs.get("value") or attrs.get("value_yul")
        kind = "StateWrite" if is_write else "StateRead"
        return self.fact(
            fn,
            overlay,
            stmt_lang,
            kind,
            condition=candidate.get("condition"),
            lvalue=access if is_write else attrs.get("target"),
            rvalue=value if is_write else access,
            reads=self.reads_for_value(value, candidate) if is_write else [access],
            writes=[access] if is_write else [],
            semantic={
                **self.state_semantic(candidate, "state_write" if is_write else "state_read"),
                "value": value if is_write else None,
                "candidate_status": candidate.get("status"),
                "unresolved_reason": candidate.get("unresolved_reason") or candidate.get("reason"),
            },
            extra_evidence={"candidate": clean_dict(candidate)},
        )

    def event_candidate_fact(self, fn: dict[str, Any], overlay: dict[str, Any], candidate: dict[str, Any], stmt_lang: dict[str, str]) -> SemanticFact:
        attrs = overlay.get("attrs") or {}
        event_attrs = {**attrs, **candidate}
        return self.fact(fn, overlay, stmt_lang, "EventEmit", condition=candidate.get("condition"), reads=flat_list(candidate.get("args")), semantic={
            "event": candidate.get("event") or (overlay.get("attrs") or {}).get("event"),
            "signature": candidate.get("signature") or attrs.get("signature"),
            "args": candidate.get("args"),
            **self.event_topic_semantics(event_attrs),
            "candidate_status": candidate.get("status"),
            "unresolved_reason": candidate.get("unresolved_reason") or candidate.get("reason"),
        }, extra_evidence={"candidate": clean_dict(candidate)})

    def revert_candidate_fact(self, fn: dict[str, Any], overlay: dict[str, Any], candidate: dict[str, Any], stmt_lang: dict[str, str]) -> SemanticFact:
        return self.fact(fn, overlay, stmt_lang, "Revert", condition=candidate.get("condition"), reads=flat_list(candidate.get("args")), semantic={
            "operation": "revert",
            "error": candidate.get("error") or candidate.get("custom_error") or candidate.get("selector_signature"),
            "payload": candidate.get("payload") or candidate.get("revert_payload"),
            "source_object": candidate.get("source_object") or (overlay.get("attrs") or {}).get("source_object"),
            "arguments": candidate.get("args") or (overlay.get("attrs") or {}).get("args"),
            "guard": candidate.get("condition"),
            # A matched selector signature is preferred above.  When only a
            # selector is proved, it is still a meaningful raw-revert
            # operation but not evidence for inventing an error name/args.
            "selector": candidate.get("selector"),
            "raw_payload": candidate.get("normalized"),
            "candidate_status": candidate.get("status"),
            "unresolved_reason": candidate.get("unresolved_reason") or candidate.get("reason"),
        }, extra_evidence={"candidate": clean_dict(candidate)})

    def call_candidate_fact(self, fn: dict[str, Any], overlay: dict[str, Any], candidate: dict[str, Any], stmt_lang: dict[str, str]) -> SemanticFact:
        base_kind = "PrecompileCall" if "Precompile" in str(overlay.get("kind")) else "ExternalCall"
        attrs = overlay.get("attrs") or {}
        result = candidate.get("result") or attrs.get("result") or self.result_from_high_level_line(candidate.get("solidity_like"))
        target = candidate.get("target_solidity") or candidate.get("target") or attrs.get("target_solidity") or attrs.get("target")
        arguments = candidate.get("arguments") or candidate.get("args")
        semantic = {
            "target": target,
            "call_kind": candidate.get("call_kind") or candidate.get("op") or attrs.get("op"),
            "selector": candidate.get("selector"),
            "selector_signature": candidate.get("selector_signature"),
            "arguments": arguments,
            "precompile": candidate.get("precompile") or attrs.get("precompile"),
            "call_status_result": result,
            "candidate_status": candidate.get("status"),
            "unresolved_reason": candidate.get("unresolved_reason") or candidate.get("reason"),
        }
        if base_kind == "PrecompileCall":
            semantic["native_projection"] = self.native_precompile_projection(candidate, attrs)
            semantic["input_words"] = candidate.get("input_words") or attrs.get("input_words")
        return self.fact(fn, overlay, stmt_lang, base_kind, condition=candidate.get("condition"), reads=clean_list([target, *flat_list(arguments)]), semantic=semantic, lvalue=result, writes=clean_list([result]), extra_evidence={"candidate": clean_dict(candidate)})

    def raw_return_candidate_fact(self, fn: dict[str, Any], overlay: dict[str, Any], candidate: dict[str, Any], stmt_lang: dict[str, str]) -> SemanticFact:
        attrs = overlay.get("attrs") or {}
        values = clean_list(candidate.get("values"))
        expression = self.raw_return_expression(candidate, fallback=attrs)
        return self.fact(
            fn, overlay, stmt_lang, "Return", condition=candidate.get("condition"),
            rvalue=expression, reads=values,
            semantic={
                "operation": "raw_return_data",
                "encoding_hint": candidate.get("encoding_hint"),
                "values": values,
                "return_expression": expression,
                "candidate_status": candidate.get("status"),
                "unresolved_reason": candidate.get("unresolved_reason") or candidate.get("reason"),
            }, extra_evidence={"candidate": clean_dict(candidate)},
        )

    def precompile_output_candidate_fact(self, fn: dict[str, Any], overlay: dict[str, Any], candidate: dict[str, Any], stmt_lang: dict[str, str]) -> SemanticFact:
        attrs = overlay.get("attrs") or {}
        target = candidate.get("target") or attrs.get("target")
        value = candidate.get("value") or "precompileOutput(unknown)"
        return self.fact(
            fn, overlay, stmt_lang, "ValueCompute", condition=candidate.get("condition"),
            lvalue=target, rvalue=value, writes=clean_list([target]),
            semantic={
                "operation": "precompile_output_read",
                "value": value,
                "source_precompile_overlay": candidate.get("source_precompile_overlay") or attrs.get("source_precompile_overlay"),
                "precompile": candidate.get("precompile"),
                "candidate_status": candidate.get("status"),
                "unresolved_reason": candidate.get("unresolved_reason") or candidate.get("reason"),
            }, extra_evidence={"candidate": clean_dict(candidate)},
        )

    def unmodeled_overlay_fact(
        self,
        fn: dict[str, Any],
        overlay: dict[str, Any],
        stmt_lang: dict[str, str],
        *,
        reason: str = "no_sfir_projection_rule",
        lvalue: Any = None,
        reads: list[Any] | None = None,
        writes: list[Any] | None = None,
    ) -> SemanticFact:
        kind = str(overlay.get("kind") or "unknown")
        return self.fact(
            fn, overlay, stmt_lang, "UnmodeledSSeirOverlay",
            lvalue=lvalue,
            rvalue="opaqueYulValue()" if lvalue else None,
            reads=clean_list(reads or []),
            writes=clean_list(writes or ([] if lvalue is None else [lvalue])),
            semantic={
                "operation": "unmodeled_sseir_overlay",
                "status": "unmodeled",
                "overlay_kind": kind,
                "unmodeled_reason": reason,
                "opaque_value": "opaqueYulValue()" if lvalue else None,
                # Attributes are deliberately summarized rather than copied:
                # they can contain upstream MemorySSA/effect transport.
                "available_fields": sorted(str(key) for key in (overlay.get("attrs") or {}).keys()),
            },
        )

    @staticmethod
    def abi_payload_name(overlay: dict[str, Any]) -> str:
        return SSeirFactAdapter.abi_payload_name_from_id(overlay.get("overlay_id"))

    @staticmethod
    def abi_payload_name_from_id(overlay_id: Any) -> str:
        safe = re.sub(r"[^A-Za-z0-9_]", "_", str(overlay_id or "unknown"))
        return f"__sfir_abi_payload_{safe}"

    @staticmethod
    def struct_field_access(attrs: dict[str, Any]) -> str:
        field = attrs.get("field") or {}
        object_name = attrs.get("struct_object") or "structObject"
        name = field.get("name") or "field"
        return f"{object_name}.{name}"

    @staticmethod
    def raw_return_expression(attrs: dict[str, Any], *, fallback: dict[str, Any] | None = None) -> str:
        attrs = attrs or {}
        fallback = fallback or {}
        hint = attrs.get("encoding_hint") or fallback.get("encoding_hint")
        values = clean_list(attrs.get("values") or fallback.get("values"))
        if hint == "abi_word" and len(values) == 1:
            return f"returnRawAbiWord({values[0]})"
        if hint == "abi_static_words" and values:
            return f"returnRawAbiWords({', '.join(map(str, values))})"
        # The data has intentionally not been decoded.  This is a high-level
        # opaque return operation, not a replay of the original Yul pointer.
        return "returnRawMemory()"

    @staticmethod
    def abi_encoding_semantics(calldata: dict[str, Any]) -> dict[str, Any]:
        """Keep ABI argument meaning while excluding write/effect transport."""
        arguments = []
        for item in calldata.get("arguments") or []:
            if isinstance(item, dict):
                arguments.append(clean_dict({
                    "index": item.get("index"), "type": item.get("type"),
                    "value": item.get("value_semantic") or item.get("value_normalized") or item.get("value"),
                    "kind": item.get("kind"), "length": item.get("length_semantic"),
                }))
            else:
                arguments.append(item)
        return clean_dict({
            "selector": calldata.get("selector"), "signature": calldata.get("signature"),
            "arguments": arguments,
        })

    @staticmethod
    def result_from_high_level_line(line: Any) -> str | None:
        """Read an assignment target only from S-SEIR's completed display form.

        Path-sensitive call overlays predate a structured ``result`` field,
        but their own completed Solidity-like operation records the target.
        This deliberately recognizes only an unambiguous identifier assignment
        and never parses or revisits the original Yul expression.
        """
        match = re.match(r"^\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*=", str(line or ""))
        return match.group(1) if match else None

    def call_fact(self, fn: dict[str, Any], overlay: dict[str, Any], stmt_lang: dict[str, str], fact_kind: str) -> SemanticFact:
        attrs = overlay.get("attrs") or {}
        arguments = attrs.get("arguments") or attrs.get("args")
        result = attrs.get("result")
        semantic = {
            "target": attrs.get("target_solidity") or attrs.get("target"),
            "call_kind": attrs.get("call_kind") or attrs.get("op") or overlay.get("kind"),
            "selector": attrs.get("selector"),
            "selector_signature": attrs.get("selector_signature"),
            "arguments": arguments,
            "decoded_input": attrs.get("decoded_input"),
            "semantic_inputs": attrs.get("semantic_inputs"),
            "value": attrs.get("value"),
            "call_status_result": result,
            "precompile": attrs.get("precompile"),
            "solidity_like": attrs.get("solidity_like"),
        }
        if fact_kind == "PrecompileCall":
            semantic["native_projection"] = self.native_precompile_projection(attrs)
            semantic["input_words"] = self.precompile_input_words(attrs)
        return self.fact(fn, overlay, stmt_lang, fact_kind, lvalue=result, writes=clean_list([result]), reads=clean_list([
            attrs.get("target_solidity") or attrs.get("target"),
            attrs.get("value"),
            *flat_list(arguments),
        ]), semantic=semantic)

    @staticmethod
    def event_topic_semantics(attrs: dict[str, Any]) -> dict[str, Any]:
        """Project event topics as source-level indexed-event meaning only."""
        topics = clean_list(attrs.get("topics"))
        return clean_dict({
            "topics": topics,
            "topic0": attrs.get("topic0") or (topics[0] if topics else None),
            "indexed_topic_values": topics[1:] if len(topics) > 1 else [],
        })

    @staticmethod
    def precompile_input_words(attrs: dict[str, Any]) -> list[Any]:
        native = attrs.get("native_precompile") or {}
        return clean_list(attrs.get("input_words") or native.get("input_words"))

    @staticmethod
    def native_precompile_projection(*sources: dict[str, Any]) -> dict[str, Any]:
        """Whitelist the completed intrinsic, never its memory/effect proof."""
        native: dict[str, Any] = {}
        precompile = None
        for source in sources:
            if not isinstance(source, dict):
                continue
            precompile = precompile or source.get("precompile")
            candidate = source.get("native_precompile") or {}
            if isinstance(candidate, dict):
                precompile = precompile or candidate.get("precompile")
                # Direct call overlays retain a small wrapper containing the
                # intrinsic projection; path candidates retain the intrinsic
                # itself.  Both are S-SEIR's documented completed forms.
                inner = candidate.get("native_precompile")
                native.update(inner if isinstance(inner, dict) else candidate)
        return clean_dict({
            "precompile": precompile,
            "result_name": native.get("result_name"),
            "output_expression": native.get("output_word_expression"),
            "solidity_like": native.get("solidity_like"),
        })

    @staticmethod
    def overlay_with_effect_refs(overlay: dict[str, Any], effect_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
        refs: list[str] = []
        referenced_effects: list[dict[str, Any]] = []
        for effect_id in overlay.get("effects") or []:
            effect = effect_by_id.get(effect_id)
            if not isinstance(effect, dict):
                continue
            referenced_effects.append(effect)
            refs.extend(str(ref) for ref in effect.get("stmt_refs") or [] if ref)
        if not refs and not referenced_effects:
            return overlay
        out = dict(overlay)
        if not overlay.get("stmt_refs") and refs:
            out["stmt_refs"] = list(dict.fromkeys(refs))

        # Supporting definitions can execute under a weaker condition than
        # the semantic sink. A MappingWrite, for example, cites both its slot
        # hash and the later StorageWrite; only the write controls mutation.
        sink_kinds = SSeirFactAdapter.sink_effect_kinds(str(overlay.get("kind") or ""))
        condition_effects = [
            effect for effect in referenced_effects
            if str(effect.get("kind") or "") in sink_kinds
        ] or referenced_effects
        path_states: list[str] = []
        for effect in condition_effects:
            attrs = effect.get("attrs") or {}
            path_states.extend(str(item) for item in attrs.get("path_states") or [] if item and item != "entry")
        unique_paths = list(dict.fromkeys(path_states))
        if len(unique_paths) == 1:
            out["_sseir_effect_condition"] = unique_paths[0]
        elif len(unique_paths) > 1:
            common = SSeirFactAdapter.common_path_condition(unique_paths)
            if common:
                out["_sseir_effect_condition"] = common
        return out

    @staticmethod
    def sink_effect_kinds(overlay_kind: str) -> set[str]:
        if overlay_kind in {"StateVariableWrite", "MappingWrite"}:
            return {"StorageWrite"}
        if overlay_kind in {"StateVariableRead", "MappingRead"}:
            return {"StorageRead"}
        if overlay_kind in {"EventEmit", "PathConditionedEventEmit"}:
            return {"EventLog"}
        if overlay_kind in {"RequireOverlay", "CustomErrorRevert", "RawRevertBytes", "RevertOverlay"}:
            return {"Revert"}
        if overlay_kind in {"ExternalCall", "LowLevelCall", "StaticCallOverlay", "DelegateCallOverlay", "PrecompileCall"}:
            return {"Call", "StaticCall", "DelegateCall", "CallCode", "ExternalCall", "InternalCall"}
        if overlay_kind == "ReturnValue":
            return {"Return"}
        return set()

    @staticmethod
    def common_path_condition(path_states: list[str]) -> str | None:
        """Keep the ordered conjunction shared by every reaching sink path."""
        paths = [
            [part.strip() for part in str(path).split(" && ") if part.strip() and part.strip() != "entry"]
            for path in path_states
            if path
        ]
        if not paths:
            return None
        common = [part for part in paths[0] if all(part in path for path in paths[1:])]
        return " && ".join(common) if common else None

    def fact(
        self,
        fn: dict[str, Any],
        overlay: dict[str, Any],
        stmt_lang: dict[str, str],
        kind: str,
        *,
        condition: str | None = None,
        lvalue: Any = None,
        rvalue: Any = None,
        reads: list[Any] | None = None,
        writes: list[Any] | None = None,
        semantic: dict[str, Any] | None = None,
        extra_evidence: dict[str, Any] | None = None,
    ) -> SemanticFact:
        stmt_refs = list(overlay.get("stmt_refs") or [])
        source_lang = source_lang_for_refs(stmt_refs, stmt_lang)
        evidence = {
            "overlay": overlay.get("overlay_id"),
            "overlay_kind": overlay.get("kind"),
            "effects": list(overlay.get("effects") or []),
        }
        if extra_evidence:
            evidence.update(extra_evidence)
        return SemanticFact(
            fact_id=self.ids.new(),
            kind=kind,
            source_lang=source_lang,
            origin="sseir_overlay",
            function=str(fn.get("function") or ""),
            fact_role=semantic_fact_role(kind),
            contract=fn.get("contract"),
            signature=fn.get("signature"),
            stmt_refs=stmt_refs,
            cfg_nodes=cfg_nodes_for_refs(stmt_refs, fn),
            condition=condition or overlay.get("_sseir_effect_condition"),
            lvalue=lvalue,
            rvalue=rvalue,
            reads=clean_list(reads or []),
            writes=clean_list(writes or []),
            semantic=clean_dict({"atomic_operation_count": 1, **(semantic or {})}),
            evidence=clean_dict(evidence),
        )

    @staticmethod
    def statement_languages(fn: dict[str, Any]) -> dict[str, str]:
        return {
            str(stmt.get("stmt_id")): str(stmt.get("lang"))
            for stmt in fn.get("source_statements") or []
            if isinstance(stmt, dict) and stmt.get("stmt_id")
        }

    @staticmethod
    def state_semantic(attrs: dict[str, Any], operation: str) -> dict[str, Any]:
        access = attrs.get("access") or attrs.get("slot")
        return clean_dict({
            "operation": operation,
            "access": access,
            "state_variable": attrs.get("state_variable") or attrs.get("state_var"),
            "keys": attrs.get("keys") or bracket_keys(str(access or "")),
            "location": SSeirFactAdapter.storage_location(attrs),
            "candidate_status": attrs.get("status"),
        })

    @staticmethod
    def storage_resolution_evidence(attrs: dict[str, Any]) -> dict[str, Any]:
        # A recovered StateRead/StateWrite already carries the complete
        # structured location in its semantic payload.  Slot hashes and their
        # version transport belong exclusively to upstream S-SEIR evidence.
        if SSeirFactAdapter.is_resolved_storage_location(SSeirFactAdapter.storage_location(attrs)):
            return {}
        return clean_dict({
            "storage_model": attrs.get("storage_model"),
            "slot": attrs.get("slot"),
            "slot_key": attrs.get("slot_key"),
            "slot_versions": attrs.get("slot_versions") or attrs.get("slot_keys"),
            "slot_effect": attrs.get("slot_effect"),
            "parent_target_key": attrs.get("parent_target_key"),
            "keys": attrs.get("keys"),
        })

    @staticmethod
    def storage_location(attrs: dict[str, Any]) -> dict[str, Any]:
        access = attrs.get("access") or attrs.get("expression") or attrs.get("slot")
        keys = attrs.get("keys") or bracket_keys(str(access or "")) or clean_list([attrs.get("key")])
        slot_kind = str(attrs.get("slot_kind") or "")
        if slot_kind == "dynamic_array_slot":
            kind = "dynamic_array"
        elif slot_kind == "mapping_slot" or keys:
            kind = "mapping"
        elif attrs.get("state_variable") or attrs.get("state_var"):
            kind = "state_variable"
        else:
            kind = "manual_slot"
        return clean_dict({
            "kind": kind,
            "access": access,
            "state_variable": attrs.get("state_variable") or attrs.get("state_var"),
            "keys": keys,
        })

    @staticmethod
    def is_resolved_storage_location(location: dict[str, Any]) -> bool:
        """Whether a location can be represented without physical slot math."""
        if not isinstance(location, dict):
            return False
        access = str(location.get("access") or "").strip()
        state_variable = str(location.get("state_variable") or "").strip()
        if not access or not state_variable:
            return False
        if str(location.get("kind") or "") not in {"mapping", "dynamic_array", "state_variable"}:
            return False
        return not any(token in access.lower() for token in ("keccak256(", "sload(", "sstore("))

    @staticmethod
    def storage_location_reads(location: dict[str, Any]) -> list[Any]:
        return clean_list([
            location.get("state_variable"),
            *(location.get("keys") or []),
        ])

    @staticmethod
    def reads_for_value(value: Any, attrs: dict[str, Any]) -> list[Any]:
        reads = rough_reads(value)
        value_state_read = attrs.get("value_state_read")
        if isinstance(value_state_read, dict):
            reads.append(value_state_read.get("access") or value_state_read.get("slot"))
        return clean_list(reads)


class SlitherFactAdapter:
    """Normalize Slither/SlithIR operations into SemanticFact rows.

    The adapter keeps the fact kind close to SlithIR's operation vocabulary
    and archives the original operation payload in evidence. This makes the
    Solidity line and the S-SEIR/Yul line meet at a common behavior-fact layer
    without losing Slither-specific semantics.
    """

    KIND_MAP = {
        "Assignment": "ValueAssign",
        "Binary": "BinaryOperation",
        "Unary": "UnaryOperation",
        "TypeConversion": "TypeConversion",
        "Index": "IndexAccess",
        "Member": "MemberAccess",
        "Length": "LengthRead",
        "Delete": "Delete",
        "InitArray": "ArrayLiteral",
        "NewArray": "NewArray",
        "NewContract": "NewContract",
        "NewElementaryType": "NewElementaryType",
        "NewStructure": "NewStructure",
        "Phi": "Phi",
        "PhiCallback": "PhiCallback",
        "Unpack": "TupleUnpack",
        "Nop": "Nop",
        "InternalCall": "InternalCall",
        "InternalDynamicCall": "InternalDynamicCall",
        "HighLevelCall": "ExternalCall",
        "LowLevelCall": "LowLevelCall",
        "LibraryCall": "LibraryCall",
        "SolidityCall": "BuiltinCall",
        "Condition": "BranchCondition",
        "Return": "Return",
        "EventCall": "EventEmit",
        "Send": "ValueTransferCall",
        "Transfer": "ValueTransferCall",
        "Call": "Call",
        "Operation": "Operation",
        "OperationWithLValue": "OperationWithLValue",
    }

    def __init__(self) -> None:
        self.ids = FactIdAllocator()

    def facts_from_slither_function(self, function: Any, *, ssa: bool = False) -> list[SemanticFact]:
        """Build facts directly from a Slither Function object."""
        operations: list[Any] = []
        for node in getattr(function, "nodes", []) or []:
            source = getattr(node, "irs_ssa" if ssa else "irs", None) or []
            operations.extend(source)
        return self.facts_from_operations(self.function_record(function), operations)

    def facts_from_slither(self, slither: Any, *, ssa: bool = False) -> dict[str, Any]:
        """Build a SemanticFact payload from a Slither object."""
        functions: list[Any] = []
        for contract in getattr(slither, "contracts", []) or []:
            for function in getattr(contract, "functions_and_modifiers_declared", []) or []:
                functions.append(function)
        facts = [fact.to_dict() for function in functions for fact in self.facts_from_slither_function(function, ssa=ssa)]
        return {
            "schema": "s-seir-semantic-facts/v1",
            "source": "slither",
            "function_count": len(functions),
            "facts": facts,
        }

    def facts_from_operations(self, function: dict[str, Any], operations: list[Any]) -> list[SemanticFact]:
        out: list[SemanticFact] = []
        for index, raw_op in enumerate(operations):
            op = self.operation_record(raw_op, index)
            slithir_kind = str(op.get("kind") or op.get("type") or "")
            kind = self.KIND_MAP.get(slithir_kind, "SlithIROperation")
            reads = self.operation_reads(op)
            writes = self.operation_writes(op)
            out.append(SemanticFact(
                fact_id=self.ids.new(),
                kind=kind,
                source_lang="solidity",
                origin="slither_ir",
                function=str(function.get("function") or function.get("name") or ""),
                fact_role=semantic_fact_role(kind),
                contract=function.get("contract"),
                signature=function.get("signature"),
                stmt_refs=list(op.get("stmt_refs") or []),
                cfg_nodes=clean_list([op.get("cfg_node") or op.get("node_id")]),
                condition=op.get("condition"),
                lvalue=op.get("lvalue") or op.get("left"),
                rvalue=self.operation_rvalue(op),
                reads=reads,
                writes=writes,
                semantic=self.operation_semantic(op, kind, reads, writes),
                evidence={"slither": clean_dict(op)},
            ))
        return out

    def facts_from_sseir_control(self, fn: Any) -> list[SemanticFact]:
        """Build Solidity-side facts from SlithIR archived in FunctionSSEIR.control.

        S-SEIR keeps Slither blocks in the unified function CFG and replaces
        assembly nodes with local Yul CFG subgraphs. This method consumes only
        the Solidity blocks, so it does not duplicate Yul facts recovered by
        SSeirFactAdapter.
        """
        function = {
            "contract": getattr(fn, "contract", None),
            "function": getattr(fn, "function", None),
            "signature": getattr(fn, "signature", None),
        }
        operations: list[dict[str, Any]] = []
        block_conditions = self.control_block_conditions((getattr(fn, "control", {}) or {}).get("control_dependencies") or [])
        for block in (getattr(fn, "control", {}) or {}).get("blocks") or []:
            if block.get("kind") != "solidity":
                continue
            attrs = block.get("attrs") or {}
            block_id = block.get("block_id")
            stmt_refs = list(block.get("stmts") or [])
            archived = attrs.get("solidity_atomic_ops") or attrs.get("slithir_ssa") or attrs.get("slithir") or []
            using_ssa = bool(attrs.get("slithir_ssa"))
            for op in archived:
                if not isinstance(op, dict):
                    continue
                item = dict(op)
                item.setdefault("cfg_node", block_id)
                item.setdefault("node_id", attrs.get("slither_node_id"))
                item.setdefault("stmt_refs", stmt_refs)
                item.setdefault("ssa", using_ssa)
                if block_id in block_conditions:
                    item.setdefault("condition", block_conditions[block_id])
                operations.append(item)
        return self.facts_from_operations(function, operations)

    @staticmethod
    def control_block_conditions(control_dependencies: list[dict[str, Any]]) -> dict[str, str]:
        by_block: dict[str, list[str]] = {}
        for dependency in control_dependencies:
            dependent = dependency.get("dependent")
            predicate = dependency.get("predicate") or dependency.get("condition")
            if not dependent or not predicate:
                continue
            text = str(predicate)
            if text == "entry":
                continue
            bucket = by_block.setdefault(str(dependent), [])
            if text not in bucket:
                bucket.append(text)
        return {
            block_id: " && ".join(predicates)
            for block_id, predicates in by_block.items()
            if predicates
        }

    @staticmethod
    def function_record(function: Any) -> dict[str, Any]:
        contract = getattr(function, "contract_declarer", None) or getattr(function, "contract", None)
        return {
            "contract": str(getattr(contract, "name", "") or ""),
            "function": str(getattr(function, "name", function)),
            "name": str(getattr(function, "name", function)),
            "signature": str(getattr(function, "full_name", getattr(function, "signature_str", "")) or ""),
        }

    def operation_record(self, raw_op: Any, index: int = 0) -> dict[str, Any]:
        if isinstance(raw_op, dict):
            item = dict(raw_op)
            item.setdefault("order", index)
            return item
        item: dict[str, Any] = {
            "order": index,
            "kind": type(raw_op).__name__,
            "text": str(raw_op),
        }
        expression = getattr(raw_op, "expression", None)
        if expression is not None:
            item["source_expression"] = str(expression)
        node = getattr(raw_op, "node", None)
        if node is not None:
            item["node_id"] = str(getattr(node, "node_id", ""))
            item["node_type"] = str(getattr(node, "type", ""))
            item["node_expression"] = str(getattr(node, "expression", "") or "")
        for name in (
            "lvalue",
            "rvalue",
            "variable",
            "variable_left",
            "variable_right",
            "value",
            "destination",
            "call_value",
            "call_gas",
            "type_call",
            "function_name",
            "name",
            "index",
            "tuple",
            "structure_name",
            "contract_name",
            "array_type",
            "type",
        ):
            if hasattr(raw_op, name):
                value = getattr(raw_op, name)
                if value is not None:
                    item[name] = self.slither_value(value)
        for name in ("read", "arguments", "values", "init_values", "names", "rvalues", "nodes"):
            if hasattr(raw_op, name):
                values = getattr(raw_op, name)
                if values is not None:
                    if isinstance(values, (set, list, tuple)):
                        item[name] = [self.slither_value(value) for value in values]
                    else:
                        item[name] = [self.slither_value(values)]
        function = getattr(raw_op, "function", None)
        if function is not None:
            item["function"] = self.slither_callable(function)
        return item

    @classmethod
    def operation_semantic(cls, op: dict[str, Any], fact_kind: str, reads: list[Any], writes: list[Any]) -> dict[str, Any]:
        slithir_kind = op.get("kind") or op.get("type")
        semantic: dict[str, Any] = {
            "semantic_category": cls.semantic_category(fact_kind),
            "slithir_kind": slithir_kind,
            "slithir_text": op.get("text"),
            "operator": cls.scalar_text(op.get("operator") or op.get("type")),
            "arguments": cls.value_list_text(op.get("arguments")),
            "values": cls.value_list_text(op.get("values")),
            "reads": reads,
            "writes": writes,
        }
        if fact_kind in {"ExternalCall", "LowLevelCall", "LibraryCall", "InternalCall", "InternalDynamicCall", "BuiltinCall"}:
            semantic.update({
                "call_target": cls.value_text(op.get("destination") or op.get("function")),
                "function_name": cls.value_text(op.get("function_name") or op.get("name")),
                "call_value": cls.value_text(op.get("call_value")),
                "call_gas": cls.value_text(op.get("call_gas")),
                "call_type": cls.scalar_text(op.get("type_call")),
            })
        if fact_kind == "EventEmit":
            semantic["event"] = cls.value_text(op.get("name") or op.get("event"))
        if fact_kind == "ValueTransferCall":
            semantic.update({
                "destination": cls.value_text(op.get("destination")),
                "value": cls.value_text(op.get("call_value") or cls.last_argument(op)),
            })
        if fact_kind in {"IndexAccess", "MemberAccess", "LengthRead"}:
            semantic.update({
                "base": cls.value_text(op.get("variable_left") or op.get("value")),
                "index_or_member": cls.value_text(op.get("variable_right")),
            })
        if fact_kind in {"NewArray", "NewContract", "NewElementaryType", "NewStructure"}:
            semantic.update({
                "created_type": cls.value_text(op.get("array_type") or op.get("contract_name") or op.get("type") or op.get("structure_name")),
            })
        if fact_kind in {"Phi", "PhiCallback"}:
            semantic.update({
                "inputs": cls.value_list_text(op.get("rvalues")),
                "source_nodes": cls.value_list_text(op.get("nodes")),
            })
        if fact_kind == "TupleUnpack":
            semantic.update({
                "tuple": cls.value_text(op.get("tuple")),
                "index": cls.value_text(op.get("index")),
            })
        return clean_dict(semantic)

    @classmethod
    def operation_reads(cls, op: dict[str, Any]) -> list[Any]:
        explicit = op.get("reads") or op.get("read")
        if explicit:
            # Slither carries literalness on the operand object.  Retain a
            # constant as a call argument, but it is never an SSA read.
            return clean_list(cls.value_list_text([
                value for value in cls.operand_items(explicit) if not cls.is_constant_value(value)
            ]))
        kind = str(op.get("kind") or op.get("type") or "")
        fields_by_kind = {
            "Assignment": ["rvalue"],
            "Binary": ["variable_left", "variable_right"],
            "Unary": ["rvalue", "variable"],
            "TypeConversion": ["variable"],
            "Condition": ["value"],
            "Index": ["variable_left", "variable_right"],
            "Member": ["variable_left"],
            "Length": ["value"],
            "Delete": ["variable"],
            "Return": ["values"],
            "EventCall": ["arguments"],
            "HighLevelCall": ["destination", "arguments", "call_value", "call_gas"],
            "LowLevelCall": ["destination", "arguments", "call_value", "call_gas"],
            "LibraryCall": ["destination", "arguments", "call_value", "call_gas"],
            "InternalCall": ["arguments"],
            "InternalDynamicCall": ["function", "arguments", "call_value", "call_gas"],
            "SolidityCall": ["arguments"],
            "Send": ["destination", "call_value"],
            "Transfer": ["destination", "call_value"],
            "InitArray": ["init_values"],
            "NewArray": ["arguments"],
            "NewContract": ["arguments", "call_value"],
            "NewElementaryType": ["arguments"],
            "NewStructure": ["arguments"],
            "Phi": ["rvalues"],
            "PhiCallback": ["rvalues"],
            "Unpack": ["tuple"],
        }
        values: list[Any] = []
        for field_name in fields_by_kind.get(kind, ["rvalue", "expression"]):
            values.extend(cls.operand_items(op.get(field_name)))
        return clean_list(cls.value_list_text(values) or rough_reads(op.get("text") or op.get("expression")))

    @classmethod
    def operation_writes(cls, op: dict[str, Any]) -> list[Any]:
        explicit = op.get("writes") or op.get("write")
        if explicit:
            return clean_list(cls.value_list_text(explicit))
        lvalue = op.get("lvalue") or op.get("left")
        return clean_list([cls.value_text(lvalue)])

    @classmethod
    def operation_rvalue(cls, op: dict[str, Any]) -> Any:
        kind = str(op.get("kind") or op.get("type") or "")
        if kind == "Binary":
            left = cls.value_text(op.get("variable_left"))
            right = cls.value_text(op.get("variable_right"))
            operator = cls.scalar_text(op.get("operator") or op.get("type"))
            return clean_dict({"left": left, "operator": operator, "right": right})
        if kind == "Unary":
            return clean_dict({"operator": cls.scalar_text(op.get("operator") or op.get("type")), "value": cls.value_text(op.get("rvalue") or op.get("variable"))})
        if kind in {"HighLevelCall", "LowLevelCall", "LibraryCall", "InternalCall", "InternalDynamicCall", "SolidityCall"}:
            return clean_dict({
                "call_target": cls.value_text(op.get("destination") or op.get("function")),
                "function": cls.value_text(op.get("function_name") or op.get("name")),
                "arguments": cls.value_list_text(op.get("arguments")),
            })
        if kind == "EventCall":
            return clean_dict({"event": cls.value_text(op.get("name")), "arguments": cls.value_list_text(op.get("arguments"))})
        if kind == "Return":
            return cls.value_list_text(op.get("values"))
        if kind == "InitArray":
            return cls.value_list_text(op.get("init_values"))
        return op.get("rvalue") or op.get("right") or op.get("expression") or op.get("text")

    @staticmethod
    def semantic_category(fact_kind: str) -> str:
        if fact_kind in {"BranchCondition"}:
            return "control"
        if fact_kind in {"Return"}:
            return "return"
        if fact_kind in {"EventEmit"}:
            return "event"
        if fact_kind in {"ExternalCall", "LowLevelCall", "LibraryCall", "InternalCall", "InternalDynamicCall", "BuiltinCall", "ValueTransferCall", "NewContract"}:
            return "call"
        if fact_kind in {"IndexAccess", "MemberAccess", "LengthRead", "Delete", "NewArray", "NewElementaryType", "NewStructure"}:
            return "data_access"
        if fact_kind in {"Phi", "PhiCallback"}:
            return "ssa"
        if fact_kind == "Nop":
            return "noop"
        return "value"

    @classmethod
    def slither_value(cls, value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, dict):
            return value
        if isinstance(value, (list, tuple, set)):
            return [cls.slither_value(item) for item in value]
        kind = type(value).__name__
        node_id = getattr(value, "node_id", None)
        if node_id is not None:
            return clean_dict({"kind": kind, "node_id": int(node_id), "text": str(value)})
        non_ssa = getattr(value, "non_ssa_version", None)
        value_type = getattr(value, "type", None)
        return clean_dict({
            "kind": kind,
            "text": str(value),
            "name": str(getattr(value, "name", "")),
            "base_name": str(non_ssa) if non_ssa is not None else str(getattr(value, "name", "")),
            "type": str(value_type) if value_type is not None else None,
            "is_state": kind in {"StateIRVariable", "StateVariable"},
            "is_reference": "ReferenceVariable" in kind,
            "is_constant": kind == "Constant",
            "is_solidity_builtin": kind in {"SolidityVariable", "SolidityVariableComposed"},
        })

    @classmethod
    def slither_callable(cls, value: Any) -> dict[str, Any]:
        if isinstance(value, tuple):
            return {"kind": "tuple", "text": ".".join(str(item) for item in value)}
        contract = getattr(value, "contract", None) or getattr(value, "contract_declarer", None)
        return clean_dict({
            "kind": type(value).__name__,
            "name": str(getattr(value, "name", value)),
            "full_name": str(getattr(value, "full_name", getattr(value, "name", value))),
            "canonical_name": str(getattr(value, "canonical_name", "") or ""),
            "contract": str(getattr(contract, "name", "") or ""),
        })

    @classmethod
    def value_text(cls, value: Any) -> Any:
        if isinstance(value, dict):
            text = value.get("text") or value.get("name") or value.get("full_name") or value.get("canonical_name") or value
            if cls.is_constant_value(value) and "string" in str(value.get("type") or "").lower():
                return json.dumps(str(text), ensure_ascii=False)
            return text
        return value

    @staticmethod
    def is_constant_value(value: Any) -> bool:
        return isinstance(value, dict) and bool(value.get("is_constant"))

    @classmethod
    def value_list_text(cls, value: Any) -> list[Any]:
        return [cls.value_text(item) for item in cls.operand_items(value)]

    @classmethod
    def operand_items(cls, value: Any) -> list[Any]:
        """Flatten operand sequences without splitting typed operand objects."""
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            out: list[Any] = []
            for item in value:
                out.extend(cls.operand_items(item))
            return out
        return [value]

    @classmethod
    def scalar_text(cls, value: Any) -> Any:
        if isinstance(value, list):
            return cls.value_text(value[0]) if value else None
        return cls.value_text(value)

    @classmethod
    def last_argument(cls, op: dict[str, Any]) -> Any:
        arguments = op.get("arguments")
        if isinstance(arguments, list) and arguments:
            return arguments[-1]
        return None


def extract_function_dicts(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if is_function_dict(item)]
    if not isinstance(payload, dict):
        return []
    source_entry = payload.get("source_entry")
    if isinstance(source_entry, dict) and isinstance(source_entry.get("functions"), list):
        return [item for item in source_entry["functions"] if is_function_dict(item)]
    if isinstance(payload.get("functions"), list):
        return [item for item in payload["functions"] if is_function_dict(item)]
    out: list[dict[str, Any]] = []
    for value in payload.values():
        out.extend(extract_function_dicts(value))
    return out


def is_function_dict(value: Any) -> bool:
    return isinstance(value, dict) and "function" in value and "semantic_overlays" in value


def build_function_level_semantic_fact_payload(
    functions: list[Any],
    *,
    source: str | None = None,
    result_dir: str | None = None,
) -> dict[str, Any]:
    """Build facts through two isolated lifters, then organize them by CFG."""
    from s_seir_solidity_semantic_lifter import SoliditySemanticLifter
    from s_seir_yul_semantic_lifter import YulSemanticLifter
    from s_seir_semantic_fact_bridge import (
        FunctionSemanticInput,
        SemanticFactBridge,
        renumber_and_relink_facts,
    )

    solidity_lifter = SoliditySemanticLifter()
    yul_lifter = YulSemanticLifter()
    bridge = SemanticFactBridge()
    merged_facts: list[dict[str, Any]] = []
    for fn in functions:
        function_solidity_facts = [
            semantic_fact_public_view(item)
            for item in solidity_lifter.facts_from_function(fn)
        ]
        function_yul_facts = [
            semantic_fact_public_view(item)
            for item in yul_lifter.facts_from_function(fn)
        ]
        merged_facts.extend(bridge.merge_function_facts(
            FunctionSemanticInput.from_function(fn),
            function_solidity_facts,
            function_yul_facts,
        ))
    facts = renumber_and_relink_facts(merged_facts)
    solidity_facts = [fact for fact in facts if fact.get("source_lang") == "solidity"]
    yul_facts = [fact for fact in facts if fact.get("source_lang") == "yul"]
    return {
        "schema": "s-seir-function-semantic-facts/v3",
        "source": source,
        "result_dir": result_dir,
        "model_boundary": {
            "processing_unit": "function",
            "fact_granularity": "one_atomic_operation_per_fact",
            "ordering": "function-level CFG partial order plus block-local operation order",
            "solidity": "SolidityAtomicOperationExtractor produces sol_atom records; SoliditySemanticLifter only projects each atom to the common schema.",
            "yul": "S-SEIR alone analyzes Yul; YulSemanticLifter only projects completed S-SEIR results to the common schema.",
            "bridge": "SemanticFactBridge restores function-level CFG order while preserving S-SEIR path instances and path-compatible control predecessors after both sources already share one schema.",
        },
        "function_count": len(functions),
        "solidity_fact_count": len(solidity_facts),
        "yul_fact_count": len(yul_facts),
        "fact_count": len(facts),
        "solidity_facts": solidity_facts,
        "yul_facts": yul_facts,
        "facts": facts,
    }


def build_function_level_semantic_fact_ir_payload(
    functions: list[Any],
    *,
    source: str | None = None,
    result_dir: str | None = None,
) -> dict[str, Any]:
    """Build the canonical Semantic Fact IR.

    The legacy SemanticFact payload remains available for compatibility, but
    the new downstream boundary is this function.  It deliberately sends the
    bridge only high-level Solidity atoms and completed Yul overlay semantics.
    In particular, no S-SEIR EffectNode is an input to ``SemanticFactIRBridge``.
    """
    from s_seir_semantic_fact_ir import SemanticFactIRBridge
    from s_seir_solidity_semantic_lifter import SoliditySemanticLifter
    from s_seir_yul_semantic_lifter import YulSemanticLifter

    solidity_lifter = SoliditySemanticLifter()
    yul_lifter = YulSemanticLifter()
    by_function: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
    for function in functions:
        fn_dict = function.to_semantic_dict() if hasattr(function, "to_semantic_dict") else function
        if not isinstance(fn_dict, dict):
            continue
        function_id = str(getattr(function, "function_id", "") or fn_dict.get("function_id") or "")
        solidity = [semantic_fact_public_view(item) for item in solidity_lifter.facts_from_function(function)]
        # YulSemanticLifter emits semantic_provenance and strips all Effect
        # transport before the Fact IR bridge sees the value.
        yul = [semantic_fact_public_view(item) for item in yul_lifter.facts_from_function(function)]
        by_function[function_id] = (solidity, yul)
    return SemanticFactIRBridge().build_program(
        functions,
        by_function,
        source=source,
        result_dir=result_dir,
    )


def renumber_facts(facts: list[dict[str, Any]], prefix: str = "fact") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, fact in enumerate(facts, start=1):
        item = dict(fact)
        item["fact_id"] = f"{prefix}_{index}"
        out.append(item)
    return out


def semantic_fact_public_view(fact: dict[str, Any]) -> dict[str, Any]:
    """Keep final SemanticFact output free of display-only projection fields.

    ``solidity_like`` belongs to the optional audit/rendering views and to the
    full S-SEIR overlay model. It is intentionally stripped from
    semantic_facts.json so downstream consumers do not treat it as exact source
    semantics.
    """
    public = strip_key_recursive(fact, "solidity_like")
    return strip_key_recursive(public, "depends_on")


def strip_key_recursive(value: Any, key_to_strip: str) -> Any:
    if isinstance(value, dict):
        return clean_dict({
            key: strip_key_recursive(item, key_to_strip)
            for key, item in value.items()
            if key != key_to_strip
        })
    if isinstance(value, list):
        return [strip_key_recursive(item, key_to_strip) for item in value]
    return value


def dedupe_semantic_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse duplicate projected facts while preserving combined evidence.

    S-SEIR may keep multiple low-level effects for the same high-level behavior.
    That is useful in sseir.json, but the final SemanticFact layer should expose
    the behavior once and archive all contributing evidence on that fact.
    """
    out: list[dict[str, Any]] = []
    index_by_key: dict[str, int] = {}
    for fact in facts:
        key = semantic_fact_dedupe_key(fact)
        if key not in index_by_key:
            item = dict(fact)
            item["_dedupe_count"] = 1
            out.append(item)
            index_by_key[key] = len(out) - 1
            continue
        existing = out[index_by_key[key]]
        merge_fact_evidence(existing, fact)
    for item in out:
        count = item.pop("_dedupe_count", 1)
        if count > 1:
            evidence = dict(item.get("evidence") or {})
            evidence["deduped_fact_count"] = count
            item["evidence"] = clean_dict(evidence)
    return out


def semantic_fact_dedupe_key(fact: dict[str, Any]) -> str:
    stable = {
        key: fact.get(key)
        for key in (
            "atom_id",
            "operation_id",
            "kind",
            "source_lang",
            "origin",
            "function",
            "contract",
            "signature",
            "condition",
            "lvalue",
            "rvalue",
            "reads",
            "writes",
            "semantic",
        )
    }
    return json.dumps(json_ready(stable), sort_keys=True, ensure_ascii=False)


def merge_fact_evidence(existing: dict[str, Any], duplicate: dict[str, Any]) -> None:
    existing["_dedupe_count"] = int(existing.get("_dedupe_count") or 1) + 1
    existing["stmt_refs"] = clean_list(list(existing.get("stmt_refs") or []) + list(duplicate.get("stmt_refs") or []))
    existing["cfg_nodes"] = clean_list(list(existing.get("cfg_nodes") or []) + list(duplicate.get("cfg_nodes") or []))
    existing["reads"] = clean_list(list(existing.get("reads") or []) + list(duplicate.get("reads") or []))
    existing["writes"] = clean_list(list(existing.get("writes") or []) + list(duplicate.get("writes") or []))
    existing["evidence"] = merge_evidence(existing.get("evidence") or {}, duplicate.get("evidence") or {})


def merge_evidence(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key, value in right.items():
        if key not in merged:
            merged[key] = value
            continue
        if merged[key] == value:
            continue
        merged[key] = clean_list(flat_list(merged[key]) + flat_list(value))
    return clean_dict(merged)


def source_lang_for_refs(stmt_refs: list[str], stmt_lang: dict[str, str]) -> str:
    langs = {stmt_lang.get(ref) for ref in stmt_refs if stmt_lang.get(ref)}
    if langs == {"yul"}:
        return "yul"
    if langs == {"solidity"}:
        return "solidity"
    if "yul" in langs and "solidity" in langs:
        return "mixed"
    return "unknown"


def semantic_fact_role(kind: str) -> str:
    if kind in {
        "StateRead", "StateWrite", "EventEmit", "ExternalCall", "InternalCall",
        "InternalDynamicCall", "LibraryCall", "BuiltinCall", "LowLevelCall",
        "StaticCall", "DelegateCall", "ValueTransferCall", "PrecompileCall",
        "Revert", "Return",
    }:
        return "effect"
    if kind in {"Require", "BranchCondition"}:
        return "control"
    if kind in {"ValuePhi", "Phi", "PhiCallback"}:
        return "analysis_support"
    return "support"


def function_dict_has_yul(fn: dict[str, Any]) -> bool:
    return any(
        isinstance(stmt, dict) and stmt.get("lang") == "yul"
        for stmt in fn.get("source_statements") or []
    )


def cfg_nodes_for_refs(stmt_refs: list[str], fn: dict[str, Any]) -> list[str]:
    out: list[str] = []
    control = fn.get("control") or {}
    for block in control.get("blocks") or []:
        block_id = block.get("block_id")
        for stmt in block.get("stmts") or []:
            if stmt in stmt_refs and block_id:
                out.append(str(block_id))
    return list(dict.fromkeys(out))


def bracket_keys(text: str) -> list[str]:
    return re.findall(r"\[([^\]]+)\]", text or "")


def rough_reads(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return clean_list([item for part in value for item in rough_reads(part)])
    if isinstance(value, dict):
        return clean_list([item for part in value.values() for item in rough_reads(part)])
    # Work on complete access paths, not individual identifier fragments.
    # In particular, ``msg.data.length`` is one environment leaf rather than
    # user symbols named ``data`` and ``length``.  Removing quoted strings
    # first likewise prevents revert messages from becoming SSA variables.
    text = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', "", str(value))
    identifiers = re.findall(r"\b[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*(?:\[[^\]]+\])?", text)
    keywords = {
        "if", "else", "return", "true", "false", "uint256", "address", "bytes", "memory",
        "storage", "keccak256", "abi", "encode", "encodePacked", "low_bytes", "bytes20",
        "msg", "block", "tx", "sender", "timestamp", "gasleft", "require", "revert",
        "bool", "string", "type", "max", "returnDataSize",
    }
    out: list[str] = []
    for item in identifiers:
        root = item.split(".", 1)[0]
        if item in keywords or root in {"msg", "block", "tx"}:
            continue
        if item not in out:
            out.append(item)
    return out


def flat_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        out: list[Any] = []
        for item in value:
            out.extend(flat_list(item))
        return out
    if isinstance(value, tuple):
        return flat_list(list(value))
    if isinstance(value, dict):
        if "value" in value:
            return flat_list(value.get("value"))
        return clean_list(list(value.values()))
    return [value]


def pick(attrs: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return clean_dict({key: attrs.get(key) for key in keys})


def clean_list(values: Any) -> list[Any]:
    if values is None:
        return []
    if not isinstance(values, list):
        values = [values]
    out: list[Any] = []
    seen: set[str] = set()
    for value in values:
        if value is None or value == "":
            continue
        marker = json.dumps(json_ready(value), sort_keys=True, ensure_ascii=False)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(value)
    return out


def clean_dict(value: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): json_ready(item)
        for key, item in value.items()
        if item is not None and item != "" and item != [] and item != {}
    }


def json_ready(value: Any) -> Any:
    if is_dataclass(value):
        return json_ready(asdict(value))
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    return value


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_ready(payload), indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Project S-SEIR semantic overlays into unified SemanticFact records.")
    parser.add_argument("input", type=Path, help="S-SEIR JSON file. Supports pipeline array output and batch source output.")
    parser.add_argument("-o", "--output", type=Path, default=Path("outputs/semantic_facts.json"))
    args = parser.parse_args()
    payload = read_json(args.input)
    facts = SSeirFactAdapter().build_from_payload(payload)
    write_json(args.output, facts)
    print(f"Wrote {args.output}")
    print(f"Semantic facts: {len(facts.get('facts') or [])}")


if __name__ == "__main__":
    main()
