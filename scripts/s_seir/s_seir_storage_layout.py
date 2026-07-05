#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path as _SSEIRPath
import sys as _sseir_sys
_SSEIR_ROOT = _SSEIRPath(__file__).resolve().parents[1]
for _sseir_path in (_SSEIR_ROOT / "legacy_yul", _SSEIR_ROOT / "s_seir"):
    _sseir_text = str(_sseir_path)
    if _sseir_text not in _sseir_sys.path:
        _sseir_sys.path.insert(0, _sseir_text)

import json
import subprocess
from pathlib import Path
from typing import Any

from s_seir_model import FunctionUnit


def extract_storage_layout(source: Path, solc_bin: str) -> dict[str, dict[str, dict[str, Any]]]:
    source = source.resolve()
    candidate = Path(solc_bin)
    if not candidate.is_absolute() and candidate.is_file():
        solc_bin = str(candidate.resolve())
    compiler_input = {
        'language': 'Solidity',
        'sources': {source.name: {'content': source.read_text(encoding='utf-8')}},
        'settings': {'outputSelection': {'*': {'*': ['storageLayout']}}},
    }
    proc = subprocess.run(
        [solc_bin, '--standard-json', '--base-path', str(source.parent), '--include-path', str(source.parent)],
        cwd=str(source.parent),
        input=json.dumps(compiler_input),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if not proc.stdout.strip():
        return {}
    data = json.loads(proc.stdout)
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for _file, contracts in data.get('contracts', {}).items():
        for contract, info in contracts.items():
            by_name: dict[str, dict[str, Any]] = {}
            for item in info.get('storageLayout', {}).get('storage', []) or []:
                label = item.get('label')
                if label:
                    by_name[str(label)] = {
                        'slot': int(str(item.get('slot', '0')), 0),
                        'offset': int(str(item.get('offset', '0')), 0),
                        'type_id': item.get('type'),
                        'ast_id': item.get('astId'),
                    }
            out[contract] = by_name
    return out


def apply_storage_layout(unit: FunctionUnit, layouts: dict[str, dict[str, dict[str, Any]]]) -> None:
    layout = layouts.get(unit.contract, {})
    for var in unit.state_variables:
        info = layout.get(var.name)
        if info:
            var.storage_slot = info.get('slot')
