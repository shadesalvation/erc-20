#!/usr/bin/env python3
"""
ERC-20 Security Anchor Extractor based on Slither.

Goal:
  1) Parse Solidity with Slither.
  2) Identify ERC-20 core entrypoints: transfer / transferFrom / approve.
  3) Expand reachable internal functions robustly.
  4) Extract raw CFG/IR facts and low-level operations.
  5) Produce candidate security labels, confidence, evidence, and human-review flags.

This script does NOT delete code and does NOT claim final semantic correctness.
It outputs high-recall candidates for manual checking / later slicing.

Usage:
  python erc20_security_anchor_extractor.py contracts/Token.sol -o outputs/anchors.json

Requirements:
  pip install slither-analyzer
  solc-select install <version>
  solc-select use <version>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

try:
    from slither import Slither
except Exception as exc:  # pragma: no cover
    print("[ERROR] Cannot import Slither. Install it with: pip install slither-analyzer", file=sys.stderr)
    raise exc


CORE_ENTRYPOINTS = {
    "transfer(address,uint256)",
    "transferFrom(address,address,uint256)",
    "approve(address,uint256)",
}

ALL_ERC20_ENTRYPOINTS = CORE_ENTRYPOINTS | {
    "totalSupply()",
    "balanceOf(address)",
    "allowance(address,address)",
}

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
APPROVAL_TOPIC = "0x8c5be1e5ebec7d5bd14f714f4f5ec7c46ab3db174da78c3f62f10b71e9aeeaa0"

POLICY_TERMS = {
    "owner", "admin", "role", "blacklist", "whitelist", "excluded", "exclude",
    "fee", "tax", "limit", "maxtx", "maxwallet", "tradingopen", "open",
    "pair", "router", "controller", "oracle", "bot", "cooldown",
}

OP_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    ("SSTORE", re.compile(r"\bsstore\b|\bSOLIDITY_CALL\s+sstore\b", re.I)),
    ("SLOAD", re.compile(r"\bsload\b|\bSOLIDITY_CALL\s+sload\b", re.I)),
    ("MSTORE", re.compile(r"\bmstore\b|\bSOLIDITY_CALL\s+mstore\b", re.I)),
    ("KECCAK256", re.compile(r"\bkeccak256\b", re.I)),
    ("STATICCALL", re.compile(r"\bstaticcall\b", re.I)),
    ("DELEGATECALL", re.compile(r"\bdelegatecall\b", re.I)),
    ("CALL", re.compile(r"(?<!static)(?<!delegate)\bcall\b|\.call\b", re.I)),
    ("LOG4", re.compile(r"\blog4\b", re.I)),
    ("LOG3", re.compile(r"\blog3\b", re.I)),
    ("REVERT", re.compile(r"\brevert\b", re.I)),
    ("REQUIRE", re.compile(r"\brequire\b", re.I)),
    ("ASSERT", re.compile(r"\bassert\b", re.I)),
    ("RETURN", re.compile(r"\breturn\b", re.I)),
]


def safe_iter(x: Any) -> List[Any]:
    if x is None:
        return []
    try:
        return list(x)
    except TypeError:
        return [x]


def obj_name(x: Any) -> str:
    return str(getattr(x, "name", x))


def obj_full_name(x: Any) -> str:
    return str(getattr(x, "full_name", getattr(x, "name", x)))


def canonical_name(x: Any) -> str:
    return str(getattr(x, "canonical_name", obj_full_name(x)))


def var_names(xs: Iterable[Any]) -> List[str]:
    return sorted({obj_name(x) for x in xs if x is not None})


def string_list(xs: Iterable[Any]) -> List[str]:
    return [str(x) for x in xs if x is not None]


def node_expression(node: Any) -> str:
    expr = getattr(node, "expression", None)
    return "" if expr is None else str(expr)


def node_irs(node: Any) -> List[str]:
    irs = safe_iter(getattr(node, "irs", []))
    return [str(ir) for ir in irs]


def node_text(node: Any) -> str:
    parts = [node_expression(node)] + node_irs(node)
    return " ".join(p for p in parts if p).strip()


def get_source_mapping(node: Any) -> str:
    sm = getattr(node, "source_mapping", None)
    return "" if sm is None else str(sm)


def function_key(function: Any) -> str:
    return canonical_name(function)


def build_function_indices(contract: Any) -> Tuple[Dict[str, Any], Dict[str, List[Any]]]:
    """Return maps by full_name and by short name."""
    by_full: Dict[str, Any] = {}
    by_name: Dict[str, List[Any]] = {}

    for f in safe_iter(getattr(contract, "functions_declared", [])):
        if not getattr(f, "is_implemented", False):
            continue
        by_full[obj_full_name(f)] = f
        by_name.setdefault(obj_name(f), []).append(f)

    for m in safe_iter(getattr(contract, "modifiers", [])):
        by_full[obj_full_name(m)] = m
        by_name.setdefault(obj_name(m), []).append(m)

    return by_full, by_name


def extract_called_function_names_from_text(text: str, known_names: Set[str]) -> Set[str]:
    """Fallback call extraction from expression/IR strings."""
    hits: Set[str] = set()
    for name in known_names:
        if not name or name.startswith("$"):
            continue
        # Match _transfer(, _transfer(, INTERNAL_CALL, etc.
        if re.search(rf"(?<![A-Za-z0-9_$]){re.escape(name)}\s*\(", text):
            hits.add(name)
        elif re.search(rf"\bINTERNAL_CALL\b.*\b{re.escape(name)}\b", text):
            hits.add(name)
    return hits


def collect_direct_callees(function: Any, by_name: Dict[str, List[Any]]) -> List[Any]:
    callees: List[Any] = []

    # 1) Slither direct internal_calls API.
    for c in safe_iter(getattr(function, "internal_calls", [])):
        if hasattr(c, "nodes") and getattr(c, "is_implemented", True):
            callees.append(c)
        else:
            # Some Slither versions expose call objects / strings.
            cname = obj_name(c)
            if cname in by_name:
                callees.extend(by_name[cname])

    # 2) Modifiers should be reachable because onlyOwner / checks may be there.
    for m in safe_iter(getattr(function, "modifiers", [])):
        if hasattr(m, "nodes"):
            callees.append(m)

    # 3) Fallback: parse node expressions and IR text.
    known_names = set(by_name.keys())
    for node in safe_iter(getattr(function, "nodes", [])):
        text = node_text(node)
        for name in extract_called_function_names_from_text(text, known_names):
            for target in by_name.get(name, []):
                if target is not function:
                    callees.append(target)

    # Deduplicate while preserving order.
    seen: Set[str] = set()
    out: List[Any] = []
    for c in callees:
        key = function_key(c)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def collect_reachable_functions(entry_func: Any, by_name: Dict[str, List[Any]]) -> List[Any]:
    visited: Set[str] = set()
    stack: List[Any] = [entry_func]
    result: List[Any] = []

    while stack:
        f = stack.pop()
        key = function_key(f)
        if key in visited:
            continue
        visited.add(key)
        result.append(f)

        for callee in reversed(collect_direct_callees(f, by_name)):
            ckey = function_key(callee)
            if ckey not in visited:
                stack.append(callee)

    return result


def infer_state_var_roles(contract: Any) -> Dict[str, Dict[str, Any]]:
    """
    Infer state variable roles using ERC-20 accessors and storage declaration order.
    This is heuristic; output includes confidence and evidence.
    """
    roles: Dict[str, Dict[str, Any]] = {}

    state_vars = safe_iter(getattr(contract, "state_variables_declared", []))
    if not state_vars:
        state_vars = safe_iter(getattr(contract, "state_variables", []))

    for idx, v in enumerate(state_vars):
        name = obj_name(v)
        roles[name] = {
            "name": name,
            "slot_index_guess": idx,
            "type": str(getattr(v, "type", "")),
            "semantic_role": "UNKNOWN_STATE",
            "confidence": "LOW",
            "evidence": [],
        }

    def mark(var: Any, role: str, confidence: str, evidence: str) -> None:
        if var is None:
            return
        n = obj_name(var)
        roles.setdefault(n, {
            "name": n,
            "slot_index_guess": None,
            "type": str(getattr(var, "type", "")),
            "semantic_role": "UNKNOWN_STATE",
            "confidence": "LOW",
            "evidence": [],
        })
        roles[n]["semantic_role"] = role
        roles[n]["confidence"] = confidence
        roles[n].setdefault("evidence", []).append(evidence)

    for f in safe_iter(getattr(contract, "functions_declared", [])):
        if not getattr(f, "is_implemented", False):
            continue
        fname = obj_full_name(f)
        reads = safe_iter(getattr(f, "state_variables_read", []))
        if fname == "balanceOf(address)" and len(reads) >= 1:
            mark(reads[0], "BALANCE_STATE", "HIGH", "balanceOf(address) reads this state variable")
        elif fname == "allowance(address,address)" and len(reads) >= 1:
            mark(reads[0], "ALLOWANCE_STATE", "HIGH", "allowance(address,address) reads this state variable")
        elif fname == "totalSupply()" and len(reads) >= 1:
            mark(reads[0], "SUPPLY_STATE", "HIGH", "totalSupply() reads this state variable")

    # Fallback role hints from type shape, only if still unknown.
    for item in roles.values():
        typ = item.get("type", "").lower().replace(" ", "")
        if item["semantic_role"] != "UNKNOWN_STATE":
            continue
        if "mapping(address=>uint256)" in typ or "mapping(address=>uint)" in typ:
            item["semantic_role"] = "POSSIBLE_BALANCE_STATE"
            item["confidence"] = "LOW"
            item["evidence"].append("type resembles mapping(address=>uint256)")
        elif "mapping(address=>mapping(address=>uint256))" in typ or "mapping(address=>mapping(address=>uint))" in typ:
            item["semantic_role"] = "POSSIBLE_ALLOWANCE_STATE"
            item["confidence"] = "LOW"
            item["evidence"].append("type resembles mapping(address=>mapping(address=>uint256))")

    return roles


def detect_op_types(text: str, node_type: str) -> List[str]:
    ops: List[str] = []
    for name, pattern in OP_PATTERNS:
        if pattern.search(text):
            ops.append(name)

    # Slither IF node may not contain literal "if" in expression.
    if "IF" in node_type.upper() and "IF" not in ops:
        ops.append("IF")

    # Deduplicate while preserving order.
    seen: Set[str] = set()
    out: List[str] = []
    for op in ops:
        if op not in seen:
            seen.add(op)
            out.append(op)
    return out


def classify_candidate(
    op_type: str,
    function: Any,
    node: Any,
    text: str,
    state_roles: Dict[str, Dict[str, Any]],
) -> Tuple[str, str, str, bool, List[str]]:
    """
    Return candidate_security_type, relevance_label, confidence, needs_human_review, evidence.
    This is intentionally conservative.
    """
    fname = obj_full_name(function)
    short = obj_name(function)
    lower = text.lower()
    evidence: List[str] = []
    security_type = "UNKNOWN_SECURITY_RELEVANT"
    relevance = "CORE_ANCHOR"
    confidence = "LOW"
    needs_review = True

    state_reads = var_names(safe_iter(getattr(node, "state_variables_read", [])))
    state_writes = var_names(safe_iter(getattr(node, "state_variables_written", [])))
    all_state = state_reads + state_writes

    # Event topics are high-confidence when topic hash is present.
    if op_type in {"LOG3", "LOG4"}:
        if TRANSFER_TOPIC in lower:
            return "TRANSFER_EVENT", "CORE_ANCHOR", "HIGH", False, ["log topic matches Transfer(address,address,uint256)"]
        if APPROVAL_TOPIC in lower:
            return "APPROVAL_EVENT", "CORE_ANCHOR", "HIGH", False, ["log topic matches Approval(address,address,uint256)"]
        return "SECURITY_RELEVANT_EVENT", "CORE_ANCHOR", "MEDIUM", True, ["low-level log operation found"]

    if op_type in {"REVERT", "REQUIRE", "ASSERT", "IF"}:
        if short in {"transfer", "transferFrom", "_transfer", "_spendAllowance"}:
            return "TRANSFER_SUCCESS_CONDITION", "CORE_ANCHOR" if op_type != "IF" else "CONTROL_DEPENDENCY", "MEDIUM", True, [
                f"{op_type} appears in ERC-20 transfer path"
            ]
        if short in {"approve", "_approve"}:
            return "APPROVAL_SUCCESS_CONDITION", "CORE_ANCHOR" if op_type != "IF" else "CONTROL_DEPENDENCY", "MEDIUM", True, [
                f"{op_type} appears in approve path"
            ]
        return "EXECUTION_SUCCESS_CONDITION", "CONTROL_DEPENDENCY" if op_type == "IF" else "CORE_ANCHOR", "LOW", True, [
            f"{op_type} may affect execution"
        ]

    if op_type in {"STATICCALL", "CALL", "DELEGATECALL"}:
        st = "EXTERNAL_CALL"
        if short in {"transfer", "transferFrom", "_transfer", "_spendAllowance"}:
            st = "EXTERNAL_CALL_AFFECTING_TRANSFER_PATH"
        return st, "CORE_ANCHOR", "MEDIUM", True, [f"{op_type} found in {fname}"]

    # State variable direct accesses, if Slither can resolve them.
    resolved_roles = {state_roles.get(v, {}).get("semantic_role", "UNKNOWN_STATE") for v in all_state}

    if op_type == "SSTORE":
        if any(r in resolved_roles for r in {"BALANCE_STATE", "POSSIBLE_BALANCE_STATE"}):
            return "BALANCE_WRITE", "CORE_ANCHOR", "HIGH", False, ["SSTORE/state write targets balance-like state variable"]
        if any(r in resolved_roles for r in {"ALLOWANCE_STATE", "POSSIBLE_ALLOWANCE_STATE"}):
            return "ALLOWANCE_WRITE", "CORE_ANCHOR", "HIGH", False, ["SSTORE/state write targets allowance-like state variable"]
        if any(r == "SUPPLY_STATE" for r in resolved_roles):
            return "SUPPLY_WRITE", "CORE_ANCHOR", "HIGH", False, ["SSTORE/state write targets totalSupply-like state variable"]

        # Assembly-heavy heuristics.
        if short in {"_transfer", "transfer"}:
            evidence.append("SSTORE occurs in transfer path")
            evidence.append("assembly storage slot may correspond to balance mapping; verify slot reconstruction")
            return "POSSIBLE_BALANCE_WRITE", "CORE_ANCHOR", "MEDIUM", True, evidence
        if short in {"approve", "_approve"}:
            evidence.append("SSTORE occurs in approve path")
            if "_amount" in text or "amount" in lower:
                evidence.append("stored value appears to be approval amount")
            if "wzha" in lower or "allow" in lower:
                evidence.append("node text references allowance-like state variable or allowance term")
            return "POSSIBLE_ALLOWANCE_WRITE", "CORE_ANCHOR", "MEDIUM", True, evidence
        if short in {"_spendAllowance", "spendAllowance"}:
            evidence.append("SSTORE occurs in allowance spending path")
            evidence.append("slot construction may be non-standard; manual review required")
            return "ALLOWANCE_OR_POLICY_WRITE", "CORE_ANCHOR", "MEDIUM", True, evidence

        return "LOW_LEVEL_STORAGE_WRITE", "CORE_ANCHOR", "MEDIUM", True, ["SSTORE found but storage semantic role is unresolved"]

    if op_type == "SLOAD":
        if any(r in resolved_roles for r in {"BALANCE_STATE", "POSSIBLE_BALANCE_STATE"}):
            return "BALANCE_READ", "CORE_ANCHOR", "HIGH", False, ["SLOAD/state read targets balance-like state variable"]
        if any(r in resolved_roles for r in {"ALLOWANCE_STATE", "POSSIBLE_ALLOWANCE_STATE"}):
            return "ALLOWANCE_READ", "CORE_ANCHOR", "HIGH", False, ["SLOAD/state read targets allowance-like state variable"]
        if any(r == "SUPPLY_STATE" for r in resolved_roles):
            return "SUPPLY_READ", "CORE_ANCHOR", "HIGH", False, ["SLOAD/state read targets totalSupply-like state variable"]
        if ".slot" in lower or "slot" in lower:
            return "POLICY_STATE_ACCESS", "DATA_DEPENDENCY", "MEDIUM", True, ["SLOAD reads a storage slot; role unresolved"]
        if short in {"_transfer", "transfer"}:
            return "POSSIBLE_BALANCE_READ", "CORE_ANCHOR", "MEDIUM", True, ["SLOAD occurs in transfer path"]
        if short in {"_spendAllowance", "spendAllowance"}:
            return "ALLOWANCE_OR_POLICY_READ", "CORE_ANCHOR", "MEDIUM", True, ["SLOAD occurs in allowance spending path"]
        return "LOW_LEVEL_STORAGE_READ", "CORE_ANCHOR", "MEDIUM", True, ["SLOAD found but semantic role is unresolved"]

    if op_type in {"MSTORE", "KECCAK256"}:
        # These often build mapping slots / call input / event data.
        if "slot" in lower or "keccak256" in lower or any(k in lower for k in ["_from", "_to", "_owner", "_spender", "caller"]):
            return "LOW_LEVEL_STORAGE_OR_CALL_DATA_DEPENDENCY", "DATA_DEPENDENCY", "MEDIUM", True, [
                f"{op_type} may construct mapping slot, call input, or event data"
            ]
        return "LOW_LEVEL_MEMORY_OPERATION", "DATA_DEPENDENCY", "LOW", True, [f"{op_type} found"]

    # Policy terms can override unknowns.
    if any(term in lower for term in POLICY_TERMS):
        return "POLICY_STATE_ACCESS", "DATA_DEPENDENCY", "MEDIUM", True, ["text matches ERC-20 policy term"]

    return security_type, relevance, confidence, needs_review, evidence or ["security relevance not fully classified"]


def extract_node_fact(function: Any, node: Any) -> Dict[str, Any]:
    return {
        "function": obj_full_name(function),
        "function_name": obj_name(function),
        "node_id": getattr(node, "node_id", None),
        "node_type": str(getattr(node, "type", "")),
        "expression": node_expression(node),
        "irs": node_irs(node),
        "text": node_text(node),
        "state_variables_read": var_names(safe_iter(getattr(node, "state_variables_read", []))),
        "state_variables_written": var_names(safe_iter(getattr(node, "state_variables_written", []))),
        "variables_read": var_names(safe_iter(getattr(node, "variables_read", []))),
        "variables_written": var_names(safe_iter(getattr(node, "variables_written", []))),
        "high_level_calls": string_list(safe_iter(getattr(node, "high_level_calls", []))),
        "low_level_calls": string_list(safe_iter(getattr(node, "low_level_calls", []))),
        "library_calls": string_list(safe_iter(getattr(node, "library_calls", []))),
        "solidity_calls": string_list(safe_iter(getattr(node, "solidity_calls", []))),
        "source_mapping": get_source_mapping(node),
    }


def extract_node_ops(entrypoint: str, function: Any, node: Any, state_roles: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    fact = extract_node_fact(function, node)
    ops = detect_op_types(fact["text"], fact["node_type"])

    # If Slither resolved a Solidity-level state write but no low-level op was detected.
    if fact["state_variables_written"] and "STATE_WRITE" not in ops and "SSTORE" not in ops:
        ops.append("STATE_WRITE")
    if fact["state_variables_read"] and "STATE_READ" not in ops and "SLOAD" not in ops:
        ops.append("STATE_READ")

    out: List[Dict[str, Any]] = []
    for op in ops:
        if op == "STATE_WRITE":
            security_type = "STATE_WRITE"
            relevance = "CORE_ANCHOR"
            confidence = "MEDIUM"
            review = True
            evidence = ["Slither reports state_variables_written"]
        elif op == "STATE_READ":
            security_type = "STATE_READ"
            relevance = "DATA_DEPENDENCY"
            confidence = "MEDIUM"
            review = True
            evidence = ["Slither reports state_variables_read"]
        else:
            security_type, relevance, confidence, review, evidence = classify_candidate(
                op, function, node, fact["text"], state_roles
            )

        item = {
            "entrypoint": entrypoint,
            "function": fact["function"],
            "function_name": fact["function_name"],
            "node_id": fact["node_id"],
            "node_type": fact["node_type"],
            "op_type": op,
            "candidate_security_type": security_type,
            "relevance_label": relevance,
            "confidence": confidence,
            "needs_human_review": review,
            "evidence": evidence,
            "statement": fact["text"],
            "expression": fact["expression"],
            "irs": fact["irs"],
            "state_variables_read": fact["state_variables_read"],
            "state_variables_written": fact["state_variables_written"],
            "variables_read": fact["variables_read"],
            "variables_written": fact["variables_written"],
            "high_level_calls": fact["high_level_calls"],
            "low_level_calls": fact["low_level_calls"],
            "library_calls": fact["library_calls"],
            "solidity_calls": fact["solidity_calls"],
            "source_mapping": fact["source_mapping"],
        }
        out.append(item)

    return out


def summarize_function(function: Any) -> Dict[str, Any]:
    return {
        "name": obj_name(function),
        "full_name": obj_full_name(function),
        "canonical_name": canonical_name(function),
        "visibility": str(getattr(function, "visibility", "")),
        "is_implemented": bool(getattr(function, "is_implemented", False)),
        "modifiers": [obj_name(m) for m in safe_iter(getattr(function, "modifiers", []))],
        "internal_calls_api": [obj_full_name(c) for c in safe_iter(getattr(function, "internal_calls", []))],
        "state_variables_read": var_names(safe_iter(getattr(function, "state_variables_read", []))),
        "state_variables_written": var_names(safe_iter(getattr(function, "state_variables_written", []))),
    }


def analyze_contract(contract: Any) -> Dict[str, Any]:
    by_full, by_name = build_function_indices(contract)
    state_roles = infer_state_var_roles(contract)

    contract_report: Dict[str, Any] = {
        "contract": obj_name(contract),
        "state_variable_roles": list(state_roles.values()),
        "erc20_entrypoints_found": [],
        "entrypoint_reports": [],
        "warnings": [],
    }

    for full in ALL_ERC20_ENTRYPOINTS:
        if full in by_full:
            contract_report["erc20_entrypoints_found"].append(full)

    for ep_full in sorted(CORE_ENTRYPOINTS):
        entry = by_full.get(ep_full)
        if entry is None:
            continue

        reachable = collect_reachable_functions(entry, by_name)
        reachable_names = [obj_full_name(f) for f in reachable]

        # Helpful warning for the exact failure mode seen in the previous JSON.
        if ep_full in {"transfer(address,uint256)", "transferFrom(address,address,uint256)"}:
            if not any(obj_name(f) == "_transfer" for f in reachable):
                contract_report["warnings"].append(
                    f"{ep_full}: _transfer is not in reachable functions; check Slither parsing or fallback call extraction."
                )

        raw_nodes: List[Dict[str, Any]] = []
        anchors: List[Dict[str, Any]] = []

        for f in reachable:
            for node in safe_iter(getattr(f, "nodes", [])):
                raw_nodes.append(extract_node_fact(f, node))
                anchors.extend(extract_node_ops(ep_full, f, node, state_roles))

        contract_report["entrypoint_reports"].append({
            "entrypoint": ep_full,
            "entrypoint_summary": summarize_function(entry),
            "reachable_functions": reachable_names,
            "reachable_function_summaries": [summarize_function(f) for f in reachable],
            "anchor_candidates": anchors,
            "raw_nodes": raw_nodes,
            "stats": {
                "reachable_function_count": len(reachable),
                "raw_node_count": len(raw_nodes),
                "anchor_candidate_count": len(anchors),
                "needs_human_review_count": sum(1 for a in anchors if a.get("needs_human_review")),
            },
        })

    return contract_report


def analyze(sol_file: str, contract_filter: Optional[str] = None) -> Dict[str, Any]:
    slither = Slither(sol_file)
    report: Dict[str, Any] = {
        "source": sol_file,
        "schema_version": "erc20-security-anchor-v2",
        "notes": [
            "candidate_security_type is rule-based and should be reviewed manually for assembly-heavy contracts.",
            "This script extracts anchors and raw facts; it does not perform full slicing or code deletion.",
        ],
        "contracts": [],
    }

    for contract in safe_iter(getattr(slither, "contracts_derived", [])):
        if contract_filter and obj_name(contract) != contract_filter:
            continue
        report["contracts"].append(analyze_contract(contract))

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Slither-based ERC-20 security anchor extractor")
    parser.add_argument("solidity_file", help="Path to Solidity source file or project target accepted by Slither")
    parser.add_argument("-o", "--output", default="outputs/erc20_security_anchors.json", help="Output JSON path")
    parser.add_argument("--contract", default=None, help="Optional contract name filter")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON to stdout as well")
    args = parser.parse_args()

    report = analyze(args.solidity_file, contract_filter=args.contract)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"[OK] saved report to {out_path}")
    if args.pretty:
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
