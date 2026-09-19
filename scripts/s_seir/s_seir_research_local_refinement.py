#!/usr/bin/env python3
"""P1-T6: bounded, query-driven refinement of supplied P1-T5 carriers.

No endpoint discovery, global path traversal, guard equivalence or dataflow
construction. Source strings only associate existing typed facts with CFG
conditions; they are never parsed as expressions. See P1-T6-001 for term rules.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Protocol

from s_seir_research_actions import build_function_ref
from s_seir_research_abstract_domain import integer_type
from s_seir_research_contracts import (
    ContractValidationError, SOLVER_VALUES, analysis_status, artifact_envelope,
    canonicalize, canonical_json, content_digest, diagnostic, evidence_record,
    producer_record, source_ref, stable_identity, validate_artifact_envelope,
)
from s_seir_research_control_edges import (
    PAYLOAD_FIELDS, validate_candidate_edge, validate_candidate_collection,
)
from s_seir_research_propagation import query_node_context_state, validate_result_evidence

VERSION = "p1-t6-v1"
TERM_FIELDS = {"op", "type", "operands", "literal", "symbol_ref", "arithmetic_mode"}
GUARD_FIELDS = {"source_predicate_refs", "expression", "symbol_bindings", "scope",
                "assumptions", "normalization_rules"}
SOLVER_FIELDS = {"query_digest", "query_artifact", "theory", "bit_widths", "assumptions",
                 "region_ref", "context_ref", "backend", "backend_version", "timeout_ms",
                 "outcome", "opaque_predicate_result", "model_or_proof_ref", "reason"}
BOOL = {"name": "bool", "bit_width": None, "signed": None}
DEFAULT_CONFIG = {"timeout_ms": 1000, "max_expansions": 512, "max_depth": 64,
                  "max_solver_calls": 128}


class Unsupported(ValueError):
    pass


class BudgetExceeded(Unsupported):
    pass


def normalize_config(config=None):
    result = {**DEFAULT_CONFIG, **(config or {})}
    if set(result) != set(DEFAULT_CONFIG):
        raise ContractValidationError("unknown local-refinement configuration")
    for key, value in result.items():
        if isinstance(value, bool) or not str(value).isdigit() or int(value) < 1:
            raise ContractValidationError("positive explicit budget required: " + key)
        result[key] = int(value)
    # Keep Python recursion bounded even for externally supplied configuration.
    if result["max_depth"] > 128:
        raise ContractValidationError("max_depth must be <= 128")
    return result


def typed_type(name):
    if name == "bool":
        return dict(BOOL)
    if name in {"address", "address payable"}:
        return {"name": name, "bit_width": "160", "signed": False}
    value = integer_type(name)
    if value is None:
        raise Unsupported("missing/unsupported explicit type, width or signedness: " + str(name))
    return value


def term(op, typ, operands=(), *, literal=None, symbol_ref=None, arithmetic_mode=None):
    return canonicalize(dict(op=op, type=typ, operands=list(operands), literal=literal,
                             symbol_ref=symbol_ref, arithmetic_mode=arithmetic_mode))


def boolean(value):
    return term("literal", BOOL, literal=value)


def conjunction(values):
    # Preserve evidence/execution order; this is not boolean canonicalization.
    result = boolean(True)
    for value in values:
        result = term("&&", BOOL, [result, value], arithmetic_mode="EAGER")
    return result


def validate_term(value, *, depth=0):
    if depth > 128 or not isinstance(value, dict) or set(value) != TERM_FIELDS:
        raise Unsupported("invalid or over-depth frozen typed term")
    typ = value["type"]
    if value["op"] == "unknown":
        if typ is not None or not isinstance(value["literal"], dict):
            raise Unsupported("unknown term must preserve reason/source refs")
        return
    if not isinstance(typ, dict) or typ != typed_type(typ.get("name")):
        raise Unsupported("inconsistent typed term type")
    args, op = value["operands"], value["op"]
    if not isinstance(args, list):
        raise Unsupported("operands must be ordered terms")
    for arg in args:
        validate_term(arg, depth=depth + 1)
    if op == "literal":
        if args or value["symbol_ref"] is not None:
            raise Unsupported("invalid literal")
        lit = value["literal"]
        if typ == BOOL:
            if not isinstance(lit, bool):
                raise Unsupported("Bool literal must be bool")
        else:
            try:
                number = int(lit)
            except (ValueError, TypeError):
                raise Unsupported("invalid bit-vector literal") from None
            width = int(typ["bit_width"])
            low, high = (-(1 << (width-1)), (1 << (width-1))-1) if typ["signed"] else (0, (1 << width)-1)
            if str(number) != lit or not low <= number <= high:
                raise Unsupported("out-of-range/noncanonical bit-vector literal")
        return
    if op == "symbol":
        if args or not isinstance(value["symbol_ref"], str) or not value["symbol_ref"].startswith("symbol:"):
            raise Unsupported("symbol requires stable binding ref")
        return
    unary = {"!", "~", "cast"}
    binary = {"==", "!=", "<", "<=", ">", ">=", "+", "-", "*", "&", "|", "^", "<<", ">>", "&&", "||"}
    if op not in unary | binary or len(args) != (1 if op in unary else 2):
        raise Unsupported("unsupported term operator/arity: " + str(op))
    types = [a["type"] for a in args]
    if None in types:
        raise Unsupported("unknown operand semantics")
    if op == "cast":
        if BOOL in types or typ == BOOL:
            raise Unsupported("Bool conversion is not supported")
    elif op in {"!", "&&", "||"}:
        if any(t != BOOL for t in [typ, *types]):
            raise Unsupported("logical operation requires Bool")
        if op != "!" and value["arithmetic_mode"] not in {"EAGER", "SHORT_CIRCUIT"}:
            raise Unsupported("logical evaluation mode missing")
    elif op in {"==", "!="}:
        if typ != BOOL or types[0] != types[1]:
            raise Unsupported("equality type mismatch; explicit cast required")
    elif op in {"<", "<=", ">", ">="}:
        if typ != BOOL or types[0] != types[1] or types[0] == BOOL:
            raise Unsupported("comparison type mismatch")
    elif typ == BOOL or any(t != typ for t in types):
        raise Unsupported("bit-vector operation requires same explicit types")
    if op in {"+", "-", "*"} and value["arithmetic_mode"] not in {"CHECKED", "WRAPPING"}:
        raise Unsupported("arithmetic checkedness missing")


class SolverBackend(Protocol):
    name: str
    version: str

    def solve(self, assertions, timeout_ms):
        """Return outcome/reason/smt2/side_conditions/bit_widths. No mutation."""


class Z3Backend:
    name = "z3"
    options = {"random_seed": 0, "proof": False, "model_retained": False}

    def __init__(self):
        import z3
        self.z3 = z3
        self.version = z3.get_version_string()

    def solve(self, assertions, timeout_ms):
        z3 = self.z3
        ctx = z3.Context()
        symbols, widths = {}, set()

        def lower(t):
            validate_term(t)
            op, typ = t["op"], t["type"]
            if op == "unknown":
                raise Unsupported("unknown term cannot be lowered")
            width = None if typ == BOOL else int(typ["bit_width"])
            if width:
                widths.add(width)
            if op == "literal":
                return (z3.BoolVal(t["literal"], ctx) if typ == BOOL else z3.BitVecVal(int(t["literal"]), width, ctx)), []
            if op == "symbol":
                ref = t["symbol_ref"]
                if ref in symbols and symbols[ref][0] != typ:
                    raise Unsupported("conflicting types for one symbolic identity")
                if ref not in symbols:
                    symbols[ref] = (typ, z3.Bool(ref, ctx) if typ == BOOL else z3.BitVec(ref, width, ctx))
                return symbols[ref][1], []
            lowered = [lower(a) for a in t["operands"]]
            args = [a for a, _ in lowered]
            conditions = [c for _, cs in lowered for c in cs]
            a = args[0]
            b = args[1] if len(args) == 2 else None
            source_type = t["operands"][0]["type"]
            signed = source_type["signed"]
            if op == "cast":
                old = a.size()
                value = z3.Extract(width-1, 0, a) if width < old else (
                    (z3.SignExt if signed else z3.ZeroExt)(width-old, a) if width > old else a)
            elif op == "!": value = z3.Not(a)
            elif op == "~": value = ~a
            elif op == "==": value = a == b
            elif op == "!=": value = a != b
            elif op == "<": value = a < b if signed else z3.ULT(a, b)
            elif op == "<=": value = a <= b if signed else z3.ULE(a, b)
            elif op == ">": value = a > b if signed else z3.UGT(a, b)
            elif op == ">=": value = a >= b if signed else z3.UGE(a, b)
            elif op == "+": value = a + b
            elif op == "-": value = a - b
            elif op == "*": value = a * b
            elif op == "&": value = a & b
            elif op == "|": value = a | b
            elif op == "^": value = a ^ b
            elif op == "<<": value = a << b
            elif op == ">>": value = a >> b if signed else z3.LShR(a, b)
            elif op in {"&&", "||"}:
                value = z3.And(a, b) if op == "&&" else z3.Or(a, b)
                if t["arithmetic_mode"] == "SHORT_CIRCUIT":
                    gate = a if op == "&&" else z3.Not(a)
                    conditions = lowered[0][1] + [z3.Implies(gate, c) for c in lowered[1][1]]
            else:
                raise Unsupported("unsupported lowering: " + op)
            if op in {"+", "-", "*"} and t["arithmetic_mode"] == "CHECKED":
                extend = z3.SignExt if signed else z3.ZeroExt
                # 2w bits suffice for exact signed/unsigned product/add/sub.
                aa, bb = extend(width, a), extend(width, b)
                wide = aa + bb if op == "+" else aa - bb if op == "-" else aa * bb
                conditions.append(wide == extend(width, value))
            return value, conditions

        solver = z3.Solver(ctx=ctx)
        solver.set(timeout=int(timeout_ms), random_seed=0)
        side_conditions = []
        try:
            for assertion in assertions:
                value, side = lower(assertion)
                if not z3.is_bool(value):
                    raise Unsupported("query assertions must be Bool")
                solver.add(*side, value)
                side_conditions.extend(c.sexpr() for c in side)
        except Unsupported as exc:
            return dict(outcome="UNSUPPORTED", reason=str(exc), smt2=None,
                        side_conditions=side_conditions, bit_widths=sorted(widths))
        smt2 = solver.to_smt2()
        outcome = solver.check()
        if outcome == z3.sat:
            status, reason = "SAT", "model exists under recorded assumptions; model not retained"
        elif outcome == z3.unsat:
            status, reason = "UNSAT", "unsatisfiable; proof generation disabled, no proof object retained"
        else:
            reason = solver.reason_unknown()
            status = "TIMEOUT" if "timeout" in reason.lower() else "UNKNOWN"
        return dict(outcome=status, reason=reason, smt2=smt2,
                    side_conditions=side_conditions, bit_widths=sorted(widths))


class _Closure:
    """Resolve only definitions demanded by carrier predicates. Never build RD."""
    def __init__(self, function, candidate, block_ids, config):
        self.function, self.candidate, self.config = function, candidate, config
        self.blocks = {b["block_id"]: b for b in function["fact_cfg"]["blocks"]}
        self.nodes = {n["semantic_id"]: n for n in function["semantic_nodes"]}
        ssa = function.get("fact_ssa") or {}
        self.defs = {d["version"]: d for d in ssa.get("definitions", []) + ssa.get("phis", [])}
        self.bindings = {b["binding_id"]: b for b in ssa.get("bindings", [])}
        self.block_ids, self.symbols, self.expansions = block_ids, {}, 0
        self.active = set()
        self.source_refs = []

    def ref(self, kind, locator):
        return source_ref(input_fingerprint=self.candidate["input_fingerprint"],
                          function_ref=self.candidate["function_ref"], source_kind=kind, locator=locator)

    def tick(self, depth):
        self.expansions += 1
        if self.expansions > self.config["max_expansions"] or depth > self.config["max_depth"]:
            raise BudgetExceeded("local symbolic expansion/depth budget exhausted")

    def operand(self, raw, node, depth):
        self.tick(depth)
        if not isinstance(raw, dict):
            raise Unsupported("operand lacks typed SFIR evidence")
        typ = typed_type(raw.get("type"))
        if raw.get("is_constant") is True:
            text = str(raw.get("text"))
            if typ == BOOL:
                if text.lower() not in {"true", "false"}:
                    raise Unsupported("invalid typed Bool literal")
                literal = text.lower() == "true"
            else:
                try:
                    literal = str(int(text, 16) if text.startswith("0x") else int(text))
                except ValueError:
                    raise Unsupported("invalid typed literal") from None
            result = term("literal", typ, literal=literal)
            validate_term(result)
            return result
        reads = [r for r in (node.get("fact_ssa") or {}).get("reads", []) if r.get("value") == raw.get("text")]
        if len(reads) != 1 or not reads[0].get("version"):
            raise Unsupported("operand requires one exact FactSSA read/definition, not display-name identity")
        read = reads[0]
        definition = self.defs.get(read["version"])
        if not definition or definition.get("binding_id") != read.get("binding_id"):
            raise Unsupported("unresolved FactSSA definition")
        ref = self.ref("DEFINITION", {"version": read["version"], "binding_id": read["binding_id"]})
        self.source_refs.append(ref)
        if definition.get("definition_kind") == "entry":
            binding = self.bindings.get(read["binding_id"], {})
            if binding.get("kind") != "parameter" or binding.get("declaration_id") is None:
                raise Unsupported("entry leaf lacks stable input declaration; state location is not a value")
            if typed_type(binding.get("type")) != typ:
                raise Unsupported("conflicting operand/declaration type")
            identity = self.ref("DEFINITION", {"binding_id": read["binding_id"], "definition_kind": "entry"})
            sid = stable_identity("symbol", identity)
            self.symbols[sid] = {"symbol_ref": sid, "type": typ, "source_refs": [identity, ref], "role": "INPUT"}
            return term("symbol", typ, symbol_ref=sid)
        if definition.get("definition_kind") == "phi":
            raise Unsupported("Phi lacks per-edge pairing; no arbitrary incoming version selection")
        owner = self.nodes.get(definition.get("owner_semantic_id"))
        if owner is None or (owner.get("placement") or {}).get("anchor_block") not in self.block_ids:
            raise Unsupported("definition outside local carrier closure")
        owner_block = owner["placement"]["anchor_block"]
        writes = (owner.get("fact_ssa") or {}).get("writes", [])
        if definition.get("block_id") != owner_block or not any(
            w.get("version") == read["version"] and w.get("binding_id") == read["binding_id"] for w in writes
        ):
            raise Unsupported("definition lacks matching owner write/block evidence")
        use_block = node["placement"]["anchor_block"]
        if self.block_ids.count(owner_block) != 1 or self.block_ids.count(use_block) != 1:
            raise Unsupported("repeated carrier block needs dynamic SSA identity")
        if self.block_ids.index(owner_block) > self.block_ids.index(use_block):
            raise Unsupported("definition does not precede use on carrier")
        if owner_block == use_block:
            order = self.blocks[owner_block].get("semantic_ids") or []
            if order.count(owner["semantic_id"]) != 1 or order.count(node["semantic_id"]) != 1 or order.index(owner["semantic_id"]) >= order.index(node["semantic_id"]):
                raise Unsupported("definition/use lacks SFIR in-block order")
        result = self.expression(owner, depth + 1)
        if result["type"] != typ:
            raise Unsupported("definition/use type mismatch")
        return result

    def expression(self, node, depth=0):
        self.tick(depth)
        sid = node["semantic_id"]
        if sid in self.active:
            raise Unsupported("cyclic local definition closure")
        self.active.add(sid)
        self.source_refs.append(self.ref("SEMANTIC_NODE", {"semantic_id": sid}))
        try:
            raw = (node.get("evidence") or {}).get("slither") or {}
            kind = raw.get("kind")
            if kind == "Condition":
                return self.operand(raw.get("value"), node, depth + 1)
            if kind == "Assignment":
                return self.operand(raw.get("rvalue"), node, depth + 1)
            if kind == "TypeConversion":
                return term("cast", typed_type((raw.get("lvalue") or {}).get("type")),
                            [self.operand(raw.get("variable"), node, depth + 1)])
            if kind in {"Binary", "Unary"}:
                op = raw.get("operator") or (node.get("semantic") or {}).get("operator")
                fields = ["variable_left", "variable_right"] if kind == "Binary" else ["rvalue" if raw.get("rvalue") else "variable"]
                operands = [self.operand(raw.get(field), node, depth + 1) for field in fields]
                typ = typed_type((raw.get("lvalue") or {}).get("type"))
                checked = raw.get("checked", (node.get("semantic") or {}).get("checked"))
                mode = ("CHECKED" if checked is True else "WRAPPING" if checked is False else None) if op in {"+", "-", "*"} else None
                if op in {"&&", "||"}:
                    # Atomic operands have already been evaluated by SFIR.
                    mode = "EAGER"
                result = term(op, typ, operands, arithmetic_mode=mode)
                validate_term(result)
                return result
            raise Unsupported("no supported typed atomic expression: " + str(kind or node.get("kind")))
        finally:
            self.active.remove(sid)


def _unique(items):
    return [canonicalize(v) for _, v in sorted({canonical_json(v): v for v in items}.items())]


def build_candidate_scope(sfir, upstream, candidate_id, propagation_result, *, config=None):
    """Build a single supplied carrier's pre-canonical Guard/query scope.

    COMPLETE requires an ENTRY-rooted closed carrier; source ACTION prefixes
    without reachability evidence remain local-only. No backward entry search.
    """
    config = normalize_config(config)
    validate_candidate_collection(upstream)
    validate_result_evidence(propagation_result)
    candidate = next((e for e in upstream["edges"] if e["id"] == candidate_id), None)
    if candidate is None:
        raise ContractValidationError("unknown input candidate_id")
    record = next(r for r in upstream["evidence_records"] if r["id"] == candidate["evidence_refs"][0])
    functions = [f for f in sfir["functions"] if build_function_ref(f, upstream["input_fingerprint"]) == candidate["function_ref"]]
    if len(functions) != 1:
        raise ContractValidationError("candidate function does not resolve uniquely in supplied SFIR")
    function, payload = functions[0], candidate["payload"]
    carrier = record["result"]["carrier"]
    if carrier != record["result"]["identity_basis"]["path_alternative"]:
        raise ContractValidationError("carrier differs from candidate identity evidence")
    if any(ref["function_ref"] != candidate["function_ref"] or ref["input_fingerprint"] != candidate["input_fingerprint"] for ref in record["source_refs"]):
        raise ContractValidationError("carrier/source evidence belongs to another function/input")
    block_ids = [r["locator"]["block_id"] for r in carrier["carrier_block_refs"]]
    closure = _Closure(function, candidate, block_ids, config)
    reasons, predicates, context_entries = [], [], []

    def incomplete(code, reason, refs):
        reasons.append(dict(code=code, reason=reason, refs=refs))

    if not block_ids or any(b not in closure.blocks for b in block_ids):
        incomplete("INSUFFICIENT_EVIDENCE", "carrier block reference unresolved", carrier["carrier_block_refs"])
    if payload["from"]["kind"] != "ENTRY" or not block_ids or block_ids[0] not in function["fact_cfg"].get("entry_blocks", []):
        incomplete("INSUFFICIENT_EVIDENCE", "source/prefix reachability not established by local carrier", [candidate_id])
    if len(set(block_ids)) != len(block_ids):
        incomplete("INSUFFICIENT_EVIDENCE", "repeated carrier block; dynamic iteration identity unavailable", carrier["carrier_block_refs"])
    account = next(a for a in upstream["accounting"] if candidate_id in a["emitted_candidate_ids"])
    if candidate["status"]["completion"] != "COMPLETE" or account["partial"] or account["truncated"]:
        incomplete("TRUNCATED" if account["truncated"] else "INSUFFICIENT_EVIDENCE",
                   "upstream candidate/function coverage is partial", [record["id"], *account["unresolved_scopes"]])
    if function.get("diagnostics"):
        incomplete("INSUFFICIENT_EVIDENCE", "SFIR diagnostics retained", function["diagnostics"])
    context = payload["context_ref"]
    if context:
        propagation = next((p for p in propagation_result["propagations"] if p["id"] == context["propagation_ref"]), None)
        if propagation is None or propagation["payload"]["region_ref"] != payload["region_ref"]:
            incomplete("INSUFFICIENT_EVIDENCE", "missing/mismatched propagation", [context])
        else:
            if propagation["input_fingerprint"] != candidate["input_fingerprint"] or propagation["function_ref"] != candidate["function_ref"]:
                raise ContractValidationError("propagation fingerprint/function differs from candidate")
            for ref in context["node_context_refs"]:
                entry = query_node_context_state(propagation, ref["node_ref"], ref["context"])
                context_entries.append({"ref": ref, "entry": entry})
                if entry is None:
                    incomplete("INSUFFICIENT_EVIDENCE", "exact cache entry absent (not UNKNOWN)", [ref])
                elif any(v["tag"] == "UNKNOWN" for v in entry["state"]["values"].values()):
                    incomplete("UNKNOWN", "reached UNKNOWN cache; FIXED_POINT is not feasibility", [ref])
            if propagation["payload"]["termination"] != "FIXED_POINT" or propagation["status"]["completion"] != "COMPLETE":
                incomplete("TRUNCATED" if propagation["payload"]["termination"] == "RESOURCE_LIMIT" else "INSUFFICIENT_EVIDENCE",
                           "partial propagation/cache/frontier retained", [propagation["id"]])
            # P1-T4 has no per-contribution transition lineage; do not conjoin
            # all incoming values or assign a mutable binding a global symbol.
            incomplete("INSUFFICIENT_EVIDENCE", "context observations lack exact path contribution/value identity; cache used only as evidence", [context])
    raw_edges = function["fact_cfg"]["edges"]
    resolved_edges = []
    for ref in carrier["carrier_edge_refs"]:
        matches = [e for e in raw_edges if all(e.get(k) == v for k, v in ref["locator"].items())]
        if len(matches) != 1:
            incomplete("INSUFFICIENT_EVIDENCE", "carrier edge does not resolve uniquely", [ref])
            continue
        resolved_edges.append((matches[0], ref))
    if len(resolved_edges) != max(0, len(block_ids)-1) or any(
        e["from"] != block_ids[i] or e["to"] != block_ids[i+1]
        for i, (e, _) in enumerate(resolved_edges) if i+1 < len(block_ids)
    ):
        incomplete("INSUFFICIENT_EVIDENCE", "ordered carrier edge/block witness is not closed", carrier["carrier_edge_refs"])
    path_closed = (len(resolved_edges) == len(carrier["carrier_edge_refs"])
                   and len(resolved_edges) == max(0, len(block_ids)-1)
                   and all(e["from"] == block_ids[i] and e["to"] == block_ids[i+1]
                           for i, (e, _) in enumerate(resolved_edges) if i+1 < len(block_ids)))
    for raw, ref in resolved_edges:
        if not raw.get("guard"):
            terminator = closure.blocks.get(raw["from"], {}).get("terminator") or {}
            if raw.get("kind") not in {"next", "unconditional", "fallthrough"} or terminator.get("condition"):
                incomplete("INSUFFICIENT_EVIDENCE", "control edge lacks guard evidence", [ref])
                path_closed = False
            continue
        try:
            block = closure.blocks[raw["from"]]
            condition = (block.get("terminator") or {}).get("condition")
            kind = raw.get("kind")
            if kind not in {"true", "false", "body", "exit", "zero"} or not condition:
                raise Unsupported("unsupported branch/switch association; raw guard string is not a typed AST")
            taken = kind in {"true", "body"}
            if raw["guard"] != (condition if taken else f"!({condition})"):
                raise Unsupported("edge guard and terminator condition association mismatch")
            nodes = [n for n in closure.nodes.values() if (n.get("placement") or {}).get("anchor_block") == raw["from"] and n.get("kind") == "BranchCondition"]
            if len(nodes) != 1:
                raise Unsupported("no unique existing BranchCondition in carrier block")
            node = nodes[0]
            value = ((node.get("evidence") or {}).get("slither") or {}).get("value")
            if not isinstance(value, dict) or value.get("text") != condition:
                raise Unsupported("BranchCondition lacks exact typed condition operand association")
            expression = closure.expression(node)
            if expression["type"] != BOOL:
                raise Unsupported("condition is not typed Bool")
            predicates.append({"predicate_ref": closure.ref("SEMANTIC_NODE", {"semantic_id": node["semantic_id"]}),
                               "edge_ref": ref, "expression": expression, "taken": taken})
        except Unsupported as exc:
            incomplete("TRUNCATED" if isinstance(exc, BudgetExceeded) else "UNSUPPORTED", str(exc), [ref])
    # Any checked/unsupported operation executed on the carrier can affect
    # reachability even when its result is not a branch operand. Only trivial
    # assignments and predicates already in the demanded closure are closed.
    used_nodes = {r["locator"].get("semantic_id") for r in closure.source_refs if r["source_kind"] == "SEMANTIC_NODE"}
    for bid in block_ids:
        block = closure.blocks.get(bid, {})
        ordered = block.get("semantic_ids") or []
        anchored = [n for n in closure.nodes.values() if (n.get("placement") or {}).get("anchor_block") == bid]
        if len(ordered) != len(set(ordered)) or set(ordered) != {n["semantic_id"] for n in anchored}:
            incomplete("INSUFFICIENT_EVIDENCE", "incomplete SFIR in-block order", [closure.ref("CFG_BLOCK", {"block_id": bid})])
        # Target action itself is not executed by the edge's precondition.
        endpoint_refs = [r for r in record["source_refs"] if r["source_kind"] == "SEMANTIC_NODE" and r["locator"].get("semantic_id") in ordered]
        target_sid = None
        if payload["to"]["kind"] == "ACTION" and bid == (block_ids[-1] if block_ids else None):
            possible = [r["locator"]["semantic_id"] for r in endpoint_refs]
            if len(possible) == 1:
                target_sid = possible[0]
        for sid in ordered:
            if sid == target_sid:
                break
            node = closure.nodes.get(sid)
            if node is None or sid in used_nodes:
                continue
            raw = (node.get("evidence") or {}).get("slither") or {}
            if raw.get("kind") == "Assignment":
                try:
                    closure.expression(node)
                    continue
                except Unsupported as exc:
                    incomplete("TRUNCATED" if isinstance(exc, BudgetExceeded) else "UNSUPPORTED", str(exc), [closure.ref("SEMANTIC_NODE", {"semantic_id": sid})])
            else:
                incomplete("INSUFFICIENT_EVIDENCE", "carrier operation/failure semantics not covered by predicate closure", [closure.ref("SEMANTIC_NODE", {"semantic_id": sid})])
    taken_terms = [p["expression"] if p["taken"] else term("!", BOOL, [p["expression"]]) for p in predicates]
    if len(predicates) > config["max_depth"]:
        incomplete("TRUNCATED", "Guard construction depth budget exhausted", [candidate_id])
    safe = (path_closed
            and len(predicates) == sum(bool(e.get("guard")) for e, _ in resolved_edges)
            and not any(r["code"] == "TRUNCATED" and "budget" in r["reason"] for r in reasons))
    scope = {"candidate_id": candidate_id, "function_ref": candidate["function_ref"],
             "region_ref": payload["region_ref"], "context_ref": context, "carrier_evidence_ref": record["id"]}
    assumptions = [{"kind": "SOURCE_REACHED", "endpoint": payload["from"],
                    "proven": payload["from"]["kind"] == "ENTRY" and not context},
                   {"kind": "CACHE_PHASE", "value": "INCOMING_BEFORE_NODE_TRANSFER"},
                   {"kind": "SEMANTICS", "value": "typed atomic operands; checked side conditions on evaluated expressions"}]
    expression = conjunction(taken_terms) if safe else term("unknown", None, literal={"reason": "path predicate closure incomplete", "source_refs": record["result"]["unrefined_condition_refs"]})
    return canonicalize({"candidate_id": candidate_id, "scope": scope, "carrier": carrier,
                         "scope_completeness": "PARTIAL" if reasons else "COMPLETE", "incomplete_reasons": _unique(reasons),
                         "query_safe": safe, "predicates": predicates, "base_assertions": [],
                         "edge_assertions": taken_terms, "context_entries": context_entries,
                         "source_refs": _unique([*record["source_refs"], *closure.source_refs]),
                         "symbol_bindings": sorted(closure.symbols.values(), key=lambda s: s["symbol_ref"]),
                         "assumptions": assumptions, "expression": expression,
                         "expansions": closure.expansions, "config": config,
                         "construction_outcome": "UNSUPPORTED" if any(r["code"] == "UNSUPPORTED" for r in reasons) and not safe else "NOT_RUN"})


def _artifact(kind, candidate, payload, status, config, evidence_refs=()):
    return artifact_envelope(
        schema=f"erc20-research/{kind}/v1", artifact_id=stable_identity(kind, {
            "candidate_id": candidate["id"], "payload": payload, "config": config}),
        function_ref=candidate["function_ref"], input_fingerprint=candidate["input_fingerprint"],
        producer=producer_record("P1-T6", VERSION, config), evidence_refs=evidence_refs,
        status=status, payload=payload)


def validate_guard(guard):
    validate_artifact_envelope(guard)
    if guard["schema"] != "erc20-research/guard/v1" or set(guard["payload"]) != GUARD_FIELDS:
        raise ContractValidationError("invalid pre-canonical Guard")
    p = guard["payload"]
    validate_term(p["expression"])
    if p["normalization_rules"] != ["p1-t6/typed-atomic-lowering/v1"]:
        raise ContractValidationError("P1-T6 Guard is pre-canonical")
    if not p["scope"].get("candidate_id") or "context_ref" not in p["scope"]:
        raise ContractValidationError("Guard lacks candidate/context scope")
    bindings = {b["symbol_ref"]: b for b in p["symbol_bindings"]}
    if len(bindings) != len(p["symbol_bindings"]):
        raise ContractValidationError("duplicate symbolic binding")

    def walk(t):
        if t["op"] == "symbol":
            b = bindings.get(t["symbol_ref"])
            if not b or b["type"] != t["type"] or not b["source_refs"]:
                raise ContractValidationError("unresolved symbol provenance/type")
        for a in t["operands"]:
            walk(a)
    walk(p["expression"])


def validate_solver_evidence(evidence):
    validate_artifact_envelope(evidence)
    p = evidence["payload"]
    if evidence["schema"] != "erc20-research/solver-evidence/v1" or set(p) != SOLVER_FIELDS:
        raise ContractValidationError("invalid SolverEvidence fields")
    if p["query_digest"] != content_digest(p["query_artifact"]):
        raise ContractValidationError("query digest mismatch")
    if p["outcome"] not in SOLVER_VALUES or evidence["status"]["solver"] != p["outcome"]:
        raise ContractValidationError("invalid solver outcome")
    if not p["reason"] or not p["backend"] or not p["backend_version"] or int(p["timeout_ms"]) < 1:
        raise ContractValidationError("missing reproducible backend config/reason")
    if p["outcome"] in {"SAT", "UNSAT"} and not p["query_artifact"].get("smt2"):
        raise ContractValidationError("executed decisive query must retain complete SMT artifact")
    if p["model_or_proof_ref"] is not None:
        raise ContractValidationError("this adapter does not retain models/proof objects")


def validate_refined_edge(edge, candidate, guard, feasibility_evidence, scope):
    validate_candidate_edge(candidate)
    validate_artifact_envelope(edge)
    validate_guard(guard)
    validate_solver_evidence(feasibility_evidence)
    if edge["schema"] != candidate["schema"] or set(edge["payload"]) != PAYLOAD_FIELDS:
        raise ContractValidationError("refinement changed frozen edge schema")
    if edge["id"] != candidate["id"] or edge["function_ref"] != candidate["function_ref"] or edge["input_fingerprint"] != candidate["input_fingerprint"]:
        raise ContractValidationError("refinement changed candidate identity")
    for field in ("from", "to", "candidate_id", "region_ref", "context_ref"):
        if edge["payload"][field] != candidate["payload"][field]:
            raise ContractValidationError("refinement changed lineage: " + field)
    if edge["payload"]["guard_ref"] != {"artifact_ref": guard["id"]}:
        raise ContractValidationError("refined guard_ref does not resolve")
    if guard["payload"]["scope"]["candidate_id"] != candidate["id"]:
        raise ContractValidationError("Guard belongs to another candidate")
    outcome = feasibility_evidence["payload"]["outcome"]
    expected = {"SAT": "FEASIBLE", "UNSAT": "INFEASIBLE"}.get(outcome, "UNRESOLVED") if scope["scope_completeness"] == "COMPLETE" else "UNRESOLVED"
    if edge["payload"]["feasibility"] != expected or edge["status"]["solver"] != outcome:
        raise ContractValidationError("unsafe solver/completeness/feasibility mapping")
    if candidate["evidence_refs"][0] not in edge["evidence_refs"]:
        raise ContractValidationError("upstream evidence lineage lost")
    for diag in candidate["status"]["diagnostics"]:
        if diag not in edge["status"]["diagnostics"]:
            raise ContractValidationError("upstream diagnostic erased")


def _refine_one(candidate, scope, backend):
    config = scope["config"]
    calls, opaque, diagnostics = [], [], list(candidate["status"]["diagnostics"])
    solver_calls = 0

    def run(assertions, role, predicate_ref=None, forced=None, reason=None):
        nonlocal solver_calls
        if solver_calls >= int(config["max_solver_calls"]) and forced is None:
            forced, reason = "NOT_RUN", "local solver-call budget exhausted"
            scope["scope_completeness"] = "PARTIAL"
            scope["incomplete_reasons"] = _unique([*scope["incomplete_reasons"],
                {"code": "TRUNCATED", "reason": reason, "refs": [candidate["id"]]}])
        if forced:
            answer = dict(outcome=forced, reason=reason, smt2=None, side_conditions=[], bit_widths=[])
        else:
            # Unexpected backend exceptions deliberately propagate; no empty success.
            answer = backend.solve(assertions, int(config["timeout_ms"]))
            solver_calls += 1
        if answer["outcome"] not in SOLVER_VALUES - {"NOT_RUN"} and not forced:
            raise ContractValidationError("backend returned invalid five-state outcome")
        query = canonicalize({"format": "p1-t6/local-query/v1", "role": role,
            "scope": scope["scope"], "predicate_ref": predicate_ref,
            "assertions": assertions, "assumptions": scope["assumptions"],
            "symbol_bindings": scope["symbol_bindings"], "config": config,
            "backend_options": getattr(backend, "options", {}),
            "smt2": answer["smt2"], "side_conditions": answer["side_conditions"]})
        result = dict(query_digest=content_digest(query), query_artifact=query,
            theory="QF_BV+Bool", bit_widths=answer["bit_widths"], assumptions=scope["assumptions"],
            region_ref=candidate["payload"]["region_ref"], context_ref=candidate["payload"]["context_ref"],
            backend=backend.name, backend_version=backend.version, timeout_ms=config["timeout_ms"],
            outcome=answer["outcome"], opaque_predicate_result=None,
            model_or_proof_ref=None, reason=answer["reason"])
        status = analysis_status(proof="CANDIDATE" if answer["outcome"] in {"SAT", "UNSAT"} else "UNKNOWN",
            completion="COMPLETE" if answer["outcome"] in {"SAT", "UNSAT"} else "PARTIAL", solver=answer["outcome"])
        artifact = _artifact("solver-evidence", candidate, result, status, config, candidate["evidence_refs"])
        validate_solver_evidence(artifact)
        calls.append(artifact)
        return artifact

    main = run(scope["base_assertions"] + scope["edge_assertions"], "FEASIBILITY",
               forced=None if scope["query_safe"] else scope["construction_outcome"],
               reason="no safe complete predicate query: " + canonical_json(scope["incomplete_reasons"]))
    prefix = list(scope["base_assertions"])
    for p in scope["predicates"]:
        predicate = p["expression"]
        # Evaluation definedness is part of B_P. Equality to itself is only a
        # typed vehicle for collecting its checked side conditions, not P.
        defined = term("==", BOOL, [predicate, predicate])
        base = [*prefix, defined]
        forced = None if scope["query_safe"] else "NOT_RUN"
        base_call = run(base, "OPAQUE_BASE", p["predicate_ref"], forced, "incomplete predicate closure")
        pos = run([*base, predicate], "OPAQUE_POSITIVE", p["predicate_ref"], forced, "incomplete predicate closure")
        neg = run([*base, term("!", BOOL, [predicate])], "OPAQUE_NEGATIVE", p["predicate_ref"], forced, "incomplete predicate closure")
        outcomes = [c["payload"]["outcome"] for c in (base_call, pos, neg)]
        classification = "UNKNOWN"
        # Reachability gaps are explicit assumptions for these local claims;
        # unmodeled evaluated operations, however, prohibit a constant claim.
        semantic_gaps = any(r["code"] in {"UNSUPPORTED", "TRUNCATED"} or "operation/failure" in r["reason"] for r in scope["incomplete_reasons"])
        if not semantic_gaps and scope["scope_completeness"] == "COMPLETE" and outcomes[0] == "SAT":
            if outcomes[1:] == ["SAT", "UNSAT"]: classification = "ALWAYS_TRUE"
            elif outcomes[1:] == ["UNSAT", "SAT"]: classification = "ALWAYS_FALSE"
            elif outcomes[1:] == ["SAT", "SAT"]: classification = "NON_CONSTANT"
        # References to content-addressed query artifacts avoid a cyclic hash
        # between a classification and its three enclosing SolverEvidence IDs.
        query_refs = ["query:" + c["payload"]["query_digest"] for c in (base_call, pos, neg)]
        replacement_calls = []
        for old in (base_call, pos, neg):
            body = deepcopy(old["payload"])
            body["opaque_predicate_result"] = {"predicate_ref": p["predicate_ref"],
                "classification": classification, "evidence_refs": query_refs}
            new = _artifact("solver-evidence", candidate, body, old["status"], config, old["evidence_refs"])
            calls[calls.index(old)] = new
            replacement_calls.append(new)
        opaque.append({"predicate_ref": p["predicate_ref"], "classification": classification,
                       "evidence_refs": [c["id"] for c in replacement_calls],
                       "prefix_assertions": base, "scope": scope["scope"],
                       "base_infeasible": outcomes[0] == "UNSAT"})
        prefix.append(predicate if p["taken"] else term("!", BOOL, [predicate]))
    outcome = main["payload"]["outcome"]
    complete = scope["scope_completeness"] == "COMPLETE"
    feasibility = {"SAT": "FEASIBLE", "UNSAT": "INFEASIBLE"}.get(outcome, "UNRESOLVED") if complete else "UNRESOLVED"
    for r in scope["incomplete_reasons"]:
        diagnostics.append(diagnostic(r["code"], r["reason"], evidence_refs=candidate["evidence_refs"],
                                      affected_refs=r["refs"], scope=scope["scope"]))
    if outcome in {"UNKNOWN", "TIMEOUT", "UNSUPPORTED", "NOT_RUN"}:
        diagnostics.append(diagnostic("INSUFFICIENT_EVIDENCE" if outcome == "NOT_RUN" else outcome,
                                     main["payload"]["reason"], evidence_refs=[main["id"]], scope=scope["scope"]))
    status = analysis_status(proof="PROVEN" if feasibility != "UNRESOLVED" else "UNKNOWN",
        completion="COMPLETE" if complete and feasibility != "UNRESOLVED" else "PARTIAL",
        solver=outcome, diagnostics=_unique(diagnostics))
    guard = _artifact("guard", candidate, {
        "source_predicate_refs": _unique([p["predicate_ref"] for p in scope["predicates"]] +
            [r for r in scope["source_refs"] if r["source_kind"] == "CFG_EDGE"]),
        "expression": scope["expression"], "symbol_bindings": scope["symbol_bindings"],
        "scope": scope["scope"], "assumptions": scope["assumptions"],
        "normalization_rules": ["p1-t6/typed-atomic-lowering/v1"]}, status, config,
        [*candidate["evidence_refs"], main["id"]])
    record = evidence_record(kind="local_candidate_refinement",
        claim={"predicate": "scoped_candidate_feasibility", "operands": {"candidate_id": candidate["id"], "feasibility": feasibility}},
        premises=[{"upstream_evidence_ref": candidate["evidence_refs"][0]},
                  {"scope_completeness": scope["scope_completeness"], "incomplete_reasons": scope["incomplete_reasons"]}],
        source_refs=scope["source_refs"], artifact_refs=[candidate["id"], guard["id"], *[c["id"] for c in calls]],
        rule="p1-t6/local-refinement/v1", scope=scope["scope"], assumptions=scope["assumptions"],
        result={"feasibility": feasibility, "opaque_predicates": opaque,
                "supersedes": ([{"evidence_ref": candidate["evidence_refs"][0],
                                  "claim": "UNRESOLVED candidate feasibility", "refinement_ref": main["id"]}]
                               if feasibility != "UNRESOLVED" else []),
                "scope_completeness": scope["scope_completeness"]},
        reason_code="SCOPED_SOLVER_REFINEMENT", producer=producer_record("P1-T6", VERSION, config), status=status)
    edge = deepcopy(candidate)
    edge["producer"], edge["status"] = producer_record("P1-T6", VERSION, config), status
    edge["payload"]["guard_ref"] = {"artifact_ref": guard["id"]}
    edge["payload"]["feasibility"] = feasibility
    edge["evidence_refs"] = [*candidate["evidence_refs"], record["id"]]
    validate_refined_edge(edge, candidate, guard, main, scope)
    return canonicalize({"candidate_id": candidate["id"], "edge": edge, "guard": guard,
        "solver_evidence": calls, "feasibility_evidence_ref": main["id"],
        "opaque_predicates": opaque, "scope": scope, "evidence_record": record,
        "solver_calls": solver_calls})


def refinement_accounting(refinements):
    return canonicalize({"input_candidates": len(refinements),
        "attempted": sum(r["edge"]["status"]["solver"] != "NOT_RUN" for r in refinements),
        "outcomes": {s: sum(r["edge"]["status"]["solver"] == s for r in refinements) for s in sorted(SOLVER_VALUES)},
        "feasibility": {s: sum(r["edge"]["payload"]["feasibility"] == s for r in refinements) for s in ("FEASIBLE", "INFEASIBLE", "UNRESOLVED")},
        "scope_completeness": {s: sum(r["scope"]["scope_completeness"] == s for r in refinements) for s in ("COMPLETE", "PARTIAL")},
        "solver_calls": sum(int(r["solver_calls"]) for r in refinements),
        "truncated_affected_scopes": [r["candidate_id"] for r in refinements if any(x["code"] == "TRUNCATED" for x in r["scope"]["incomplete_reasons"])],
        "candidate_ids": sorted(r["candidate_id"] for r in refinements)})


def refine_candidates(sfir, candidate_result, propagation_result, *, config=None, backend=None):
    """Batch refine P1-T5 result without mutation; P1-T7 consumes rows by ID."""
    config = normalize_config(config)
    validate_candidate_collection(candidate_result)
    validate_result_evidence(propagation_result)
    if sfir.get("schema") != "s-seir-semantic-fact-ir/v1":
        raise ContractValidationError("existing SFIR required")
    backend = backend or Z3Backend()
    rows = []
    for candidate in sorted(candidate_result["edges"], key=lambda e: e["id"]):
        scope = build_candidate_scope(sfir, candidate_result, candidate["id"], propagation_result, config=config)
        rows.append(_refine_one(candidate, scope, backend))
    diagnostics = _unique([*candidate_result["status"]["diagnostics"],
        *propagation_result["status"]["diagnostics"], *[d for r in rows for d in r["edge"]["status"]["diagnostics"]]])
    partial = (candidate_result["status"]["completion"] != "COMPLETE"
               or propagation_result["status"]["completion"] != "COMPLETE"
               or any(r["edge"]["status"]["completion"] != "COMPLETE" for r in rows))
    return canonicalize({"producer": producer_record("P1-T6", VERSION, config),
        "input_fingerprint": candidate_result["input_fingerprint"], "config": config,
        "refinements": rows, "accounting": refinement_accounting(rows),
        "upstream_accounting": sorted(deepcopy(candidate_result["accounting"]), key=canonical_json),
        "unresolved_scopes": _unique(candidate_result["unresolved_scopes"]),
        "upstream_evidence_records": sorted(deepcopy(candidate_result["evidence_records"]), key=lambda r: r["id"]),
        "propagation_evidence_records": sorted(deepcopy(propagation_result["evidence_records"]), key=lambda r: r["id"]),
        "upstream_status": candidate_result["status"], "propagation_status": propagation_result["status"],
        "status": analysis_status(proof="CANDIDATE" if rows else "UNKNOWN",
                                  completion="PARTIAL" if partial else "COMPLETE", diagnostics=diagnostics)})


def query_refinements(result, *, candidate_id=None, function_ref=None, outcome=None, feasibility=None):
    return canonicalize([r for r in result["refinements"] if
        (candidate_id is None or r["candidate_id"] == candidate_id) and
        (function_ref is None or r["edge"]["function_ref"] == function_ref) and
        (outcome is None or r["edge"]["status"]["solver"] == outcome) and
        (feasibility is None or r["edge"]["payload"]["feasibility"] == feasibility)])


def serialize_refinement_result(result):
    if result["accounting"] != refinement_accounting(result["refinements"]):
        raise ContractValidationError("refinement accounting mismatch")
    return canonical_json(result)
