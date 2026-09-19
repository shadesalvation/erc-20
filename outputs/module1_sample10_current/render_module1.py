#!/usr/bin/env python3
"""Read-only Module1Result presentation and accounting view."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path


def load_gzip(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def compact(value, limit=180):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def guard_text(t):
    op = t["op"]
    typ = t["type"]
    type_name = typ["name"] if isinstance(typ, dict) else "unknown"
    if op == "literal":
        value = t["literal"]
        return ("true" if value else "false") if type_name == "bool" else f"{value}:{type_name}"
    if op == "symbol":
        return f"{t['symbol_ref']}:{type_name}"
    if op == "unknown":
        literal = t.get("literal") or {}
        return "UNKNOWN(" + str(literal.get("reason") or literal.get("reason_code") or compact(literal, 80)) + ")"
    args = [guard_text(a) for a in t["operands"]]
    if op == "cast":
        return f"cast<{type_name}>({args[0]})"
    if len(args) == 1:
        return f"{op}({args[0]})"
    mode = f"[{t['arithmetic_mode']}]" if t.get("arithmetic_mode") else ""
    return f"({args[0]} {op}{mode} {args[1]})"


def action_info(action):
    payload = action["payload"]
    operands = [{"role": o.get("role"), "value": o.get("value"),
                 "field_path": o.get("field_path")} for o in payload["operand_refs"]]
    return {"action_id": action["id"], "kind": payload["kind"],
            "key_semantics": operands, "status": action["status"]}


def endpoint_info(endpoint, actions):
    if endpoint["kind"] != "ACTION":
        return {"kind": endpoint["kind"], "label": endpoint["kind"], "action_id": None}
    action_id = endpoint["action_ref"]
    action = actions.get(action_id)
    return {"kind": "ACTION", "label": action["payload"]["kind"] if action else "UNKNOWN_ACTION_REF",
            "action_id": action_id}


def dot_quote(value):
    return json.dumps(str(value), ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--module1", type=Path, required=True)
    parser.add_argument("--refinement", type=Path, required=True)
    parser.add_argument("--sfir", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir
    results = load_gzip(args.module1)
    refinement = load_gzip(args.refinement)
    sfir = json.loads(args.sfir.read_text(encoding="utf-8"))
    row_by_id = {row["candidate_id"]: row for row in refinement["refinements"]}
    summary = {"head_sha": (output / "head.sha").read_text().strip(),
               "source_path": str(args.source),
               "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
               "sfir_sha256": hashlib.sha256(args.sfir.read_bytes()).hexdigest(),
               "sfir_schema": sfir["schema"],
               "run_status": "COMPLETED_WITH_RECORDED_ANALYSIS_STATUS",
               "functions": []}
    dot = ["digraph Module1Control {", "  rankdir=LR;", "  compound=true;",
           "  // Presentation only: edges and Guards are copied from Module1Result."]
    lines = ["Module 1 control view — presentation of persisted Module1Result only", ""]
    total_actions = total_candidates = total_feasible = total_rejected = total_unresolved = 0
    for index, result in enumerate(results):
        payload = result["payload"]
        function_ref = result["function_ref"]
        signature = function_ref["canonical_signature"]
        actions = {a["id"]: a for a in payload["actions"]}
        guards = {g["id"]: g for g in payload["guards"]}
        edges = []
        for name, feasibility in (("feasible_edges", "FEASIBLE"),
                                  ("rejected_edges", "INFEASIBLE"),
                                  ("unresolved_edges", "UNRESOLVED")):
            for edge in payload[name]:
                cid = edge["id"]
                row = row_by_id[cid]
                main = next(item for item in row["solver_evidence"]
                            if item["id"] == row["feasibility_evidence_ref"])
                guard = guards[edge["payload"]["guard_ref"]["artifact_ref"]]
                scope_reasons = row["scope"]["incomplete_reasons"]
                diagnostic_reasons = [{"code": d["code"], "reason": d["reason"]}
                                      for d in edge["status"]["diagnostics"]]
                reasons = {"scope_incomplete_reasons": scope_reasons,
                           "solver_reason": main["payload"]["reason"],
                           "edge_diagnostics": diagnostic_reasons}
                info = {"candidate_id": cid, "from": endpoint_info(edge["payload"]["from"], actions),
                        "to": endpoint_info(edge["payload"]["to"], actions),
                        "feasibility": feasibility, "canonical_guard_ref": guard["id"],
                        "canonical_guard": guard["payload"]["expression"],
                        "canonical_guard_text": guard_text(guard["payload"]["expression"]),
                        "guard_scope": guard["payload"]["scope"],
                        "guard_assumptions": guard["payload"]["assumptions"],
                        "scope_completeness": row["scope"]["scope_completeness"],
                        "solver_outcome": main["payload"]["outcome"],
                        "unresolved_reason": reasons if feasibility == "UNRESOLVED" else None,
                        "solver_evidence_ref": row["feasibility_evidence_ref"]}
                edges.append(info)
        edges.sort(key=lambda e: e["candidate_id"])
        counts = {"semantic_actions": len(actions), "candidate_edges": len(edges),
                  "feasible": len(payload["feasible_edges"]), "rejected": len(payload["rejected_edges"]),
                  "unresolved": len(payload["unresolved_edges"])}
        total_actions += counts["semantic_actions"]
        total_candidates += counts["candidate_edges"]
        total_feasible += counts["feasible"]
        total_rejected += counts["rejected"]
        total_unresolved += counts["unresolved"]
        summary["functions"].append({"function_ref": function_ref, "signature": signature,
            "status": result["status"], "counts": counts,
            "actions": [action_info(a) for a in sorted(actions.values(), key=lambda a: a["id"])],
            "edges": edges})

        lines.append(f"Function: {signature}  status={result['status']['completion']}")
        lines.append("Counts: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
        lines.append("Semantic Actions:")
        for action in sorted(actions.values(), key=lambda a: a["id"]):
            info = action_info(action)
            lines.append(f"  {info['action_id']} | {info['kind']}")
            for operand in info["key_semantics"]:
                lines.append(f"      {operand['role']}: {compact(operand['value'], 260)}")
        lines.append("Semantic Control Edges:")
        dot.append(f"  subgraph cluster_{index} {{")
        dot.append(f"    label={dot_quote(signature)};")
        dot.append("    color=lightgray;")
        for kind in ("ENTRY", "EXIT"):
            node_id = f"f{index}_{kind.lower()}"
            dot.append(f"    {dot_quote(node_id)} [label={dot_quote(kind)},shape=oval,style=filled,fillcolor=white];")
        for action in sorted(actions.values(), key=lambda a: a["id"]):
            node_id = f"f{index}_{action['id']}"
            label = action["payload"]["kind"] + "\n" + action["id"][-12:]
            dot.append(f"    {dot_quote(node_id)} [label={dot_quote(label)},shape=box];")
        for edge in edges:
            src, dst = edge["from"], edge["to"]
            src_node = f"f{index}_{src['action_id']}" if src["action_id"] else f"f{index}_{src['kind'].lower()}"
            dst_node = f"f{index}_{dst['action_id']}" if dst["action_id"] else f"f{index}_{dst['kind'].lower()}"
            style, color = {"FEASIBLE": ("solid", "forestgreen"),
                            "INFEASIBLE": ("dashed", "firebrick"),
                            "UNRESOLVED": ("dotted", "gray40")}[edge["feasibility"]]
            shown_guard = edge["canonical_guard_text"]
            if len(shown_guard) > 130:
                shown_guard = shown_guard[:129] + "…"
            label = f"{edge['feasibility']} | {shown_guard}"
            dot.append(f"    {dot_quote(src_node)} -> {dot_quote(dst_node)} "
                       f"[label={dot_quote(label)},style={style},color={color}," 
                       f"tooltip={dot_quote(edge['candidate_id'])}];")
            src_text = f"{src['label']} {src['action_id'] or ''}".strip()
            dst_text = f"{dst['label']} {dst['action_id'] or ''}".strip()
            lines.append(f"  [{src_text}] --[guard: {edge['canonical_guard_text']}]--> [{dst_text}]  {edge['feasibility']}")
            lines.append(f"    candidate_id: {edge['candidate_id']}")
            lines.append(f"    scope: {edge['scope_completeness']}; solver: {edge['solver_outcome']}")
            if edge["unresolved_reason"] is not None:
                reason_parts = [r.get("reason", "") for r in edge["unresolved_reason"]["scope_incomplete_reasons"]]
                reason_parts.extend(r.get("reason", "") for r in edge["unresolved_reason"]["edge_diagnostics"])
                if edge["unresolved_reason"]["solver_reason"]:
                    reason_parts.append(edge["unresolved_reason"]["solver_reason"])
                for reason in dict.fromkeys(str(part) for part in reason_parts if part):
                    lines.append(f"    reason: {reason}")
        dot.append("  }")
        lines.append("")
    dot.append("}")
    summary["overall"] = {"functions": len(results), "semantic_actions": total_actions,
        "candidate_edges": total_candidates, "feasible": total_feasible,
        "rejected": total_rejected, "unresolved": total_unresolved,
        "complete_functions": sum(r["status"]["completion"] == "COMPLETE" for r in results),
        "partial_functions": sum(r["status"]["completion"] == "PARTIAL" for r in results)}
    summary["accounting_verified"] = (total_candidates == len(row_by_id)
                                      and {e["candidate_id"] for f in summary["functions"] for e in f["edges"]}
                                      == set(row_by_id))
    (output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    (output / "summary.txt").write_text("\n".join(lines) + "\nOverall: " + compact(summary["overall"]) + "\n", encoding="utf-8")
    (output / "module1_control.dot").write_text("\n".join(dot) + "\n", encoding="utf-8")
    (output / "module1_control.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary["overall"], ensure_ascii=False, sort_keys=True))
    print("candidate accounting verified:", summary["accounting_verified"])


if __name__ == "__main__":
    main()
