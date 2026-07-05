#!/usr/bin/env python3
from pathlib import Path as _SSEIRPath
import sys as _sseir_sys
_SSEIR_ROOT = _SSEIRPath(__file__).resolve().parents[1]
for _sseir_path in (_SSEIR_ROOT / "legacy_yul", _SSEIR_ROOT / "s_seir"):
    _sseir_text = str(_sseir_path)
    if _sseir_text not in _sseir_sys.path:
        _sseir_sys.path.insert(0, _sseir_text)
class IdAllocator:
    def __init__(self): self.counts={}
    def new(self,prefix:str)->str:
        self.counts[prefix]=self.counts.get(prefix,0)+1
        return f"{prefix}_{self.counts[prefix]}"
