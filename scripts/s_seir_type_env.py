#!/usr/bin/env python3
"""Compatibility wrapper for scripts/s_seir/s_seir_type_env.py."""
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
    runpy.run_path(str(_ROOT / "s_seir" / "s_seir_type_env.py"), run_name="__main__")
else:
    from s_seir.s_seir_type_env import *  # noqa: F401,F403
