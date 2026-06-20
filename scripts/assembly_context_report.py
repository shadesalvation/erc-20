#!/usr/bin/env python3
"""
Locate Solidity inline assembly blocks and report their function context.

This script intentionally uses only the Python standard library. It parses the
source text directly, preserving character offsets so reports can point back to
exact source ranges.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable


ERC20_STANDARD_SIGNATURES = {
    "name": (),
    "symbol": (),
    "decimals": (),
    "totalSupply": (),
    "balanceOf": ("address",),
    "transfer": ("address", "uint256"),
    "allowance": ("address", "address"),
    "approve": ("address", "uint256"),
    "transferFrom": ("address", "address", "uint256"),
}

REACHABILITY_ENTRYPOINTS = {"transfer", "transferFrom", "approve"}
VISIBILITY_KEYWORDS = {"public", "external", "internal", "private"}


@dataclass
class SourcePosition:
    start_offset: int
    end_offset: int
    start_line: int
    start_column: int
    end_line: int
    end_column: int


@dataclass
class FunctionInfo:
    contract: str
    name: str
    full_name: str
    parameters: list[dict[str, str]]
    visibility: str | None
    body_start: int
    body_end: int
    source_position: SourcePosition
    is_erc20_standard_entry: bool
    erc20_signature: str | None
    calls: list[str]


@dataclass
class AssemblyBlockInfo:
    source_file: str
    contract: str
    function: str
    function_parameters: list[dict[str, str]]
    function_visibility: str | None
    function_is_erc20_standard_entry: bool
    function_erc20_signature: str | None
    assembly_position: SourcePosition
    assembly_snippet: str
    reachable_from_erc20_entrypoints: bool
    reachable_from: list[str]


def mask_comments_and_strings(source: str) -> str:
    chars = list(source)
    i = 0
    n = len(chars)
    while i < n:
        c = chars[i]
        nxt = chars[i + 1] if i + 1 < n else ""

        if c == "/" and nxt == "/":
            chars[i] = chars[i + 1] = " "
            i += 2
            while i < n and chars[i] != "\n":
                chars[i] = " "
                i += 1
            continue

        if c == "/" and nxt == "*":
            chars[i] = chars[i + 1] = " "
            i += 2
            while i + 1 < n and not (chars[i] == "*" and chars[i + 1] == "/"):
                if chars[i] != "\n":
                    chars[i] = " "
                i += 1
            if i + 1 < n:
                chars[i] = chars[i + 1] = " "
                i += 2
            continue

        if c in {"'", '"'}:
            quote = c
            chars[i] = " "
            i += 1
            while i < n:
                if chars[i] == "\\":
                    chars[i] = " "
                    if i + 1 < n and chars[i + 1] != "\n":
                        chars[i + 1] = " "
                    i += 2
                    continue
                if chars[i] == quote:
                    chars[i] = " "
                    i += 1
                    break
                if chars[i] != "\n":
                    chars[i] = " "
                i += 1
            continue

        i += 1
    return "".join(chars)


def build_line_starts(source: str) -> list[int]:
    starts = [0]
    for match in re.finditer(r"\n", source):
        starts.append(match.end())
    return starts


def offset_to_line_col(line_starts: list[int], offset: int) -> tuple[int, int]:
    lo, hi = 0, len(line_starts)
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if line_starts[mid] <= offset:
            lo = mid
        else:
            hi = mid
    return lo + 1, offset - line_starts[lo] + 1


def source_position(source: str, line_starts: list[int], start: int, end: int) -> SourcePosition:
    start_line, start_column = offset_to_line_col(line_starts, start)
    end_line, end_column = offset_to_line_col(line_starts, max(start, end - 1))
    return SourcePosition(
        start_offset=start,
        end_offset=end,
        start_line=start_line,
        start_column=start_column,
        end_line=end_line,
        end_column=end_column,
    )


def find_matching(text: str, open_index: int, open_ch: str, close_ch: str) -> int:
    depth = 0
    for i in range(open_index, len(text)):
        if text[i] == open_ch:
            depth += 1
        elif text[i] == close_ch:
            depth -= 1
            if depth == 0:
                return i
    return -1


def find_contracts(masked: str) -> list[tuple[str, int, int, int, int]]:
    contracts = []
    pattern = re.compile(r"\b(contract|library|interface)\s+([A-Za-z_$][A-Za-z0-9_$]*)\b")
    for match in pattern.finditer(masked):
        brace = masked.find("{", match.end())
        if brace == -1:
            continue
        end = find_matching(masked, brace, "{", "}")
        if end == -1:
            continue
        contracts.append((match.group(2), match.start(), brace, brace + 1, end))
    return contracts


def split_top_level_commas(text: str) -> list[str]:
    parts = []
    start = 0
    depth = 0
    for i, ch in enumerate(text):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def normalize_type(type_text: str) -> str:
    return re.sub(r"\s+", " ", type_text.strip())


def parse_parameters(params_text: str) -> list[dict[str, str]]:
    params = []
    if not params_text.strip():
        return params

    storage_modifiers = {"memory", "calldata", "storage", "indexed", "payable"}
    for raw in split_top_level_commas(params_text):
        tokens = raw.split()
        if not tokens:
            continue

        name = ""
        type_tokens = tokens[:]
        if len(tokens) > 1 and re.match(r"^[A-Za-z_$][A-Za-z0-9_$]*$", tokens[-1]):
            if tokens[-1] not in storage_modifiers:
                name = tokens[-1]
                type_tokens = tokens[:-1]

        clean_type = " ".join(token for token in type_tokens if token not in storage_modifiers)
        params.append({"type": normalize_type(clean_type), "name": name, "raw": raw})
    return params


def signature_for(name: str, params: list[dict[str, str]]) -> str:
    types = ",".join(param["type"] for param in params)
    return f"{name}({types})"


def is_erc20_standard_entry(name: str, params: list[dict[str, str]]) -> tuple[bool, str | None]:
    expected = ERC20_STANDARD_SIGNATURES.get(name)
    if expected is None:
        return False, None
    actual = tuple(param["type"] for param in params)
    if actual == expected:
        return True, signature_for(name, params)
    return False, None


def find_visibility(header_tail: str) -> str | None:
    for token in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", header_tail):
        if token in VISIBILITY_KEYWORDS:
            return token
    return None


def find_functions(source: str, masked: str, contract_name: str, contract_body_start: int, contract_body_end: int, line_starts: list[int]) -> list[FunctionInfo]:
    functions = []
    pattern = re.compile(r"\b(function|constructor|fallback|receive)\b")
    pos = contract_body_start

    while True:
        match = pattern.search(masked, pos, contract_body_end)
        if not match:
            break

        keyword = match.group(1)
        paren = masked.find("(", match.end(), contract_body_end)
        if paren == -1:
            pos = match.end()
            continue

        if keyword == "function":
            name_text = masked[match.end():paren].strip()
            name_match = re.match(r"([A-Za-z_$][A-Za-z0-9_$]*)$", name_text)
            if not name_match:
                pos = match.end()
                continue
            name = name_match.group(1)
        else:
            name = keyword

        paren_end = find_matching(masked, paren, "(", ")")
        if paren_end == -1 or paren_end > contract_body_end:
            pos = paren + 1
            continue

        brace = masked.find("{", paren_end, contract_body_end)
        semi = masked.find(";", paren_end, contract_body_end)
        if brace == -1 or (semi != -1 and semi < brace):
            pos = paren_end + 1
            continue

        body_end = find_matching(masked, brace, "{", "}")
        if body_end == -1 or body_end > contract_body_end:
            pos = brace + 1
            continue

        params = parse_parameters(source[paren + 1:paren_end])
        header_tail = masked[paren_end + 1:brace]
        visibility = find_visibility(header_tail)
        is_entry, erc20_sig = is_erc20_standard_entry(name, params)
        function_body = masked[brace + 1:body_end]
        calls = sorted(set(re.findall(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", function_body)))
        calls = [call for call in calls if call not in {"if", "for", "while", "require", "assert", "revert", "emit", "assembly"}]

        functions.append(
            FunctionInfo(
                contract=contract_name,
                name=name,
                full_name=f"{contract_name}.{name}",
                parameters=params,
                visibility=visibility,
                body_start=brace + 1,
                body_end=body_end,
                source_position=source_position(source, line_starts, match.start(), body_end + 1),
                is_erc20_standard_entry=is_entry,
                erc20_signature=erc20_sig,
                calls=calls,
            )
        )
        pos = body_end + 1

    return functions


def find_assembly_blocks(source: str, masked: str, function: FunctionInfo, line_starts: list[int]) -> list[tuple[SourcePosition, str]]:
    blocks = []
    pattern = re.compile(r"\bassembly\b")
    pos = function.body_start
    while True:
        match = pattern.search(masked, pos, function.body_end)
        if not match:
            break
        brace = masked.find("{", match.end(), function.body_end)
        if brace == -1:
            pos = match.end()
            continue
        end = find_matching(masked, brace, "{", "}")
        if end == -1 or end > function.body_end:
            pos = brace + 1
            continue
        block_start = match.start()
        block_end = end + 1
        blocks.append((source_position(source, line_starts, block_start, block_end), source[block_start:block_end]))
        pos = block_end
    return blocks


def compute_reachability(functions: list[FunctionInfo]) -> dict[str, set[str]]:
    by_contract: dict[str, dict[str, FunctionInfo]] = {}
    for function in functions:
        by_contract.setdefault(function.contract, {})[function.name] = function

    reachable_to_sources: dict[str, set[str]] = {function.full_name: set() for function in functions}
    for contract, fn_by_name in by_contract.items():
        entrypoints = [
            fn for fn in fn_by_name.values()
            if fn.name in REACHABILITY_ENTRYPOINTS and fn.is_erc20_standard_entry
        ]

        for entry in entrypoints:
            stack = [entry.name]
            seen: set[str] = set()
            while stack:
                current_name = stack.pop()
                if current_name in seen:
                    continue
                seen.add(current_name)
                current = fn_by_name.get(current_name)
                if current is None:
                    continue
                reachable_to_sources[current.full_name].add(entry.name)
                for callee in current.calls:
                    if callee in fn_by_name and callee not in seen:
                        stack.append(callee)

    return reachable_to_sources


def analyze_file(path: Path) -> list[AssemblyBlockInfo]:
    source = path.read_text(encoding="utf-8")
    masked = mask_comments_and_strings(source)
    line_starts = build_line_starts(source)

    all_functions: list[FunctionInfo] = []
    for contract_name, _contract_start, _contract_brace, body_start, body_end in find_contracts(masked):
        all_functions.extend(find_functions(source, masked, contract_name, body_start, body_end, line_starts))

    reachable = compute_reachability(all_functions)

    blocks: list[AssemblyBlockInfo] = []
    for function in all_functions:
        for pos, snippet in find_assembly_blocks(source, masked, function, line_starts):
            reachable_from = sorted(reachable.get(function.full_name, set()))
            blocks.append(
                AssemblyBlockInfo(
                    source_file=str(path),
                    contract=function.contract,
                    function=function.name,
                    function_parameters=function.parameters,
                    function_visibility=function.visibility,
                    function_is_erc20_standard_entry=function.is_erc20_standard_entry,
                    function_erc20_signature=function.erc20_signature,
                    assembly_position=pos,
                    assembly_snippet=snippet,
                    reachable_from_erc20_entrypoints=bool(reachable_from),
                    reachable_from=reachable_from,
                )
            )

    return blocks


def collect_solidity_files(inputs: Iterable[str]) -> list[Path]:
    files: list[Path] = []
    for item in inputs:
        path = Path(item)
        if path.is_dir():
            files.extend(sorted(path.rglob("*.sol")))
        elif path.is_file() and path.suffix == ".sol":
            files.append(path)
        else:
            raise SystemExit(f"Input is not a Solidity file or directory: {item}")
    return files


def to_jsonable(blocks: list[AssemblyBlockInfo]) -> list[dict]:
    return [asdict(block) for block in blocks]


def format_markdown(blocks: list[AssemblyBlockInfo]) -> str:
    lines = [
        "# Assembly Context Report",
        "",
        f"Assembly blocks found: {len(blocks)}",
        "",
    ]

    for idx, block in enumerate(blocks, 1):
        pos = block.assembly_position
        params = ", ".join(
            f"{param['type']} {param['name']}".strip()
            for param in block.function_parameters
        )
        reachable = ", ".join(block.reachable_from) if block.reachable_from else "no"
        erc20 = block.function_erc20_signature if block.function_is_erc20_standard_entry else "no"

        lines.extend(
            [
                f"## Assembly Block {idx}",
                "",
                f"- Source file: `{block.source_file}`",
                f"- Contract: `{block.contract}`",
                f"- Function: `{block.function}({params})`",
                f"- Visibility: `{block.function_visibility or 'unknown'}`",
                f"- ERC20 standard entry function: `{erc20}`",
                f"- Source range: line {pos.start_line}, column {pos.start_column} to line {pos.end_line}, column {pos.end_column}",
                f"- Offset range: `{pos.start_offset}:{pos.end_offset}`",
                f"- Reachable from transfer/transferFrom/approve: `{reachable}`",
                "",
                "```solidity",
                block.assembly_snippet.strip(),
                "```",
                "",
            ]
        )

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Locate Solidity inline assembly blocks and report contract/function context."
    )
    parser.add_argument("inputs", nargs="+", help="Solidity files or directories to analyze.")
    parser.add_argument("-o", "--output", default="assembly_context_report.json", help="JSON report path.")
    parser.add_argument("--markdown", help="Optional Markdown report path.")
    parser.add_argument("--no-snippet", action="store_true", help="Omit assembly source snippets from JSON output.")
    args = parser.parse_args()

    blocks: list[AssemblyBlockInfo] = []
    for path in collect_solidity_files(args.inputs):
        blocks.extend(analyze_file(path))

    report = to_jsonable(blocks)
    if args.no_snippet:
        for block in report:
            block.pop("assembly_snippet", None)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.markdown:
        markdown_path = Path(args.markdown)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(format_markdown(blocks), encoding="utf-8")

    print(f"Wrote JSON report: {output_path}")
    if args.markdown:
        print(f"Wrote Markdown report: {args.markdown}")
    print(f"Assembly blocks found: {len(blocks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
