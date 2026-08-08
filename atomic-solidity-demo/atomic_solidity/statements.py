from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from atomic_solidity.lowerer import LoweringContext

from atomic_solidity.ast_index import DeclarationKind
from atomic_solidity.atomic_ir import LocationRef
from atomic_solidity.expressions import lower_expression


def lower_statement(node: dict[str, Any], context: LoweringContext) -> None:
    node_type = node.get("nodeType")
    if context.current_block.terminator is not None:
        return
    if node_type == "Block":
        for statement in node.get("statements", []):
            lower_statement(statement, context)
        return
    if node_type == "ExpressionStatement":
        expression = node.get("expression")
        if isinstance(expression, dict):
            lower_expression(expression, context)
        return
    if node_type == "VariableDeclarationStatement":
        _lower_variable_declaration_statement(node, context)
        return
    if node_type == "Return":
        _lower_return(node, context)
        return
    if node_type == "IfStatement":
        _lower_if(node, context)
        return
    if node_type == "ForStatement":
        _lower_for(node, context)
        return
    if node_type == "WhileStatement":
        _lower_while(node, context)
        return
    if node_type == "DoWhileStatement":
        _lower_do_while(node, context)
        return
    if node_type == "Break":
        _lower_loop_jump(node, context, is_continue=False)
        return
    if node_type == "Continue":
        _lower_loop_jump(node, context, is_continue=True)
        return
    if node_type == "UncheckedBlock":
        context.unchecked_depth += 1
        try:
            for statement in node.get("statements", []):
                lower_statement(statement, context)
        finally:
            context.unchecked_depth -= 1
        return
    context.diagnostic(
        "UNSUPPORTED_NODE",
        f"Unsupported statement node {node_type}",
        node,
    )
    context.emit("UNKNOWN_OPERATION", source_node=node, may_revert=True)


def _lower_variable_declaration_statement(
    node: dict[str, Any], context: LoweringContext
) -> None:
    declarations = [item for item in node.get("declarations", []) if isinstance(item, dict)]
    initial_value = node.get("initialValue")
    if not isinstance(initial_value, dict):
        return
    value = lower_expression(initial_value, context)
    if not declarations:
        return
    # Demo scope: tuple destructuring is represented, but only the first declared
    # target receives the tuple value until per-component assignment is added.
    declaration = declarations[0]
    location = LocationRef(
        id=f"local:{declaration.get('id')}",
        kind=DeclarationKind.LOCAL_VARIABLE.value,
        name=declaration.get("name") or f"<unnamed:{declaration.get('id')}>",
        declaration_ast_id=declaration.get("id"),
        type=declaration.get("typeDescriptions", {}).get("typeString"),
        source=context.source_info(declaration),
    )
    context.write_location(location, value, node)


def _lower_return(node: dict[str, Any], context: LoweringContext) -> None:
    expression = node.get("expression")
    inputs: list[str] = []
    if isinstance(expression, dict):
        inputs.append(lower_expression(expression, context))
    context.emit(
        "RETURN",
        inputs=inputs,
        attributes={"function_return_parameters": node.get("functionReturnParameters")},
        source_node=node,
        terminator=True,
    )


def _lower_if(node: dict[str, Any], context: LoweringContext) -> None:
    condition = lower_expression(node["condition"], context)
    true_block = context.new_block()
    false_block = context.new_block()
    join_block = context.new_block()
    context.branch_to(condition, true_block, false_block, node)

    context.current_block = true_block
    true_body = node.get("trueBody")
    if isinstance(true_body, dict):
        lower_statement(true_body, context)
    if context.current_block.terminator is None:
        context.jump_to(join_block, node)

    context.current_block = false_block
    false_body = node.get("falseBody")
    if isinstance(false_body, dict):
        lower_statement(false_body, context)
    if context.current_block.terminator is None:
        context.jump_to(join_block, node)

    context.current_block = join_block


def _lower_for(node: dict[str, Any], context: LoweringContext) -> None:
    initialization = node.get("initializationExpression")
    if isinstance(initialization, dict):
        lower_statement(initialization, context)
    if context.current_block.terminator is not None:
        return

    condition_block = context.new_block()
    body_block = context.new_block()
    loop_block = context.new_block()
    exit_block = context.new_block()
    context.jump_to(condition_block, node)

    condition = node.get("condition")
    if isinstance(condition, dict):
        condition_value = lower_expression(condition, context)
    else:
        condition_value = context.ids.value()
        context.emit(
            "CONST",
            output=condition_value,
            attributes={"value": "true", "kind": "bool"},
            source_node=node,
        )
    context.branch_to(condition_value, body_block, exit_block, node)

    context.current_block = body_block
    context.loop_targets.append((loop_block, exit_block))
    try:
        body = node.get("body")
        if isinstance(body, dict):
            lower_statement(body, context)
    finally:
        context.loop_targets.pop()
    if context.current_block.terminator is None:
        context.terminate_with_jump(loop_block, node)

    context.current_block = loop_block
    loop_expression = node.get("loopExpression")
    if isinstance(loop_expression, dict):
        lower_statement(loop_expression, context)
    if context.current_block.terminator is None:
        context.terminate_with_jump(condition_block, node)

    context.current_block = exit_block


def _lower_while(node: dict[str, Any], context: LoweringContext) -> None:
    condition_block = context.new_block()
    body_block = context.new_block()
    exit_block = context.new_block()
    context.jump_to(condition_block, node)

    condition_value = lower_expression(node["condition"], context)
    context.branch_to(condition_value, body_block, exit_block, node)

    context.current_block = body_block
    context.loop_targets.append((condition_block, exit_block))
    try:
        body = node.get("body")
        if isinstance(body, dict):
            lower_statement(body, context)
    finally:
        context.loop_targets.pop()
    if context.current_block.terminator is None:
        context.terminate_with_jump(condition_block, node)

    context.current_block = exit_block


def _lower_do_while(node: dict[str, Any], context: LoweringContext) -> None:
    body_block = context.new_block()
    condition_block = context.new_block()
    exit_block = context.new_block()
    context.jump_to(body_block, node)

    context.loop_targets.append((condition_block, exit_block))
    try:
        body = node.get("body")
        if isinstance(body, dict):
            lower_statement(body, context)
    finally:
        context.loop_targets.pop()
    if context.current_block.terminator is None:
        context.terminate_with_jump(condition_block, node)

    context.current_block = condition_block
    condition_value = lower_expression(node["condition"], context)
    context.branch_to(condition_value, body_block, exit_block, node)
    context.current_block = exit_block


def _lower_loop_jump(
    node: dict[str, Any], context: LoweringContext, *, is_continue: bool
) -> None:
    if not context.loop_targets:
        context.diagnostic(
            "INVALID_LOOP_CONTROL",
            f"{node.get('nodeType')} appears outside a loop",
            node,
        )
        context.emit("UNKNOWN_OPERATION", source_node=node)
        return
    continue_target, break_target = context.loop_targets[-1]
    context.terminate_with_jump(
        continue_target if is_continue else break_target,
        node,
    )
