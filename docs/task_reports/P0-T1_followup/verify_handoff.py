"""Record task-local source changes, baseline counts, and Git scope checks."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OUT = HERE / "final"
expected = {
    "scripts/s_seir/s_seir_control_builder.py",
    "scripts/s_seir/s_seir_solidity_semantic_lifter.py",
    "scripts/s_seir/s_seir_semantic_fact_bridge.py",
    "scripts/s_seir/s_seir_solidity_atomic_ops_tests.py",
    "scripts/s_seir/s_seir_solidity_slithir_tests.py",
    "scripts/s_seir/s_seir_semantic_fact_bridge_tests.py",
}
baseline = json.loads((ROOT / "docs/task_reports/P0-T1_baseline/source_hashes_before.json").read_text())
current = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
           for path in (ROOT / "scripts").rglob("*.py")}
changed = {path for path in baseline if baseline[path] != current.get(path)}
assert changed == expected, changed
assert current.keys() == baseline.keys(), "unexpected script addition/deletion"

def git(*args):
    return subprocess.check_output(["git", "-c", "core.quotePath=false", *args], cwd=ROOT, text=True)

status = git("status", "--short")
previous = set((HERE / "before/git_status.txt").read_text().splitlines())
now = set(status.splitlines())
assert not previous - now, "pre-existing Git status entries changed"
assert now - previous == {" M " + path for path in expected}, now - previous
subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
(OUT / "git_status.txt").write_text(status)
(OUT / "git_diff.txt").write_text(git("diff", "--", *sorted(expected)))

for path in HERE.rglob("*.json"):
    json.loads(path.read_text())
before = json.loads((HERE / "before/results.json").read_text())
final = json.loads((OUT / "results.json").read_text())
assert before["counts"] == {"PASS": 24, "FAIL": 2, "SKIP": 0, "TIMEOUT": 0}
assert final["counts"] == {"PASS": 26, "FAIL": 0, "SKIP": 0, "TIMEOUT": 0}
sensitivity = json.loads((OUT / "regression_sensitivity.json").read_text())
assert len(sensitivity["checks"]) == 5
assert all(item["status"] == "PASS" for item in sensitivity["checks"])
verification = {
    "status": "PASS", "source_files_checked": len(baseline),
    "source_files_unchanged": len(baseline) - len(changed),
    "source_changes": [{"path": path, "before_sha256": baseline[path], "after_sha256": current[path]}
                       for path in sorted(changed)],
    "unexpected_status_changes": [], "git_diff_check": "PASS",
    "baseline_counts": before["counts"], "final_counts": final["counts"],
    "regression_sensitivity_checks_passed": 5,
}
(OUT / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2) + "\n")
criteria = [
    ("主要目录均有解释", "PASS", "docs/architecture/repository_overview.md"),
    ("SFIR/frontend 入口定位", "PASS", "docs/architecture/repository_overview.md"),
    ("测试命令可复现", "PASS", "results.json; ../before/results.json"),
    ("baseline 数字与原因明确", "PASS", "results.json; docs/decisions/P0-T1-001-baseline-semantic-contracts.md"),
    ("无大规模代码修改", "PASS", "verification.json; git_diff.txt"),
    ("必要测试通过，无未解释 regression", "PASS", "results.json; regression_sensitivity.json"),
    ("新研究模块/独立 Yul/SMT/benchmark", "N/A", "outside current task"),
]
(OUT / "acceptance.json").write_text(json.dumps({
    "task": "P0-T1 with authorized baseline followup", "status": "COMPLETE",
    "criteria": [{"criterion": name, "status": status, "evidence": evidence} for name, status, evidence in criteria],
}, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(verification, ensure_ascii=False, indent=2))
