from __future__ import annotations

from conftest import lower_example, operations


def test_mapping_state_write_uses_storage_operations() -> None:
    program = lower_example("StorageExample.sol")
    ops = operations(program)
    kinds = [op["kind"] for op in ops]
    assert "STORAGE_LOCATION" in kinds
    assert "STORAGE_WRITE" in kinds
    assert not any(
        op["kind"] == "WRITE_LOCAL" and op["attributes"].get("name") == "balances"
        for op in ops
    )
    storage_location = next(op for op in ops if op["kind"] == "STORAGE_LOCATION")
    assert storage_location["attributes"]["base_state_variable"] == "balances"
    assert storage_location["attributes"]["indices"]
