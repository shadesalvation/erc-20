#!/usr/bin/env python3
"""P1-T6 semantic/scope/evidence acceptance over real P1-T5 producer output."""
from copy import deepcopy
import ast
import inspect
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "s_seir", ROOT / "legacy_yul"):
    sys.path.insert(0, str(path))

import s_seir_research_local_refinement as r
from s_seir_research_contracts import ContractValidationError, canonical_json, content_digest
from s_seir_research_control_edges import validate_candidate_edge, reconstruct_candidate_control_edges
from s_seir_research_control_edges_tests import (
    prepare, straight_fixture, chain_fixture, LIMITS,
)
from s_seir_research_propagation_tests import typed_fixture
from s_seir_research_propagation import propagate_regions
from s_seir_research_actions import extract_semantic_actions
from s_seir_research_regions_tests import block, edge, node, action_node, payload, flattened_fixture, INPUT_FINGERPRINT
from s_seir_semantic_fact_adapter import SlitherFactAdapter


def operand(name, typ="uint8", literal=False):
    return {"kind": "Constant" if literal else "LocalIRVariable", "text": str(name),
            "type": typ, "is_constant": literal}


def binary(name, op, left, right, typ="bool", checked=False):
    return {"kind": "Binary", "lvalue": operand(name, typ), "operator": op,
            "variable_left": left, "variable_right": right, "checked": checked}


def typed_branch_fixture(operations=None, *, condition="p", parameter_type="uint8", prefix_action=False):
    """Typed SlitherFactAdapter nodes + existing FactSSA schema, not a new AST."""
    operations = operations if operations is not None else [binary("p", "<", operand("x"), operand(8, literal=True))]
    nodes, definitions, reads_by_name = [], [], {"x": ("decl:x:value", "fssa:x:entry")}
    definitions.append({"version": "fssa:x:entry", "binding_id": "decl:x:value", "definition_kind": "entry", "block_id": "entry", "owner_semantic_id": None})
    for index, operation in enumerate([*operations, {"kind": "Condition", "value": operand(condition, "bool")} ]):
        sid = "pred" if operation["kind"] == "Condition" else f"compute:{index}"
        adapted = SlitherFactAdapter().facts_from_operations({"function": "run", "contract": "Token"}, [operation])[0].to_dict()
        fact = node(sid, "entry", kind=adapted["kind"])
        fact.update({k: adapted[k] for k in ("kind", "semantic", "evidence", "reads", "writes", "lvalue", "rvalue", "fact_role") if k in adapted})
        fact["fact_ssa"] = {"reads": [], "writes": []}
        for value in adapted.get("reads", []):
            if value in reads_by_name:
                binding, version = reads_by_name[value]
                fact["fact_ssa"]["reads"].append({"value": value, "binding_id": binding, "version": version})
        for value in adapted.get("writes", []):
            binding, version = "temporary:" + value, "fssa:" + value
            fact["fact_ssa"]["writes"].append({"value": value, "binding_id": binding, "version": version})
            definitions.append({"version": version, "binding_id": binding, "definition_kind": "semantic_definition", "block_id": "entry", "owner_semantic_id": sid})
            reads_by_name[value] = binding, version
        nodes.append(fact)
    if prefix_action:
        nodes.insert(0, action_node("source", "entry"))
    nodes += [action_node("yes", "yes"), action_node("no", "no")]
    blocks = [block("entry", terminator="Branch"), block("yes", terminator="Return"), block("no", terminator="Return")]
    blocks[0]["terminator"]["condition"] = condition
    edges = [edge(1, "entry", "yes", "true"), edge(2, "entry", "no", "false")]
    edges[0]["guard"], edges[1]["guard"] = condition, f"!({condition})"
    sfir = payload(blocks, edges, nodes)
    sfir["functions"][0]["fact_ssa"].update({"definitions": definitions, "bindings": [
        {"binding_id": "decl:x:value", "declaration_id": 100, "kind": "parameter", "type": parameter_type}]})
    return sfir


def execute(sfir, **kwargs):
    _, _, propagation, upstream = prepare(sfir)
    return upstream, propagation, r.refine_candidates(sfir, upstream, propagation, **kwargs)


def entry_rows(result):
    return [row for row in result["refinements"] if row["edge"]["payload"]["from"]["kind"] == "ENTRY"]


def lit(value, typ="uint8"):
    return r.term("literal", r.typed_type(typ), literal=str(value) if typ != "bool" else value)


def op(operator, *args, typ="bool", mode=None):
    return r.term(operator, r.typed_type(typ), args, arithmetic_mode=mode)


class StubBackend:
    name, version = "deterministic-contract-stub", "v1"
    def __init__(self, outcome): self.outcome = outcome
    def solve(self, assertions, timeout_ms):
        # Stub only exercises nondecisive outcome mapping; SAT/UNSAT use Z3.
        return dict(outcome=self.outcome, reason="injected " + self.outcome,
                    smt2=None, side_conditions=[], bit_widths=[])


class TypedSemanticsTests(unittest.TestCase):
    def check(self, expression, expected="SAT"):
        answer = r.Z3Backend().solve([expression], 1000)
        self.assertEqual(expected, answer["outcome"], answer)
        return answer

    def test_real_signed_unsigned_comparisons(self):
        self.check(op("<", lit(255), lit(1)), "UNSAT")
        self.check(op("<", lit(-1, "int8"), lit(1, "int8")))
        self.check(op(">=", lit(255), lit(1)))
        self.check(op("!=", lit(255), lit(1)))

    def test_wrapping_and_checked_overflow(self):
        wrapping = op("+", lit(255), lit(1), typ="uint8", mode="WRAPPING")
        checked = op("+", lit(255), lit(1), typ="uint8", mode="CHECKED")
        self.check(op("==", wrapping, lit(0)))
        result = self.check(op("==", checked, lit(0)), "UNSAT")
        self.assertTrue(result["side_conditions"])

    def test_checked_signed_multiply_and_underflow(self):
        self.check(op("==", op("*", lit(127, "int8"), lit(2, "int8"), typ="int8", mode="CHECKED"), lit(-2, "int8")), "UNSAT")
        self.check(op("==", op("-", lit(0), lit(1), typ="uint8", mode="CHECKED"), lit(255)), "UNSAT")
        self.check(op("==", op("*", lit(-2, "int8"), lit(3, "int8"), typ="int8", mode="CHECKED"), lit(-6, "int8")))

    def test_bitwise_shifts_and_bool(self):
        for operator, a, b, expected in [("&", 7, 3, 3), ("|", 4, 1, 5), ("^", 7, 3, 4), ("<<", 128, 1, 0), (">>", 128, 1, 64)]:
            self.check(op("==", op(operator, lit(a), lit(b), typ="uint8"), lit(expected)))
        self.check(op("==", op("~", lit(0), typ="uint8"), lit(255)))
        self.check(op("&&", lit(True, "bool"), op("!", lit(False, "bool")), mode="EAGER"))

    def test_cast_sign_zero_extension_truncation_address(self):
        self.check(op("==", op("cast", lit(-1, "int8"), typ="int16"), lit(-1, "int16")))
        self.check(op("==", op("cast", lit(255), typ="uint16"), lit(255, "uint16")))
        self.check(op("==", op("cast", lit(256, "uint16"), typ="uint8"), lit(0)))
        self.check(op("==", lit((1 << 160)-1, "address"), lit((1 << 160)-1, "address")))

    def test_missing_type_width_mode_operator_are_unsupported(self):
        for name in (None, "uint", "bytes", "int7"):
            with self.assertRaises(r.Unsupported): r.typed_type(name)
        self.check(op("==", op("+", lit(1), lit(2), typ="uint8"), lit(3)), "UNSUPPORTED")
        self.check(op("/", lit(1), lit(2), typ="uint8"), "UNSUPPORTED")
        self.check(op("==", lit(1), lit(1, "uint16")), "UNSUPPORTED")

    def test_logical_short_circuit_side_condition(self):
        bad = op("==", op("+", lit(255), lit(1), typ="uint8", mode="CHECKED"), lit(0))
        self.check(op("||", lit(True, "bool"), bad, mode="SHORT_CIRCUIT"))
        self.check(op("||", lit(True, "bool"), bad, mode="EAGER"), "UNSAT")

    def test_large_literals_lossless(self):
        value = (1 << 256)-1
        expression = op("==", lit(value, "uint256"), lit(value, "uint256"))
        self.check(expression)
        self.assertIn(str(value), canonical_json(expression))

    def test_evm_word_wrapping_is_256_bit(self):
        maximum = (1 << 256)-1
        for mode, outcome in (("WRAPPING", "SAT"), ("CHECKED", "UNSAT")):
            expression = op("+", lit(maximum, "uint256"), lit(1, "uint256"), typ="uint256", mode=mode)
            self.check(op("==", expression, lit(0, "uint256")), outcome)


class RefinementTests(unittest.TestCase):
    def test_real_sat_complete_and_partial_are_distinct(self):
        _, _, result = execute(typed_branch_fixture())
        self.assertEqual(2, len(entry_rows(result)))
        self.assertTrue(all(row["edge"]["payload"]["feasibility"] == "FEASIBLE" for row in entry_rows(result)))
        _, _, partial = execute(typed_branch_fixture(prefix_action=True))
        self.assertTrue(any(row["edge"]["status"]["solver"] == "SAT" and row["edge"]["payload"]["feasibility"] == "UNRESOLVED" for row in partial["refinements"]))

    def test_unsat_retained_lineage_and_validator_not_relaxed(self):
        sfir = typed_branch_fixture([binary("p", "==", operand("x"), operand("x"))])
        original = deepcopy(sfir)
        upstream, propagation, result = execute(sfir)
        self.assertEqual(original, sfir)
        before_upstream, before_propagation = deepcopy(upstream), deepcopy(propagation)
        again = r.refine_candidates(sfir, upstream, propagation)
        self.assertEqual(before_upstream, upstream)
        self.assertEqual(before_propagation, propagation)
        self.assertEqual(result, again)
        self.assertEqual({e["id"] for e in upstream["edges"]}, {row["candidate_id"] for row in result["refinements"]})
        self.assertEqual(1, len(r.query_refinements(result, feasibility="INFEASIBLE")))
        for row in result["refinements"]:
            candidate = next(e for e in upstream["edges"] if e["id"] == row["candidate_id"])
            r.validate_refined_edge(row["edge"], candidate, row["guard"], row["solver_evidence"][0], row["scope"])
            validate_candidate_edge(candidate)
            with self.assertRaises(ContractValidationError): validate_candidate_edge(row["edge"])

    def test_unknown_timeout_unsupported_stub_mapping(self):
        for outcome in ("UNKNOWN", "TIMEOUT", "UNSUPPORTED"):
            _, _, result = execute(typed_branch_fixture(), backend=StubBackend(outcome))
            self.assertTrue(all(row["edge"]["payload"]["feasibility"] == "UNRESOLVED" for row in result["refinements"]))
            self.assertEqual(str(len(result["refinements"])), result["accounting"]["outcomes"][outcome])
            self.assertTrue(all(p["classification"] == "UNKNOWN" for row in result["refinements"] for p in row["opaque_predicates"]))

    def test_symbolic_budget_not_run_truncated(self):
        _, _, result = execute(typed_branch_fixture(), config={"max_expansions": 1})
        for row in entry_rows(result):
            self.assertEqual("NOT_RUN", row["edge"]["status"]["solver"])
            self.assertEqual("PARTIAL", row["scope"]["scope_completeness"])
            self.assertTrue(any(r["code"] == "TRUNCATED" for r in row["scope"]["incomplete_reasons"]))

    def test_missing_guard_is_not_unconditional(self):
        sfir = typed_branch_fixture()
        sfir["functions"][0]["fact_cfg"]["edges"][0]["guard"] = None
        _, _, result = execute(sfir)
        unresolved = [row for row in entry_rows(result) if row["edge"]["status"]["solver"] == "NOT_RUN"]
        self.assertEqual(1, len(unresolved))
        self.assertEqual("PARTIAL", unresolved[0]["scope"]["scope_completeness"])

    def test_solver_call_budget_marks_partial(self):
        _, _, result = execute(typed_branch_fixture(), config={"max_solver_calls": 1})
        for row in entry_rows(result):
            self.assertEqual("SAT", row["edge"]["status"]["solver"])
            self.assertEqual("UNRESOLVED", row["edge"]["payload"]["feasibility"])
            self.assertEqual("1", row["solver_calls"])
            self.assertEqual("UNKNOWN", row["opaque_predicates"][0]["classification"])

    def test_incomplete_unsat_never_rejects(self):
        sfir = typed_branch_fixture([binary("p", "!=", operand("x"), operand("x"))], prefix_action=True)
        _, _, result = execute(sfir)
        unsat = r.query_refinements(result, outcome="UNSAT")
        self.assertTrue(unsat)
        self.assertTrue(all(x["edge"]["payload"]["feasibility"] == "UNRESOLVED" for x in unsat))

    def test_missing_typed_predicate_is_not_parsed(self):
        sfir = typed_branch_fixture()
        del sfir["functions"][0]["semantic_nodes"][0]["evidence"]["slither"]["variable_left"]["type"]
        _, _, result = execute(sfir)
        self.assertTrue(all(x["edge"]["status"]["solver"] == "UNSUPPORTED" for x in entry_rows(result)))
        self.assertTrue(all(x["guard"]["payload"]["expression"]["op"] == "unknown" for x in entry_rows(result)))

    def test_checked_vs_wrapping_through_sfir_definitions(self):
        for checked, outcome in [(True, "UNSAT"), (False, "SAT")]:
            sfir = typed_branch_fixture([
                binary("v", "+", operand(255, literal=True), operand(1, literal=True), "uint8", checked),
                binary("p", "==", operand("v"), operand(0, literal=True))])
            _, _, result = execute(sfir)
            row = next(x for x in entry_rows(result) if x["scope"]["predicates"][0]["taken"])
            self.assertEqual(outcome, row["edge"]["status"]["solver"])
            self.assertEqual("COMPLETE", row["scope"]["scope_completeness"])

    def test_no_name_based_identity_or_phi_guess(self):
        sfir = typed_branch_fixture()
        sfir["functions"][0]["semantic_nodes"][0]["fact_ssa"]["reads"][0].pop("version")
        _, _, result = execute(sfir)
        self.assertTrue(all(x["edge"]["status"]["solver"] == "UNSUPPORTED" for x in entry_rows(result)))
        sfir = typed_branch_fixture()
        sfir["functions"][0]["fact_ssa"]["definitions"][0]["definition_kind"] = "phi"
        _, _, result = execute(sfir)
        self.assertTrue(all(x["edge"]["status"]["solver"] == "UNSUPPORTED" for x in entry_rows(result)))

    def test_outside_carrier_definition_external_result_and_order_gap(self):
        for case in ("outside", "external", "order"):
            sfir = typed_branch_fixture()
            function = sfir["functions"][0]
            if case == "outside":
                function["semantic_nodes"][0]["placement"]["anchor_block"] = "elsewhere"
            elif case == "external":
                function["semantic_nodes"][0]["evidence"]["slither"]["kind"] = "LowLevelCall"
            else:
                function["fact_cfg"]["blocks"][0]["semantic_ids"].reverse()
            _, _, result = execute(sfir)
            self.assertTrue(all(x["edge"]["payload"]["feasibility"] == "UNRESOLVED" for x in entry_rows(result)))

    def test_cast_definition_and_bool_parameter(self):
        sfir = typed_branch_fixture([
            {"kind": "TypeConversion", "variable": operand("x"), "lvalue": operand("wide", "uint16")},
            binary("p", "<=", operand("wide", "uint16"), operand(255, "uint16", True))])
        _, _, result = execute(sfir)
        self.assertTrue(all(row["opaque_predicates"][0]["classification"] == "ALWAYS_TRUE" for row in entry_rows(result)))
        sfir = typed_branch_fixture([], condition="x", parameter_type="bool")
        _, _, result = execute(sfir)
        self.assertTrue(all(row["opaque_predicates"][0]["classification"] == "NON_CONSTANT" for row in entry_rows(result)))

    def test_unused_checked_operation_cannot_be_ignored(self):
        sfir = typed_branch_fixture([
            binary("unused", "+", operand(255, literal=True), operand(1, literal=True), "uint8", True),
            binary("p", "==", operand("x"), operand("x"))])
        _, _, result = execute(sfir)
        self.assertTrue(all(row["scope"]["scope_completeness"] == "PARTIAL" for row in entry_rows(result)))
        self.assertTrue(all(row["edge"]["payload"]["feasibility"] == "UNRESOLVED" for row in entry_rows(result)))
        self.assertTrue(all(row["opaque_predicates"][0]["classification"] == "UNKNOWN" for row in entry_rows(result)))

    def test_no_transitive_candidates(self):
        upstream, _, result = execute(chain_fixture())
        self.assertEqual(len(upstream["edges"]), len(result["refinements"]))
        for row in result["refinements"]:
            candidate = next(e for e in upstream["edges"] if e["id"] == row["candidate_id"])
            for key in ("from", "to", "context_ref", "region_ref", "candidate_id"):
                self.assertEqual(candidate["payload"][key], row["edge"]["payload"][key])

    def test_reorder_deterministic_query_guard_accounting(self):
        sfir = typed_branch_fixture()
        upstream, propagation, result = execute(sfir)
        shuffled = deepcopy(sfir)
        for key in ("semantic_nodes",): shuffled["functions"][0][key].reverse()
        for key in ("blocks", "edges"): shuffled["functions"][0]["fact_cfg"][key].reverse()
        for key in ("definitions", "bindings"): shuffled["functions"][0]["fact_ssa"][key].reverse()
        upstream["edges"].reverse(); upstream["evidence_records"].reverse()
        again = r.refine_candidates(shuffled, upstream, propagation)
        self.assertEqual(r.serialize_refinement_result(result), r.serialize_refinement_result(again))

    def test_query_artifact_replay_and_refs(self):
        upstream, _, result = execute(typed_branch_fixture())
        import z3
        for row in result["refinements"]:
            r.validate_guard(row["guard"])
            for evidence in row["solver_evidence"]:
                r.validate_solver_evidence(evidence)
                p = evidence["payload"]
                self.assertEqual(content_digest(p["query_artifact"]), p["query_digest"])
                self.assertEqual(z3.get_version_string(), p["backend_version"])
                if p["query_artifact"]["smt2"]:
                    solver = z3.Solver(); solver.from_string(p["query_artifact"]["smt2"])
                    self.assertEqual(p["outcome"], str(solver.check()).upper())
            self.assertEqual(len(row["solver_evidence"]), len({e["id"] for e in row["solver_evidence"]}))
            query_refs = {"query:" + e["payload"]["query_digest"] for e in row["solver_evidence"]}
            for evidence in row["solver_evidence"]:
                opaque = evidence["payload"]["opaque_predicate_result"]
                if opaque:
                    self.assertTrue(set(opaque["evidence_refs"]) <= query_refs)
            self.assertEqual([row], r.query_refinements(result, candidate_id=row["candidate_id"]))
        self.assertEqual(result["accounting"], r.refinement_accounting(result["refinements"]))

    def test_unexpected_backend_exception_is_not_empty_success(self):
        class Broken(StubBackend):
            def solve(self, *args): raise RuntimeError("backend bug")
        with self.assertRaisesRegex(RuntimeError, "backend bug"):
            execute(typed_branch_fixture(), backend=Broken("UNKNOWN"))


class OpaqueTests(unittest.TestCase):
    def test_true_false_nonconstant(self):
        for operator, expected in [("==", "ALWAYS_TRUE"), ("!=", "ALWAYS_FALSE"), ("<", "NON_CONSTANT")]:
            rhs = operand(8, literal=True) if operator == "<" else operand("x")
            _, _, result = execute(typed_branch_fixture([binary("p", operator, operand("x"), rhs)]))
            for row in entry_rows(result):
                self.assertEqual(expected, row["opaque_predicates"][0]["classification"])
                self.assertEqual(4, len(row["solver_evidence"]))
                self.assertEqual({"FEASIBILITY", "OPAQUE_BASE", "OPAQUE_POSITIVE", "OPAQUE_NEGATIVE"}, {e["payload"]["query_artifact"]["role"] for e in row["solver_evidence"]})

    def test_multiple_predicates_prefix_and_unsat_base(self):
        # Build second branch using the same stable input definition, with a
        # false first alternative carried to it. No whole-function guard AND.
        sfir = typed_branch_fixture([binary("p", "==", operand("x"), operand("x"))])
        function = sfir["functions"][0]
        second = typed_branch_fixture()["functions"][0]
        second_nodes = deepcopy(second["semantic_nodes"][:2])
        for n in second_nodes:
            n["semantic_id"] += ":second"
            n["placement"]["anchor_block"] = "no"
            for value in n["fact_ssa"]["reads"] + n["fact_ssa"]["writes"]:
                if value["version"] == "fssa:p": value["version"] = "fssa:p:second"
        function["semantic_nodes"] = [n for n in function["semantic_nodes"] if n["semantic_id"] != "no"] + second_nodes + [action_node("last", "last")]
        b = next(b for b in function["fact_cfg"]["blocks"] if b["block_id"] == "no")
        b["terminator"] = {"kind": "Branch", "condition": "p"}; b["semantic_ids"] = [n["semantic_id"] for n in second_nodes]
        last = block("last", terminator="Return"); last["semantic_ids"] = ["last"]
        function["fact_cfg"]["blocks"].append(last)
        extra = edge(3, "no", "last", "true"); extra["guard"] = "p"
        function["fact_cfg"]["edges"].append(extra)
        function["fact_ssa"]["definitions"].append({"version": "fssa:p:second", "binding_id": "temporary:p", "definition_kind": "semantic_definition", "block_id": "no", "owner_semantic_id": "compute:0:second"})
        _, _, result = execute(sfir)
        row = next(row for row in entry_rows(result) if len(row["opaque_predicates"]) == 2)
        self.assertEqual("ALWAYS_TRUE", row["opaque_predicates"][0]["classification"])
        self.assertEqual("UNKNOWN", row["opaque_predicates"][1]["classification"])
        self.assertTrue(row["opaque_predicates"][1]["base_infeasible"])
        self.assertEqual("UNSAT", row["edge"]["status"]["solver"])


class UpstreamBoundaryTests(unittest.TestCase):
    def test_real_propagation_unsupported_error_are_not_empty_success(self):
        sfir = flattened_fixture()
        broken = deepcopy(sfir)
        broken["functions"][0]["fact_cfg"]["blocks"].append(deepcopy(broken["functions"][0]["fact_cfg"]["blocks"][0]))
        for source, termination in [({"schema": "other/v1", "functions": []}, "UNSUPPORTED"), (broken, "ERROR")]:
            _, _, propagation, upstream = prepare(sfir, propagation_sfir=source)
            self.assertEqual(termination, propagation["propagations"][0]["payload"]["termination"])
            result = r.refine_candidates(sfir, upstream, propagation)
            self.assertEqual("PARTIAL", result["status"]["completion"])
            self.assertIn("PROPAGATION_" + termination, canonical_json(result["unresolved_scopes"]))
            self.assertEqual(len(upstream["edges"]), len(result["refinements"]))

    def test_known_unknown_fixed_point_and_flattened_mixed_consumption(self):
        for sfir in (typed_fixture()[0], flattened_fixture()):
            upstream, propagation, result = execute(sfir)
            self.assertTrue(propagation["propagations"])
            self.assertEqual("FIXED_POINT", propagation["propagations"][0]["payload"]["termination"])
            self.assertEqual(len(upstream["edges"]), len(result["refinements"]))
            contextual = [x for x in result["refinements"] if x["edge"]["payload"]["context_ref"]]
            self.assertTrue(contextual)
            self.assertTrue(all(x["scope"]["context_entries"] for x in contextual))
            self.assertTrue(all(x["edge"]["payload"]["feasibility"] == "UNRESOLVED" for x in contextual))
            origins = {x["scope"]["carrier"]["origin"] for x in result["refinements"]}
            self.assertIn("FLATTENED_PROPAGATION_CONTROL", origins)
            self.assertIn("MIXED_BOUNDARY_CONTROL", origins)

    def test_resource_limit_frontier_preserved(self):
        sfir = typed_fixture()[0]
        _, _, propagation, upstream = prepare(sfir, budget={**LIMITS, "max_processed_items": 2})
        self.assertEqual("RESOURCE_LIMIT", propagation["propagations"][0]["payload"]["termination"])
        result = r.refine_candidates(sfir, upstream, propagation)
        self.assertEqual(upstream["accounting"], result["upstream_accounting"])
        self.assertEqual(upstream["unresolved_scopes"], result["unresolved_scopes"])
        self.assertEqual(len(upstream["edges"]), len(result["refinements"]))
        self.assertEqual("PARTIAL", result["status"]["completion"])

    def test_upstream_partial_unsupported_error_preserved(self):
        for code in ("UNKNOWN", "UNSUPPORTED", "ERROR"):
            sfir = typed_branch_fixture()
            upstream, propagation, _ = execute(sfir)
            diag = r.diagnostic(code, "upstream fixture diagnostic", affected_refs=[upstream["edges"][0]["id"]])
            upstream["status"]["completion"] = "PARTIAL"
            upstream["status"]["diagnostics"].append(diag)
            for a in upstream["accounting"]: a["partial"] = True
            for e in upstream["edges"]:
                e["status"]["completion"] = "PARTIAL"; e["status"]["diagnostics"].append(diag)
            result = r.refine_candidates(sfir, upstream, propagation)
            self.assertIn(diag, result["status"]["diagnostics"])
            self.assertTrue(all(x["edge"]["payload"]["feasibility"] == "UNRESOLVED" for x in result["refinements"]))

    def test_production_boundary_and_no_oracle(self):
        source = inspect.getsource(r)
        for forbidden in ("reconstruct_candidate_control_edges(", "Module1Result", "compare_guards", "reaching_definitions_in", "oracle", "evaluator", "subprocess", "open("):
            self.assertNotIn(forbidden, source)
        tree = ast.parse(source)
        self.assertFalse(any(isinstance(n, ast.Constant) and isinstance(n.value, str) and
                             ("/expected" in n.value or "frozen_oracle" in n.value) for n in ast.walk(tree)))
        self.assertIn("query_node_context_state(", source)
        self.assertIn("validate_candidate_collection(", source)
        self.assertNotIn("eval(", source)


def yul_branch_fixture(*, address=False, state=False):
    """AST producer -> additive SFIR evidence -> exact existing SSA fixture."""
    from s_seir_yul_eval_order import YulEvaluationOrder
    from s_seir_predicate_lifter import PredicateLifter
    from s_seir_model import EffectNode
    from s_seir_semantic_fact_adapter import SSeirFactAdapter
    from s_seir_semantic_fact_adapter_tests import function, overlay
    name = "x"
    if address:
        attrs = {"variable": name, "variable_type": "address", "check": "is_zero",
                 "projection": "identity", "projection_expression": name,
                 "condition": "x == address(0)", "status": "resolved"}
        source = overlay("AddressZeroCheck", attrs)
        condition = attrs["condition"]
    else:
        ast_node = {"nodeType": "YulFunctionCall", "functionName": {"nodeType": "YulIdentifier", "name": "lt"}, "arguments": [
            {"nodeType": "YulIdentifier", "name": name}, {"nodeType": "YulLiteral", "kind": "number", "value": "8"}]}
        evaluation = YulEvaluationOrder("branch").materialize(ast_node)
        branch = EffectNode("branch", "Branch", ["stmt"], {"condition_evaluation": evaluation})
        condition = "(x < 8)"
        source = overlay("Predicate", {"predicate_id": "pred_ast", "expression": condition,
                         "context": "condition", "status": "resolved",
                         "typed_predicate": PredicateLifter._typed_evaluation(branch, "pred_ast")})
    adapted = SSeirFactAdapter().function_facts(function([source]))[0].to_dict()
    sfir = typed_branch_fixture([], condition=condition, parameter_type="address" if address else "uint256")
    f = sfir["functions"][0]
    pred = f["semantic_nodes"][0]
    pred.update({k: adapted[k] for k in ("kind", "semantic", "evidence", "reads", "writes", "rvalue", "fact_role") if k in adapted})
    pred["fact_ssa"]["reads"] = [{"value": "x", "binding_id": "decl:x:value", "version": "fssa:x:entry"}]
    if state:
        read = node("read", "entry", kind="StateRead")
        read.update(fact_role="value", lvalue="x", writes=["x"], reads=["balances[key]"], semantic={
            "operation": "state_read", "result_type": "uint256", "resolution_status": "resolved",
            "location": {"kind": "mapping", "state_variable": "balances", "access": "balances[key]", "keys": ["key"]}})
        read["fact_ssa"] = {"reads": [{"value": "balances[key]", "binding_id": "location", "version": "storage:entry"}],
                            "writes": [{"value": "x", "binding_id": "read:value", "version": "read:result"}]}
        f["semantic_nodes"].insert(0, read)
        f["fact_cfg"]["blocks"][0]["semantic_ids"].insert(0, "read")
        f["fact_ssa"]["bindings"] += [{"binding_id": "location", "facet": "storage_location", "identity_status": "semantic_storage_location",
            "state_variable": "balances", "access": "balances[key]", "keys": ["key"]}, {"binding_id": "read:value", "type": "uint256"}]
        f["fact_ssa"]["definitions"] += [{"version": "storage:entry", "binding_id": "location", "definition_kind": "entry", "owner_semantic_id": None},
            {"version": "read:result", "binding_id": "read:value", "definition_kind": "semantic_definition", "block_id": "entry", "owner_semantic_id": "read"}]
        pred["fact_ssa"]["reads"] = [{"value": "x", "binding_id": "read:value", "version": "read:result"}]
    return sfir


class PosteriorCorrectionTests(unittest.TestCase):
    def test_address_zero_without_slither_uses_exact_ssa(self):
        sfir = yul_branch_fixture(address=True)
        self.assertNotIn("slither", sfir["functions"][0]["semantic_nodes"][0]["evidence"])
        rows = entry_rows(execute(sfir)[2])
        self.assertEqual(2, len(rows))
        self.assertTrue(all(x["edge"]["payload"]["feasibility"] == "FEASIBLE" for x in rows))
        self.assertTrue(all(x["scope"]["predicates"][0]["expression"]["type"] == r.BOOL for x in rows))
        sfir["functions"][0]["semantic_nodes"][0]["fact_ssa"]["reads"] = []
        self.assertTrue(all(x["edge"]["status"]["solver"] == "UNSUPPORTED" for x in entry_rows(execute(sfir)[2])))

    def test_structured_yul_ast_required_no_string_fallback(self):
        sfir = yul_branch_fixture()
        self.assertTrue(all(x["edge"]["payload"]["feasibility"] == "FEASIBLE" for x in entry_rows(execute(sfir)[2])))
        for field in ("op", "type", "identity"):
            broken = deepcopy(sfir)
            tree = broken["functions"][0]["semantic_nodes"][0]["semantic"]["typed_predicate"]["expression"]
            (tree["operands"][0] if field == "identity" else tree).pop(field)
            rows = entry_rows(execute(broken)[2])
            self.assertTrue(all(x["edge"]["status"]["solver"] == "UNSUPPORTED" for x in rows), field)
            self.assertTrue(all(x["scope"]["scope_completeness"] == "PARTIAL" for x in rows))

    def test_exact_state_leaf_and_p1_t7_consumption(self):
        from s_seir_research_guards import build_module1_result, validate_module1_result
        sfir = yul_branch_fixture(state=True)
        before = deepcopy(sfir)
        upstream, _, result = execute(sfir)
        rows = entry_rows(result)
        self.assertTrue(all(x["edge"]["payload"]["feasibility"] == "FEASIBLE" for x in rows))
        symbols = rows[0]["scope"]["symbol_bindings"]
        self.assertEqual("STATE_READ", symbols[0]["role"])
        self.assertEqual({"SEMANTIC_NODE", "STORAGE_ACCESS", "DEFINITION"}, {x["source_kind"] for x in symbols[0]["source_refs"]})
        actions = extract_semantic_actions(sfir, input_fingerprint=INPUT_FINGERPRINT)
        sealed = build_module1_result(result, actions)
        validate_module1_result(sealed)
        self.assertEqual(before, sfir)
        self.assertEqual({e["id"] for e in upstream["edges"]}, {x["candidate_id"] for x in result["refinements"]})

    def test_state_leaf_missing_type_location_version_owner(self):
        for missing in ("type", "location", "read_version", "write_version", "owner", "phi", "target_type"):
            sfir = yul_branch_fixture(state=True); f = sfir["functions"][0]; read = f["semantic_nodes"][0]
            if missing == "type": read["semantic"].pop("result_type")
            if missing == "location": read["semantic"]["location"].pop("state_variable")
            if missing == "read_version": read["fact_ssa"]["reads"][0].pop("version")
            if missing == "write_version": read["fact_ssa"]["writes"][0].pop("version")
            if missing == "owner": f["fact_ssa"]["definitions"][-1].pop("owner_semantic_id")
            if missing == "phi": f["fact_ssa"]["definitions"][-2]["definition_kind"] = "phi"
            if missing == "target_type": f["fact_ssa"]["bindings"][-1]["type"] = "uint128"
            rows = entry_rows(execute(sfir)[2])
            self.assertTrue(all(x["edge"]["payload"]["feasibility"] == "UNRESOLVED" for x in rows), missing)
            self.assertTrue(all(x["edge"]["status"]["solver"] == "UNSUPPORTED" for x in rows), missing)

    def test_read_occurrence_identity_no_alias_and_post_effect_rejected(self):
        sfir = yul_branch_fixture(state=True); f = sfir["functions"][0]
        read = f["semantic_nodes"][0]; other = deepcopy(read); other["semantic_id"] = "other_read"
        other["fact_ssa"]["writes"][0].update(binding_id="other:value", version="other:result")
        f["fact_ssa"]["definitions"].append({**f["fact_ssa"]["definitions"][-1], "version": "other:result", "binding_id": "other:value", "owner_semantic_id": "other_read"})
        f["semantic_nodes"].insert(1, other); f["fact_cfg"]["blocks"][0]["semantic_ids"].insert(1, "other_read")
        upstream, _, result = execute(sfir)
        row = entry_rows(result)[0]
        self.assertEqual(2, len({s["symbol_ref"] for s in row["scope"]["symbol_bindings"] if s["role"] == "STATE_READ"}))
        candidate = upstream["edges"][0]
        for kind in ("StateWrite", "ExternalCall"):
            broken = deepcopy(f); prior = node("prior", "entry", kind=kind); prior["fact_role"] = "effect"
            broken["semantic_nodes"].insert(0, prior); broken["fact_cfg"]["blocks"][0]["semantic_ids"].insert(0, "prior")
            closure = r._Closure(broken, candidate, ["entry"], r.DEFAULT_CONFIG)
            with self.assertRaisesRegex(r.Unsupported, "storage/effectful"):
                closure.state_read(read)
        closure = r._Closure(f, candidate, ["entry", "entry"], r.DEFAULT_CONFIG)
        with self.assertRaisesRegex(r.Unsupported, "dynamic iteration"):
            closure.state_read(read)

    def test_explicit_transparency_totality_and_missing_root(self):
        sfir = yul_branch_fixture(); f = sfir["functions"][0]
        support = node("location", "entry", kind="StorageLocationResolve")
        support.update(fact_role="support", semantic={"operation": "storage_location_resolve", "resolution_status": "resolved",
            "location": {"kind": "mapping", "state_variable": "balances", "access": "balances[key]", "keys": ["key"]}},
            fact_ssa={"reads": [{"binding_id": "root", "version": "root:entry"}], "writes": []})
        f["fact_ssa"]["bindings"].append({"binding_id": "root", "kind": "state", "declaration_id": 45, "base_name": "balances"})
        f["fact_ssa"]["definitions"].append({"binding_id": "root", "version": "root:entry", "definition_kind": "entry"})
        f["semantic_nodes"].insert(0, support); f["fact_cfg"]["blocks"][0]["semantic_ids"].insert(0, "location")
        rows = entry_rows(execute(sfir)[2])
        self.assertTrue(all(x["scope"]["scope_completeness"] == "COMPLETE" for x in rows))
        self.assertEqual("resolved-semantic-storage-location-is-total/v1", rows[0]["scope"]["reachability_transparent_evidence"][0]["rule"])
        for mutation in ("root", "unresolved", "unknown", "effect"):
            broken = deepcopy(sfir); n = broken["functions"][0]["semantic_nodes"][0]
            if mutation == "root": n["fact_ssa"]["reads"] = []
            if mutation == "unresolved": n["semantic"]["resolution_status"] = "unresolved"
            if mutation == "unknown": n["kind"] = "ValueCompute"
            if mutation == "effect": n["fact_role"] = "effect"
            self.assertTrue(all(x["scope"]["scope_completeness"] == "PARTIAL" for x in entry_rows(execute(broken)[2])), mutation)

    def test_unused_checked_computation_does_not_become_transparent(self):
        for checked, completeness in ((False, "COMPLETE"), (True, "PARTIAL")):
            sfir = typed_branch_fixture([binary("unused", "+", operand("x"), operand(1, literal=True), "uint8", checked),
                                        binary("p", "<", operand("x"), operand(8, literal=True))])
            self.assertTrue(all(x["scope"]["scope_completeness"] == completeness for x in entry_rows(execute(sfir)[2])))
        for kind, role in (("ValueCompute", "support"), ("ExternalCall", "effect")):
            sfir = typed_branch_fixture([binary("unused", "+", operand("x"), operand(1, literal=True), "uint8", False),
                                        binary("p", "<", operand("x"), operand(8, literal=True))])
            sfir["functions"][0]["semantic_nodes"][0].update(kind=kind, fact_role=role)
            self.assertTrue(all(x["scope"]["scope_completeness"] == "PARTIAL" for x in entry_rows(execute(sfir)[2])))

    def test_typed_evidence_reorder_and_conflicting_literal(self):
        sfir = yul_branch_fixture(state=True)
        original = execute(sfir)[2]
        reordered = deepcopy(sfir)
        f = reordered["functions"][0]
        f["semantic_nodes"].reverse()
        f["fact_ssa"]["definitions"].reverse()
        f["fact_ssa"]["bindings"].reverse()
        f["fact_cfg"]["edges"].reverse()
        self.assertEqual(r.serialize_refinement_result(original), r.serialize_refinement_result(execute(reordered)[2]))
        sfir["functions"][0]["semantic_nodes"][1]["semantic"]["typed_predicate"]["expression"]["operands"][1]["literal"] = "9"
        self.assertTrue(all(x["edge"]["status"]["solver"] == "UNSUPPORTED" for x in entry_rows(execute(sfir)[2])))

    def test_unused_unsupported_cast_is_not_transparent(self):
        sfir = typed_branch_fixture([
            {"kind": "TypeConversion", "lvalue": operand("unused", "bool"), "variable": operand("x")},
            binary("p", "<", operand("x"), operand(8, literal=True))])
        rows = entry_rows(execute(sfir)[2])
        self.assertTrue(all(x["scope"]["scope_completeness"] == "PARTIAL" for x in rows))
        self.assertTrue(all(any(reason["reason"] == "Bool conversion is not supported"
                                for reason in x["scope"]["incomplete_reasons"]) for x in rows))

    def test_require_polarity_is_structural_not_inferred_from_text(self):
        from s_seir_semantic_fact_ir import SemanticFactIRBridge
        sfir = yul_branch_fixture(); f = sfir["functions"][0]; cfg = f["fact_cfg"]
        cfg["block_by_id"] = {b["block_id"]: b for b in cfg["blocks"]}
        require = {"kind": "Require", "condition": "opposite rendering", "placement": {"anchor_block": "entry"},
                   "semantic_provenance": {"require_failure_cfg_node": "raw_yes"}}
        SemanticFactIRBridge._normalize_require_guards(cfg, [*f["semantic_nodes"], require], {"raw_yes": "yes"})
        binding = cfg["blocks"][0]["terminator"]["predicate_binding"]
        self.assertFalse(binding["condition_polarity"])
        rows = entry_rows(execute(sfir)[2])
        self.assertTrue(all(x["scope"]["predicates"][0]["expression"]["op"] == "!" for x in rows))
        self.assertTrue(all(x["edge"]["payload"]["feasibility"] == "FEASIBLE" for x in rows))
        binding["semantic_id"] = "wrong"
        self.assertTrue(all(x["edge"]["status"]["solver"] == "UNSUPPORTED" for x in entry_rows(execute(sfir)[2])))

if __name__ == "__main__":
    unittest.main(verbosity=2)
