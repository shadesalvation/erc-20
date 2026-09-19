#!/usr/bin/env python3
"""Evaluate persisted P1-T7 micro artifacts; writes evaluator evidence only."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

from module1 import evaluate_case

ROOT = Path(__file__).resolve().parents[2]
INPUT = Path(__file__).with_name("module1_micro_cases.json.gz")
OUTPUT = ROOT / "docs" / "task_reports" / "P1-T7_evidence" / "module1_evaluation.json"


def main():
    with gzip.open(INPUT, "rt", encoding="utf-8") as stream:
        cases = json.load(stream)
    evaluations = [evaluate_case(case["case_id"], case["recovery"], case["expected_claims"],
                                 runtime_seconds=case["fixture_runtime_seconds"], config=case["config"])
                   for case in cases]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(evaluations, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    print(OUTPUT, len(evaluations), "cases")


if __name__ == "__main__":
    main()
