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
import os
import subprocess
from pathlib import Path
from typing import Any

from s_seir_model import FunctionUnit, VariableInfo
from assembly_ast_cfg import solc_supports_option


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
    include_paths = [
        Path(item).resolve()
        for item in os.environ.get('SSEIR_SOLC_INCLUDE_PATHS', '').split(os.pathsep)
        if item.strip()
    ]
    remappings = [
        item
        for item in os.environ.get('SSEIR_SOLC_REMAPPINGS', '').split(os.pathsep)
        if item.strip()
    ]
    if remappings:
        compiler_input['settings']['remappings'] = remappings
    command = [solc_bin, '--standard-json']
    if solc_supports_option(solc_bin, '--base-path'):
        command.extend(['--base-path', str(source.parent)])
    include_paths = [path for path in include_paths if path != source.parent]
    if include_paths and solc_supports_option(solc_bin, '--include-path'):
        for include_path in include_paths:
            command.extend(['--include-path', str(include_path)])
    elif include_paths and solc_supports_option(solc_bin, '--allow-paths'):
        command.extend(['--allow-paths', ','.join(str(path) for path in [source.parent, *include_paths])])
    proc = subprocess.run(
        command,
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
            types = info.get('storageLayout', {}).get('types', {}) or {}
            by_name: dict[str, dict[str, Any]] = {}
            for item in info.get('storageLayout', {}).get('storage', []) or []:
                label = item.get('label')
                if label:
                    type_id = item.get('type')
                    type_info = types.get(type_id, {}) if type_id else {}
                    by_name[str(label)] = {
                        'slot': int(str(item.get('slot', '0')), 0),
                        'offset': int(str(item.get('offset', '0')), 0),
                        'type_id': type_id,
                        'type_string': type_info.get('label') or type_id or 'unknown',
                        'ast_id': item.get('astId'),
                    }
            out[contract] = by_name
    return out


def apply_storage_layout(unit: FunctionUnit, layouts: dict[str, dict[str, dict[str, Any]]]) -> None:
    layout = layouts.get(unit.contract, {})
    known = {var.name for var in unit.state_variables}
    for var in unit.state_variables:
        info = layout.get(var.name)
        if info:
            var.storage_slot = info.get('slot')
            if info.get('type_string'):
                var.type_string = str(info.get('type_string'))
    for name, info in sorted(layout.items(), key=lambda item: (item[1].get('slot', 0), item[1].get('offset', 0), item[0])):
        if name in known:
            continue
        unit.state_variables.append(VariableInfo(
            name=name,
            kind='state',
            type_string=str(info.get('type_string') or info.get('type_id') or 'unknown'),
            data_location='storage',
            src='',
            storage_slot=info.get('slot'),
        ))
        known.add(name)
