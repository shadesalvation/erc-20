#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "legacy_yul", ROOT / "s_seir"):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from s_seir_solidity_like_export import SolidityLikeRenderer
from s_seir_model import EffectNode, FunctionSSEIR, SemanticOverlay, SourceStatement


def expect(case: str, got, expected) -> None:
    if got != expected:
        raise AssertionError(f"{case}: expected {expected!r}, got {got!r}")


def test_tautological_branch_join_removed() -> None:
    got = SolidityLikeRenderer.simplify_disjunction(["cond", "!(cond)"])
    expect("tautology", got, [])


def test_common_prefix_branch_join_kept() -> None:
    got = SolidityLikeRenderer.simplify_disjunction([
        "outer && cond",
        "outer && !(cond)",
    ])
    expect("common_prefix", got, ["outer"])


def test_non_complement_paths_unchanged() -> None:
    got = SolidityLikeRenderer.simplify_disjunction([
        "outer && left",
        "outer && right",
    ])
    expect("non_complement", got, ["left && outer", "outer && right"])


def ov(kind: str, attrs: dict) -> SemanticOverlay:
    return SemanticOverlay("ov", kind, ["eff"], ["asm_s_1"], attrs)


def yul_stmt(stmt_id: str, text: str, start: int, length: int = 5) -> SourceStatement:
    origin = {"nodeType": "YulForLoop"} if text == "for" else {}
    return SourceStatement(stmt_id, "yul", text, f"{start}:{length}:0", "C.f()", "asm_block_1", origin)


def eff(effect_id: str, kind: str, refs: list[str], attrs: dict) -> EffectNode:
    return EffectNode(effect_id, kind, refs, attrs)


def memory_effect(effect_id: str, stmt_id: str, address: str, value: str, path_states: list[str]) -> EffectNode:
    return eff(effect_id, "MemoryWrite", [stmt_id], {
        "address": address,
        "value": value,
        "path_states": path_states,
    })


def loop_function(
    condition: str,
    loop_src: str,
    statements: list[SourceStatement],
    effects: list[EffectNode],
) -> FunctionSSEIR:
    control = {
        "blocks": [
            {
                "block_id": "bb_loop",
                "stmts": ["asm_s_1"],
                "terminator": {"kind": "Branch", "text": f"for condition {condition}", "node_kind": "loop-condition"},
                "attrs": {"src": loop_src},
            },
            {
                "block_id": "bb_body",
                "stmts": [stmt.stmt_id for stmt in statements if stmt.src.startswith(("120:", "130:", "140:", "150:"))],
                "terminator": {"kind": "YulNode", "node_kind": "statement"},
            },
            {
                "block_id": "bb_after",
                "stmts": [stmt.stmt_id for stmt in statements if stmt.src.startswith(("190:", "200:"))],
                "terminator": {"kind": "YulNode", "node_kind": "loop-merge"},
            },
        ],
        "edges": [
            {"from": "bb_loop", "to": "bb_body", "kind": f"true: {condition}"},
            {"from": "bb_loop", "to": "bb_after", "kind": f"false: !({condition})"},
            {"from": "bb_body", "to": "bb_loop", "kind": "loop back"},
            {"from": "bb_body", "to": "bb_after", "kind": "break"},
        ],
    }
    return FunctionSSEIR("C.f()", "C", "f", "f()", statements, control, [], effects, [])


def render_body(fn: FunctionSSEIR) -> str:
    return "\n".join(SolidityLikeRenderer(fn).render_assembly_block_body("asm_block_1"))


def test_consumed_keccak_step_hidden_by_recovered_state_read() -> None:
    lines = SolidityLikeRenderer.evaluation_step_lines([
        ov("EvaluationStep", {
            "temp": "t1",
            "call": "keccak256",
            "evaluated_args": ["0x0c", "0x20"],
            "solidity_like": "t1 = keccak256(0x0c, 0x20);",
            "order": 1,
        }),
        ov("EvaluationStep", {
            "temp": "t2",
            "call": "sload",
            "evaluated_args": ["t1"],
            "solidity_like": "t2 = sload(t1);",
            "order": 2,
        }),
        ov("StateVariableRead", {
            "target": "t2",
            "solidity_like": "t2 = storage[slot];",
        }),
    ])
    expect("consumed_keccak", lines, ["t2 = storage[slot];"])


def test_keccak_step_kept_when_used_elsewhere() -> None:
    lines = SolidityLikeRenderer.evaluation_step_lines([
        ov("EvaluationStep", {
            "temp": "t1",
            "call": "keccak256",
            "evaluated_args": ["0x0c", "0x20"],
            "solidity_like": "t1 = keccak256(0x0c, 0x20);",
            "order": 1,
        }),
        ov("EvaluationStep", {
            "temp": "t2",
            "call": "sload",
            "evaluated_args": ["t1"],
            "solidity_like": "t2 = sload(t1);",
            "order": 2,
        }),
        ov("EvaluationStep", {
            "temp": "t3",
            "call": "eq",
            "evaluated_args": ["t1", "other"],
            "solidity_like": "t3 = (t1 == other);",
            "order": 3,
        }),
        ov("StateVariableRead", {
            "target": "t2",
            "solidity_like": "t2 = storage[slot];",
        }),
    ])
    expect("keccak_used_elsewhere", lines, [
        "t1 = keccak256(0x0c, 0x20);",
        "t2 = storage[slot];",
        "t3 = (t1 == other);",
    ])


def test_consumed_not_slot_step_hidden_by_recovered_state_read() -> None:
    lines = SolidityLikeRenderer.evaluation_step_lines([
        ov("EvaluationStep", {
            "temp": "t1",
            "call": "not",
            "evaluated_args": ["_ROLE_SLOT_SEED"],
            "solidity_like": "t1 = (~_ROLE_SLOT_SEED);",
            "order": 1,
        }),
        ov("EvaluationStep", {
            "temp": "t2",
            "call": "sload",
            "evaluated_args": ["t1"],
            "solidity_like": "t2 = sload(t1);",
            "order": 2,
        }),
        ov("StateVariableRead", {
            "target": "t2",
            "solidity_like": "t2 = storage[not(_ROLE_SLOT_SEED)];",
        }),
    ])
    expect("consumed_not_slot", lines, ["t2 = storage[not(_ROLE_SLOT_SEED)];"])


def test_infinite_loop_break_exit_guard_not_applied_to_after_loop_statement() -> None:
    statements = [
        yul_stmt("asm_s_1", "for", 100, 80),
        yul_stmt("asm_s_2", "mstore(ptr, o)", 120),
        yul_stmt("asm_s_3", "if iszero(t)", 140),
        yul_stmt("asm_s_4", "break", 150),
        yul_stmt("asm_s_5", "mstore(ordinals, len)", 190),
    ]
    effects = [
        memory_effect("eff_body_write", "asm_s_2", "ptr", "o", ["1"]),
        eff("eff_branch", "Branch", ["asm_s_3"], {
            "condition": "iszero(t)",
            "condition_final_temp": "__c",
            "path_states": ["1"],
        }),
        eff("eff_break", "ControlTransfer", ["asm_s_4"], {
            "op": "break",
            "path_states": ["1 && iszero(t)"],
        }),
        memory_effect("eff_after_write", "asm_s_5", "ordinals", "len", ["1 && iszero(t)"]),
    ]
    out = render_body(loop_function("1", "100:80:0", statements, effects))
    if "if ((1 != 0) && __c)" in out or "if (__c) {\n    memory[ordinals]" in out:
        raise AssertionError(out)
    if "memory[ordinals] = len;" not in out:
        raise AssertionError(out)


def test_static_true_loop_condition_renders_as_while_true() -> None:
    statements = [
        yul_stmt("asm_s_1", "for", 100, 80),
        yul_stmt("asm_s_2", "mstore(ptr, o)", 120),
    ]
    effects = [
        memory_effect("eff_body_write", "asm_s_2", "ptr", "o", ["1"]),
    ]
    out = render_body(loop_function("1", "100:80:0", statements, effects))
    if "while (true)" not in out:
        raise AssertionError(out)


def test_symbolic_loop_condition_keeps_for_condition() -> None:
    statements = [
        yul_stmt("asm_s_1", "for", 100, 80),
        yul_stmt("asm_s_2", "mstore(ptr, i)", 120),
    ]
    effects = [
        memory_effect("eff_body_write", "asm_s_2", "ptr", "i", ["lt(i, n)"]),
    ]
    out = render_body(loop_function("lt(i, n)", "100:80:0", statements, effects))
    if "for (; (i < n); )" not in out:
        raise AssertionError(out)
    if "while (true)" in out:
        raise AssertionError(out)


def test_finite_loop_normal_exit_guard_not_applied_to_after_loop_statement() -> None:
    statements = [
        yul_stmt("asm_s_1", "for", 100, 80),
        yul_stmt("asm_s_2", "mstore(ptr, i)", 120),
        yul_stmt("asm_s_5", "mstore(done, 1)", 190),
    ]
    effects = [
        memory_effect("eff_body_write", "asm_s_2", "ptr", "i", ["lt(i, n)"]),
        memory_effect("eff_after_write", "asm_s_5", "done", "1", ["!(lt(i, n))"]),
    ]
    out = render_body(loop_function("lt(i, n)", "100:80:0", statements, effects))
    if "if (!(i < n))" in out or "if (!(lt(i, n)))" in out:
        raise AssertionError(out)
    if "memory[done] = 1;" not in out:
        raise AssertionError(out)


def test_outer_guard_preserved_when_loop_exit_guard_is_suppressed() -> None:
    statements = [
        yul_stmt("asm_s_1", "for", 100, 80),
        yul_stmt("asm_s_2", "mstore(ptr, o)", 120),
        yul_stmt("asm_s_3", "if iszero(t)", 140),
        yul_stmt("asm_s_4", "break", 150),
        yul_stmt("asm_s_5", "mstore(out, v)", 190),
    ]
    effects = [
        memory_effect("eff_body_write", "asm_s_2", "ptr", "o", ["outer && 1"]),
        eff("eff_branch", "Branch", ["asm_s_3"], {
            "condition": "iszero(t)",
            "condition_final_temp": "__c",
            "path_states": ["outer && 1"],
        }),
        eff("eff_break", "ControlTransfer", ["asm_s_4"], {
            "op": "break",
            "path_states": ["outer && 1 && iszero(t)"],
        }),
        memory_effect("eff_after_write", "asm_s_5", "out", "v", ["outer && 1 && iszero(t)"]),
    ]
    out = render_body(loop_function("1", "100:80:0", statements, effects))
    if "if ((outer != 0))" not in out:
        raise AssertionError(out)
    before_out = out.rsplit("memory[out] = v;", 1)[0]
    nearest_if = before_out.rsplit("if ", 1)[-1]
    if "&&" in nearest_if:
        raise AssertionError(out)
    if "memory[out] = v;" not in out:
        raise AssertionError(out)


def return_rewrite_function(overlays: list[SemanticOverlay]) -> FunctionSSEIR:
    return FunctionSSEIR(
        "C.isContract(address)",
        "C",
        "isContract",
        "isContract(address)",
        [
            SourceStatement(
                "asm_s_1",
                "yul",
                "size := extcodesize(account)",
                "80:30:0",
                "C.isContract(address)",
                "asm_block_1",
            )
        ],
        {},
        [],
        [],
        overlays,
    )


def test_function_return_keeps_source_and_adds_unique_recovered_assignment_note() -> None:
    source = (
        "function isContract(address account) internal view returns (bool) {\n"
        "    uint256 size;\n"
        "    assembly { size := extcodesize(account) }\n"
        "    return size > 0;\n"
        "}"
    )
    fn = return_rewrite_function([
        ov("AddressCodeSize", {
            "target": "size",
            "code_size": "account.code.length",
            "solidity_like": "size = account.code.length;",
        })
    ])
    out = SolidityLikeRenderer(fn).function_solidity_like_text(source)
    if "return size > 0; /* s-seir: size == account.code.length */" not in out:
        raise AssertionError(out)


def test_function_return_not_rewritten_for_path_dependent_assignment() -> None:
    source = (
        "function f(address account) internal view returns (bool) {\n"
        "    uint256 size;\n"
        "    assembly { if cond { size := extcodesize(account) } }\n"
        "    return size > 0;\n"
        "}"
    )
    fn = return_rewrite_function([
        ov("AddressCodeSize", {
            "target": "size",
            "code_size": "account.code.length",
            "path_states": ["cond"],
            "solidity_like": "size = account.code.length;",
        })
    ])
    out = SolidityLikeRenderer(fn).function_solidity_like_text(source)
    if "return size > 0;" not in out:
        raise AssertionError(out)
    if "s-seir:" in out:
        raise AssertionError(out)


def test_path_conditioned_storage_renders_as_condition_blocks() -> None:
    lines = SolidityLikeRenderer.path_conditioned_lines(ov("PathConditionedStorageWrite", {
        "candidates": [
            {
                "status": "resolved",
                "condition": "cond",
                "solidity_like": "storage[keccak256(abi.encode(a, p.slot))] = 1;",
            },
            {
                "status": "resolved",
                "condition": "!(cond)",
                "solidity_like": "storage[keccak256(abi.encode(b, p.slot))] = 1;",
            },
        ]
    }))
    expect("path_conditioned_storage_lines", lines, [
        "if ((cond != 0)) {",
        "    storage[keccak256(abi.encode(a, p.slot))] = 1;",
        "}",
        "if (!(cond)) {",
        "    storage[keccak256(abi.encode(b, p.slot))] = 1;",
        "}",
    ])


def test_path_conditioned_same_line_complement_conditions_become_unconditional() -> None:
    lines = SolidityLikeRenderer.path_conditioned_lines(ov("PathConditionedEventEmit", {
        "candidates": [
            {
                "status": "resolved",
                "condition": "cond && fee",
                "solidity_like": "emit Transfer(from, to, amount);",
            },
            {
                "status": "resolved",
                "condition": "!(cond) && fee",
                "solidity_like": "emit Transfer(from, to, amount);",
            },
        ]
    }))
    expect("path_conditioned_same_line_complement", lines, [
        "if ((fee != 0)) {",
        "    emit Transfer(from, to, amount);",
        "}",
    ])


def test_path_conditioned_same_line_all_complements_become_unconditional() -> None:
    lines = SolidityLikeRenderer.path_conditioned_lines(ov("PathConditionedStorageWrite", {
        "candidates": [
            {
                "status": "resolved",
                "condition": "cond",
                "solidity_like": "x = value;",
            },
            {
                "status": "resolved",
                "condition": "!(cond)",
                "solidity_like": "x = value;",
            },
        ]
    }))
    expect("path_conditioned_same_line_unconditional", lines, ["x = value;"])


def test_path_conditioned_different_lines_are_not_merged() -> None:
    lines = SolidityLikeRenderer.path_conditioned_lines(ov("PathConditionedStorageWrite", {
        "candidates": [
            {
                "status": "resolved",
                "condition": "cond",
                "solidity_like": "x = left;",
            },
            {
                "status": "resolved",
                "condition": "!(cond)",
                "solidity_like": "x = right;",
            },
        ]
    }))
    expect("path_conditioned_different_lines", lines, [
        "if ((cond != 0)) {",
        "    x = left;",
        "}",
        "if (!(cond)) {",
        "    x = right;",
        "}",
    ])


def test_adjacent_identical_single_line_if_blocks_are_compacted() -> None:
    lines = SolidityLikeRenderer.optimize_condition_blocks([
        "if (!(am)) {",
        "    ao = (to == pairAddr); // yul: let ao := eq(to, pairAddr)",
        "}",
        "if (am && !(__reverted)) {",
        "    ao = (to == pairAddr);",
        "}",
        "if (!(am)) {",
        "    ap = (ao & flagVal);",
        "}",
        "if (am && !(__reverted)) {",
        "    ap = (ao & flagVal);",
        "}",
    ])
    expect("adjacent_identical_if_compaction", lines, [
        "if ((!(__reverted)) || (!(am))) {",
        "    ao = (to == pairAddr); // yul: let ao := eq(to, pairAddr)",
        "    ap = (ao & flagVal);",
        "}",
    ])


def test_require_overlay_keeps_outer_path_condition() -> None:
    stmt = yul_stmt("asm_s_1", "revert(0, 0)", 100)
    effects = [
        eff("eff_revert", "Revert", ["asm_s_1"], {
            "path_states": ["am && iszero(an)"],
        })
    ]
    overlays = [
        SemanticOverlay("ov_req", "RequireOverlay", ["eff_revert"], ["asm_s_1"], {
            "nearest_condition": "iszero(an)",
            "require_conditions": ["iszero(an)"],
            "require_like": "require((an != 0));",
        })
    ]
    fn = FunctionSSEIR("C.f()", "C", "f", "f()", [stmt], {}, [], effects, overlays)
    out = render_body(fn)
    expect("require_outer_path", out.splitlines(), [
        "if ((am != 0)) {",
        "    require((an != 0)); // yul: revert(0, 0)",
        "}",
    ])


def test_path_conditioned_overlay_drops_duplicate_outer_guard() -> None:
    stmt = yul_stmt("asm_s_1", "log3(0, 32, topic, owner_, spender)", 100)
    effects = [
        eff("eff_branch", "Branch", ["asm_s_0"], {
            "condition": "or(iszero(owner_), iszero(spender))",
            "condition_final_temp": "__guard",
        }),
        eff("eff_event", "EventLog", ["asm_s_1"], {
            "path_states": ["!(or(iszero(owner_), iszero(spender)))"],
        }),
    ]
    overlays = [
        SemanticOverlay("ov_event", "PathConditionedEventEmit", ["eff_event"], ["asm_s_1"], {
            "candidates": [
                {
                    "status": "resolved",
                    "condition": "!(or(iszero(owner_), iszero(spender)))",
                    "solidity_like": "emit Approval(owner_, spender, amount);",
                }
            ]
        })
    ]
    fn = FunctionSSEIR("C.f()", "C", "f", "f()", [stmt], {}, [], effects, overlays)
    out = render_body(fn)
    expect("dedupe_outer_guard", out.splitlines(), [
        "if (!(__guard)) {",
        "    emit Approval(owner_, spender, amount); // yul: log3(0, 32, topic, owner_, spender)",
        "}",
    ])


def test_division_guard_and_assignment_both_render() -> None:
    stmt = yul_stmt("asm_s_1", "let feeAmount := div(mul(amount, 5), 100)", 100)
    effects = [
        eff("eff_value", "ValueDef", ["asm_s_1"], {
            "path_states": ["guard"],
        })
    ]
    overlays = [
        SemanticOverlay("ov_req", "RequireOverlay", ["eff_value"], ["asm_s_1"], {
            "condition": "100 != 0",
            "require_like": "require(100 != 0);",
        }),
        SemanticOverlay("ov_expr", "ExpressionNormalization", ["eff_value"], ["asm_s_1"], {
            "target": "feeAmount",
            "solidity_like": "feeAmount = ((amount * 5) / 100);",
            "context": "value",
        }),
    ]
    fn = FunctionSSEIR("C.f()", "C", "f", "f()", [stmt], {}, [], effects, overlays)
    out = render_body(fn)
    expect("division_guard_assignment", out.splitlines(), [
        "if ((guard != 0)) {",
        "    require(100 != 0); // yul: let feeAmount := div(mul(amount, 5), 100)",
        "    feeAmount = ((amount * 5) / 100);",
        "}",
    ])


def test_raw_yul_if_scaffold_without_effect_is_not_rendered() -> None:
    if_stmt = SourceStatement(
        "asm_s_1",
        "yul",
        "if eq(a, b)",
        "100:20:0",
        "C.f()",
        "asm_block_1",
        {"nodeType": "YulIf"},
    )
    write_stmt = yul_stmt("asm_s_2", "mstore(ptr, value)", 130)
    effects = [memory_effect("eff_write", "asm_s_2", "ptr", "value", ["eq(a, b)"])]
    fn = FunctionSSEIR("C.f()", "C", "f", "f()", [if_stmt, write_stmt], {}, [], effects, [])
    out = render_body(fn)
    expect("skip_raw_if_scaffold", out.splitlines(), [
        "if ((a == b)) {",
        "    memory[ptr] = value; // yul: mstore(ptr, value)",
        "}",
    ])


def test_dnf_condition_renders_as_nested_condition_tree() -> None:
    stmt = yul_stmt("asm_s_1", "mstore(ptr, value)", 100)
    effects = [
        memory_effect("eff_write", "asm_s_1", "ptr", "value", [
            "a && b",
            "a && c",
        ])
    ]
    fn = FunctionSSEIR("C.f()", "C", "f", "f()", [stmt], {}, [], effects, [])
    out = render_body(fn)
    expect("dnf_condition_tree", out.splitlines(), [
        "if (a != 0) {",
        "    if ((b != 0) || (c != 0)) {",
        "        memory[ptr] = value; // yul: mstore(ptr, value)",
        "    }",
        "}",
    ])


def test_dnf_condition_with_impossible_branch_renders_simplified_single_guard() -> None:
    stmt = yul_stmt("asm_s_1", "mstore(ptr, value)", 100)
    effects = [
        memory_effect("eff_write", "asm_s_1", "ptr", "value", [
            "a && b",
            "a && !(a) && b",
        ])
    ]
    fn = FunctionSSEIR("C.f()", "C", "f", "f()", [stmt], {}, [], effects, [])
    out = render_body(fn)
    expect("dnf_impossible_branch", out.splitlines(), [
        "if ((a != 0) && (b != 0)) {",
        "    memory[ptr] = value; // yul: mstore(ptr, value)",
        "}",
    ])


def test_path_conditioned_overlay_suppressed_by_loop_condition() -> None:
    stmt = yul_stmt("asm_s_1", "log3(ptr, 32, topic, from, to)", 100)
    effects = [
        eff("eff_event", "EventLog", ["asm_s_1"], {
            "path_states": ["lt(i, len)"],
        })
    ]
    overlays = [
        SemanticOverlay("ov_event", "PathConditionedEventEmit", ["eff_event"], ["asm_s_1"], {
            "candidates": [
                {
                    "status": "resolved",
                    "condition": "(i < len)",
                    "solidity_like": "emit Transfer(from, to, value);",
                }
            ]
        })
    ]
    fn = FunctionSSEIR("C.f()", "C", "f", "f()", [stmt], {}, [], effects, overlays)
    rendered = SolidityLikeRenderer(fn).render_stmt(stmt, suppress_predicates=["lt(i, len)"])
    expect("loop_suppressed_path_conditioned_event", rendered, [
        "emit Transfer(from, to, value);",
    ])


def test_minified_function_view_is_indented_for_audit() -> None:
    source = (
        "function f() public {\n"
        "assembly {\n"
        "mstore(0, 1)\n"
        "}\n"
        "return;\n"
        "}"
    )
    stmt = yul_stmt("asm_s_1", "mstore(0, 1)", 20)
    effects = [memory_effect("eff_write", "asm_s_1", "0", "1", ["entry"])]
    fn = FunctionSSEIR("C.f()", "C", "f", "f()", [stmt], {}, [], effects, [])
    out = SolidityLikeRenderer(fn).function_solidity_like_text(source)
    expect("minified_indent", out.splitlines(), [
        "function f() public {",
        "    assembly /* s-seir solidity-like view */ {",
        "        memory[0] = 1; // yul: mstore(0, 1)",
        "    }",
        "    return;",
        "}",
    ])


if __name__ == "__main__":
    tests = [
        test_tautological_branch_join_removed,
        test_common_prefix_branch_join_kept,
        test_non_complement_paths_unchanged,
        test_consumed_keccak_step_hidden_by_recovered_state_read,
        test_keccak_step_kept_when_used_elsewhere,
        test_consumed_not_slot_step_hidden_by_recovered_state_read,
        test_infinite_loop_break_exit_guard_not_applied_to_after_loop_statement,
        test_static_true_loop_condition_renders_as_while_true,
        test_symbolic_loop_condition_keeps_for_condition,
        test_finite_loop_normal_exit_guard_not_applied_to_after_loop_statement,
        test_outer_guard_preserved_when_loop_exit_guard_is_suppressed,
        test_function_return_keeps_source_and_adds_unique_recovered_assignment_note,
        test_function_return_not_rewritten_for_path_dependent_assignment,
        test_path_conditioned_storage_renders_as_condition_blocks,
        test_path_conditioned_same_line_complement_conditions_become_unconditional,
        test_path_conditioned_same_line_all_complements_become_unconditional,
        test_path_conditioned_different_lines_are_not_merged,
        test_adjacent_identical_single_line_if_blocks_are_compacted,
        test_require_overlay_keeps_outer_path_condition,
        test_path_conditioned_overlay_drops_duplicate_outer_guard,
        test_division_guard_and_assignment_both_render,
        test_raw_yul_if_scaffold_without_effect_is_not_rendered,
        test_dnf_condition_renders_as_nested_condition_tree,
        test_dnf_condition_with_impossible_branch_renders_simplified_single_guard,
        test_path_conditioned_overlay_suppressed_by_loop_condition,
        test_minified_function_view_is_indented_for_audit,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
