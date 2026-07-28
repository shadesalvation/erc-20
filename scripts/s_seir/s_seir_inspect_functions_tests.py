#!/usr/bin/env python3
from __future__ import annotations

from s_seir_inspect_functions import is_function_sseir, load_functions, summarize


def test_load_functions_ignores_nested_source_statements() -> None:
    fn = {
        "function_id": "C.f()",
        "contract": "C",
        "function": "f",
        "signature": "f()",
        "source_statements": [
            {"stmt_id": "asm_s_1", "function_id": "C.f()", "lang": "yul", "block_id": "asm_block_1"},
        ],
        "effects": [],
        "semantic_overlays": [],
        "expr_roles": [],
        "control": {},
    }
    data = {"source_entry": {"functions": [fn]}}
    functions = load_functions(data)
    assert functions == [fn]
    assert is_function_sseir(fn)
    assert not is_function_sseir(fn["source_statements"][0])
    assert summarize(fn)["yul_statement_count"] == 1


if __name__ == "__main__":
    test_load_functions_ignores_nested_source_statements()
    print("PASS test_load_functions_ignores_nested_source_statements")
