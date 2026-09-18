"""Verify that strengthened tests reject the pre-fix bridge and lost guards."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/s_seir"))
import s_seir_semantic_fact_bridge_tests as bridge_tests
import s_seir_solidity_slithir_tests as guard_tests

source_path = "scripts/s_seir/s_seir_semantic_fact_bridge.py"
old_source = subprocess.check_output(["git", "show", f"HEAD:{source_path}"], cwd=ROOT)
old_hash = hashlib.sha256(old_source).hexdigest()
baseline_hashes = json.loads((ROOT / "docs/task_reports/P0-T1_baseline/source_hashes_before.json").read_text())
assert old_hash == baseline_hashes[source_path], "HEAD is not the captured pre-fix bridge"
old_module = types.ModuleType("baseline_bridge")
sys.modules[old_module.__name__] = old_module
exec(compile(old_source, source_path, "exec"), old_module.__dict__)

checks = []
with patch.object(bridge_tests, "SemanticFactBridge", old_module.SemanticFactBridge):
    try:
        bridge_tests.test_semantic_provenance_anchors_without_effect_transport()
    except AssertionError:
        checks.append({"check": "pre_fix_bridge_rejected", "status": "PASS", "source_sha256": old_hash})
    else:
        raise AssertionError("new bridge regression did not reject pre-fix implementation")
bridge_tests.test_semantic_provenance_anchors_without_effect_transport()
checks.append({"check": "current_bridge_accepted", "status": "PASS"})

payload = json.loads((Path(__file__).parent / "final/semantic_evidence.json").read_text())
function = next(fn for fn in payload["functions"] if fn["contract"] == "ConstantContextCase")
for mutation in ("missing_true_guard", "false_path_reaches_assignment", "duplicate_yul_assignment"):
    changed = deepcopy(function)
    assignment = next(node for node in changed["semantic_nodes"] if node["source_lang"] == "yul")
    true_edge = next(edge for edge in changed["fact_cfg"]["edges"] if edge["kind"] == "true")
    false_edge = next(edge for edge in changed["fact_cfg"]["edges"] if edge["kind"] == "false")
    if mutation == "missing_true_guard":
        true_edge["guard"] = ""
    elif mutation == "false_path_reaches_assignment":
        false_edge["to"] = assignment["placement"]["anchor_block"]
    else:
        changed["semantic_nodes"].append(deepcopy(assignment))
    with patch.object(guard_tests, "build_function_level_semantic_fact_ir_payload", return_value={"functions": [changed]}):
        try:
            guard_tests.assert_guarded_yul_paths(None)
        except AssertionError:
            checks.append({"check": mutation + "_rejected", "status": "PASS"})
        else:
            raise AssertionError("guard regression accepted mutation: " + mutation)
print(json.dumps({"checks": checks}, indent=2))
