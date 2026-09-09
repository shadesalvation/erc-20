#!/usr/bin/env python3
"""Human-auditable projections of the canonical Semantic Fact IR.

This module deliberately accepts only the final SFIR payload.  In particular,
it never consults S-SEIR source statements, effects, or MemorySSA traces as a
fallback: a presentation must not reintroduce the lower-level representation
that the Fact IR boundary intentionally removed.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any


Json = dict[str, Any]


def safe_filename(text: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text).strip())
    return re.sub(r"_+", "_", value).strip("_.") or "function"


def dot_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "")


def _short(value: Any, limit: int = 140) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _ordered_blocks(function: Json) -> list[Json]:
    cfg = function.get("fact_cfg") or {}
    order = cfg.get("reverse_postorder") or {}
    return sorted(
        cfg.get("blocks") or [],
        key=lambda block: (order.get(block.get("block_id"), 1 << 30), str(block.get("block_id", ""))),
    )


def _aliases(function: Json) -> dict[str, str]:
    return {str(block.get("block_id")): f"B{index}" for index, block in enumerate(_ordered_blocks(function))}


def _nodes_by_id(function: Json) -> dict[str, Json]:
    return {str(node.get("semantic_id")): node for node in function.get("semantic_nodes") or [] if node.get("semantic_id")}


def _semantic(node: Json) -> Json:
    value = node.get("semantic")
    return value if isinstance(value, dict) else {}


def _event_line(node: Json) -> str:
    semantic = _semantic(node)
    event = semantic.get("event") or node.get("event") or "Event"
    args = semantic.get("args") or node.get("arguments") or []
    return f"emit {event}({', '.join(map(str, args))});"


def render_sfir_node_c_like(node: Json) -> str | None:
    """Render one final semantic operation without descending into Yul evidence."""
    kind = str(node.get("kind") or "")
    semantic = _semantic(node)
    lvalue = node.get("lvalue") or semantic.get("target")
    rvalue = node.get("rvalue") or semantic.get("value") or semantic.get("expression_normalized")

    if kind == "StorageLocationResolve":
        location = semantic.get("location") or {}
        access = location.get("access") or node.get("rvalue")
        return f"/* resolved storage location: {access}; */" if access else "/* resolved storage location */"
    if kind == "StateRead":
        access = semantic.get("access") or rvalue
        return f"{lvalue} = {access};" if lvalue else f"read({access});"
    if kind == "StateWrite":
        access = semantic.get("access") or node.get("lvalue")
        value = semantic.get("value") or node.get("rvalue")
        return f"{access} = {value};" if access and value is not None else "/* unresolved state write */"
    if kind == "Require":
        condition = node.get("condition") or semantic.get("condition")
        return f"require({condition});" if condition else "require(/* unresolved condition */);"
    if kind == "EventEmit":
        return _event_line(node)
    if kind == "ExternalCall":
        target = semantic.get("target") or node.get("lvalue") or "target"
        signature = semantic.get("selector_signature")
        args = semantic.get("arguments") or node.get("arguments") or []
        call_kind = semantic.get("call_kind") or "call"
        if signature:
            call = f"{call_kind} {target}.{signature.split('(')[0]}({', '.join(map(str, args))})"
        else:
            call = f"{call_kind}({target}, {', '.join(map(str, args))})"
        return f"{lvalue} = {call};" if lvalue else f"{call};"
    if kind == "Return":
        value = node.get("rvalue") or (semantic.get("resolved_operands") or semantic.get("values") or [None])[0]
        return f"return {value};" if value else "return;"
    if kind == "ValueCompute":
        if lvalue and rvalue is not None:
            return f"{lvalue} = {rvalue};"
        operation = semantic.get("operation")
        args = semantic.get("arguments") or node.get("arguments") or []
        if lvalue and operation:
            return f"{lvalue} = {operation}({', '.join(map(str, args))});"
        return f"/* value computation: {operation or 'unresolved'} */"
    if kind in {"InternalCall", "ExternalCall", "YulLocalFunctionCall", "FunctionCall"}:
        target = semantic.get("target") or node.get("target") or semantic.get("function") or "call"
        args = semantic.get("arguments") or node.get("arguments") or []
        return f"{lvalue + ' = ' if lvalue else ''}{target}({', '.join(map(str, args))});"
    if kind == "Revert":
        return "revert();"
    if kind == "BranchCondition":
        # A branch condition is rendered at the terminator rather than as an
        # independent assignment.  Keeping it out of block bodies avoids a
        # duplicate condition beside the same outgoing edges.
        return None
    operation = semantic.get("operation") or kind
    return f"/* {operation}: semantic node {node.get('semantic_id', '<unknown>')} */"


def _block_nodes(block: Json, by_id: dict[str, Json]) -> list[Json]:
    # ``semantic_ids`` is the canonical SFIR block-local execution order.  It
    # is CFG-derived: a fused block contains its predecessor's operations
    # followed by its unique successor's operations.  Do not impose a display
    # ranking by operation kind here, because that could reverse a real
    # def-use sequence (for example a calculation followed by a state write).
    return [by_id[item] for item in block.get("semantic_ids") or [] if item in by_id]


def _branch_expression(block: Json, by_id: dict[str, Json]) -> str | None:
    for node in _block_nodes(block, by_id):
        if node.get("kind") == "BranchCondition":
            return str(node.get("rvalue") or _semantic(node).get("expression") or "") or None
    return None


def _block_lines(block: Json, by_id: dict[str, Json]) -> list[str]:
    lines: list[str] = []
    nodes = _block_nodes(block, by_id)
    rendered_values = "\n".join(str(node.get("rvalue") or _semantic(node).get("value") or "") for node in nodes if node.get("kind") != "StateRead")
    for node in nodes:
        # A synthetic evaluator temporary is source evidence for a high-level
        # expression already rendered in this block.  Rendering it as another
        # assignment would falsely look like a second state read.  Keep its
        # SFIR fact visible as a comment at the same CFG position instead.
        if node.get("kind") == "StateRead" and str(node.get("lvalue") or "").startswith("__sseir_eval_"):
            access = _semantic(node).get("access") or node.get("rvalue")
            if access and str(access) in rendered_values:
                lines.append(f"/* state read used by enclosing expression: {access}; */")
                continue
        line = render_sfir_node_c_like(node)
        if line:
            lines.append(line)
    terminator = block.get("terminator") or {}
    kind = str(terminator.get("kind") or "")
    if kind == "Revert" and not any(line.startswith("revert(") for line in lines):
        lines.append("revert();")
    elif kind in {"Stop", "Terminal"} and not any(line.startswith("return") or line.startswith("revert") for line in lines):
        lines.append("stop;")
    return lines


def _collapsed_transport_lines(block: Json) -> list[str]:
    """Show compressed CFG provenance without restoring empty labels."""
    transports = block.get("collapsed_entry_transport") or []
    roles = list(dict.fromkeys(
        str(item.get("role") or "transport")
        for item in transports if isinstance(item, dict)
    ))
    transitions = block.get("entry_boundary_transitions") or []
    boundaries = list(dict.fromkeys(
        f"{item.get('from_lang')}->{item.get('to_lang')}"
        for item in transitions if isinstance(item, dict) and item.get("from_lang") and item.get("to_lang")
    ))
    notes: list[str] = []
    if roles:
        notes.append(f"collapsed transport: {', '.join(roles)}")
    if boundaries:
        notes.append(f"boundary: {', '.join(boundaries)}")
    return [f"/* {'; '.join(notes)} */"] if notes else []


def _control_line(block: Json, function: Json, by_id: dict[str, Json]) -> str | None:
    edges = _outgoing(function, str(block.get("block_id")))
    true_edge = next((edge for edge in edges if edge.get("kind") == "true"), None)
    false_edge = next((edge for edge in edges if edge.get("kind") == "false"), None)
    if true_edge and false_edge:
        return f"if ({true_edge.get('guard') or _branch_expression(block, by_id) or '/* unresolved condition */'})"
    cases = [edge for edge in edges if str(edge.get("kind", "")).startswith("case") or edge.get("kind") == "default"]
    if cases:
        expression = _branch_expression(block, by_id) or "/* unresolved discriminant */"
        return f"switch ({expression})"
    return None


def _outgoing(function: Json, block_id: str) -> list[Json]:
    return [edge for edge in (function.get("fact_cfg") or {}).get("edges") or [] if edge.get("from") == block_id]


def _edge_statement_lines(block: Json, function: Json, aliases: dict[str, str], by_id: dict[str, Json]) -> list[str]:
    edges = _outgoing(function, str(block.get("block_id")))
    if not edges:
        return []
    target = lambda edge: aliases.get(str(edge.get("to")), "<missing>")
    true_edge = next((edge for edge in edges if edge.get("kind") == "true"), None)
    false_edge = next((edge for edge in edges if edge.get("kind") == "false"), None)
    expression = _branch_expression(block, by_id)
    if true_edge and false_edge:
        guard = true_edge.get("guard") or expression or "/* unresolved condition */"
        return [f"if ({guard}) goto {target(true_edge)}; else goto {target(false_edge)};"]
    cases = [edge for edge in edges if str(edge.get("kind", "")).startswith("case") or edge.get("kind") == "default"]
    if cases:
        expression = _branch_expression(block, by_id) or "/* unresolved discriminant */"
        lines = [f"switch ({expression}) {{"]
        for edge in cases:
            kind = str(edge.get("kind") or "case")
            if kind == "default":
                label = "default"
            elif kind.startswith("case:"):
                label = f"case {kind.split(':', 1)[1].strip()}"
            else:
                label = f"case /* {edge.get('guard') or 'unresolved'} */"
            lines.append(f"  {label}: goto {target(edge)};")
        lines.append("}")
        return lines
    if len(edges) == 1:
        edge = edges[0]
        notes: list[str] = []
        if edge.get("kind") == "loop back":
            notes.append("loop back")
        collapsed = edge.get("contracted_blocks") or []
        if collapsed:
            notes.append(f"collapsed {len(collapsed)} inert block{'s' if len(collapsed) != 1 else ''}")
        transitions = edge.get("boundary_transitions") or []
        boundaries = list(dict.fromkeys(
            f"{item.get('from_lang')}->{item.get('to_lang')}"
            for item in transitions if isinstance(item, dict) and item.get("from_lang") and item.get("to_lang")
        ))
        if boundaries:
            notes.append(f"boundary {'/'.join(boundaries)}")
        suffix = f" /* {', '.join(notes)} */" if notes else ""
        return [f"goto {target(edge)};{suffix}"]
    return [f"/* {edge.get('kind')}: goto {target(edge)}; guard={edge.get('guard', '')} */" for edge in edges]


def _function_header(function: Json) -> str:
    signature = function.get("signature") or function.get("function") or "function()"
    return f"function {signature}"


def render_sfir_c_like(payload: Json) -> str:
    lines = ["// Semantic Fact IR C-like view", "// Canonical source: final SFIR only; labels preserve Fact CFG control flow.", ""]
    for function in payload.get("functions") or []:
        aliases = _aliases(function)
        by_id = _nodes_by_id(function)
        lines.extend([f"// {function.get('function_id')}", _function_header(function) + " {"])
        for block in _ordered_blocks(function):
            block_id = str(block.get("block_id"))
            fused = block.get("fused_source_blocks") or []
            fusion = f"; fused={len(fused)} linear semantic blocks" if len(fused) > 1 else ""
            lines.append(f"{aliases[block_id]}: /* {block_id}; {block.get('kind', 'unknown')}{fusion} */")
            for text in _collapsed_transport_lines(block):
                lines.append(f"  {text}")
            for text in _block_lines(block, by_id):
                lines.append(f"  {text}")
            for text in _edge_statement_lines(block, function, aliases, by_id):
                lines.append(f"  {text}")
        lines.extend(["}", ""])
    return "\n".join(lines).rstrip() + "\n"


def render_fact_cfg_text(payload: Json) -> str:
    lines = ["Semantic Fact CFG", f"Source: {payload.get('source') or '<unknown>'}"]
    for function in payload.get("functions") or []:
        aliases = _aliases(function)
        by_id = _nodes_by_id(function)
        lines.extend(["", f"Function {function.get('function_id')}"])
        for block in _ordered_blocks(function):
            block_id = str(block.get("block_id"))
            term = block.get("terminator") or {}
            fused = block.get("fused_source_blocks") or []
            fusion = f" fused={len(fused)}" if len(fused) > 1 else ""
            lines.append(f"  {aliases[block_id]} [{block.get('kind')}] {block_id}{fusion}")
            if term.get("kind"):
                lines.append(f"    terminator: {term.get('kind')}")
            for text in _collapsed_transport_lines(block):
                lines.append(f"    {text}")
            for text in _block_lines(block, by_id):
                lines.append(f"    {text}")
            if control := _control_line(block, function, by_id):
                lines.append(f"    {control}")
            for edge in _outgoing(function, block_id):
                label = edge.get("kind") or "edge"
                guard = f" guard={edge['guard']}" if edge.get("guard") else ""
                collapsed = edge.get("contracted_blocks") or []
                via = f" collapsed={len(collapsed)}" if collapsed else ""
                lines.append(f"    -> {aliases.get(str(edge.get('to')), '<missing>')} [{label}{guard}{via}]")
        phis = (function.get("fact_ssa") or {}).get("phis") or []
        if phis:
            lines.append("  FactPhi:")
            for phi in phis:
                lines.append(f"    {phi['version']} = phi({', '.join(phi['incoming_versions'])})")
    return "\n".join(lines).rstrip() + "\n"


def _dot_block_style(block: Json) -> Json:
    term = block.get("terminator") or {}
    term_kind = str(term.get("kind") or "")
    style: Json = {"shape": "box", "style": "rounded,filled", "fontname": "Consolas", "fontsize": "10", "color": "#8a8f98", "fillcolor": "#ffffff"}
    if block.get("kind") == "yul":
        style.update({"fillcolor": "#fff4d6", "color": "#c48700"})
    elif block.get("kind") == "solidity":
        style.update({"fillcolor": "#e8f2ff", "color": "#3574b7"})
    if term_kind == "Branch":
        style.update({"shape": "diamond", "fillcolor": "#f5ecff", "color": "#7a4bb3"})
    elif term_kind in {"Return", "Revert", "Stop", "Terminal"}:
        style.update({"peripheries": "2", "fillcolor": "#ffe7e7" if term_kind == "Revert" else "#e9ffe8"})
    return style


def _attrs_to_dot(attrs: Json) -> str:
    return ", ".join(f'{key}="{dot_escape(value)}"' for key, value in attrs.items())


def render_fact_cfg_dot(function: Json) -> str:
    aliases = _aliases(function)
    by_id = _nodes_by_id(function)
    name = safe_filename(str(function.get("function_id") or "function"))
    lines = [
        f'digraph "{dot_escape(name)}" {{',
        f'  graph [rankdir=TB, bgcolor="#ffffff", labelloc=t, fontsize=14, fontname="Consolas", label="Fact CFG: {dot_escape(function.get("function_id"))}"];',
        '  node [fontname="Consolas"];',
        '  edge [fontname="Consolas", fontsize="9"];',
    ]
    for block in _ordered_blocks(function):
        block_id = str(block.get("block_id"))
        fused = block.get("fused_source_blocks") or []
        fusion = f"; fused {len(fused)} linear semantic blocks" if len(fused) > 1 else ""
        label_lines = [f"{aliases[block_id]} [{block.get('kind', 'unknown')}]{fusion}", _short(block_id, 100)]
        label_lines.extend(_short(line, 120) for line in _collapsed_transport_lines(block))
        label_lines.extend(_short(line, 120) for line in _block_lines(block, by_id))
        if control := _control_line(block, function, by_id):
            label_lines.append(_short(control, 120))
        attrs = _dot_block_style(block)
        attrs["label"] = "\\l".join(label_lines) + "\\l"
        lines.append(f'  "{dot_escape(block_id)}" [{_attrs_to_dot(attrs)}];')
    for edge in (function.get("fact_cfg") or {}).get("edges") or []:
        source = str(edge.get("from") or "")
        target = str(edge.get("to") or "")
        if not source or not target:
            continue
        label = str(edge.get("kind") or "edge")
        if edge.get("guard"):
            label += f": {edge['guard']}"
        collapsed = edge.get("contracted_blocks") or []
        if collapsed:
            label += f" [collapsed {len(collapsed)} inert blocks]"
        color = "#22863a" if edge.get("kind") in {"true", "case"} else "#cb2431" if edge.get("kind") in {"false", "default"} else "#6f42c1" if edge.get("kind") == "loop back" else "#586069"
        lines.append(f'  "{dot_escape(source)}" -> "{dot_escape(target)}" [label="{dot_escape(_short(label, 150))}", color="{color}"];')
    lines.append("}")
    return "\n".join(lines) + "\n"


def write_sfir_c_like_text(path: Path, payload: Json) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_sfir_c_like(payload), encoding="utf-8")


def write_fact_cfg_text(path: Path, payload: Json) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_fact_cfg_text(payload), encoding="utf-8")


def write_fact_cfg_dot_files(payload: Json, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    used: set[str] = set()
    for function in payload.get("functions") or []:
        name = safe_filename(str(function.get("function_id") or "function"))
        base = name
        suffix = 2
        while name in used:
            name = f"{base}_{suffix}"
            suffix += 1
        used.add(name)
        path = output_dir / f"{name}.dot"
        path.write_text(render_fact_cfg_dot(function), encoding="utf-8")
        written.append(path)
    return written
