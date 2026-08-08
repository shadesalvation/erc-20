from __future__ import annotations

from typing import TYPE_CHECKING, Any

from atomic_solidity.ast_index import DeclarationKind
from atomic_solidity.atomic_ir import LocationRef

if TYPE_CHECKING:
    from atomic_solidity.lowerer import LoweringContext


COMPOUND_ASSIGNMENTS = {
    "+=": "+",
    "-=": "-",
    "*=": "*",
    "/=": "/",
    "%=": "%",
    "|=": "|",
    "&=": "&",
    "^=": "^",
}


def lower_expression(node: dict[str, Any], context: LoweringContext) -> str:
    node_type = node.get("nodeType")
    if node_type == "Literal":
        return _lower_literal(node, context)
    if node_type == "Identifier":
        location = lower_lvalue(node, context)
        return context.read_location(location, node)
    if node_type == "BinaryOperation":
        return _lower_binary_operation(node, context)
    if node_type == "UnaryOperation":
        return _lower_unary_operation(node, context)
    if node_type == "Assignment":
        return _lower_assignment(node, context)
    if node_type in {"IndexAccess", "MemberAccess"}:
        location = lower_lvalue(node, context)
        return context.read_location(location, node)
    if node_type == "FunctionCall":
        return _lower_function_call(node, context)
    if node_type == "TupleExpression":
        return _lower_tuple(node, context)
    if node_type == "Conditional":
        return _lower_conditional(node, context)
    return _unsupported_expression(node, context)


def lower_lvalue(node: dict[str, Any], context: LoweringContext) -> LocationRef:
    node_type = node.get("nodeType")
    if node_type == "Identifier":
        declaration_id = _referenced_declaration(node)
        declaration = context.index.node(declaration_id)
        kind = context.index.declaration_kind(declaration_id)
        name = node.get("name") or (declaration or {}).get("name") or "<unknown>"
        type_string = node.get("typeDescriptions", {}).get("typeString")
        if kind == DeclarationKind.STATE_VARIABLE:
            loc_id = context.ids.location()
            storage_attrs = _storage_attributes(context, declaration_id, name, [])
            context.emit(
                "STATE_LOCATION",
                output=loc_id,
                attributes=storage_attrs,
                source_node=node,
            )
            return LocationRef(
                id=loc_id,
                kind="storage",
                name=name,
                declaration_ast_id=declaration_id,
                type=type_string,
                source=context.source_info(node),
                attributes=storage_attrs,
            )
        return LocationRef(
            id=f"local:{declaration_id if declaration_id is not None else name}",
            kind=kind.value,
            name=name,
            declaration_ast_id=declaration_id,
            type=type_string,
            source=context.source_info(node),
        )
    if node_type == "IndexAccess":
        base = node.get("baseExpression")
        index_expression = node.get("indexExpression")
        if not isinstance(base, dict):
            return _unknown_location(node, context, "IndexAccess without baseExpression")
        index_values: list[str] = []
        if isinstance(index_expression, dict):
            index_values.append(lower_expression(index_expression, context))
        base_location = lower_lvalue(base, context)
        if base_location.kind != "storage":
            loc_id = context.ids.location()
            attrs = {
                "base": base_location.name,
                "base_location": base_location.id,
                "indices": index_values,
                "declaration_ast_id": base_location.declaration_ast_id,
            }
            context.emit(
                "LOCATION",
                inputs=[base_location.id, *index_values],
                output=loc_id,
                attributes=attrs,
                source_node=node,
            )
            return LocationRef(
                id=loc_id,
                kind="local_variable",
                name=f"{base_location.name}[]",
                declaration_ast_id=base_location.declaration_ast_id,
                type=node.get("typeDescriptions", {}).get("typeString"),
                source=context.source_info(node),
                attributes=attrs,
            )
        loc_id = context.ids.location()
        attrs = dict(base_location.attributes)
        attrs.update(
            {
                "base_state_variable": base_location.name,
                "base_location": base_location.id,
                "indices": index_values,
            }
        )
        context.emit(
            "STORAGE_LOCATION",
            inputs=[base_location.id, *index_values],
            output=loc_id,
            attributes=attrs,
            source_node=node,
        )
        return LocationRef(
            id=loc_id,
            kind="storage",
            name=base_location.name,
            declaration_ast_id=base_location.declaration_ast_id,
            type=node.get("typeDescriptions", {}).get("typeString"),
            source=context.source_info(node),
            attributes=attrs,
        )
    if node_type == "MemberAccess":
        expression = node.get("expression")
        member_name = node.get("memberName", "<member>")
        if isinstance(expression, dict):
            base_value = lower_expression(expression, context)
            loc_id = context.ids.location()
            attrs = {
                "member": member_name,
                "base_value": base_value,
                "referenced_declaration": _referenced_declaration(node),
            }
            context.emit(
                "MEMBER_LOCATION",
                inputs=[base_value],
                output=loc_id,
                attributes=attrs,
                source_node=node,
            )
            return LocationRef(
                id=loc_id,
                kind="member",
                name=member_name,
                declaration_ast_id=_referenced_declaration(node),
                type=node.get("typeDescriptions", {}).get("typeString"),
                source=context.source_info(node),
                attributes=attrs,
            )
    return _unknown_location(node, context, f"unsupported lvalue node {node_type}")


def _lower_literal(node: dict[str, Any], context: LoweringContext) -> str:
    output = context.ids.value()
    context.emit(
        "CONST",
        output=output,
        attributes={
            "value": node.get("value"),
            "hex_value": node.get("hexValue"),
            "kind": node.get("kind"),
        },
        source_node=node,
    )
    return output


def _lower_binary_operation(node: dict[str, Any], context: LoweringContext) -> str:
    operator = node.get("operator")
    if operator in {"&&", "||"}:
        return _lower_short_circuit(node, context)
    if _may_have_side_effect(node.get("leftExpression")) and _may_have_side_effect(
        node.get("rightExpression")
    ):
        context.diagnostic(
            "UNSPECIFIED_SIBLING_EVALUATION_ORDER",
            "Sibling expressions may have side effects; the demo emits AST recursion order but does not claim Solidity guarantees that order.",
            node,
        )
    left = lower_expression(node["leftExpression"], context)
    right = lower_expression(node["rightExpression"], context)
    output = context.ids.value()
    context.emit(
        "BINARY_OP",
        inputs=[left, right],
        output=output,
        attributes={
            "operator": operator,
            "common_type": node.get("commonType"),
            "checked": context.unchecked_depth == 0,
        },
        source_node=node,
        may_revert=operator in {"+", "-", "*"} and context.unchecked_depth == 0,
    )
    return output


def _lower_short_circuit(node: dict[str, Any], context: LoweringContext) -> str:
    operator = node.get("operator")
    left_value = lower_expression(node["leftExpression"], context)
    rhs_block = context.new_block()
    const_block = context.new_block()
    join_block = context.new_block()
    if operator == "&&":
        context.branch_to(left_value, rhs_block, const_block, node)
        const_value = "false"
    else:
        context.branch_to(left_value, const_block, rhs_block, node)
        const_value = "true"

    context.current_block = rhs_block
    rhs_value = lower_expression(node["rightExpression"], context)
    rhs_from = context.current_block
    context.jump_to(join_block, node)

    context.current_block = const_block
    constant = context.ids.value()
    context.emit(
        "CONST",
        output=constant,
        attributes={"value": const_value, "kind": "bool"},
        source_node=node,
    )
    const_from = context.current_block
    context.jump_to(join_block, node)

    context.current_block = join_block
    output = context.ids.value()
    context.emit(
        "PHI",
        inputs=[rhs_value, constant],
        output=output,
        attributes={
            "operator": operator,
            "incoming": {rhs_from.id: rhs_value, const_from.id: constant},
        },
        source_node=node,
    )
    return output


def _lower_conditional(node: dict[str, Any], context: LoweringContext) -> str:
    condition = lower_expression(node["condition"], context)
    true_block = context.new_block()
    false_block = context.new_block()
    join_block = context.new_block()
    context.branch_to(condition, true_block, false_block, node)

    context.current_block = true_block
    true_value = lower_expression(node["trueExpression"], context)
    true_from = context.current_block
    context.jump_to(join_block, node)

    context.current_block = false_block
    false_value = lower_expression(node["falseExpression"], context)
    false_from = context.current_block
    context.jump_to(join_block, node)

    context.current_block = join_block
    output = context.ids.value()
    context.emit(
        "PHI",
        inputs=[true_value, false_value],
        output=output,
        attributes={"incoming": {true_from.id: true_value, false_from.id: false_value}},
        source_node=node,
    )
    return output


def _lower_unary_operation(node: dict[str, Any], context: LoweringContext) -> str:
    operator = node.get("operator")
    sub_expression = node.get("subExpression")
    if operator in {"++", "--"} and isinstance(sub_expression, dict):
        location = lower_lvalue(sub_expression, context)
        old_value = context.read_location(location, sub_expression)
        one = context.ids.value()
        context.emit(
            "CONST",
            output=one,
            attributes={"value": "1", "kind": "number"},
            source_node=node,
        )
        new_value = context.ids.value()
        context.emit(
            "BINARY_OP",
            inputs=[old_value, one],
            output=new_value,
            attributes={
                "operator": "+" if operator == "++" else "-",
                "checked": context.unchecked_depth == 0,
            },
            source_node=node,
            may_revert=context.unchecked_depth == 0,
        )
        context.write_location(location, new_value, node)
        return new_value if node.get("prefix") else old_value
    if isinstance(sub_expression, dict):
        value = lower_expression(sub_expression, context)
        output = context.ids.value()
        context.emit(
            "UNARY_OP",
            inputs=[value],
            output=output,
            attributes={"operator": operator, "prefix": node.get("prefix")},
            source_node=node,
        )
        return output
    return _unsupported_expression(node, context)


def _lower_assignment(node: dict[str, Any], context: LoweringContext) -> str:
    operator = node.get("operator")
    left_node = node["leftHandSide"]
    location = lower_lvalue(left_node, context)
    if operator == "=":
        value = lower_expression(node["rightHandSide"], context)
        context.write_location(location, value, node)
        return value
    if operator in COMPOUND_ASSIGNMENTS:
        old_value = context.read_location(location, left_node)
        right = lower_expression(node["rightHandSide"], context)
        result = context.ids.value()
        binary_operator = COMPOUND_ASSIGNMENTS[operator]
        context.emit(
            "BINARY_OP",
            inputs=[old_value, right],
            output=result,
            attributes={
                "operator": binary_operator,
                "compound_assignment_operator": operator,
                "checked": context.unchecked_depth == 0,
            },
            source_node=node,
            may_revert=binary_operator in {"+", "-", "*"} and context.unchecked_depth == 0,
        )
        context.write_location(location, result, node)
        return result
    context.diagnostic("UNSUPPORTED_NODE", f"Unsupported assignment operator {operator}", node)
    return _unsupported_expression(node, context)


def _lower_function_call(node: dict[str, Any], context: LoweringContext) -> str:
    expression = node.get("expression")
    arguments = node.get("arguments", [])
    argument_values = [
        lower_expression(argument, context)
        for argument in arguments
        if isinstance(argument, dict)
    ]
    output = context.ids.value()
    call_kind = _call_kind(node, context)
    context.emit(
        call_kind,
        inputs=argument_values,
        output=output,
        attributes={
            "callee": _callee_name(expression),
            "callee_ast_id": _referenced_declaration(expression),
            "kind": node.get("kind"),
        },
        source_node=node,
        effects=["call"] if call_kind in {"CALL", "INTERNAL_CALL", "EXTERNAL_CALL"} else [],
        may_revert=call_kind in {"CALL", "EXTERNAL_CALL"},
    )
    return output


def _lower_tuple(node: dict[str, Any], context: LoweringContext) -> str:
    values = [
        lower_expression(component, context)
        for component in node.get("components", [])
        if isinstance(component, dict)
    ]
    if len(values) == 1:
        return values[0]
    output = context.ids.value()
    context.emit("TUPLE", inputs=values, output=output, source_node=node)
    return output


def _call_kind(node: dict[str, Any], context: LoweringContext) -> str:
    expression = node.get("expression")
    if node.get("kind") == "typeConversion" or (
        isinstance(expression, dict)
        and expression.get("nodeType") == "ElementaryTypeNameExpression"
    ):
        return "TYPE_CONVERSION"
    declaration = context.index.node(_referenced_declaration(expression))
    if declaration and declaration.get("nodeType") == "FunctionDefinition":
        return "INTERNAL_CALL"
    if isinstance(expression, dict) and expression.get("nodeType") == "MemberAccess":
        base = expression.get("expression", {})
        if isinstance(base, dict) and base.get("nodeType") == "Identifier":
            if base.get("name") in {"abi", "assert", "require", "keccak256"}:
                return "BUILTIN_CALL"
        return "EXTERNAL_CALL"
    if isinstance(expression, dict) and expression.get("nodeType") == "Identifier":
        name = expression.get("name")
        if name in {"require", "assert", "revert", "keccak256"}:
            return "BUILTIN_CALL"
    context.diagnostic(
        "UNKNOWN_CALL_KIND",
        "Could not confidently classify this function call.",
        node,
    )
    return "CALL"


def _callee_name(node: dict[str, Any] | None) -> str | None:
    if not isinstance(node, dict):
        return None
    if node.get("nodeType") == "Identifier":
        return node.get("name")
    if node.get("nodeType") == "MemberAccess":
        return node.get("memberName")
    if node.get("nodeType") == "ElementaryTypeNameExpression":
        return node.get("typeName", {}).get("name")
    return node.get("nodeType")


def _referenced_declaration(node: dict[str, Any] | None) -> int | None:
    if not isinstance(node, dict):
        return None
    value = node.get("referencedDeclaration")
    return value if isinstance(value, int) and value >= 0 else None


def _storage_attributes(
    context: LoweringContext, declaration_id: int | None, name: str, indices: list[str]
) -> dict[str, Any]:
    storage = context.index.storage_info(declaration_id) or {}
    return {
        "declaration_ast_id": declaration_id,
        "state_variable": name,
        "storage_slot": storage.get("slot"),
        "storage_offset": storage.get("offset"),
        "storage_type": storage.get("type"),
        "storage_label": storage.get("label"),
        "storage_contract": storage.get("contract"),
        "indices": indices,
    }


def _may_have_side_effect(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    if node.get("nodeType") in {"Assignment", "FunctionCall"}:
        return True
    return any(_may_have_side_effect(value) for value in node.values())


def _unsupported_expression(node: dict[str, Any], context: LoweringContext) -> str:
    context.diagnostic(
        "UNSUPPORTED_NODE",
        f"Unsupported expression node {node.get('nodeType')}",
        node,
    )
    output = context.ids.value()
    context.emit("UNKNOWN_OPERATION", output=output, source_node=node, may_revert=True)
    return output


def _unknown_location(
    node: dict[str, Any], context: LoweringContext, message: str
) -> LocationRef:
    context.diagnostic("UNSUPPORTED_NODE", message, node)
    loc_id = context.ids.location()
    context.emit("UNKNOWN_OPERATION", output=loc_id, source_node=node, may_revert=True)
    return LocationRef(
        id=loc_id,
        kind="unknown",
        name="<unknown>",
        declaration_ast_id=None,
        type=node.get("typeDescriptions", {}).get("typeString"),
        source=context.source_info(node),
    )
