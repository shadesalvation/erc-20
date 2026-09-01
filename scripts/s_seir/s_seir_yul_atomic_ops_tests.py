#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for item in (ROOT / "legacy_yul", ROOT / "s_seir"):
    text = str(item)
    if text not in sys.path:
        sys.path.insert(0, text)

from assembly_ast_cfg import AssemblyAstBlock, FunctionContext, yul_statement_text
from s_seir_model import FunctionUnit, SourceStatement
from s_seir_yul_atomic_ops import YulAtomicOperationExtractor


def yid(name: str, src: str = "") -> dict:
    return {"nodeType": "YulIdentifier", "name": name, "src": src}


def ylit(value: str, src: str = "") -> dict:
    return {"nodeType": "YulLiteral", "value": value, "src": src}


def ycall(name: str, *args: dict, src: str = "") -> dict:
    return {
        "nodeType": "YulFunctionCall",
        "functionName": yid(name),
        "arguments": list(args),
        "src": src,
    }


def statement(expr: dict, src: str) -> dict:
    return {"nodeType": "YulExpressionStatement", "expression": expr, "src": src}


def assignment(name: str, expr: dict, src: str) -> dict:
    return {"nodeType": "YulAssignment", "variableNames": [yid(name)], "value": expr, "src": src}


def extract(*statements: dict) -> dict:
    root = {"nodeType": "YulBlock", "statements": list(statements), "src": "0:500:0"}
    source = " " * 500
    block = AssemblyAstBlock(
        1,
        Path("Atomic.sol"),
        source,
        {"nodeType": "InlineAssembly", "src": "0:500:0", "AST": root},
        root,
        FunctionContext(contract="Atomic", name="run", parameters=[]),
    )
    refs = [
        SourceStatement(
            f"asm_s_{index}", "yul", yul_statement_text(item), item["src"],
            "Atomic.run()", "asm_block_1", {"assembly_block": 1},
        )
        for index, item in enumerate(statements, start=1)
    ]
    unit = FunctionUnit("Atomic.run()", "Atomic", "run", "run()", {"nodeType": "FunctionDefinition"})
    unit.assembly_blocks = [block]
    unit.source_statements = refs
    return YulAtomicOperationExtractor().extract(unit)


def test_nested_storage_update_is_atomic() -> None:
    update = statement(
        ycall(
            "sstore",
            yid("slot"),
            ycall("add", ycall("sload", yid("slot")), yid("amount")),
            src="10:50:0",
        ),
        "10:50:0",
    )
    operations = extract(update)["operations"]
    assert [item["operation"] for item in operations] == ["sload", "add", "sstore"]
    assert [item["atomic_kind"] for item in operations] == ["StorageRead", "ValueCompute", "StorageWrite"]
    assert operations[1]["arguments"][0] == operations[0]["result"]
    assert operations[2]["arguments"][1] == operations[1]["result"]
    assert operations[2]["dependencies"] == [operations[1]["atom_id"]]


def test_call_arguments_follow_yul_right_to_left_order() -> None:
    condition = {
        "nodeType": "YulIf",
        "condition": ycall(
            "iszero",
            ycall(
                "and",
                ycall("eq", ycall("mload", yid("out")), ylit("1")),
                ycall(
                    "call",
                    ycall("gas"), yid("target"), ylit("0"),
                    ycall("add", yid("ptr"), ylit("0x1c")), yid("size"), yid("out"), ylit("0x20"),
                ),
            ),
            src="70:100:0",
        ),
        "body": {"nodeType": "YulBlock", "statements": [], "src": "150:2:0"},
        "src": "70:100:0",
    }
    operations = extract(condition)["operations"]
    calls = [item["operation"] for item in operations]
    assert calls == ["add", "gas", "call", "mload", "eq", "and", "iszero", "branch"]
    call_atom = operations[2]
    assert call_atom["arguments"][0] == operations[1]["result"]
    assert call_atom["arguments"][3] == operations[0]["result"]
    assert operations[-1]["atomic_kind"] == "BranchCondition"


def test_assignment_keeps_explicit_final_copy() -> None:
    item = assignment("result", ycall("add", ycall("sload", yid("slot")), yid("delta")), "200:40:0")
    operations = extract(item)["operations"]
    assert [operation["operation"] for operation in operations] == ["sload", "add", "assign"]
    assert operations[-1]["result"] == "result"
    assert operations[-1]["arguments"] == [operations[-2]["result"]]
    assert operations[-1]["dependencies"] == [operations[-2]["atom_id"]]


def test_cfg_and_source_anchors_are_preserved() -> None:
    item = statement(ycall("mstore", ylit("0x00"), ycall("caller")), "300:30:0")
    payload = extract(item)
    operations = payload["operations"]
    assert len({operation["cfg_node_id"] for operation in operations}) == 1
    assert {operation["stmt_ref"] for operation in operations} == {"asm_s_1"}
    assert all(operation["cfg_block_id"].startswith("bb_asm1_n") for operation in operations)
    assert operations[0]["operation"] == "caller"
    assert operations[1]["arguments"][1] == operations[0]["result"]


def test_local_yul_function_body_is_atomized_in_its_own_cfg_namespace() -> None:
    body_statement = assignment("r", ycall("add", yid("x"), ylit("1")), "420:20:0")
    definition = {
        "nodeType": "YulFunctionDefinition",
        "name": "inc",
        "parameters": [yid("x")],
        "returnVariables": [yid("r")],
        "body": {"nodeType": "YulBlock", "statements": [body_statement], "src": "415:35:0"},
        "src": "400:55:0",
    }
    payload = extract(definition)
    operations = [item for item in payload["operations"] if item.get("yul_function") == "inc"]
    assert [item["operation"] for item in operations] == ["add", "assign"]
    assert all("_fn_inc_n" in item["cfg_block_id"] for item in operations)
    assert operations[-1]["result"] == "r"


def test_member_access_assignment_is_kept_as_atomic_lvalue() -> None:
    item = {
        "nodeType": "YulAssignment",
        "variableNames": [{
            "nodeType": "YulMemberAccess",
            "expression": yid("result"),
            "memberName": "slot",
        }],
        "value": ylit("0xa20d6e21d0e5255308"),
        "src": "460:30:0",
    }
    operations = extract(item)["operations"]
    assert len(operations) == 1
    assert operations[0]["operation"] == "assign"
    assert operations[0]["result"] == "result.slot"
    assert operations[0]["arguments"] == ["0xa20d6e21d0e5255308"]


def test_nested_condition_atoms_use_value_normalization_context() -> None:
    condition = {
        "nodeType": "YulIf",
        "condition": ycall("iszero", ycall("call", ycall("gas"), yid("target"), ylit("0"), ylit("0"), ylit("0"), ylit("0"), ylit("0"))),
        "body": {"nodeType": "YulBlock", "statements": [], "src": "490:2:0"},
        "src": "470:80:0",
    }
    operations = extract(condition)["operations"]
    gas = next(item for item in operations if item["operation"] == "gas")
    call = next(item for item in operations if item["operation"] == "call")
    root = next(item for item in operations if item["operation"] == "iszero")
    assert gas["execution_expression"] == "gas()"
    assert gas["normalized_expression"] == "gasleft()"
    assert "!= 0" not in gas["normalized_expression"]
    assert call["normalized_expression"].startswith("call(")
    assert root["normalized_expression"].endswith("== 0)")


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
    print(f"ok: {len(tests)} Yul atomic-operation tests")
