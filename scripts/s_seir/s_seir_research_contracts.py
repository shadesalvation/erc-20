#!/usr/bin/env python3
"""Shared frozen-v1 research artifact contracts.

This module owns only the common envelope, provenance, status, identity and
serialization protocol frozen by P0-T3.  It deliberately contains no control,
dependency, solver, slicing, order or transition algorithm.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping


SCHEMA_PATTERN = re.compile(r"^erc20-research/[a-z0-9]+(?:-[a-z0-9]+)*/v1$")

PROOF_VALUES = frozenset({"PROVEN", "CANDIDATE", "UNKNOWN"})
COMPLETION_VALUES = frozenset({"COMPLETE", "PARTIAL", "NOT_RUN"})
SOLVER_VALUES = frozenset({"NOT_RUN", "SAT", "UNSAT", "UNKNOWN", "TIMEOUT", "UNSUPPORTED"})
DIAGNOSTIC_CODES = frozenset({
    "UNKNOWN", "TIMEOUT", "UNSUPPORTED", "TRUNCATED", "FALLBACK",
    "OPAQUE", "INSUFFICIENT_EVIDENCE", "ERROR",
})
SOURCE_KINDS = frozenset({
    "SEMANTIC_NODE", "CFG_BLOCK", "CFG_EDGE", "DEFINITION", "USE",
    "STORAGE_ACCESS", "RECOVERY_DIAGNOSTIC",
})


class ContractValidationError(ValueError):
    """Raised when a frozen-v1 research artifact violates its contract."""


def canonicalize(value: Any) -> Any:
    """Return the frozen JSON-compatible canonical representation.

    Integer values become decimal strings so 256-bit values survive consumers
    with narrower JSON number implementations.  Boolean and null keep their
    JSON types.  Mapping keys are sorted by ``canonical_json``; set-like Python
    containers are sorted because they have no semantic order.
    """

    if is_dataclass(value):
        value = asdict(value)
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractValidationError("non-finite JSON numbers are forbidden")
        return value
    if isinstance(value, bytes):
        return "0x" + value.hex()
    if isinstance(value, Mapping):
        return {
            str(key): canonicalize(value[key])
            for key in sorted(value, key=lambda item: str(item))
        }
    if isinstance(value, (set, frozenset)):
        converted = [canonicalize(item) for item in value]
        return sorted(converted, key=canonical_json)
    if isinstance(value, (list, tuple)):
        return [canonicalize(item) for item in value]
    raise ContractValidationError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Serialize canonical JSON with stable keys and no insignificant space."""

    return json.dumps(
        canonicalize(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )


def content_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_identity(kind: str, identity_basis: Any) -> str:
    """Build ``kind:SHA256(canonical_JSON(identity_basis))``."""

    normalized = re.sub(r"[^a-z0-9]+", "-", str(kind).strip().lower()).strip("-")
    if not normalized:
        raise ContractValidationError("identity kind must not be empty")
    return f"{normalized}:{content_digest(identity_basis)}"


def producer_record(
    task: str,
    implementation_version: str,
    config: Any | None = None,
) -> dict[str, Any]:
    return canonicalize({
        "task": task,
        "implementation_version": implementation_version,
        "config_fingerprint": content_digest(config or {}),
    })


def diagnostic(
    code: str,
    reason: str,
    *,
    evidence_refs: Iterable[str] = (),
    affected_refs: Iterable[Any] = (),
    scope: Any = None,
) -> dict[str, Any]:
    if code not in DIAGNOSTIC_CODES:
        raise ContractValidationError(f"unknown diagnostic code: {code}")
    return canonicalize({
        "code": code,
        "reason": reason,
        "evidence_refs": list(evidence_refs),
        "affected_refs": list(affected_refs),
        "scope": scope,
    })


def analysis_status(
    *,
    proof: str,
    completion: str,
    solver: str = "NOT_RUN",
    diagnostics: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    result = canonicalize({
        "proof": proof,
        "completion": completion,
        "solver": solver,
        "diagnostics": list(diagnostics),
    })
    validate_analysis_status(result)
    return result


def source_ref(
    *,
    input_fingerprint: str,
    function_ref: Any,
    source_kind: str,
    locator: Any,
) -> dict[str, Any]:
    if source_kind not in SOURCE_KINDS:
        raise ContractValidationError(f"unknown SourceRef kind: {source_kind}")
    result = canonicalize({
        "input_fingerprint": input_fingerprint,
        "function_ref": function_ref,
        "source_kind": source_kind,
        "locator": locator,
    })
    validate_source_ref(result)
    return result


def evidence_record(
    *,
    kind: str,
    claim: Mapping[str, Any],
    premises: Iterable[Any],
    source_refs: Iterable[Mapping[str, Any]],
    artifact_refs: Iterable[str],
    rule: str,
    scope: Any,
    assumptions: Iterable[Any],
    result: Any,
    reason_code: str,
    producer: Mapping[str, Any],
    status: Mapping[str, Any],
) -> dict[str, Any]:
    body = canonicalize({
        "kind": kind,
        "claim": claim,
        "premises": list(premises),
        "source_refs": list(source_refs),
        "artifact_refs": list(artifact_refs),
        "rule": rule,
        "scope": scope,
        "assumptions": list(assumptions),
        "result": result,
        "reason_code": reason_code,
        "producer": producer,
        "status": status,
    })
    record = {"id": stable_identity("evidence", body), **body}
    validate_evidence_record(record)
    return record


def artifact_envelope(
    *,
    schema: str,
    artifact_id: str,
    function_ref: Any,
    input_fingerprint: str,
    producer: Mapping[str, Any],
    evidence_refs: Iterable[str],
    status: Mapping[str, Any],
    payload: Mapping[str, Any],
    extensions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = canonicalize({
        "schema": schema,
        "id": artifact_id,
        "function_ref": function_ref,
        "input_fingerprint": input_fingerprint,
        "producer": producer,
        "evidence_refs": list(evidence_refs),
        "status": status,
        "payload": payload,
        "extensions": dict(extensions or {}),
    })
    validate_artifact_envelope(result)
    return result


def serialize_artifact(value: Any) -> str:
    """Return canonical UTF-8 JSON text (without a trailing newline)."""

    return canonical_json(value)


def validate_analysis_status(value: Any) -> None:
    _require_mapping(value, "AnalysisStatus")
    _require_exact_keys(value, {"proof", "completion", "solver", "diagnostics"}, "AnalysisStatus")
    if value["proof"] not in PROOF_VALUES:
        raise ContractValidationError(f"invalid proof status: {value['proof']}")
    if value["completion"] not in COMPLETION_VALUES:
        raise ContractValidationError(f"invalid completion status: {value['completion']}")
    if value["solver"] not in SOLVER_VALUES:
        raise ContractValidationError(f"invalid solver status: {value['solver']}")
    if not isinstance(value["diagnostics"], list):
        raise ContractValidationError("AnalysisStatus diagnostics must be a list")
    for item in value["diagnostics"]:
        _require_mapping(item, "diagnostic")
        _require_exact_keys(
            item, {"code", "reason", "evidence_refs", "affected_refs", "scope"}, "diagnostic"
        )
        if item["code"] not in DIAGNOSTIC_CODES:
            raise ContractValidationError(f"invalid diagnostic code: {item['code']}")
        if not isinstance(item["reason"], str) or not item["reason"]:
            raise ContractValidationError("diagnostic reason must be non-empty")
        if not isinstance(item["evidence_refs"], list) or not isinstance(item["affected_refs"], list):
            raise ContractValidationError("diagnostic refs must be lists")


def validate_source_ref(value: Any) -> None:
    _require_mapping(value, "SourceRef")
    _require_exact_keys(
        value, {"input_fingerprint", "function_ref", "source_kind", "locator"}, "SourceRef"
    )
    if not isinstance(value["input_fingerprint"], str) or not value["input_fingerprint"]:
        raise ContractValidationError("SourceRef input_fingerprint must be non-empty")
    if value["source_kind"] not in SOURCE_KINDS:
        raise ContractValidationError(f"invalid SourceRef kind: {value['source_kind']}")
    _require_mapping(value["locator"], "SourceRef.locator")


def validate_evidence_record(value: Any) -> None:
    _require_mapping(value, "EvidenceRecord")
    required = {
        "id", "kind", "claim", "premises", "source_refs", "artifact_refs", "rule",
        "scope", "assumptions", "result", "reason_code", "producer", "status",
    }
    _require_exact_keys(value, required, "EvidenceRecord")
    if not isinstance(value["id"], str) or not value["id"].startswith("evidence:"):
        raise ContractValidationError("EvidenceRecord id must use evidence:<digest>")
    _require_mapping(value["claim"], "EvidenceRecord.claim")
    if set(value["claim"]) != {"predicate", "operands"}:
        raise ContractValidationError("EvidenceRecord.claim requires predicate and operands")
    for key in ("premises", "source_refs", "artifact_refs", "assumptions"):
        if not isinstance(value[key], list):
            raise ContractValidationError(f"EvidenceRecord.{key} must be a list")
    for item in value["source_refs"]:
        validate_source_ref(item)
    _validate_producer(value["producer"])
    validate_analysis_status(value["status"])


def validate_artifact_envelope(value: Any) -> None:
    _require_mapping(value, "ArtifactEnvelope")
    required = {
        "schema", "id", "function_ref", "input_fingerprint", "producer",
        "evidence_refs", "status", "payload", "extensions",
    }
    _require_exact_keys(value, required, "ArtifactEnvelope")
    if not isinstance(value["schema"], str) or not SCHEMA_PATTERN.fullmatch(value["schema"]):
        raise ContractValidationError(f"invalid research schema: {value['schema']!r}")
    if not isinstance(value["id"], str) or ":" not in value["id"]:
        raise ContractValidationError("ArtifactEnvelope id must be kind:<digest>")
    if not isinstance(value["input_fingerprint"], str) or not value["input_fingerprint"]:
        raise ContractValidationError("ArtifactEnvelope input_fingerprint must be non-empty")
    _validate_producer(value["producer"])
    if not isinstance(value["evidence_refs"], list):
        raise ContractValidationError("ArtifactEnvelope evidence_refs must be a list")
    validate_analysis_status(value["status"])
    _require_mapping(value["payload"], "ArtifactEnvelope.payload")
    _require_mapping(value["extensions"], "ArtifactEnvelope.extensions")


def _validate_producer(value: Any) -> None:
    _require_mapping(value, "producer")
    _require_exact_keys(value, {"task", "implementation_version", "config_fingerprint"}, "producer")
    if not all(isinstance(value[key], str) and value[key] for key in value):
        raise ContractValidationError("producer fields must be non-empty strings")


def _require_mapping(value: Any, label: str) -> None:
    if not isinstance(value, Mapping):
        raise ContractValidationError(f"{label} must be an object")


def _require_exact_keys(value: Mapping[str, Any], keys: set[str], label: str) -> None:
    missing = keys.difference(value)
    extra = set(value).difference(keys)
    if missing or extra:
        raise ContractValidationError(
            f"{label} fields mismatch; missing={sorted(missing)}, extra={sorted(extra)}"
        )
