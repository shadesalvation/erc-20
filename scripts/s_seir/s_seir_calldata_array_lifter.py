#!/usr/bin/env python3
"""Complete typed calldata-array candidates with CFG-backed bounds proofs.

The overlay builder can recognize the ABI layout of ``calldataload`` but that
alone is not equivalent to Solidity indexing: out-of-range calldata reads
yield zero whereas ``array[index]`` reverts.  This pass owns the missing
control proof.  It consumes completed Predicates and the function CFG, then
replaces a raw-word overlay only when the successful bounds edge is proven.
"""
from __future__ import annotations

from typing import Any

from s_seir_id import IdAllocator
from s_seir_model import EffectNode, SemanticOverlay


Json = dict[str, Any]


class CalldataArrayLifter:
    """Turn proven ABI-word reads into canonical ``array[index]`` overlays."""

    def __init__(self) -> None:
        self.ids = IdAllocator()

    def lift(
        self,
        type_env: Any,
        effects: list[EffectNode],
        overlays: list[SemanticOverlay],
        control: Json | None = None,
    ) -> list[SemanticOverlay]:
        # ``type_env`` is intentionally accepted at this completion boundary:
        # candidates have already used it to prove ABI layout, while this pass
        # proves only CFG/dataflow equivalence.
        del type_env
        control = control or {}
        predicates = [item for item in overlays if item.kind == 'Predicate']
        candidates = [item for item in overlays if item.kind == 'CalldataArrayElementCandidate']
        if not candidates:
            return overlays

        effect_by_id = {effect.effect_id: effect for effect in effects}
        blocks = {
            str(item.get('block_id')): item
            for item in control.get('blocks') or []
            if isinstance(item, dict) and item.get('block_id')
        }
        successors = self._successors(control)
        dominators = self._dominators(blocks, successors)
        replacements: dict[str, SemanticOverlay] = {}
        candidate_ids = {candidate.overlay_id for candidate in candidates}

        for candidate in candidates:
            proof = self._bounds_proof(candidate, predicates, effect_by_id, blocks, successors, dominators, control)
            if not proof:
                continue
            attrs = dict(candidate.attrs)
            source_word = str(attrs.pop('source_word_overlay', '') or '')
            attrs.pop('candidate_status', None)
            attrs.pop('source_expression', None)
            attrs.update({
                'solidity_like': f"{attrs.get('target')} = {attrs.get('access')};",
                'solidity_equivalent': True,
                'semantic_model': 'typed_calldata_array_element_read',
                'bounds_proof': proof,
            })
            replacements[source_word] = SemanticOverlay(
                self.ids.new('calldata_array'),
                'CalldataArrayElementRead',
                list(candidate.effects),
                list(candidate.stmt_refs),
                attrs,
            )

        out: list[SemanticOverlay] = []
        for overlay in overlays:
            if overlay.overlay_id in replacements:
                out.append(replacements[overlay.overlay_id])
                continue
            if overlay.overlay_id in candidate_ids:
                # A candidate is a transient proof obligation.  Its unresolved
                # semantic is retained by the original CalldataWordRead; its
                # resolved semantic is represented by the replacement above.
                continue
            out.append(overlay)
        return out

    def _bounds_proof(
        self,
        candidate: SemanticOverlay,
        predicates: list[SemanticOverlay],
        effect_by_id: dict[str, EffectNode],
        blocks: dict[str, Json],
        successors: dict[str, list[tuple[str, str]]],
        dominators: dict[str, set[str]],
        control: Json,
    ) -> Json | None:
        candidate_block = self._overlay_block(candidate, effect_by_id, blocks)
        if not candidate_block:
            return None
        array = str(candidate.attrs.get('array') or '')
        index = str(candidate.attrs.get('index') or '')
        if not array or not index:
            return None
        expected = self._canonical_text(f'{index} < {array}.length')
        dependencies = [item for item in control.get('control_dependencies') or [] if isinstance(item, dict)]
        for predicate in predicates:
            attrs = predicate.attrs
            controller = str(attrs.get('semantic_anchor_cfg_node') or '')
            if not controller or attrs.get('status') != 'resolved':
                continue
            if self._canonical_text(attrs.get('expression')) != expected:
                continue
            if controller not in dominators.get(candidate_block, set()):
                continue
            matching = [
                item for item in dependencies
                if str(item.get('controller') or '') == controller
                and str(item.get('dependent') or '') == candidate_block
                and self._is_true_edge(str(item.get('edge_kind') or ''))
            ]
            true_targets = [
                target for target, edge_kind in successors.get(controller, [])
                if self._is_true_edge(edge_kind)
            ]
            if not true_targets:
                continue
            # Some pure-Yul CFGs do not materialize control-dependency rows.
            # The edge itself is still a complete CFG proof when the read is
            # reachable from a true successor and unreachable from every
            # non-true successor without returning through the controller.
            # When a dependency row is available, retain it as the stronger
            # precomputed proof rather than weakening that case.
            if dependencies and not matching:
                continue
            if not matching and not self._is_true_edge_exclusive(
                controller, candidate_block, true_targets, successors
            ):
                continue
            if any(self._index_redefined_before_read(index, target, candidate_block, controller, effect_by_id, blocks, successors) for target in true_targets):
                continue
            return {
                'predicate_id': attrs.get('predicate_id') or predicate.overlay_id,
                'condition': attrs.get('expression'),
                'controller': controller,
                'controlled_edge': 'true',
                'proof': 'cfg_dominating_bounds_edge_without_index_redefinition',
            }
        return None

    @staticmethod
    def _is_true_edge_exclusive(
        controller: str,
        candidate: str,
        true_targets: list[str],
        successors: dict[str, list[tuple[str, str]]],
    ) -> bool:
        def reaches(start: str) -> bool:
            seen: set[str] = set()
            stack = [start]
            while stack:
                node = stack.pop()
                if node == controller or node in seen:
                    continue
                if node == candidate:
                    return True
                seen.add(node)
                stack.extend(target for target, _ in successors.get(node, []))
            return False

        if not any(reaches(target) for target in true_targets):
            return False
        return not any(
            reaches(target)
            for target, edge_kind in successors.get(controller, [])
            if not CalldataArrayLifter._is_true_edge(edge_kind)
        )

    @staticmethod
    def _overlay_block(
        overlay: SemanticOverlay,
        effect_by_id: dict[str, EffectNode],
        blocks: dict[str, Json],
    ) -> str | None:
        node_ids = {
            str((effect_by_id[effect_id].attrs or {}).get('cfg_node_id'))
            for effect_id in overlay.effects
            if effect_id in effect_by_id and (effect_by_id[effect_id].attrs or {}).get('cfg_node_id') is not None
        }
        matches = [
            block_id for block_id, block in blocks.items()
            if str((block.get('attrs') or {}).get('node_id')) in node_ids
            and set(str(ref) for ref in overlay.stmt_refs).intersection(str(ref) for ref in block.get('stmts') or [])
        ]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _successors(control: Json) -> dict[str, list[tuple[str, str]]]:
        out: dict[str, list[tuple[str, str]]] = {}
        for edge in control.get('edges') or []:
            if not isinstance(edge, dict) or not edge.get('from') or not edge.get('to'):
                continue
            out.setdefault(str(edge['from']), []).append((str(edge['to']), str(edge.get('kind') or '')))
        return out

    @staticmethod
    def _dominators(blocks: dict[str, Json], successors: dict[str, list[tuple[str, str]]]) -> dict[str, set[str]]:
        node_ids = set(blocks)
        predecessors = {node_id: set() for node_id in node_ids}
        for source, targets in successors.items():
            for target, _kind in targets:
                if source in node_ids and target in node_ids:
                    predecessors[target].add(source)
        roots = {node_id for node_id, incoming in predecessors.items() if not incoming}
        result = {node_id: ({node_id} if node_id in roots else set(node_ids)) for node_id in node_ids}
        changed = True
        while changed:
            changed = False
            for node_id in node_ids - roots:
                incoming = predecessors[node_id]
                shared = set.intersection(*(result[parent] for parent in incoming)) if incoming else set()
                updated = {node_id, *shared}
                if updated != result[node_id]:
                    result[node_id] = updated
                    changed = True
        return result

    def _index_redefined_before_read(
        self,
        index: str,
        start: str,
        read: str,
        controller: str,
        effect_by_id: dict[str, EffectNode],
        blocks: dict[str, Json],
        successors: dict[str, list[tuple[str, str]]],
    ) -> bool:
        if index == '0':
            return False
        region = self._nodes_on_path_without_controller(start, read, controller, successors)
        if not region:
            return True
        node_ids = {
            str((blocks[node_id].get('attrs') or {}).get('node_id'))
            for node_id in region - {read}
        }
        for effect in effect_by_id.values():
            if effect.kind != 'ValueDef':
                continue
            attrs = effect.attrs or {}
            if str(attrs.get('cfg_node_id')) not in node_ids:
                continue
            if index in {str(target) for target in attrs.get('targets') or []}:
                return True
        return False

    @staticmethod
    def _nodes_on_path_without_controller(start: str, goal: str, controller: str, successors: dict[str, list[tuple[str, str]]]) -> set[str]:
        forward: set[str] = set()
        stack = [start]
        while stack:
            node = stack.pop()
            if node == controller or node in forward:
                continue
            forward.add(node)
            if node != goal:
                stack.extend(target for target, _kind in successors.get(node, []))
        if goal not in forward:
            return set()
        reverse: dict[str, set[str]] = {}
        for source, targets in successors.items():
            for target, _kind in targets:
                if source != controller:
                    reverse.setdefault(target, set()).add(source)
        backward: set[str] = set()
        stack = [goal]
        while stack:
            node = stack.pop()
            if node == controller or node in backward:
                continue
            backward.add(node)
            stack.extend(reverse.get(node, set()))
        return forward.intersection(backward)

    @staticmethod
    def _is_true_edge(edge_kind: str) -> bool:
        return edge_kind == 'true' or edge_kind.startswith('true:')

    @staticmethod
    def _canonical_text(value: Any) -> str:
        text = ''.join(str(value or '').split())
        while text.startswith('(') and text.endswith(')'):
            text = text[1:-1]
        return text
