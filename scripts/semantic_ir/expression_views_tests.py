from __future__ import annotations

from pathlib import Path
import sys


SCRIPTS = Path(__file__).resolve().parents[1]
SSEIR = SCRIPTS / "s_seir"
for path in (SCRIPTS, SSEIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from semantic_fact import build_function_level_semantic_fact_payload
from semantic_ir import build_semantic_ir_program
from s_seir_pipeline import build_sseir


FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "semantic_ir_expression_views.sol"


def render_expression(function, expr_id: str | None) -> str:
    if not expr_id:
        return ""
    node = function.expressions[expr_id]
    if node.kind == "Variable":
        return node.name or ""
    if node.kind == "Constant":
        return node.raw or str(node.value)
    kind = node.callee if node.kind == "Call" and node.callee else node.kind
    return f"{kind}({', '.join(render_expression(function, item) for item in node.operands)})"


def instructions(function, op: str):
    return [
        instruction
        for block in function.blocks.values()
        for instruction in block.instructions
        if instruction.op == op
    ]


def build():
    functions = build_sseir(FIXTURE, workdir=FIXTURE.parents[2], branch_preprocess=False)
    facts = build_function_level_semantic_fact_payload(functions, source=str(FIXTURE))
    return build_semantic_ir_program(functions, facts, source=str(FIXTURE))


def test_nested_storage_update(program) -> None:
    function = next(item for item in program.functions if ".update(" in item.function_id)
    read = instructions(function, "StateRead")[0]
    write = instructions(function, "StateWrite")[0]
    execution = render_expression(function, write.execution_expr)
    normalized = render_expression(function, write.normalized_expr)
    assert read.result and read.result in execution, (read.result, execution)
    assert "total" in normalized and "amount" in normalized, normalized
    assert write.instruction_id in function.values[read.result_value].uses


def test_environment_builtins(program) -> None:
    function = next(item for item in program.functions if ".environment(" in item.function_id)
    assignments = instructions(function, "Assign")
    pairs = {
        item.result: (
            render_expression(function, item.execution_expr),
            render_expression(function, item.normalized_expr),
        )
        for item in assignments
    }
    assert any("caller" in execution and "msg.sender" in normalized for execution, normalized in pairs.values()), pairs
    assert any("gas" in execution and "gasleft" in normalized for execution, normalized in pairs.values()), pairs


def test_memory_hash(program) -> None:
    function = next(item for item in program.functions if ".storeHash(" in item.function_id)
    write = instructions(function, "StateWrite")[0]
    execution = render_expression(function, write.execution_expr)
    normalized = render_expression(function, write.normalized_expr)
    assert "Keccak" in execution and "mload" in execution, execution
    assert normalized, normalized


def test_call_and_branch(program) -> None:
    function = next(item for item in program.functions if ".guardedCall(" in item.function_id)
    call = instructions(function, "ExternalCall")[0]
    assert call.execution_arguments
    assert call.data_objects
    call_data = function.data_objects[call.data_objects[0]]
    assert call_data.kind == "CallData"
    assert call_data.values
    branch = next(block.terminator for block in function.blocks.values() if block.terminator.kind == "Branch")
    assert branch.execution_condition and branch.normalized_condition


def test_raw_return(program) -> None:
    function = next(item for item in program.functions if ".rawReturn(" in item.function_id)
    terminal = next(block.terminator for block in function.blocks.values() if block.terminator.kind == "Return")
    assert len(terminal.execution_values) == 2, terminal.execution_values
    assert terminal.execution_values != terminal.normalized_values


if __name__ == "__main__":
    semantic_program = build()
    tests = [
        test_nested_storage_update,
        test_environment_builtins,
        test_memory_hash,
        test_call_and_branch,
        test_raw_return,
    ]
    for test in tests:
        test(semantic_program)
        print(f"PASS {test.__name__}")
