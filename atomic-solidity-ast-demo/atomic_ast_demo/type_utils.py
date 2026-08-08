from __future__ import annotations

import re
from typing import Any


VALUE_TYPE_RE = re.compile(
    r"^(bool|address payable|address|uint(?:8|16|24|32|40|48|56|64|72|80|88|96|104|112|120|128|136|144|152|160|168|176|184|192|200|208|216|224|232|240|248|256)?|int(?:8|16|24|32|40|48|56|64|72|80|88|96|104|112|120|128|136|144|152|160|168|176|184|192|200|208|216|224|232|240|248|256)?|bytes(?:[1-9]|[12][0-9]|3[0-2]))$"
)


def type_descriptions(expr: dict[str, Any]) -> dict[str, str] | None:
    value = expr.get("typeDescriptions")
    if isinstance(value, dict) and value.get("typeString"):
        return {
            "typeIdentifier": str(value.get("typeIdentifier", "")),
            "typeString": str(value["typeString"]),
        }
    return None


def is_supported_value_type(type_string: str) -> bool:
    return bool(VALUE_TYPE_RE.match(type_string))


def elementary_type_name(type_desc: dict[str, str], src: str, node_id: int) -> dict[str, Any]:
    type_string = type_desc["typeString"]
    if not is_supported_value_type(type_string):
        raise ValueError(f"unsupported temporary type: {type_string}")

    if type_string == "address payable":
        node = {
            "id": node_id,
            "name": "address",
            "nodeType": "ElementaryTypeName",
            "src": src,
            "stateMutability": "payable",
            "typeDescriptions": dict(type_desc),
        }
        return node

    name = "uint256" if type_string == "uint" else "int256" if type_string == "int" else type_string
    return {
        "id": node_id,
        "name": name,
        "nodeType": "ElementaryTypeName",
        "src": src,
        "typeDescriptions": dict(type_desc),
    }

