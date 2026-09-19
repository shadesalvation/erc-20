#!/usr/bin/env python3
"""Reproduce P1-T6R source run with unchanged production thin drivers."""
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'outputs/module1_sample10_p1t6r'
SOURCE = ROOT / '人工构造样例/10_大量Yul_统一语义模型/contracts/YulHeavyERC20.sol'
PYTHON = ROOT / '.venv/bin/python'
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
OUT.mkdir(parents=True, exist_ok=True)
(OUT / 'head.sha').write_text(subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True))

def run(name, args):
    with (OUT / (name + '_stdout.log')).open('w') as out, (OUT / (name + '_stderr.log')).open('w') as err:
        subprocess.run([str(PYTHON), *map(str, args)], cwd=ROOT, env=ENV, stdout=out, stderr=err, check=True)

run('sfir', ['scripts/s_seir/s_seir_pipeline.py', SOURCE,
    '--solc-bin', ROOT / '.venv/bin/solc', '--slither-bin', ROOT / '.venv/bin/slither', '--workdir', ROOT,
    '--output', OUT / 'sseir.json', '--text-output', OUT / 'sseir.txt',
    '--semantic-fact-ir-output', OUT / 'sfir.json', '--semantic-fact-ir-text-output', OUT / 'sfir.txt',
    '--branch-preprocessed-output', OUT / 'analysis_source.sol', '--branch-report-output', OUT / 'branch_preprocess_report.txt'])
run('module1', ['outputs/module1_sample10_current/run_module1.py', '--sfir', OUT / 'sfir.json', '--output-dir', OUT])
run('render', ['outputs/module1_sample10_current/render_module1.py', '--module1', OUT / 'p1_t7_module1_results.json.gz',
    '--refinement', OUT / 'p1_t6_refinement.json.gz', '--sfir', OUT / 'sfir.json', '--source', SOURCE, '--output-dir', OUT])
print('Completed fresh SFIR and Module 1 run:', OUT)
