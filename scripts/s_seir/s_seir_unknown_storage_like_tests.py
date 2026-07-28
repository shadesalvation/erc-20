#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "legacy_yul", ROOT / "s_seir"):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from s_seir_model import EffectNode
from s_seir_overlay_builder import SemanticOverlayBuilder


class FakeTypeEnv:
    def state_var_by_slot(self, _slot: str):
        return None


def test_unknown_storage_read_gets_solidity_like() -> None:
    effect = EffectNode("eff_read", "StorageRead", ["asm_s_1"], {
        "slot": "not(_ROLE_SLOT_SEED)",
        "slot_versions": [],
        "value": "tmp_owner",
    })
    overlays = SemanticOverlayBuilder().storage_overlays(FakeTypeEnv(), [effect])
    reads = [item for item in overlays if item.kind == "StateVariableRead"]
    assert len(reads) == 1, [item.kind for item in overlays]
    attrs = reads[0].attrs
    assert attrs["access"] == "storage[not(_ROLE_SLOT_SEED)]"
    assert attrs["solidity_like"] == "tmp_owner = storage[not(_ROLE_SLOT_SEED)];"


if __name__ == "__main__":
    test_unknown_storage_read_gets_solidity_like()
    print("PASS test_unknown_storage_read_gets_solidity_like")
