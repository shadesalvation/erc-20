#!/usr/bin/env python3
from __future__ import annotations

import json
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
        "CodeSize": "CodeSizeQuery",
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

    # Frozen from Slither's ``SOLIDITY_FUNCTIONS`` registry (the Solidity,
    # rather than Vyper-only, entries).  Most are intentionally represented
    # by the generic BuiltinCall: exact signature, arguments, result and CFG
    # position are enough to reconstruct their source meaning.  The smaller
    # set that needs a dedicated SFIR operation is selected below.
    KNOWN_SOLIDITY_BUILTIN_SIGNATURES = frozenset({
        "blobhash(uint256)", "gasleft()", "assert(bool)", "require(bool)",
        "require(bool,string)", "require(bool,error)", "revert()",
        "revert(string)", "addmod(uint256,uint256,uint256)",
        "mulmod(uint256,uint256,uint256)", "keccak256()", "keccak256(bytes)",
        "sha256()", "sha256(bytes)", "sha3()", "ripemd160()",
        "ripemd160(bytes)", "ecrecover(bytes32,uint8,bytes32,bytes32)",
        "selfdestruct(address)", "suicide(address)", "log0(bytes32)",
        "log1(bytes32,bytes32)", "log2(bytes32,bytes32,bytes32)",
        "log3(bytes32,bytes32,bytes32,bytes32)", "blockhash(uint256)",
        "prevrandao()", "erc7201(string)", "this.balance()", "abi.encode()",
        "abi.encodePacked()", "abi.encodeWithSelector()",
        "abi.encodeWithSignature()", "abi.encodeCall()", "bytes.concat()",
        "string.concat()", "abi.decode()", "type(address)", "type()",
        "balance(address)", "code(address)", "codehash(address)",
    })

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
            if atom.get("fact_eligible") is False:
                continue
            atom_id = str(atom.get("atom_id") or "")
            kind = self.atomic_fact_kind(atom)
            operands = self.atomic_operands(atom, kind)
            # Constants participate in the displayed operation but do not
            # demand FactSSA definitions.  Keep these two roles separate.
            reads = self.atomic_ssa_reads(atom, kind, operands)
            lvalue, rvalue, writes = self.atomic_assignment(atom, kind, operands)
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
        if atom.get("kind") == "InternalCall" and atom.get("is_modifier_call") is True:
            return "ModifierApply"
        if atom.get("kind") == "InternalCall" and (atom.get("function") or {}).get("is_constructor") is True:
            # Slither represents an explicit base-constructor invocation in a
            # derived constructor as InternalCall.  It is not a normal
            # runtime internal function call, so keep its deployment-time
            # semantic role distinct while retaining the resolved declaration
            # and argument data from the same SlithIR operation.
            return "BaseConstructorCall"
        reference = atom.get("reference_definition") or {}
        if reference.get("state_variable"):
            return "StorageLocationResolve"
        kind = str(atom.get("kind") or "")
        if kind == "SolidityCall":
            function_signature = self.atomic_function_signature(atom)
            function_name = self.atomic_function_name(atom)
            if function_name.startswith("require"):
                return "Require"
            if function_name.startswith("revert"):
                return "Revert"
            if function_name.startswith("assert"):
                return "Assert"
            if function_signature == "abi.decode()":
                return "AbiDecode"
            if function_signature.startswith("abi.encode"):
                return "AbiEncode"
            if function_signature.startswith(("bytes.concat(", "string.concat(")):
                return "Concat"
            if function_signature.startswith(("keccak256", "sha256", "sha3", "ripemd160")):
                return "HashCompute"
            if function_signature.startswith(("addmod(", "mulmod(")):
                return "ModularArithmetic"
            if function_signature.startswith("ecrecover("):
                return "SignatureRecover"
            if function_signature.startswith("gasleft("):
                return "GasQuery"
            if function_signature.startswith(("blockhash(", "blobhash(")):
                return "BlockHashQuery"
            if function_signature.startswith(("balance(address)", "code(address)", "codehash(address)")):
                return "AddressPropertyRead"
            if function_signature.startswith(("selfdestruct(", "suicide(")):
                return "SelfDestruct"
            if not self.is_known_solidity_builtin_signature(function_signature):
                return "UnmodeledBuiltinCall"
        return self.ATOMIC_KIND_MAP.get(kind, "UnmodeledSlithIROperation")

    @classmethod
    def is_known_solidity_builtin_signature(cls, signature: str) -> bool:
        """Return true only for current Slither Solidity builtin signatures.

        Custom errors are intentionally a ``revert `` prefix rather than a
        registry entry.  Slither resolves them as SolidityCall too, and their
        source-level error name/arguments are retained by the Revert rule.
        """
        return signature in cls.KNOWN_SOLIDITY_BUILTIN_SIGNATURES or signature.startswith("revert ")

    @classmethod
    def atomic_operands(cls, atom: Json, fact_kind: str | None = None) -> list[str]:
        if fact_kind == "StateRead":
            access = (atom.get("storage_access") or {}).get("access")
            return [str(access)] if access else []
        if fact_kind == "StateWrite" and atom.get("kind") == "Delete":
            storage = atom.get("storage_access") or {}
            return [str(value) for value in storage.get("keys") or []]
        values = atom.get("read") or atom.get("arguments") or atom.get("values") or []
        return cls.unique_text(cls.ssa_value(value) for value in values)

    @classmethod
    def atomic_ssa_reads(cls, atom: Json, fact_kind: str | None, operands: list[str]) -> list[str]:
        if fact_kind == "StateRead":
            access = (atom.get("storage_access") or {}).get("access")
            return [str(access)] if access else []
        if fact_kind == "StateWrite" and atom.get("kind") == "Delete":
            storage = atom.get("storage_access") or {}
            return [str(value) for value in storage.get("keys") or []]
        values = atom.get("read") or atom.get("arguments") or atom.get("values") or []
        return cls.unique_text(
            cls.ssa_value(value)
            for value in values
            if not cls.is_constant(value)
        )

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
            if operation == "Delete":
                rvalue = "0"
            elif operation not in {"Binary", "Unary"}:
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
        if kind == "Member" and reads:
            member = cls.source_value(atom.get("variable_right"))
            if member:
                return f"{reads[0]}.{member}"
            return f"{reads[0]}.{reads[1]}" if len(reads) >= 2 else reads[0]
        if kind == "Length" and reads:
            return f"{reads[0]}.length"
        if kind == "CodeSize":
            # CodeSize is a first-class SlithIR operation (rather than a
            # textual Yul opcode).  Its AST-linked source expression is the
            # only safe spelling of the high-level property across Solidity
            # and Vyper frontends, so retain that evidence without parsing
            # SlithIR's ``CODESIZE`` display text.
            return atom.get("source_expression") or (f"code_size({reads[0]})" if reads else "code_size")
        if kind in {"Phi", "PhiCallback"}:
            return reads
        if kind in {"Condition", "Return"}:
            return reads[0] if len(reads) == 1 else reads
        if kind == "EventCall":
            return f"{atom.get('name') or 'event'}({', '.join(reads)})"
        if kind == "NewArray":
            array_type = cls.ssa_value(atom.get("array_type")) or cls.value_type(atom.get("lvalue"))
            arguments = cls.atomic_value_operands(atom)
            return f"new {array_type}({', '.join(arguments)})"
        if kind == "InitArray":
            return f"[{', '.join(cls.atomic_value_operands(atom))}]"
        if kind == "NewStructure":
            structure = cls.ssa_value(atom.get("structure_name") or atom.get("structure")) or cls.value_type(atom.get("lvalue"))
            names = list(atom.get("argument_names") or [])
            values = cls.atomic_value_operands(atom)
            arguments = [
                f"{name}: {value}" for name, value in zip(names, values)
            ] if names and len(names) == len(values) else values
            return f"{structure}({{{', '.join(arguments)}}})" if names else f"{structure}({', '.join(arguments)})"
        if kind == "Unpack":
            tuple_value = cls.ssa_value(atom.get("tuple")) or (reads[0] if reads else "tuple")
            return f"{tuple_value}[{atom.get('index')}]"
        if kind in {"Send", "Transfer"}:
            destination = cls.ssa_value(atom.get("destination"))
            value = cls.ssa_value(atom.get("call_value"))
            method = "send" if kind == "Send" else "transfer"
            return f"{destination}.{method}({value})"
        if kind == "NewContract":
            contract = cls.ssa_value(atom.get("contract_name")) or cls.value_type(atom.get("lvalue"))
            options = cls.call_options(atom, include_salt=True)
            arguments = [cls.ssa_value(value) for value in atom.get("arguments") or []]
            return f"new {contract}{options}({', '.join(arguments)})"
        if kind in cls.ATOMIC_KIND_MAP and (
            kind.endswith("Call") or kind in {"Send", "Transfer"}
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
            raw_rvalue = atom.get("rvalue")
            # For an ordinary assignment SlithIR already supplies the value
            # that has been evaluated on this CFG path.  ``expression`` is a
            # display-oriented def-use expansion and may inline a call that
            # has already occurred (making a write appear to invoke it a
            # second time).  Preserve the SSA value at the semantic boundary;
            # Binary/Unary writes have no rvalue field and continue to use
            # their normalized high-level expression.
            value = (
                cls.ssa_value(raw_rvalue)
                if raw_rvalue is not None
                else (atom.get("expression") or cls.atomic_rvalue(atom, reads))
            )
            semantic.update({
                "operation": "state_write",
                "access": storage.get("access"),
                "state_variable": storage.get("state_variable"),
                "keys": storage.get("keys"),
                "location": cls.storage_location(storage),
                "value": value,
            })
        elif fact_kind == "ValueAssign" and atom.get("storage_alias"):
            alias = atom.get("storage_alias") or {}
            semantic.update({
                "operation": "storage_alias_bind",
                "storage_alias": {
                    "local": alias.get("local"),
                    "location": cls.storage_location(alias.get("location") or {}),
                },
            })
        elif fact_kind == "ValueAssign":
            rvalue = atom.get("rvalue") or {}
            if isinstance(rvalue, dict) and rvalue.get("canonical_name"):
                semantic["function_value_canonical_name"] = rvalue.get("canonical_name")
        elif fact_kind == "NewArray":
            semantic.update({
                "array_type": cls.ssa_value(atom.get("array_type")) or cls.value_type(atom.get("lvalue")),
                "length": (cls.atomic_value_operands(atom) or [None])[0],
            })
        elif fact_kind == "ArrayConstruct":
            semantic.update({
                "elements": cls.atomic_value_operands(atom, preferred="init_values"),
            })
        elif fact_kind == "NewStructure":
            semantic.update({
                "structure": cls.ssa_value(atom.get("structure_name") or atom.get("structure")) or cls.value_type(atom.get("lvalue")),
                "arguments": [cls.ssa_value(value) for value in atom.get("arguments") or atom.get("read") or []],
                "argument_names": list(atom.get("argument_names") or []),
            })
        elif fact_kind == "TupleUnpack":
            semantic.update({
                "tuple": cls.ssa_value(atom.get("tuple")) or (reads[0] if reads else None),
                "index": atom.get("index"),
            })
        elif fact_kind == "NewContract":
            semantic.update({
                "contract": cls.ssa_value(atom.get("contract_name")) or cls.value_type(atom.get("lvalue")),
                "constructor_arguments": [cls.ssa_value(value) for value in atom.get("arguments") or []],
                "argument_names": list(atom.get("argument_names") or []),
                "call_value": cls.ssa_value(atom.get("call_value")),
                "salt": cls.ssa_value(atom.get("call_salt")),
                "call_id": atom.get("call_id"),
            })
        elif fact_kind == "ValueTransferCall":
            semantic.update({
                "target": cls.ssa_value(atom.get("destination")),
                "value": cls.ssa_value(atom.get("call_value")),
                "method": "send" if atom.get("kind") == "Send" else "transfer",
            })
        elif fact_kind in {
            "ModifierApply", "BaseConstructorCall", "InternalCall", "InternalDynamicCall", "ExternalCall", "LibraryCall",
            "LowLevelCall", "BuiltinCall", "UnmodeledBuiltinCall", "Require", "Assert", "Revert", "SelfDestruct",
        }:
            semantic.update({
                "function": cls.atomic_function_name(atom),
                "arguments": [cls.ssa_value(value) for value in atom.get("arguments") or atom.get("read") or []],
                "target": cls.ssa_value(atom.get("destination")),
                "argument_names": list(atom.get("argument_names") or []),
                "argument_count": atom.get("nbr_arguments"),
                "call_value": cls.ssa_value(atom.get("call_value")),
                "call_gas": cls.ssa_value(atom.get("call_gas")),
                "call_id": atom.get("call_id"),
                "call_return_type": atom.get("type_call"),
                "call_kind": cls.atomic_function_name(atom) if fact_kind == "LowLevelCall" else None,
                "function_type": atom.get("function_type") if fact_kind == "InternalDynamicCall" else None,
                "dynamic_function_ssa": (
                    (atom.get("function") or {}).get("text")
                    if fact_kind == "InternalDynamicCall" and isinstance(atom.get("function"), dict)
                    else None
                ),
            })
            function = atom.get("function") or {}
            if isinstance(function, dict):
                semantic.update({
                    "function_signature": function.get("full_name"),
                    "function_canonical_name": function.get("canonical_name"),
                    "function_contract": function.get("contract"),
                })
            if fact_kind == "Require":
                resolved = list(atom.get("resolved_reads") or [])
                semantic["guard"] = resolved[0] if resolved else (reads[0] if reads else None)
            if fact_kind == "Assert":
                resolved = list(atom.get("resolved_reads") or [])
                semantic["guard"] = resolved[0] if resolved else (reads[0] if reads else None)
            if fact_kind == "ModifierApply":
                semantic.update({
                    "operation": "modifier_apply",
                    "modifier": cls.atomic_function_name(atom),
                    "modifier_function_id": semantic.get("function_canonical_name"),
                    "execution_model": "modifier_cfg_runs_before_placeholder; placeholder resumes_wrapped_function_body; modifier_cfg_continues_after_body",
                })
            if fact_kind == "BaseConstructorCall":
                semantic.update({
                    "operation": "base_constructor_call",
                    "constructor_contract": semantic.get("function_contract"),
                    "execution_phase": "contract_construction",
                })
            if fact_kind == "BuiltinCall":
                semantic.update({
                    "builtin_signature": cls.atomic_function_signature(atom),
                    "model_status": "generic_known",
                })
            if fact_kind == "UnmodeledBuiltinCall":
                semantic.update({
                    "builtin_signature": cls.atomic_function_signature(atom),
                    "model_status": "unmodeled",
                    "unmodeled_reason": "unknown_slither_solidity_function_signature",
                })
            if fact_kind == "SelfDestruct":
                semantic.update({
                    "builtin_signature": cls.atomic_function_signature(atom),
                    "model_status": "specialized",
                })
        elif fact_kind in {
            "AbiEncode", "AbiDecode", "Concat", "HashCompute", "ModularArithmetic", "SignatureRecover",
            "GasQuery", "BlockHashQuery",
        }:
            signature = cls.atomic_function_signature(atom)
            semantic.update({
                "builtin_signature": signature,
                "arguments": [cls.ssa_value(value) for value in atom.get("arguments") or atom.get("read") or []],
                "result_type": cls.value_type(atom.get("lvalue")),
                "builtin_operation": cls.atomic_operation_name(fact_kind),
                "model_status": "specialized",
            })
        elif fact_kind == "AddressPropertyRead":
            signature = cls.atomic_function_signature(atom)
            values = atom.get("arguments") or atom.get("read") or []
            semantic.update({
                "builtin_signature": signature,
                "address": cls.ssa_value(values[0]) if values else None,
                "property": signature.split("(", 1)[0],
                "result_type": cls.value_type(atom.get("lvalue")),
                "builtin_operation": "address_property_read",
                "model_status": "specialized",
            })
        elif fact_kind == "CodeSizeQuery":
            semantic.update({
                "operation": "code_size_query",
                "value": cls.ssa_value((atom.get("read") or [None])[0]),
                "source_expression": atom.get("source_expression"),
                "model_status": "specialized",
            })
        elif fact_kind == "EventEmit":
            semantic.update({
                "operation": "event_emit", "event": atom.get("name"),
                "arguments": [cls.ssa_value(value) for value in atom.get("arguments") or atom.get("read") or []],
            })
        elif fact_kind == "Return":
            semantic.update({
                "operation": "return",
                "values": [cls.ssa_value(value) for value in atom.get("values") or atom.get("read") or []],
            })
        elif fact_kind == "ValuePhi":
            semantic.update({
                "operation": "phi",
                "phi_role": atom.get("phi_role"),
                "inputs": reads,
                "origin_nodes": list(atom.get("phi_origin_nodes") or []),
                "runtime_operation": False,
            })
        elif fact_kind == "BranchCondition":
            resolved = list(atom.get("resolved_reads") or [])
            semantic.update({
                "operation": "branch_condition",
                "predicate": resolved[0] if resolved else (reads[0] if reads else None),
                "runtime_operation": False,
            })
        elif fact_kind == "UnmodeledSlithIROperation":
            semantic.update({
                "operation": "unmodeled_slithir_operation",
                "model_status": "unmodeled",
                "unmodeled_reason": "unknown_slithir_operation_kind",
                "slithir_kind": atom.get("kind"),
            })
        # Named compile-time operands have declarations, not runtime SSA
        # definitions. Retain their initializer as evidence without evaluating
        # it or treating the declaration as a persistent-state dependency.
        constant_operands = []
        for value in atom.get("read") or []:
            declaration = value.get("constant_declaration") if isinstance(value, dict) else None
            if declaration and declaration not in constant_operands:
                constant_operands.append(dict(declaration))
        if constant_operands:
            semantic["constant_operands"] = constant_operands
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
            "StateRead", "StateWrite", "EventEmit", "ModifierApply", "BaseConstructorCall", "InternalCall",
            "InternalDynamicCall", "ExternalCall", "LibraryCall", "BuiltinCall", "UnmodeledBuiltinCall",
            "LowLevelCall", "ValueTransferCall", "NewContract", "Revert", "Return", "SelfDestruct",
        }:
            return "effect"
        if kind in {"Require", "Assert", "BranchCondition"}:
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
        if value.get("constant_declaration"):
            return str(value["constant_declaration"]["name"])
        text = str(value.get("text") or value.get("name") or value.get("base_name") or "")
        if value.get("is_constant") and text in {"True", "False"}:
            return text.lower()
        if value.get("is_constant") and "string" in str(value.get("type") or "").lower():
            return json.dumps(text, ensure_ascii=False)
        return text

    @staticmethod
    def is_constant(value: Any) -> bool:
        return isinstance(value, dict) and bool(value.get("is_constant"))

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
    def atomic_function_signature(atom: Json) -> str:
        function = atom.get("function") or {}
        if isinstance(function, dict):
            return str(function.get("full_name") or function.get("name") or "")
        return str(function or "")

    @classmethod
    def atomic_value_operands(cls, atom: Json, *, preferred: str = "arguments") -> list[str]:
        values = atom.get(preferred) or atom.get("arguments") or atom.get("read") or atom.get("values") or []
        return [cls.ssa_value(value) for value in values]

    @classmethod
    def call_options(cls, atom: Json, *, include_salt: bool = False) -> str:
        options: list[str] = []
        value = cls.ssa_value(atom.get("call_value"))
        if value:
            options.append(f"value: {value}")
        gas = cls.ssa_value(atom.get("call_gas"))
        if gas:
            options.append(f"gas: {gas}")
        if include_salt:
            salt = cls.ssa_value(atom.get("call_salt"))
            if salt:
                options.append(f"salt: {salt}")
        return f"{{{', '.join(options)}}}" if options else ""

    @staticmethod
    def atomic_operation_name(fact_kind: str) -> str:
        return re.sub(r"(?<!^)(?=[A-Z])", "_", fact_kind).lower()


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value
