"""Capture production SFIR for the baseline conflict fixtures (no oracle input)."""
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/s_seir"))
from s_seir_solidity_slithir_tests import SOURCE
from s_seir_solidity_atomic_ops_tests import SAMPLES
from s_seir_pipeline import build_sseir
from s_seir_semantic_fact_adapter import (
    build_function_level_semantic_fact_ir_payload,
    build_function_level_semantic_fact_payload,
)

with tempfile.TemporaryDirectory(prefix="baseline_semantics_") as directory:
    workdir = Path(directory)
    source = workdir / "Cases.sol"
    source.write_text(SOURCE, encoding="utf-8")
    functions = build_sseir(source, workdir=workdir, branch_preprocess=False)
    functions += build_sseir(SAMPLES["guarded"], workdir=workdir, branch_preprocess=False)
    functions += build_sseir(SAMPLES["assembly"], workdir=workdir, branch_preprocess=False)
    selected = [fn for fn in functions if
                (fn.contract in {"ConstantContextCase", "StateContextCase"} and fn.function == "guarded")
                or (fn.contract == "GuardedERC20" and fn.function == "approve")
                or (fn.contract == "AssemblyERC20" and fn.function == "_assemblyMove")]
    payload = build_function_level_semantic_fact_ir_payload(selected)
    payload["compatibility_facts_for_audit"] = build_function_level_semantic_fact_payload(selected)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
