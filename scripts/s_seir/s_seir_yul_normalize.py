#!/usr/bin/env python3
from __future__ import annotations

import re
from typing import Any


def split_args(text: str) -> list[str]:
    out: list[str] = []
    cur: list[str] = []
    depth = 0
    for ch in text:
        if ch == ',' and depth == 0:
            out.append(''.join(cur).strip())
            cur = []
            continue
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        cur.append(ch)
    if cur:
        out.append(''.join(cur).strip())
    return out


def call_parts(expr: str) -> tuple[str | None, list[str]]:
    text = str(expr or '').strip()
    m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\((.*)\)$', text)
    if not m:
        return None, []
    return m.group(1), split_args(m.group(2))


def int_text(value: str) -> int | None:
    try:
        return int(str(value).strip(), 0)
    except Exception:
        return None


def normalize_expr(expr: Any) -> str:
    text = str(expr or '').strip()
    if not text:
        return text
    name, args = call_parts(text)
    if not name:
        return text
    vals = [normalize_expr(a) for a in args]
    if name == 'caller' and not vals:
        return 'msg.sender'
    if name == 'origin' and not vals:
        return 'tx.origin'
    if name == 'callvalue' and not vals:
        return 'msg.value'
    if name == 'calldatasize' and not vals:
        return 'msg.data.length'
    if name == 'gas' and not vals:
        return 'gasleft()'
    if name == 'timestamp' and not vals:
        return 'block.timestamp'
    if name == 'number' and not vals:
        return 'block.number'
    if name == 'basefee' and not vals:
        return 'block.basefee'
    if name == 'coinbase' and not vals:
        return 'block.coinbase'
    if name == 'chainid' and not vals:
        return 'block.chainid'
    if name == 'selfbalance' and not vals:
        return 'address(this).balance'
    binary = {
        'add': '+', 'sub': '-', 'mul': '*', 'div': '/', 'mod': '%',
        'lt': '<', 'gt': '>', 'eq': '==', 'and': '&', 'or': '|', 'xor': '^',
        'shl': '<<', 'shr': '>>',
    }
    if name in binary and len(vals) == 2:
        if name in {'shl', 'shr'}:
            return f'({vals[1]} {binary[name]} {vals[0]})'
        return f'({vals[0]} {binary[name]} {vals[1]})'
    if name == 'iszero' and len(vals) == 1:
        inner = vals[0]
        return f'({inner} == 0)'
    if name == 'not' and len(vals) == 1:
        return f'(~{vals[0]})'
    return f"{name}({', '.join(vals)})"


def invert_condition(expr: Any, type_env: Any | None = None) -> str:
    text = str(expr or '').strip()
    name, args = call_parts(text)
    if name == 'iszero' and len(args) == 1:
        inner = args[0].strip()
        v = type_env.lookup(inner) if type_env is not None and hasattr(type_env, 'lookup') else None
        if v and 'address' in str(v.type_string):
            return f'{inner} != address(0)'
        inner_name, inner_args = call_parts(inner)
        if inner_name in {'staticcall', 'call', 'delegatecall', 'callcode'}:
            return f'({normalize_expr(inner)} != 0)'
        return normalize_expr(inner)
    if name == 'lt' and len(args) == 2:
        return f'({normalize_expr(args[0])} >= {normalize_expr(args[1])})'
    if name == 'gt' and len(args) == 2:
        return f'({normalize_expr(args[0])} <= {normalize_expr(args[1])})'
    if name == 'eq' and len(args) == 2:
        return f'({normalize_expr(args[0])} != {normalize_expr(args[1])})'
    return f'!({normalize_expr(text)})'


def words_from_memory_query(query: dict[str, Any] | None, prefer_known_branch: bool = True) -> list[dict[str, Any]]:
    words = []
    for word in (query or {}).get('words', []) or []:
        item = dict(word)
        value = item.get('value')
        if prefer_known_branch and (value in {None, 'unknown'}):
            known = [c for c in item.get('branch_candidates', []) or [] if c.get('value') not in {None, 'unknown'}]
            if known:
                vals = []
                for cand in known:
                    if cand.get('value') not in vals:
                        vals.append(cand.get('value'))
                if len(vals) == 1:
                    item['value'] = vals[0]
                    item['discarded_unknown_branch'] = True
                    item['known_branch_candidates'] = known
                else:
                    item['value'] = vals
                    item['discarded_unknown_branch'] = True
                    item['known_branch_candidates'] = known
        words.append(item)
    return words


def abi_packed_word(value: str, size: str | None = None) -> str:
    v = normalize_expr(value)
    n = int_text(size or '')
    if n == 20:
        name, args = call_parts(str(value))
        if name == 'shl' and len(args) == 2 and int_text(args[0]) == 96:
            return f'abi.encodePacked(bytes20({normalize_expr(args[1])}))'
        return f'abi.encodePacked(bytes20({v}))'
    return f'abi.encodePacked({v})'


def division_guards(expr: Any) -> list[str]:
    text = str(expr or '').strip()
    guards: list[str] = []

    def walk(item: str) -> None:
        name, args = call_parts(item)
        if not name:
            return
        if name in {'div', 'mod', 'sdiv', 'smod'} and len(args) >= 2:
            denominator = normalize_expr(args[1])
            den_int = int_text(str(args[1]))
            if den_int is None or den_int != 0:
                guard = f'require({denominator} != 0);'
                if guard not in guards:
                    guards.append(guard)
        for arg in args:
            walk(arg)

    walk(text)
    return guards
