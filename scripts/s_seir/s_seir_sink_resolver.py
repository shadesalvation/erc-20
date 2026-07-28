#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SinkArgResolution:
    expr: str
    normalized: str | None = None
    ssa_key: str | None = None
    memory_slice: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class SinkPathResolution:
    path_id: str
    condition: str | None
    arg_resolutions: dict[str, SinkArgResolution]
    status: str = "resolved"
    reason: str | None = None


@dataclass
class SinkResolution:
    sink_id: str
    stmt_refs: list[str]
    cfg_node_id: int | None
    sink_kind: str
    args: list[str]
    path_resolutions: list[SinkPathResolution]
    path_sensitive: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "sink_id": self.sink_id,
            "stmt_refs": self.stmt_refs,
            "cfg_node_id": self.cfg_node_id,
            "sink_kind": self.sink_kind,
            "args": self.args,
            "path_sensitive": self.path_sensitive,
            "path_resolutions": [
                {
                    "path_id": path.path_id,
                    "condition": path.condition,
                    "status": path.status,
                    "reason": path.reason,
                    "arg_resolutions": {
                        name: {
                            "expr": arg.expr,
                            "normalized": arg.normalized,
                            "ssa_key": arg.ssa_key,
                            "memory_slice": arg.memory_slice,
                            "notes": arg.notes,
                        }
                        for name, arg in path.arg_resolutions.items()
                    },
                }
                for path in self.path_resolutions
            ],
        }


class SinkResolver:
    """Unified S-SEIR sink query layer.

    Effects still carry the original MemorySSA query fields for compatibility.
    This resolver normalizes those fields into one path-sensitive structure so
    downstream overlays do not invent separate matching rules.
    """

    MEMORY_RANGE_BY_KIND = {
        "MemoryHash": [("slot", "memory_read", "ptr", "size", "hash_input")],
        "EventLog": [("data", "data_memory", "data_ptr", "data_size", "event_data")],
        "Return": [("payload", "payload_memory", "payload_ptr", "payload_size", "return_payload")],
        "Revert": [("payload", "payload_memory", "payload_ptr", "payload_size", "revert_payload")],
        "Call": [("input", "input_memory", "input_ptr", "input_size", "call_input")],
        "StaticCall": [("input", "input_memory", "input_ptr", "input_size", "staticcall_input")],
        "DelegateCall": [("input", "input_memory", "input_ptr", "input_size", "delegatecall_input")],
        "CallCode": [("input", "input_memory", "input_ptr", "input_size", "callcode_input")],
    }

    def attach_all(self, effects: list[Any]) -> list[Any]:
        for effect in effects:
            resolution = self.resolve_effect(effect)
            if resolution:
                attrs = getattr(effect, "attrs", None)
                if isinstance(attrs, dict):
                    attrs["sink_resolution"] = resolution.to_dict()
        return effects

    def resolve_effect(self, effect: Any) -> SinkResolution | None:
        kind = str(getattr(effect, "kind", "") or "")
        if kind == "MemoryHash":
            return self.resolve_memory_hash(effect)
        specs = self.MEMORY_RANGE_BY_KIND.get(kind)
        if not specs:
            return None
        return self.resolve_memory_range_effect(effect, specs)

    def resolve_memory_hash(self, effect: Any) -> SinkResolution | None:
        return build_memory_range_resolution(
            effect,
            sink_kind="MemoryHash",
            role_specs=[("slot", "memory_read", "ptr", "size", "hash_input")],
            hash_mode=True,
        )

    def resolve_memory_range_effect(
        self,
        effect: Any,
        specs: list[tuple[str, str, str, str, str]],
    ) -> SinkResolution | None:
        return build_memory_range_resolution(effect, sink_kind=str(getattr(effect, "kind", "") or "Sink"), role_specs=specs)


def packed_hash_sink_resolution(effect: Any, *, sink_kind: str = "MemoryHash") -> SinkResolution | None:
    """Build path-sensitive resolution for a keccak over memory bytes.

    The MemorySSA query already owns CFG/path reasoning. This adapter only keeps
    per-path byte slices distinct so later overlays never key by expression text
    alone.
    """

    return build_memory_range_resolution(
        effect,
        sink_kind=sink_kind,
        role_specs=[("slot", "memory_read", "ptr", "size", "hash_input")],
        hash_mode=True,
    )


def build_memory_range_resolution(
    effect: Any,
    *,
    sink_kind: str,
    role_specs: list[tuple[str, str, str, str, str]],
    hash_mode: bool = False,
) -> SinkResolution | None:
    attrs = getattr(effect, "attrs", {}) or {}
    per_role_paths: dict[str, list[SinkPathResolution]] = {}
    path_order: list[str | None] = []
    unresolved = False

    for role, memory_attr, ptr_attr, size_attr, semantic_role in role_specs:
        memory_read = attrs.get(memory_attr) or {}
        paths = memory_range_path_resolutions(
            effect,
            memory_read=memory_read,
            role=role,
            ptr=attrs.get(ptr_attr),
            size=attrs.get(size_attr),
            semantic_role=semantic_role,
            hash_mode=hash_mode,
        )
        if not paths:
            continue
        per_role_paths[role] = paths
        for path in paths:
            if path.status != "resolved":
                unresolved = True
            if path.condition not in path_order:
                path_order.append(path.condition)

    if not per_role_paths:
        return None

    cfg_node_id = attrs.get("cfg_node_id")
    stmt_refs = list(getattr(effect, "stmt_refs", []) or [])
    sink_id = f"sink_{'_'.join(stmt_refs) or getattr(effect, 'effect_id', 'effect')}_{sink_kind}_{cfg_node_id}"
    merged: list[SinkPathResolution] = []
    for index, condition in enumerate(path_order or [None]):
        arg_resolutions: dict[str, SinkArgResolution] = {}
        status = "resolved"
        reason = None
        for role, paths in per_role_paths.items():
            selected = next((path for path in paths if path.condition == condition), None)
            if selected is None and len(paths) == 1:
                selected = paths[0]
            if selected is None:
                status = "unresolved"
                reason = "missing_role_path_resolution"
                continue
            if selected.status != "resolved":
                status = selected.status
                reason = selected.reason
            arg_resolutions.update(selected.arg_resolutions)
        merged.append(SinkPathResolution(
            path_id=f"path_{index}",
            condition=condition,
            arg_resolutions=arg_resolutions,
            status=status,
            reason=reason,
        ))

    signatures = {path_resolution_signature(path) for path in merged if path.status == "resolved"}
    conditions = {path.condition for path in merged}
    return SinkResolution(
        sink_id=sink_id,
        stmt_refs=stmt_refs,
        cfg_node_id=cfg_node_id if isinstance(cfg_node_id, int) else None,
        sink_kind=sink_kind,
        args=sink_args(attrs, role_specs),
        path_resolutions=merged,
        path_sensitive=len(signatures) > 1 or any(conditions) or unresolved,
    )


def memory_range_path_resolutions(
    effect: Any,
    *,
    memory_read: dict[str, Any],
    role: str,
    ptr: Any,
    size: Any,
    semantic_role: str,
    hash_mode: bool = False,
) -> list[SinkPathResolution]:
    byte_slice = memory_read.get("byte_slice") or {}
    if not byte_slice or not byte_slice.get("complete"):
        return []

    path_items = byte_slice.get("path_slices") or []
    if not path_items and byte_slice.get("slices"):
        path_items = [{
            "path": "entry",
            "complete": byte_slice.get("complete"),
            "slices": byte_slice.get("slices"),
        }]
    if not path_items:
        return []

    attrs = getattr(effect, "attrs", {}) or {}
    expr = str(attrs.get("value") if hash_mode else f"{ptr}:{size}")
    slot_key = first_hash_result_key(effect)
    resolutions: list[SinkPathResolution] = []
    seen: set[tuple[str | None, tuple[str, ...]]] = set()

    for index, item in enumerate(path_items):
        slices = item.get("slices") or []
        if not item.get("complete") or not slices or any(not part.get("extraction") for part in slices):
            resolutions.append(SinkPathResolution(
                path_id=f"path_{index}",
                condition=path_condition(item.get("path")),
                status="unresolved",
                reason="incomplete_memory_slice",
                arg_resolutions={
                    role: SinkArgResolution(
                        expr=expr,
                        ssa_key=slot_key,
                        memory_slice=item,
                        notes=[semantic_role, "incomplete_memory_slice"],
                    )
                },
            ))
            continue
        parts = tuple(str(part.get("extraction")) for part in slices)
        if hash_mode and len(parts) == 1 and int(byte_slice.get("size") or 0) == 32:
            continue
        condition = path_condition(item.get("path"))
        key = (condition, parts)
        if key in seen:
            continue
        seen.add(key)
        normalized = (
            f"keccak256(abi.encodePacked({', '.join(parts)}))"
            if hash_mode
            else f"MemorySlice({', '.join(parts)})"
        )
        resolutions.append(SinkPathResolution(
            path_id=f"path_{index}",
            condition=condition,
            arg_resolutions={
                role: SinkArgResolution(
                    expr=expr,
                    normalized=normalized,
                    ssa_key=slot_key,
                    memory_slice={
                        "query_kind": "PathMemoryByteSlice",
                        "path": item.get("path"),
                        "complete": item.get("complete"),
                        "slices": slices,
                    },
                    notes=[semantic_role, "byte_axis_memory_slice", "path_sensitive_sink"],
                )
            },
        ))

    return resolutions


def path_resolution_signature(path: SinkPathResolution) -> tuple[Any, ...]:
    return tuple(
        (name, arg.normalized, slice_signature(arg.memory_slice))
        for name, arg in sorted(path.arg_resolutions.items())
    )


def slice_signature(memory_slice: dict[str, Any] | None) -> tuple[Any, ...]:
    return tuple(
        (
            item.get("query_offset"),
            item.get("size"),
            item.get("extraction"),
            item.get("source_version"),
            item.get("source_node_id"),
        )
        for item in (memory_slice or {}).get("slices") or []
    )


def sink_args(attrs: dict[str, Any], role_specs: list[tuple[str, str, str, str, str]]) -> list[str]:
    out: list[str] = []
    for _role, _memory_attr, ptr_attr, size_attr, _semantic_role in role_specs:
        out.extend([str(attrs.get(ptr_attr) or ""), str(attrs.get(size_attr) or "")])
    return out


def first_hash_result_key(effect: Any) -> str | None:
    attrs = getattr(effect, "attrs", {}) or {}
    value = attrs.get("value")
    versions = attrs.get("value_versions") or {}
    if value and isinstance(versions, dict):
        items = versions.get(str(value)) or []
        if items:
            return str(items[0])
    inline = attrs.get("inline_slot_key")
    return str(inline) if inline else (str(value) if value else None)


def path_condition(path: Any) -> str | None:
    text = str(path or "").strip()
    if not text or text == "entry":
        return None
    return text
