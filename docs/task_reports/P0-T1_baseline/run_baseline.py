"""P0-T1 evidence runner: run existing entrypoints unchanged, preserve failures."""
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent
OUT.mkdir(parents=True, exist_ok=True)
commands = [(str(p.relative_to(ROOT)), [str(ROOT / '.venv/bin/python'), str(p.relative_to(ROOT))], 'active')
            for p in sorted((ROOT / 'scripts/s_seir').glob('*tests.py'))]
commands.append(('archived_semantic_ir', [str(ROOT / '.venv/bin/python'), '-m', 'unittest', '-v',
    'scripts.废弃_sfir_pipeline未使用.semantic_ir.tests'], 'archived'))
results = []
env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
for name, command, scope in commands:
    start = time.monotonic()
    log = OUT / (Path(name).stem + '.log')
    with log.open('w') as stream:
        try:
            process = subprocess.run(command, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=300)
            code = process.returncode
            status = 'PASS' if code == 0 else 'FAIL'
        except subprocess.TimeoutExpired:
            code, status = None, 'TIMEOUT'
            stream.write('\nRUNNER TIMEOUT: 300 seconds\n')
    results.append({'name': name, 'scope': scope, 'command': command, 'status': status,
                    'returncode': code, 'seconds': round(time.monotonic()-start,3), 'log': log.name})
    payload = {'started_date_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
               'cwd': str(ROOT), 'counting_unit': 'existing script/module entrypoint, not assertion or test case',
               'entrypoints_planned': len(commands), 'results': results,
               'counts': {s: sum(r['status']==s for r in results) for s in ('PASS','FAIL','SKIP','TIMEOUT')}}
    (OUT/'results.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n')
    print(status, name, flush=True)
sys.exit(0 if all(r['status']=='PASS' for r in results) else 1)
