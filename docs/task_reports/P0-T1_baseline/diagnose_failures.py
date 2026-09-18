"""Read-only diagnostics: rerun unchanged failing tests and retain failing locals."""
from pathlib import Path
import importlib.util
import json
import traceback

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
results = []
for stem in ('s_seir_solidity_atomic_ops_tests','s_seir_solidity_slithir_tests'):
    path = ROOT/'scripts/s_seir'/f'{stem}.py'
    spec = importlib.util.spec_from_file_location(stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        module.run()
    except AssertionError as exc:
        tb = exc.__traceback__
        while tb.tb_next:
            tb = tb.tb_next
        values = tb.tb_frame.f_locals
        evidence = ({'allowance_write': values['allowance_write']}
                    if 'allowance_write' in values else
                    {'guarded_facts': module.semantic_facts(values['facts'], 'ConstantContextCase', 'guarded')})
        results.append({'test':stem,'status':'FAIL','traceback':traceback.format_exc(),'evidence':evidence})
    else:
        results.append({'test':stem,'status':'PASS'})
(OUT/'failure_diagnostics.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
print(json.dumps([{'test':r['test'],'status':r['status']} for r in results]))
