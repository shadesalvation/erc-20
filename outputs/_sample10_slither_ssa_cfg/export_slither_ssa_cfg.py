#!/usr/bin/env python3
"""Export a Slither function CFG whose IR labels use ``node.irs_ssa``.

Slither's built-in ``--print cfg`` deliberately renders ``node.irs`` (the
non-SSA SlithIR).  This tiny audit helper preserves the identical CFG and
branch edges, but renders the SSA IR Slither has already computed for each
node.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from slither.core.cfg.node import NodeType
from slither.slither import Slither


def _dot_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")


def ssa_cfg_to_dot(function: object) -> str:
    """Mirror Slither's CFG exporter, replacing ``irs`` with ``irs_ssa``."""
    content = ["digraph{", "node [shape=box];"]
    for node in function.nodes:
        label = f"Node Type: {node.type.value} {node.node_id}\n"
        if node.expression:
            label += f"\nSOURCE EXPRESSION (non-SSA):\n{node.expression}\n"
        if node.irs_ssa:
            label += "\nSlithIR-SSA:\n" + "\n".join(str(ir) for ir in node.irs_ssa)
        content.append(f'{node.node_id}[label="{_dot_label(label)}"];')
        if node.type in (NodeType.IF, NodeType.IFLOOP):
            if node.son_true:
                content.append(f'{node.node_id}->{node.son_true.node_id}[label="True"];')
            if node.son_false:
                content.append(f'{node.node_id}->{node.son_false.node_id}[label="False"];')
        else:
            content.extend(f"{node.node_id}->{son.node_id};" for son in node.sons)
    content.append("}")
    return "\n".join(content) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    source = args.source.resolve()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    slither = Slither(str(source))
    index: list[dict[str, str]] = []
    for contract in slither.contracts:
        for function in contract.functions + list(contract.modifiers):
            filename = _safe_filename(f"{contract.name}.{function.full_name}") + ".ssa.dot"
            (out_dir / filename).write_text(ssa_cfg_to_dot(function), encoding="utf-8")
            index.append({"contract": contract.name, "function": function.full_name, "file": filename})
    (out_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
