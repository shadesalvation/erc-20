from __future__ import annotations

from enum import Enum
from typing import Any


class DeclarationKind(Enum):
    LOCAL_VARIABLE = "local_variable"
    PARAMETER = "parameter"
    RETURN_PARAMETER = "return_parameter"
    STATE_VARIABLE = "state_variable"
    FUNCTION = "function"
    UNKNOWN = "unknown"


class ASTIndex:
    def __init__(self, root: dict[str, Any], contracts: dict[str, Any]) -> None:
        self.ast_id_to_node: dict[int, dict[str, Any]] = {}
        self.declaration_kinds: dict[int, DeclarationKind] = {}
        self.storage_layout: dict[int, dict[str, Any]] = {}
        self._index(root)
        self._classify_declarations(root)
        self._index_storage_layout(contracts)

    def node(self, ast_id: int | None) -> dict[str, Any] | None:
        if ast_id is None:
            return None
        return self.ast_id_to_node.get(ast_id)

    def declaration_kind(self, ast_id: int | None) -> DeclarationKind:
        if ast_id is None:
            return DeclarationKind.UNKNOWN
        return self.declaration_kinds.get(ast_id, DeclarationKind.UNKNOWN)

    def storage_info(self, ast_id: int | None) -> dict[str, Any] | None:
        if ast_id is None:
            return None
        return self.storage_layout.get(ast_id)

    def _index(self, value: Any) -> None:
        if isinstance(value, dict):
            node_id = value.get("id")
            if isinstance(node_id, int):
                self.ast_id_to_node[node_id] = value
            for child in value.values():
                self._index(child)
        elif isinstance(value, list):
            for child in value:
                self._index(child)

    def _classify_declarations(self, root: dict[str, Any]) -> None:
        for node in self.ast_id_to_node.values():
            if node.get("nodeType") == "FunctionDefinition":
                node_id = node.get("id")
                if isinstance(node_id, int):
                    self.declaration_kinds[node_id] = DeclarationKind.FUNCTION
                self._mark_parameter_list(node.get("parameters"), DeclarationKind.PARAMETER)
                self._mark_parameter_list(
                    node.get("returnParameters"), DeclarationKind.RETURN_PARAMETER
                )
            elif node.get("nodeType") == "VariableDeclaration":
                node_id = node.get("id")
                if not isinstance(node_id, int):
                    continue
                if node.get("stateVariable"):
                    self.declaration_kinds[node_id] = DeclarationKind.STATE_VARIABLE
                else:
                    self.declaration_kinds.setdefault(node_id, DeclarationKind.LOCAL_VARIABLE)

    def _mark_parameter_list(self, node: dict[str, Any] | None, kind: DeclarationKind) -> None:
        if not node:
            return
        for parameter in node.get("parameters", []):
            node_id = parameter.get("id")
            if isinstance(node_id, int):
                self.declaration_kinds[node_id] = kind

    def _index_storage_layout(self, contracts: dict[str, Any]) -> None:
        for contract_name, contract_output in contracts.items():
            layout = contract_output.get("storageLayout", {})
            for item in layout.get("storage", []):
                ast_id = item.get("astId")
                if ast_id is None:
                    continue
                entry = dict(item)
                entry["contract"] = contract_name
                try:
                    self.storage_layout[int(ast_id)] = entry
                except (TypeError, ValueError):
                    continue
