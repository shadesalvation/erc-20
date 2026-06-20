#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
slice_from_anchor_json.py

Build a first-version backward/forward security slice from
erc20-security-anchor-v2 JSON produced by erc20_security_anchor_extractor.py.

This is a JSON-layer slicer, not a complete PDG slicer. Use its output as
candidate security slices for manual review, not as automatic code deletion.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

DEFAULT_ANCHOR_LABELS = {"CORE_ANCHOR"}
DEFAULT_FORWARD_SOURCES = {"msg.sender", "_from", "_to", "_amount", "_owner", "_spender", "tx.origin"}


def as_list(x: Any) -> List[Any]:
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


def node_key(node: Dict[str, Any]) -> str:
    return f"{node.get('function')}::node{node.get('node_id')}"


def parse_source_mapping(sm: str) -> Tuple[Optional[int], Optional[int]]:
    """Parse simple source_mapping strings like path/Token.sol#96 or #67-69."""
    if not sm or "#" not in sm:
        return None, None
    tail = sm.rsplit("#", 1)[-1].strip()
    m = re.match(r"^(\d+)(?:-(\d+))?$", tail)
    if not m:
        return None, None
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) else start
    return start, end


def source_start(node: Dict[str, Any]) -> int:
    s, _ = parse_source_mapping(str(node.get("source_mapping", "")))
    if s is not None:
        return s
    nid = node.get("node_id")
    return int(nid) if isinstance(nid, int) else 10**12


def normalize_node(node: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "key": node_key(node),
        "entrypoint": node.get("entrypoint"),
        "function": node.get("function"),
        "function_name": node.get("function_name"),
        "node_id": node.get("node_id"),
        "node_type": node.get("node_type"),
        "op_type": node.get("op_type"),
        "candidate_security_type": node.get("candidate_security_type"),
        "relevance_label": node.get("relevance_label"),
        "confidence": node.get("confidence"),
        "needs_human_review": node.get("needs_human_review"),
        "evidence": as_list(node.get("evidence")),
        "expression": node.get("expression", ""),
        "statement": node.get("statement") or node.get("text") or "",
        "irs": as_list(node.get("irs")),
        "state_variables_read": as_list(node.get("state_variables_read")),
        "state_variables_written": as_list(node.get("state_variables_written")),
        "variables_read": as_list(node.get("variables_read")),
        "variables_written": as_list(node.get("variables_written")),
        "source_mapping": node.get("source_mapping", ""),
    }


def build_indexes(raw_nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
    norm_nodes = [normalize_node(n) for n in raw_nodes]
    by_key = {n["key"]: n for n in norm_nodes}

    by_func: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for n in norm_nodes:
        by_func[n.get("function")].append(n)

    for f in by_func:
        by_func[f].sort(key=lambda n: (source_start(n), n.get("node_id") if isinstance(n.get("node_id"), int) else 10**9))

    defs_by_func_var: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    uses_by_func_var: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)

    for f, nodes in by_func.items():
        for n in nodes:
            for v in n.get("variables_written", []):
                defs_by_func_var[(f, str(v))].append(n)
            for v in n.get("variables_read", []):
                uses_by_func_var[(f, str(v))].append(n)

    return {
        "nodes": norm_nodes,
        "by_key": by_key,
        "by_func": by_func,
        "defs_by_func_var": defs_by_func_var,
        "uses_by_func_var": uses_by_func_var,
    }


def previous_definitions(var: str, anchor_node: Dict[str, Any], indexes: Dict[str, Any]) -> List[Dict[str, Any]]:
    f = anchor_node.get("function")
    anchor_pos = source_start(anchor_node)
    defs = indexes["defs_by_func_var"].get((f, str(var)), [])
    return [d for d in defs if source_start(d) <= anchor_pos and d["key"] != anchor_node["key"]]


def enclosing_or_preceding_ifs(anchor_node: Dict[str, Any], indexes: Dict[str, Any], max_preceding: int = 4) -> List[Dict[str, Any]]:
    f = anchor_node.get("function")
    anchor_s, _ = parse_source_mapping(str(anchor_node.get("source_mapping", "")))
    if_nodes = [
        n for n in indexes["by_func"].get(f, [])
        if "NodeType.IF" in str(n.get("node_type", "")) or n.get("op_type") == "IF"
    ]

    selected: List[Dict[str, Any]] = []
    selected_keys: Set[str] = set()

    for n in if_nodes:
        s, e = parse_source_mapping(str(n.get("source_mapping", "")))
        if s is not None and e is not None and anchor_s is not None and s <= anchor_s <= e:
            selected.append(n)
            selected_keys.add(n["key"])

    preceding = [n for n in if_nodes if source_start(n) <= source_start(anchor_node)]
    for n in preceding[-max_preceding:]:
        if n["key"] not in selected_keys:
            selected.append(n)
            selected_keys.add(n["key"])

    return selected


def backward_slice_from_anchor(anchor: Dict[str, Any], indexes: Dict[str, Any], max_depth: int = 8) -> Dict[str, Any]:
    start = normalize_node(anchor)
    included: Dict[str, Dict[str, Any]] = {start["key"]: start}
    edges: List[Dict[str, str]] = []
    q: deque[Tuple[Dict[str, Any], int]] = deque([(start, 0)])

    while q:
        current, depth = q.popleft()
        if depth >= max_depth:
            continue

        vars_to_trace = set(map(str, current.get("variables_read", [])))
        vars_to_trace.update(map(str, current.get("state_variables_read", [])))

        for var in sorted(vars_to_trace):
            for d in previous_definitions(var, current, indexes):
                if d["key"] not in included:
                    included[d["key"]] = d
                    q.append((d, depth + 1))
                edges.append({"type": "data_dependency", "from": d["key"], "to": current["key"], "via": var})

    for c in enclosing_or_preceding_ifs(start, indexes):
        if c["key"] not in included:
            included[c["key"]] = c
        edges.append({"type": "possible_control_dependency", "from": c["key"], "to": start["key"], "via": "IF/source-range-or-preceding"})

    return {
        "anchor": start,
        "slice_nodes": sorted(included.values(), key=lambda n: (n.get("function") or "", source_start(n), n.get("node_id") or 0)),
        "slice_edges": edges,
    }


def forward_slice_from_sources(raw_nodes: List[Dict[str, Any]], indexes: Dict[str, Any], sources: Set[str], anchor_keys: Set[str], max_depth: int = 8) -> Dict[str, Any]:
    starts = []
    for n in indexes["nodes"]:
        reads = set(map(str, n.get("variables_read", [])))
        if reads & sources:
            starts.append(n)

    included: Dict[str, Dict[str, Any]] = {n["key"]: n for n in starts}
    edges: List[Dict[str, str]] = []
    reached_anchors: Set[str] = set()
    q: deque[Tuple[Dict[str, Any], int]] = deque((n, 0) for n in starts)

    while q:
        current, depth = q.popleft()
        if current["key"] in anchor_keys:
            reached_anchors.add(current["key"])
        if depth >= max_depth:
            continue

        f = current.get("function")
        cur_pos = source_start(current)
        for v in map(str, current.get("variables_written", [])):
            for use in indexes["uses_by_func_var"].get((f, v), []):
                if source_start(use) < cur_pos:
                    continue
                if use["key"] not in included:
                    included[use["key"]] = use
                    q.append((use, depth + 1))
                edges.append({"type": "forward_data_flow", "from": current["key"], "to": use["key"], "via": v})
                if use["key"] in anchor_keys:
                    reached_anchors.add(use["key"])

    return {
        "sources": sorted(sources),
        "slice_nodes": sorted(included.values(), key=lambda n: (n.get("function") or "", source_start(n), n.get("node_id") or 0)),
        "slice_edges": edges,
        "reached_anchor_keys": sorted(reached_anchors),
    }


def select_reviewed_anchors(anchors: List[Dict[str, Any]], anchor_labels: Set[str], include_needs_review: bool = True) -> List[Dict[str, Any]]:
    selected = []
    for a in anchors:
        if a.get("relevance_label") not in anchor_labels:
            continue
        if not include_needs_review and a.get("needs_human_review") is True:
            continue
        selected.append(a)
    return selected


def process_report(report: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    output = {
        "source": report.get("source"),
        "input_schema_version": report.get("schema_version"),
        "schema_version": "security-slices-v1",
        "notes": [
            "Backward slices are approximate JSON-layer def-use slices.",
            "Control dependencies are conservative approximations based on IF source ranges and preceding IF nodes.",
            "Use this output as a candidate slice for manual review, not as automatic code deletion.",
        ],
        "contracts": [],
    }

    anchor_labels = set(args.anchor_labels.split(",")) if args.anchor_labels else DEFAULT_ANCHOR_LABELS
    forward_sources = set(args.forward_sources.split(",")) if args.forward_sources else DEFAULT_FORWARD_SOURCES

    for contract in report.get("contracts", []):
        contract_out = {"contract": contract.get("contract"), "entrypoint_slices": []}

        for ep in contract.get("entrypoint_reports", []):
            raw_nodes = ep.get("raw_nodes", [])
            anchor_candidates = ep.get("anchor_candidates", [])
            indexes = build_indexes(raw_nodes)

            anchors = select_reviewed_anchors(anchor_candidates, anchor_labels=anchor_labels, include_needs_review=args.include_needs_review)
            anchor_keys = {node_key(a) for a in anchors}

            backward_slices = [backward_slice_from_anchor(a, indexes, max_depth=args.max_depth) for a in anchors]

            forward_slice = None
            if args.forward:
                forward_slice = forward_slice_from_sources(
                    raw_nodes=raw_nodes,
                    indexes=indexes,
                    sources=forward_sources,
                    anchor_keys=anchor_keys,
                    max_depth=args.max_depth,
                )

            contract_out["entrypoint_slices"].append({
                "entrypoint": ep.get("entrypoint"),
                "reachable_functions": ep.get("reachable_functions", []),
                "selected_anchor_count": len(anchors),
                "backward_slices": backward_slices,
                "forward_slice": forward_slice,
                "stats": {
                    "raw_node_count": len(raw_nodes),
                    "anchor_candidate_count": len(anchor_candidates),
                    "selected_anchor_count": len(anchors),
                },
            })

        output["contracts"].append(contract_out)

    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_json", help="Path to erc20-security-anchor-v2 JSON")
    parser.add_argument("-o", "--output", default="slices_v1.json", help="Output path")
    parser.add_argument("--max-depth", type=int, default=8, help="Max backward/forward tracing depth")
    parser.add_argument("--anchor-labels", default="CORE_ANCHOR", help="Comma-separated relevance_label values used as slicing anchors")
    parser.add_argument("--include-needs-review", action="store_true", help="Include anchors with needs_human_review=true. Recommended for high recall.")
    parser.add_argument("--forward", action="store_true", help="Also compute conservative forward slice from source variables.")
    parser.add_argument("--forward-sources", default="msg.sender,_from,_to,_amount,_owner,_spender,tx.origin", help="Comma-separated source variables for forward tracing")
    args = parser.parse_args()

    with open(args.input_json, "r", encoding="utf-8") as f:
        report = json.load(f)

    out = process_report(report, args)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"[OK] wrote {out_path}")


if __name__ == "__main__":
    main()
