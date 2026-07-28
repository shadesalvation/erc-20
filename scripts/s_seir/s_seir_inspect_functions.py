#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


FUNCTION_KEYS = {
    "function_id",
    "contract",
    "function",
    "signature",
    "source_statements",
    "effects",
    "semantic_overlays",
    "control",
}


def is_function_sseir(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    if not FUNCTION_KEYS.issubset(obj):
        return False
    return (
        isinstance(obj.get("source_statements"), list)
        and isinstance(obj.get("effects"), list)
        and isinstance(obj.get("semantic_overlays"), list)
        and isinstance(obj.get("control"), dict)
    )


def load_functions(data: dict[str, Any]) -> list[dict[str, Any]]:
    source_entry = data.get("source_entry")
    if isinstance(source_entry, dict):
        functions = source_entry.get("functions")
        if isinstance(functions, list):
            return [item for item in functions if is_function_sseir(item)]
    functions = data.get("functions")
    if isinstance(functions, list):
        return [item for item in functions if is_function_sseir(item)]
    return list(structural_function_walk(data))


def structural_function_walk(value: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    stack = [value]
    seen: set[int] = set()
    while stack:
        item = stack.pop()
        item_id = id(item)
        if item_id in seen:
            continue
        seen.add(item_id)
        if is_function_sseir(item):
            out.append(item)
            continue
        if isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return out


def function_has_assembly(fn: dict[str, Any]) -> bool:
    if fn.get("assembly_sources"):
        return True
    return any(stmt.get("lang") == "yul" for stmt in fn.get("source_statements") or [] if isinstance(stmt, dict))


def matches(fn: dict[str, Any], args: argparse.Namespace) -> bool:
    if args.function_id and fn.get("function_id") != args.function_id:
        return False
    if args.contract and fn.get("contract") != args.contract:
        return False
    if args.signature and fn.get("signature") != args.signature:
        return False
    if args.name and fn.get("function") != args.name:
        return False
    if args.has_assembly and not function_has_assembly(fn):
        return False
    return True


def summarize(fn: dict[str, Any]) -> dict[str, Any]:
    source_statements = fn.get("source_statements") or []
    effects = fn.get("effects") or []
    overlays = fn.get("semantic_overlays") or []
    roles = fn.get("expr_roles") or []
    return {
        "function_id": fn.get("function_id"),
        "contract": fn.get("contract"),
        "function": fn.get("function"),
        "signature": fn.get("signature"),
        "source_statement_count": len(source_statements),
        "yul_statement_count": sum(1 for item in source_statements if isinstance(item, dict) and item.get("lang") == "yul"),
        "effect_count": len(effects),
        "overlay_count": len(overlays),
        "expr_role_count": len(roles),
        "effect_kinds": count_kinds(effects),
        "overlay_kinds": count_kinds(overlays),
        "expr_roles": count_kinds(roles, key="role"),
        "assembly_blocks": sorted({
            str(item.get("block_id"))
            for item in source_statements
            if isinstance(item, dict) and item.get("lang") == "yul" and item.get("block_id")
        }),
        "control_notes": (fn.get("control") or {}).get("notes") or [],
    }


def count_kinds(items: list[Any], key: str = "kind") -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get(key) or "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return dict(sorted(counts.items()))


def print_text(summaries: list[dict[str, Any]]) -> None:
    print(f"matched_functions={len(summaries)}")
    for index, item in enumerate(summaries, 1):
        print(f"\n[{index}] {item['contract']}.{item['signature']}")
        print(f"  function_id={item['function_id']}")
        print(f"  source_statements={item['source_statement_count']} yul={item['yul_statement_count']}")
        print(f"  effects={item['effect_count']} overlays={item['overlay_count']} expr_roles={item['expr_role_count']}")
        print(f"  assembly_blocks={', '.join(item['assembly_blocks']) if item['assembly_blocks'] else '-'}")
        print(f"  effect_kinds={json.dumps(item['effect_kinds'], ensure_ascii=False)}")
        print(f"  overlay_kinds={json.dumps(item['overlay_kinds'], ensure_ascii=False)}")
        print(f"  expr_roles={json.dumps(item['expr_roles'], ensure_ascii=False)}")
        if item["control_notes"]:
            print(f"  control_notes={json.dumps(item['control_notes'], ensure_ascii=False)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect FunctionSSEIR records without matching nested statement objects.")
    parser.add_argument("json_file", type=Path)
    parser.add_argument("--function-id")
    parser.add_argument("--contract")
    parser.add_argument("--signature")
    parser.add_argument("--name")
    parser.add_argument("--has-assembly", action="store_true")
    parser.add_argument("--json", action="store_true", help="Emit JSON summaries.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data = json.loads(args.json_file.read_text(encoding="utf-8"))
    functions = [fn for fn in load_functions(data) if matches(fn, args)]
    summaries = [summarize(fn) for fn in functions]
    if args.json:
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
    else:
        print_text(summaries)


if __name__ == "__main__":
    main()
