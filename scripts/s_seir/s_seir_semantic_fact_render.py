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
    args = semantic.get("arguments") or semantic.get("args") or node.get("arguments") or []
    return f"emit {event}({', '.join(map(str, args))});"


def _call_options(semantic: Json, *, include_salt: bool = False) -> str:
    options: list[str] = []
    for key, label in (("call_value", "value"), ("call_gas", "gas")):
        value = semantic.get(key)
        if value not in {None, ""}:
            options.append(f"{label}: {value}")
    if include_salt and semantic.get("salt") not in {None, ""}:
        options.append(f"salt: {semantic['salt']}")
    return f"{{{', '.join(options)}}}" if options else ""


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
        condition = node.get("condition") or semantic.get("condition") or semantic.get("guard")
        return f"require({condition});" if condition else "require(/* unresolved condition */);"
    if kind == "Assert":
        condition = node.get("condition") or semantic.get("guard")
        return f"assert({condition});" if condition else "assert(/* unresolved condition */);"
    if kind == "EventEmit":
        return _event_line(node)
    if kind == "ExternalCall":
        target = semantic.get("target") or node.get("lvalue") or "target"
        signature = semantic.get("selector_signature")
        function = semantic.get("function")
        args = semantic.get("arguments") or node.get("arguments") or []
        call_kind = semantic.get("call_kind") or "call"
        if signature:
            call = f"{call_kind} {target}.{signature.split('(')[0]}({', '.join(map(str, args))})"
        elif function and target:
            call = f"{target}.{function}({', '.join(map(str, args))})"
        else:
            call = f"{call_kind}({target}, {', '.join(map(str, args))})"
        return f"{lvalue} = {call};" if lvalue else f"{call};"
    if kind == "ModifierApply":
        modifier = semantic.get("modifier") or semantic.get("function") or "modifier"
        args = semantic.get("arguments") or []
        return f"apply modifier {modifier}({', '.join(map(str, args))}); /* wraps this function body at its placeholder */"
    if kind == "BaseConstructorCall":
        contract = semantic.get("constructor_contract") or semantic.get("function_contract") or "BaseContract"
        args = semantic.get("arguments") or []
        return f"{contract}({', '.join(map(str, args))}); /* base constructor */"
    if kind in {
        "AbiEncode", "AbiDecode", "Concat", "HashCompute", "ModularArithmetic", "SignatureRecover",
        "GasQuery", "BlockHashQuery",
    }:
        signature = semantic.get("builtin_signature") or semantic.get("function") or kind
        name = str(signature).split("(", 1)[0]
        args = semantic.get("arguments") or []
        call = f"{name}({', '.join(map(str, args))})"
        return f"{lvalue} = {call};" if lvalue else f"{call};"
    if kind == "AddressPropertyRead":
        address = semantic.get("address") or "address"
        property_name = semantic.get("property") or "property"
        expression = f"{address}.{property_name}"
        return f"{lvalue} = {expression};" if lvalue else f"read({expression});"
    if kind == "UnmodeledBuiltinCall":
        signature = semantic.get("builtin_signature") or semantic.get("function") or "<unknown builtin>"
        args = semantic.get("arguments") or []
        return f"/* unmodeled Slither builtin: {signature}; args=[{', '.join(map(str, args))}] */"
    if kind == "UnmodeledSlithIROperation":
        operation = semantic.get("slithir_kind") or "<unknown operation>"
        return f"/* unmodeled SlithIR operation: {operation}; */"
    if kind == "SelfDestruct":
        function = semantic.get("function") or "selfdestruct"
        args = semantic.get("arguments") or []
        return f"{function}({', '.join(map(str, args))});"
    if kind in {"LowLevelCall", "LibraryCall", "InternalCall", "InternalDynamicCall", "BuiltinCall"}:
        target = semantic.get("target") or semantic.get("function_contract") or semantic.get("function") or "call"
        function = semantic.get("function") or "call"
        args = semantic.get("arguments") or []
        call = f"{target}.{function}{_call_options(semantic)}({', '.join(map(str, args))})" if semantic.get("target") else f"{function}({', '.join(map(str, args))})"
        return f"{lvalue} = {call};" if lvalue else f"{call};"
    if kind == "NewContract":
        contract = semantic.get("contract") or "Contract"
        args = semantic.get("constructor_arguments") or []
        return f"{lvalue} = new {contract}{_call_options(semantic, include_salt=True)}({', '.join(map(str, args))});" if lvalue else f"new {contract}{_call_options(semantic, include_salt=True)}({', '.join(map(str, args))});"
    if kind == "NewArray":
        array_type = semantic.get("array_type") or "array"
        length = semantic.get("length")
        return f"{lvalue} = new {array_type}({length});" if lvalue else f"new {array_type}({length});"
    if kind == "ArrayConstruct":
        values = semantic.get("elements") or []
        return f"{lvalue} = [{', '.join(map(str, values))}];" if lvalue else f"[{', '.join(map(str, values))}];"
    if kind == "NewStructure":
        structure = semantic.get("structure") or "Struct"
        values = semantic.get("arguments") or []
        names = semantic.get("argument_names") or []
        arguments = [f"{name}: {value}" for name, value in zip(names, values)] if names and len(names) == len(values) else [str(value) for value in values]
        initializer = "{" + ", ".join(arguments) + "}" if names else ", ".join(arguments)
        return f"{lvalue} = {structure}({initializer});" if lvalue else f"{structure}({initializer});"
    if kind == "TupleUnpack":
        tuple_value = semantic.get("tuple") or "tuple"
        return f"{lvalue} = {tuple_value}[{semantic.get('index')}];" if lvalue else None
    if kind == "ValueTransferCall":
        target = semantic.get("target") or "recipient"
        method = semantic.get("method") or "transfer"
        value = semantic.get("value")
        # ``Transfer`` has no SlithIR lvalue.  ``target`` is an operand, not
        # an assignment destination; only ``Send`` may render its bool result
        # assignment when the final fact actually owns an lvalue.
        result = node.get("lvalue")
        return f"{result + ' = ' if result else ''}{target}.{method}({value});"
    if kind == "Return":
        value = node.get("rvalue") or (semantic.get("resolved_operands") or semantic.get("values") or [None])[0]
        if isinstance(value, (list, tuple)):
            return f"return ({', '.join(map(str, value))});"
        return f"return {value};" if value else "return;"
    if kind in {"ValueAssign", "ValueCompute", "TypeConversion", "IndexAccess", "MemberAccess", "LengthRead", "CodeSizeQuery", "NewElementaryType"}:
        if lvalue and rvalue is not None:
            return f"{lvalue} = {rvalue};"
        operation = semantic.get("operation")
        args = semantic.get("arguments") or node.get("arguments") or []
        if lvalue and operation:
            return f"{lvalue} = {operation}({', '.join(map(str, args))});"
        return f"/* value computation: {operation or 'unresolved'} */"
    if kind == "Delete":
        return f"delete {lvalue};" if lvalue else "delete /* unresolved target */;"
    if kind in {"InternalCall", "ExternalCall", "YulLocalFunctionCall", "FunctionCall"}:
        target = semantic.get("target") or node.get("target") or semantic.get("function") or "call"
        args = semantic.get("arguments") or node.get("arguments") or []
        return f"{lvalue + ' = ' if lvalue else ''}{target}({', '.join(map(str, args))});"
    if kind == "Revert":
        function = str(semantic.get("function") or "")
        args = semantic.get("arguments") or []
        if function.startswith("revert "):
            error = function[len("revert "):].split("(", 1)[0].strip()
            return f"revert {error}({', '.join(map(str, args))});"
        if args:
            return f"revert({', '.join(map(str, args))});"
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
    if kind == "ModifierPlaceholder":
        lines.append("/* modifier placeholder: resume wrapped function body; continue modifier CFG afterward */")
    elif kind == "Break":
        lines.append("break;")
    elif kind == "Continue":
        lines.append("continue;")
    elif kind == "Revert" and not any(line.startswith("revert") for line in lines):
        lines.append("revert();")
    elif kind in {"Stop", "Terminal"} and not any(line.startswith("return") or line.startswith("revert") or line.startswith(("selfdestruct", "suicide")) for line in lines):
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
    terminator = block.get("terminator") or {}
    if terminator.get("kind") == "Try":
        return "try-call outcome dispatch"
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
    if (block.get("terminator") or {}).get("kind") in {"Break", "Continue"}:
        return []
    if (block.get("terminator") or {}).get("kind") == "Try":
        success = next((edge for edge in edges if edge.get("kind") == "try_success"), None)
        catches = [edge for edge in edges if str(edge.get("kind") or "").startswith("catch")]
        # A Solidity try call has outcome alternatives, not a Boolean guard.
        # Retain the AST-proven labels instead of inventing a condition.
        if success and len(catches) + 1 == len(edges):
            lines = [f"try {{ goto {target(success)}; }}"]
            for edge in catches:
                role = str(edge.get("kind") or "catch")
                suffix = role.split(":", 1)[1] if ":" in role else ""
                clause = f"catch ({suffix})" if suffix else "catch"
                lines.append(f"{clause} {{ goto {target(edge)}; }}")
            return lines
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
    declaration = function.get("declaration") or {}
    if declaration.get("is_constructor"):
        keyword = "constructor"
    elif declaration.get("is_receive"):
        keyword = "receive"
    elif declaration.get("is_fallback"):
        keyword = "fallback"
    else:
        keyword = "modifier" if declaration.get("declaration_kind") == "modifier" else "function"
    name = declaration.get("name") or function.get("function") or "function"
    parameters = declaration.get("parameters") or []
    if parameters:
        rendered_parameters = []
        for parameter in parameters:
            if not isinstance(parameter, dict):
                continue
            parameter_type = str(parameter.get("type") or "unknown")
            parameter_name = str(parameter.get("base_name") or parameter.get("name") or "").strip()
            rendered_parameters.append(f"{parameter_type} {parameter_name}".strip())
        signature = f"{name}({', '.join(rendered_parameters)})"
    else:
        signature = function.get("signature") or f"{name}()"
    if keyword == "modifier":
        return f"modifier {signature}"
    suffixes: list[str] = []
    visibility = "" if keyword == "constructor" else str(declaration.get("visibility") or "").strip()
    if visibility:
        suffixes.append(visibility)
    if declaration.get("pure"):
        suffixes.append("pure")
    elif declaration.get("view"):
        suffixes.append("view")
    if declaration.get("payable"):
        suffixes.append("payable")
    if declaration.get("is_virtual"):
        suffixes.append("virtual")
    if declaration.get("is_override"):
        suffixes.append("override")
    returns = declaration.get("returns") or []
    if returns:
        rendered_returns = []
        for value in returns:
            if not isinstance(value, dict):
                continue
            value_type = str(value.get("type") or "unknown")
            value_name = str(value.get("base_name") or value.get("name") or "").strip()
            rendered_returns.append(f"{value_type} {value_name}".strip())
        if rendered_returns:
            suffixes.append(f"returns ({', '.join(rendered_returns)})")
    if keyword == "constructor":
        # Slither's constructor name is literally ``constructor``.  It is a
        # special Solidity declaration, never a normal ``function`` header.
        return " ".join([signature, *suffixes])
    if keyword in {"receive", "fallback"}:
        return " ".join([f"{keyword}()", *suffixes])
    return " ".join([f"function {signature}", *suffixes])


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
    modifier_links = payload.get("modifier_application_links") or []
    if modifier_links:
        lines.extend(["", "Modifier application links"])
        for link in modifier_links:
            if not isinstance(link, dict):
                continue
            target = link.get("modifier_function_id") or link.get("modifier_function_canonical_name") or "<unresolved>"
            placeholders = ", ".join(link.get("modifier_placeholder_blocks") or []) or "<unresolved>"
            continuation = link.get("caller_continuation") or {}
            caller_block = continuation.get("fact_block") or "<unresolved>"
            lines.append(
                f"  {link.get('caller_function_id')}:{link.get('modifier_apply_semantic_id')} -> {target} "
                f"[resolution={link.get('resolution')}; caller_continuation={caller_block}; placeholders={placeholders}]"
            )
    call_links = payload.get("direct_call_links") or []
    if call_links:
        lines.extend(["", "Direct call declaration links"])
        for link in call_links:
            if not isinstance(link, dict):
                continue
            target = link.get("callee_function_id") or link.get("callee_canonical_name") or "<unresolved>"
            lines.append(
                f"  {link.get('caller_function_id')}:{link.get('call_semantic_id')} -> {target} "
                f"[{link.get('kind')}; resolution={link.get('resolution')}; block={link.get('caller_fact_block') or '<unresolved>'}]"
            )
    dynamic_links = payload.get("dynamic_call_links") or []
    if dynamic_links:
        lines.extend(["", "Dynamic call candidate links"])
        for link in dynamic_links:
            if not isinstance(link, dict):
                continue
            candidates = ", ".join(link.get("callee_function_ids") or link.get("candidate_canonical_names") or []) or "<unresolved>"
            lines.append(
                f"  {link.get('caller_function_id')}:{link.get('call_semantic_id')} -> {{{candidates}}} "
                f"[ssa={link.get('function_ssa') or '<unresolved>'}; type={link.get('function_type') or '<unknown>'}; resolution={link.get('resolution')}]"
            )
    contracts = payload.get("contracts") or []
    if contracts:
        lines.extend(["", "Contract declaration relations"])
        for contract in contracts:
            if not isinstance(contract, dict):
                continue
            immediate = ", ".join(item.get("name", "") for item in contract.get("immediate_inheritance") or [] if isinstance(item, dict)) or "<none>"
            linearized = ", ".join(item.get("name", "") for item in contract.get("inheritance") or [] if isinstance(item, dict)) or "<none>"
            lines.append(f"  {contract.get('name')}: immediate=[{immediate}]; execution_order=[{linearized}]")
    constructor_links = payload.get("base_constructor_links") or []
    if constructor_links:
        lines.extend(["", "Base constructor declaration links"])
        for link in constructor_links:
            if not isinstance(link, dict):
                continue
            caller = link.get("caller_function_id") or link.get("caller_contract") or "<unknown>"
            target = link.get("callee_function_id") or link.get("callee_canonical_name") or "<unresolved>"
            calls = ", ".join(link.get("call_semantic_ids") or []) or "declaration-only"
            lines.append(
                f"  {caller} -> {target} [{link.get('kind')}; resolution={link.get('resolution')}; call={calls}]"
            )
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
    elif term_kind in {"Return", "Revert", "Stop", "Terminal", "SelfDestruct"}:
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
