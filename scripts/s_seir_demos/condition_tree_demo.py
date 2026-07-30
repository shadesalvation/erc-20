#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable


Term = frozenset[str]
DNF = list[Term]


@dataclass(frozen=True)
class GuardedStatement:
    label: str
    condition: str
    line: str


@dataclass(frozen=True)
class Occurrence:
    label: str
    term: Term
    line: str


def strip_outer_parens(text: str) -> str:
    value = text.strip()
    while value.startswith("(") and value.endswith(")") and encloses_all(value):
        value = value[1:-1].strip()
    return value


def encloses_all(text: str) -> bool:
    depth = 0
    for index, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0 and index != len(text) - 1:
                return False
            if depth < 0:
                return False
    return depth == 0


def split_top_level(text: str, op: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth -= 1
            i += 1
            continue
        if depth == 0 and text.startswith(op, i):
            parts.append(text[start:i].strip())
            i += len(op)
            start = i
            continue
        i += 1
    parts.append(text[start:].strip())
    return [part for part in parts if part]


def normalize_atom(text: str) -> str:
    value = strip_outer_parens(text)
    if value.startswith("!(") and value.endswith(")") and encloses_all(value[1:]):
        return f"!({normalize_atom(value[2:-1])})"
    return value


def negated(atom: str) -> str:
    value = atom.strip()
    if value.startswith("!(") and value.endswith(")") and encloses_all(value[1:]):
        return normalize_atom(value[2:-1])
    return f"!({value})"


def parse_condition(condition: str) -> DNF:
    text = strip_outer_parens(condition)
    if not text or text == "entry":
        return [frozenset()]
    terms: DNF = []
    for disjunct in split_top_level(text, "||"):
        term_atoms: list[str] = []
        for conjunct in split_top_level(strip_outer_parens(disjunct), "&&"):
            atom = normalize_atom(conjunct)
            if atom:
                term_atoms.append(atom)
        terms.append(frozenset(term_atoms))
    return simplify_dnf(terms)


def simplify_dnf(terms: DNF) -> DNF:
    cleaned: set[Term] = set()
    for term in terms:
        atoms = set(term)
        if any(negated(atom) in atoms for atom in atoms):
            continue
        cleaned.add(frozenset(atoms))

    absorbed: set[Term] = set(cleaned)
    for left in cleaned:
        for right in cleaned:
            if left == right:
                continue
            if left.issubset(right):
                absorbed.discard(right)

    return sorted(absorbed, key=lambda item: (len(item), sorted(item)))


def term_to_condition(term: Term) -> str:
    atoms = sorted(term, key=atom_sort_key)
    if not atoms:
        return "true"
    return " && ".join(atoms)


def atom_sort_key(atom: str) -> tuple[int, str]:
    return (atom.count("__sseir_eval"), atom)


def occurrences(statements: Iterable[GuardedStatement]) -> list[Occurrence]:
    out: list[Occurrence] = []
    for stmt in statements:
        for index, term in enumerate(parse_condition(stmt.condition)):
            suffix = f"#{index + 1}" if index else ""
            out.append(Occurrence(f"{stmt.label}{suffix}", term, stmt.line))
    return out


def render_flat(statements: list[GuardedStatement]) -> list[str]:
    lines: list[str] = []
    for stmt in statements:
        dnf = parse_condition(stmt.condition)
        if dnf == [frozenset()]:
            lines.append(stmt.line)
            continue
        condition = " || ".join(f"({term_to_condition(term)})" for term in dnf)
        lines.append(f"if ({condition}) {{")
        lines.append(f"    {stmt.line}")
        lines.append("}")
    return lines


def render_tree(statements: list[GuardedStatement]) -> list[str]:
    return render_occurrences(occurrences(statements), 0)


def render_occurrences(items: list[Occurrence], indent: int) -> list[str]:
    lines: list[str] = []
    current = [item for item in items if not item.term]
    remaining = [item for item in items if item.term]

    for item in sorted(current, key=lambda item: item.label):
        lines.append(f"{'    ' * indent}{item.line}  // {item.label}")

    while remaining:
        atom = choose_group_atom(remaining)
        if atom is None:
            for item in sorted(remaining, key=lambda item: (len(item.term), item.label)):
                lines.extend(render_leaf(item, indent))
            break

        group = [item for item in remaining if atom in item.term]
        remaining = [item for item in remaining if atom not in item.term]
        child_items = [
            Occurrence(item.label, frozenset(set(item.term) - {atom}), item.line)
            for item in group
        ]
        lines.append(f"{'    ' * indent}if ({atom}) {{")
        lines.extend(render_occurrences(child_items, indent + 1))
        lines.append(f"{'    ' * indent}}}")

    return lines


def choose_group_atom(items: list[Occurrence]) -> str | None:
    counts: dict[str, int] = {}
    for item in items:
        for atom in item.term:
            counts[atom] = counts.get(atom, 0) + 1
    if not counts:
        return None

    best_atom, best_count = max(
        counts.items(),
        key=lambda pair: (pair[1], -atom_sort_key(pair[0])[0], pair[0]),
    )
    if best_count <= 1:
        return None
    return best_atom


def render_leaf(item: Occurrence, indent: int) -> list[str]:
    condition = term_to_condition(item.term)
    return [
        f"{'    ' * indent}if ({condition}) {{",
        f"{'    ' * (indent + 1)}{item.line}  // {item.label}",
        f"{'    ' * indent}}}",
    ]


def extract_current_sample_conditions() -> list[GuardedStatement]:
    path = Path(
        "outputs/assembly样本_sseir_by_contract/"
        "0x2bac62804952d980c0d207641101aa3b4d53652f__Token/solidity_like.txt"
    )
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    candidates: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("if (") and stripped.endswith(") {"):
            condition = stripped[4:-3].strip()
            if "__sseir_eval" in condition and "||" in condition:
                candidates.append(condition)
    candidates.sort(key=len, reverse=True)
    if not candidates:
        return []
    condition = candidates[0]
    return [
        GuardedStatement(
            "current.longest-condition",
            condition,
            "memory[SARKO$] = _amount;",
        )
    ]


def demo_cases() -> list[tuple[str, list[GuardedStatement]]]:
    parent_child = [
        GuardedStatement("s1", "a", "x = 1;"),
        GuardedStatement("s2", "a && b", "y = 2;"),
        GuardedStatement("s3", "a && b && c", "z = 3;"),
        GuardedStatement("s4", "a && d", "w = 4;"),
    ]
    or_with_shared_prefix = [
        GuardedStatement("s1", "(a && b) || (a && c)", "emit E();"),
        GuardedStatement("s2", "a && b && x", "storeA();"),
        GuardedStatement("s3", "!(a) && q", "storeB();"),
    ]
    contradictions = [
        GuardedStatement(
            "s1",
            "(a && b) || (a && b && c) || (a && !(a) && z)",
            "mappingWrite();",
        ),
        GuardedStatement("s2", "a && b && d", "emit Transfer();"),
    ]
    nested_branch_join = [
        GuardedStatement("load", "(guard && left) || (guard && right)", "value = m[key];"),
        GuardedStatement("write-left", "guard && left && after", "m[key] = value - amount;"),
        GuardedStatement("write-right", "guard && right && after", "m[alt] = value - amount;"),
        GuardedStatement("emit", "(guard && left && after) || (guard && right && after)", "emit Transfer();"),
    ]
    mutually_exclusive_paths = [
        GuardedStatement("init", "entry", "ptr = mload(0x40);"),
        GuardedStatement("a-path", "condA && !(condB)", "slotA = keccak256(ptr, 64);"),
        GuardedStatement("b-path", "!(condA) && condB", "slotB = keccak256(ptr, 64);"),
        GuardedStatement("both-path", "condA && condB", "slotBoth = keccak256(ptr, 64);"),
        GuardedStatement(
            "sink",
            "(condA && !(condB) && ok) || (!(condA) && condB && ok) || (condA && condB && ok)",
            "sstore(resolvedSlot, value);",
        ),
    ]
    repeated_realistic_prefix = [
        GuardedStatement(
            "fee-amount",
            "notFromZero && notToZero && staticOk && balanceOk && takeFee && routerOk",
            "feeAmount = amount / 20;",
        ),
        GuardedStatement(
            "fee-write",
            "notFromZero && notToZero && staticOk && balanceOk && takeFee && routerOk && feeSlotKnown",
            "balances[feeTo] += feeAmount;",
        ),
        GuardedStatement(
            "to-write-no-fee",
            "notFromZero && notToZero && staticOk && balanceOk && !(takeFee && routerOk)",
            "balances[to] += amount;",
        ),
        GuardedStatement(
            "to-write-fee",
            "notFromZero && notToZero && staticOk && balanceOk && takeFee && routerOk",
            "balances[to] += finalAmount;",
        ),
    ]
    large_dnf_with_noise = [
        GuardedStatement(
            "state-write",
            """
            (p0 && p1 && p2 && p3)
            || (p0 && p1 && p2 && !(p3) && p4)
            || (p0 && p1 && !(p2) && p5)
            || (p0 && !(p1) && p6)
            || (p0 && p1 && p2 && p3 && redundant)
            || (p0 && p1 && p2 && !(p2) && impossible)
            """,
            "state[x] = y;",
        ),
        GuardedStatement("event", "p0 && p1 && p2 && p3 && eventReady", "emit Updated(x, y);"),
    ]
    cases = [
        ("parent-child condition ownership", parent_child),
        ("or branches and shared prefixes", or_with_shared_prefix),
        ("contradiction and absorption cleanup", contradictions),
        ("nested branch join with shared guard", nested_branch_join),
        ("mutually exclusive branch families", mutually_exclusive_paths),
        ("realistic fee transfer shared prefix", repeated_realistic_prefix),
        ("large DNF with redundant and impossible paths", large_dnf_with_noise),
    ]
    current = extract_current_sample_conditions()
    if current:
        cases.append(("current Token longest condition", current))
    return cases


def main() -> None:
    for title, statements in demo_cases():
        print("=" * 88)
        print(title)
        print("- flat")
        print("\n".join(render_flat(statements)))
        print("- tree")
        print("\n".join(render_tree(statements)))


if __name__ == "__main__":
    main()
