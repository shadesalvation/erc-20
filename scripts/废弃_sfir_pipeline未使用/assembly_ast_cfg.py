#!/usr/bin/env python3
"""废弃：当前 SFIR pipeline 不使用的兼容入口；原目标为 scripts/legacy_yul/assembly_ast_cfg.py。"""
from __future__ import annotations

from pathlib import Path
import runpy
import sys

_ROOT = Path(__file__).resolve().parent
for _path in (_ROOT / "legacy_yul", _ROOT / "s_seir"):
    _text = str(_path)
    if _text not in sys.path:
        sys.path.insert(0, _text)

if __name__ == "__main__":
    runpy.run_path(str(_ROOT / "legacy_yul" / "assembly_ast_cfg.py"), run_name="__main__")
else:
    from legacy_yul.assembly_ast_cfg import *  # noqa: F401,F403
