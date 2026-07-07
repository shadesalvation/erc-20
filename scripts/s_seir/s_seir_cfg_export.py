#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from s_seir_model import FunctionSSEIR, SourceStatement


def safe_filename(text: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", text.strip())
    value = re.sub(r"_+", "_", value).strip("_.")
    return value or "function"


def dot_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    return (
        text.replace("\\", "\\\\")
        .replace("\"", "\\\"")
        .replace("\n", "\\n")
        .replace("\r", "")
    )


def short_text(value: str, limit: int = 120) -> str:
    text = " ".join(value.strip().split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def stmt_lookup(statements: list[SourceStatement]) -> dict[str, SourceStatement]:
    return {stmt.stmt_id: stmt for stmt in statements}


def block_label(block: dict[str, Any], statements: dict[str, SourceStatement]) -> str:
    block_id = str(block.get("block_id", ""))
    kind = str(block.get("kind", ""))
    attrs = block.get("attrs") if isinstance(block.get("attrs"), dict) else {}
    term = block.get("terminator") if isinstance(block.get("terminator"), dict) else {}
    stmt_ids = [str(x) for x in block.get("stmts", [])]

    lines = [block_id, f"kind={kind}"]
    if block.get("assembly_block") is not None:
        lines.append(f"assembly_block={block.get('assembly_block')}")
    if attrs.get("slither_node_id") is not None:
        lines.append(f"slither_node={attrs.get('slither_node_id')}")
    if attrs.get("node_id") is not None:
        lines.append(f"yul_node={attrs.get('node_id')}")
    if stmt_ids:
        lines.append("stmts=" + ", ".join(stmt_ids))
        for stmt_id in stmt_ids[:4]:
            stmt = statements.get(stmt_id)
            if stmt:
                lines.append(short_text(f"{stmt_id}: {stmt.text}"))
    elif attrs.get("text"):
        lines.append(short_text(str(attrs.get("text"))))

    term_kind = term.get("kind")
    if term_kind:
        term_text = term.get("condition") or term.get("text") or term.get("node_kind") or ""
        if term_text:
            lines.append(short_text(f"term={term_kind}: {term_text}"))
        else:
            lines.append(f"term={term_kind}")
    return "\\n".join(dot_escape(line) for line in lines)


def block_style(block: dict[str, Any]) -> dict[str, str]:
    kind = str(block.get("kind", ""))
    term = block.get("terminator") if isinstance(block.get("terminator"), dict) else {}
    term_kind = str(term.get("kind", ""))

    style = {
        "shape": "box",
        "style": "rounded,filled",
        "fontname": "Consolas",
        "fontsize": "10",
        "color": "#8a8f98",
        "fillcolor": "#ffffff",
    }
    if kind == "yul":
        style["fillcolor"] = "#fff4d6"
        style["color"] = "#c48700"
    elif kind == "solidity":
        style["fillcolor"] = "#e8f2ff"
        style["color"] = "#3574b7"

    if term_kind == "Branch":
        style["shape"] = "diamond"
        style["fillcolor"] = "#f5ecff" if kind != "yul" else "#ffefd0"
        style["color"] = "#7a4bb3"
    elif term_kind in {"Return", "Revert", "Stop", "Terminal"}:
        style["shape"] = "box"
        style["peripheries"] = "2"
        style["fillcolor"] = "#ffe7e7" if term_kind == "Revert" else "#e9ffe8"
        style["color"] = "#b73d3d" if term_kind == "Revert" else "#3f8f42"
    return style


def attrs_to_dot(attrs: dict[str, str]) -> str:
    return ", ".join(f'{key}="{dot_escape(value)}"' for key, value in attrs.items())


def edge_style(kind: str) -> dict[str, str]:
    style = {"fontname": "Consolas", "fontsize": "9", "label": kind}
    if kind in {"true", "case", "body"}:
        style["color"] = "#22863a"
    elif kind in {"false", "exit"}:
        style["color"] = "#cb2431"
    elif kind in {"loop", "back", "backedge"}:
        style["color"] = "#6f42c1"
    else:
        style["color"] = "#586069"
    return style


def render_function_cfg_dot(fn: FunctionSSEIR) -> str:
    statements = stmt_lookup(fn.source_statements)
    blocks = fn.control.get("blocks", []) if isinstance(fn.control, dict) else []
    edges = fn.control.get("edges", []) if isinstance(fn.control, dict) else []
    notes = fn.control.get("notes", []) if isinstance(fn.control, dict) else []

    graph_name = safe_filename(fn.function_id)
    lines = [
        f'digraph "{dot_escape(graph_name)}" {{',
        "  graph [rankdir=TB, bgcolor=\"#ffffff\", labelloc=t, fontsize=14, fontname=\"Consolas\", label=\""
        + dot_escape(fn.function_id)
        + "\"];",
        "  node [fontname=\"Consolas\"];",
        "  edge [fontname=\"Consolas\"];",
    ]

    if notes:
        note_label = "CFG notes:\\n" + "\\n".join(dot_escape(short_text(str(note), 100)) for note in notes)
        lines.append(
            f'  "__cfg_notes" [shape=note, style="filled", fillcolor="#f6f8fa", color="#d0d7de", '
            f'fontsize="9", label="{note_label}"];'
        )

    for block in blocks:
        block_id = str(block.get("block_id", ""))
        if not block_id:
            continue
        attrs = block_style(block)
        attrs["label"] = block_label(block, statements)
        lines.append(f'  "{dot_escape(block_id)}" [{attrs_to_dot(attrs)}];')

    for edge in edges:
        src = str(edge.get("from", ""))
        dst = str(edge.get("to", ""))
        if not src or not dst:
            continue
        kind = str(edge.get("kind", ""))
        lines.append(f'  "{dot_escape(src)}" -> "{dot_escape(dst)}" [{attrs_to_dot(edge_style(kind))}];')

    lines.append("}")
    return "\n".join(lines) + "\n"


def write_function_cfg_dot_files(functions: list[FunctionSSEIR], output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    used: set[str] = set()
    for fn in functions:
        base = safe_filename(f"{fn.contract}.{fn.signature}")
        name = base
        suffix = 2
        while name in used:
            name = f"{base}_{suffix}"
            suffix += 1
        used.add(name)
        path = output_dir / f"{name}.dot"
        path.write_text(render_function_cfg_dot(fn), encoding="utf-8")
        written.append(path)
    return written
