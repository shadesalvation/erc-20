#!/usr/bin/env python3
from __future__ import annotations

import re
from typing import Any


Json = dict[str, Any]


class SoliditySemanticLifter:
    """Project ``sol_atom`` records to the common SemanticFact schema.

    Slither and ``SolidityAtomicOperationExtractor`` have already performed
    lowering and SSA construction. This class neither consumes S-SEIR/Yul
    results nor performs overlay recovery, expression expansion, or semantic
    guessing. Every input atom produces exactly one output fact.
    """

    ATOMIC_KIND_MAP = {
        "Assignment": "ValueAssign",
        "Binary": "ValueCompute",
        "Unary": "ValueCompute",
        "TypeConversion": "TypeConversion",
        "Index": "IndexAccess",
        "Member": "MemberAccess",
        "StorageRead": "StateRead",
        "Length": "LengthRead",
        "Unpack": "TupleUnpack",
        "InitArray": "ArrayConstruct",
        "NewArray": "NewArray",
        "NewStructure": "NewStructure",
        "NewElementaryType": "NewElementaryType",
        "NewContract": "NewContract",
        "Phi": "ValuePhi",
        "PhiCallback": "ValuePhi",
        "InternalCall": "InternalCall",
        "InternalDynamicCall": "InternalDynamicCall",
        "HighLevelCall": "ExternalCall",
        "LibraryCall": "LibraryCall",
        "LowLevelCall": "LowLevelCall",
        "SolidityCall": "BuiltinCall",
        "EventCall": "EventEmit",
        "Condition": "BranchCondition",
        "Return": "Return",
        "Send": "ValueTransferCall",
        "Transfer": "ValueTransferCall",
        "Delete": "Delete",
        "Nop": "Nop",
    }

    def facts_from_function(self, fn: Any) -> list[Json]:
        fn_dict = fn.to_semantic_dict() if hasattr(fn, "to_semantic_dict") else fn
        if not isinstance(fn_dict, dict):
            return []
        atomic_table = getattr(fn, "_sseir_solidity_atomic_operations", None)
        if not isinstance(atomic_table, dict) and isinstance(fn, dict):
            atomic_table = fn.get("solidity_atomic_operations")
        if not isinstance(atomic_table, dict):
            return []
        return self.atomic_facts(fn_dict, atomic_table)

    def atomic_facts(self, fn_dict: Json, atomic_table: Json) -> list[Json]:
        out: list[Json] = []
        function_id = str(atomic_table.get("function_id") or fn_dict.get("function_id") or "")
        for atom in atomic_table.get("operations") or []:
            if not isinstance(atom, dict):
                continue
            atom_id = str(atom.get("atom_id") or "")
            kind = self.atomic_fact_kind(atom)
            reads = self.atomic_operands(atom, kind)
            lvalue, rvalue, writes = self.atomic_assignment(atom, kind, reads)
            out.append(_clean({
                "fact_id": f"solidity:{function_id}:{atom_id}",
                "operation_id": atom_id,
                "kind": kind,
                "source_lang": "solidity",
                "origin": "solidity_atomic_operation",
                "fact_role": self.fact_role(kind),
                "function_id": function_id,
                "function": fn_dict.get("function") or atomic_table.get("function"),
                "contract": fn_dict.get("contract") or atomic_table.get("contract"),
                "signature": fn_dict.get("signature") or atomic_table.get("signature"),
                "stmt_refs": list(atom.get("stmt_refs") or []),
                "cfg_nodes": [atom.get("cfg_block_id")] if atom.get("cfg_block_id") else [],
                "condition": atom.get("condition"),
                "lvalue": lvalue,
                "rvalue": rvalue,
                "reads": reads,
                "writes": writes,
                "depends_on": list(atom.get("depends_on_atoms") or []),
                "cfg_predecessor_blocks": list(atom.get("cfg_predecessor_blocks") or []),
                "order": {
                    "kind": "cfg_partial_order",
                    "cfg_block_order": atom.get("block_order"),
                    "operation_order": atom.get("operation_order"),
                    "atomic_sequence": atom.get("sequence"),
                },
                "semantic": self.atomic_semantic(atom, kind, reads),
                "evidence": {
                    "atomic_operation": {
                        "atom_id": atom_id,
                        "source_span": atom.get("source_span"),
                        "source_expression": atom.get("source_expression"),
                        "slithir_kind": atom.get("kind"),
                        "slithir_text": atom.get("text"),
                        "implicit_slithir_operation": atom.get("implicit_slithir_operation"),
                        "physical_reference": atom.get("result_ssa"),
                    },
                },
            }))
        return out

    def atomic_fact_kind(self, atom: Json) -> str:
        if atom.get("atomic_kind") == "StateRead":
            return "StateRead"
        if atom.get("atomic_kind") == "StateWrite":
            return "StateWrite"
        reference = atom.get("reference_definition") or {}
        if reference.get("state_variable"):
            return "StorageLocationResolve"
        kind = str(atom.get("kind") or "")
        if kind == "SolidityCall":
            function_name = self.atomic_function_name(atom)
            if function_name.startswith(("require", "assert")):
                return "Require"
            if function_name.startswith("revert"):
                return "Revert"
        return self.ATOMIC_KIND_MAP.get(kind, "AtomicOperation")

    @classmethod
    def atomic_operands(cls, atom: Json, fact_kind: str | None = None) -> list[str]:
        if fact_kind == "StateRead":
            access = (atom.get("storage_access") or {}).get("access")
            return [str(access)] if access else []
        values = atom.get("read") or atom.get("arguments") or atom.get("values") or []
        return cls.unique_text(cls.ssa_value(value) for value in values)

    @classmethod
    def atomic_assignment(cls, atom: Json, fact_kind: str, reads: list[str]) -> tuple[Any, Any, list[str]]:
        operation = str(atom.get("kind") or "")
        result_ssa = str(atom.get("result_ssa") or "")
        lvalue: Any = result_ssa or None
        writes = [result_ssa] if result_ssa else []
        rvalue: Any = cls.atomic_rvalue(atom, reads)
        if fact_kind == "StateRead":
            storage = atom.get("storage_access") or {}
            access = storage.get("access")
            lvalue = result_ssa or None
            rvalue = access
            writes = [result_ssa] if result_ssa else []
        elif fact_kind == "StorageLocationResolve":
            reference = atom.get("reference_definition") or {}
            lvalue = result_ssa or None
            rvalue = reference.get("access")
            writes = [result_ssa] if result_ssa else []
        elif fact_kind == "StateWrite":
            storage = atom.get("storage_access") or {}
            access = storage.get("access") or cls.source_value(atom.get("lvalue"))
            lvalue = access
            writes = [access] if access else []
            if operation not in {"Binary", "Unary"}:
                value = atom.get("rvalue") or ((atom.get("read") or [None])[0])
                rvalue = cls.ssa_value(value)
        elif operation == "Delete":
            storage = atom.get("storage_access") or {}
            target = storage.get("access") or cls.source_value(atom.get("variable") or atom.get("lvalue"))
            lvalue = target
            rvalue = "0"
            writes = [target] if target else []
        return lvalue, rvalue, writes

    @classmethod
    def atomic_rvalue(cls, atom: Json, reads: list[str]) -> Any:
        kind = str(atom.get("kind") or "")
        operator = str(atom.get("operator") or "")
        if kind == "Assignment":
            return cls.ssa_value(atom.get("rvalue") or ((atom.get("read") or [None])[0]))
        if kind == "Binary" and len(reads) >= 2:
            return f"{reads[0]} {operator} {reads[1]}"
        if kind == "Unary" and reads:
            return f"{operator}{reads[0]}"
        if kind == "TypeConversion" and reads:
            target_type = cls.value_type(atom.get("lvalue"))
            return f"{target_type}({reads[0]})" if target_type else f"convert({reads[0]})"
        if kind == "Index" and len(reads) >= 2:
            return f"{reads[0]}[{reads[1]}]"
        if kind == "Member" and len(reads) >= 2:
            return f"{reads[0]}.{reads[1]}"
        if kind == "Length" and reads:
            return f"{reads[0]}.length"
        if kind in {"Phi", "PhiCallback"}:
            return reads
        if kind in {"Condition", "Return"}:
            return reads[0] if len(reads) == 1 else reads
        if kind == "EventCall":
            return f"{atom.get('name') or 'event'}({', '.join(reads)})"
        if kind in cls.ATOMIC_KIND_MAP and (
            kind.endswith("Call") or kind in {"Send", "Transfer", "NewContract", "NewArray", "NewStructure"}
        ):
            name = cls.atomic_function_name(atom) or kind
            destination = cls.ssa_value(atom.get("destination"))
            call_name = f"{destination}.{name}" if destination else name
            arguments = [cls.ssa_value(value) for value in atom.get("arguments") or atom.get("read") or []]
            return f"{call_name}({', '.join(arguments)})"
        return atom.get("text") or atom.get("source_expression") or kind

    @classmethod
    def atomic_semantic(cls, atom: Json, fact_kind: str, reads: list[str]) -> Json:
        if fact_kind in {"StorageLocationResolve", "StateRead", "StateWrite"}:
            semantic: Json = {
                "operation": cls.atomic_operation_name(fact_kind),
                "atomic_operation_count": 1,
            }
        else:
            semantic = {
                "operation": cls.atomic_operation_name(fact_kind),
                "atomic_operation": atom.get("kind"),
                "atomic_operation_count": 1,
                "operator": atom.get("operator"),
                "result_ssa": atom.get("result_ssa"),
                "operand_ssa": reads,
                "resolved_operands": list(atom.get("resolved_reads") or []),
                "checked": atom.get("checked"),
                "runtime_operation": atom.get("runtime_operation"),
            }
        if fact_kind == "StorageLocationResolve":
            reference = atom.get("reference_definition") or {}
            semantic.update({
                "operation": "storage_location_resolve",
                "location": cls.storage_location(reference),
            })
        elif fact_kind == "StateRead":
            storage = atom.get("storage_access") or {}
            semantic.update({
                "operation": "state_read",
                "access": storage.get("access"),
                "state_variable": storage.get("state_variable"),
                "keys": storage.get("keys"),
                "location": cls.storage_location(storage),
            })
        elif fact_kind == "StateWrite":
            storage = atom.get("storage_access") or {}
            semantic.update({
                "operation": "state_write",
                "access": storage.get("access"),
                "state_variable": storage.get("state_variable"),
                "keys": storage.get("keys"),
                "location": cls.storage_location(storage),
                "value": atom.get("expression") or cls.atomic_rvalue(atom, reads),
            })
        elif fact_kind in {
            "InternalCall", "InternalDynamicCall", "ExternalCall", "LibraryCall",
            "LowLevelCall", "BuiltinCall", "Require", "Revert",
        }:
            semantic.update({
                "function": cls.atomic_function_name(atom),
                "arguments": [cls.ssa_value(value) for value in atom.get("arguments") or atom.get("read") or []],
                "target": cls.ssa_value(atom.get("destination")),
            })
            if fact_kind == "Require":
                resolved = list(atom.get("resolved_reads") or [])
                semantic["guard"] = resolved[0] if resolved else (reads[0] if reads else None)
        elif fact_kind == "EventEmit":
            semantic.update({"operation": "event_emit", "event": atom.get("name"), "arguments": reads})
        elif fact_kind == "Return":
            semantic.update({"operation": "return", "values": reads})
        elif fact_kind == "ValuePhi":
            semantic.update({"operation": "phi", "runtime_operation": False})
        elif fact_kind == "BranchCondition":
            resolved = list(atom.get("resolved_reads") or [])
            semantic.update({
                "operation": "branch_condition",
                "predicate": resolved[0] if resolved else (reads[0] if reads else None),
                "runtime_operation": False,
            })
        return _clean(semantic)

    @staticmethod
    def storage_location(storage: Json) -> Json:
        keys = list(storage.get("keys") or [])
        reference_kind = str(storage.get("reference_kind") or "")
        if reference_kind == "index" or keys:
            container_type = str(storage.get("container_type") or "")
            kind = "mapping" if "mapping" in container_type else "indexed_storage"
        elif reference_kind == "member":
            kind = "storage_member"
        else:
            kind = "state_variable"
        return _clean({
            "kind": kind,
            "access": storage.get("access"),
            "state_variable": storage.get("state_variable"),
            "keys": keys,
            "member": storage.get("member"),
        })

    @staticmethod
    def fact_role(kind: str) -> str:
        if kind in {
            "StateRead", "StateWrite", "EventEmit", "InternalCall",
            "InternalDynamicCall", "ExternalCall", "LibraryCall", "BuiltinCall",
            "LowLevelCall", "ValueTransferCall", "Revert", "Return",
        }:
            return "effect"
        if kind in {"Require", "BranchCondition"}:
            return "control"
        if kind == "ValuePhi":
            return "analysis_support"
        return "support"

    @staticmethod
    def ssa_value(value: Any) -> str:
        if value is None:
            return ""
        if not isinstance(value, dict):
            return str(value)
        text = str(value.get("text") or value.get("name") or value.get("base_name") or "")
        if value.get("is_constant") and text in {"True", "False"}:
            return text.lower()
        return text

    @staticmethod
    def source_value(value: Any) -> str:
        if value is None:
            return ""
        if not isinstance(value, dict):
            return str(value)
        return str(value.get("base_name") or value.get("name") or value.get("text") or "")

    @staticmethod
    def value_type(value: Any) -> str:
        return str(value.get("type") or "") if isinstance(value, dict) else ""

    @staticmethod
    def unique_text(values: Any) -> list[str]:
        return list(dict.fromkeys(str(value) for value in values if value not in {None, ""}))

    @staticmethod
    def atomic_function_name(atom: Json) -> str:
        function = atom.get("function") or {}
        if isinstance(function, dict):
            name = str(atom.get("function_name") or function.get("name") or function.get("full_name") or atom.get("name") or "")
        else:
            name = str(atom.get("function_name") or function or atom.get("name") or "")
        if not name.startswith(("revert ", "require", "assert")):
            match = re.fullmatch(r"([A-Za-z_$][A-Za-z0-9_.$]*)\([^)]*\)", name)
            if match:
                return match.group(1)
        return name

    @staticmethod
    def atomic_operation_name(fact_kind: str) -> str:
        return re.sub(r"(?<!^)(?=[A-Z])", "_", fact_kind).lower()


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value
