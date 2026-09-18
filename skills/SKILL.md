---
name: project-bootstrap
description: Bootstrap a fresh Codex conversation for the Solidity/Yul semantic deobfuscation project using the repository sources of truth, with minimal token usage and without repeating prior capability reviews.
---

# Project Bootstrap Skill

## Purpose

Use this workflow whenever a fresh Codex conversation starts a Task in the Solidity/Yul semantic deobfuscation project after P0-T3, including conditional repair Tasks.

This Skill is only a bootstrap procedure. It is **not** a copy of the project plan and is **not** a source of current project facts.

## Repository Sources of Truth

Read project context in this order:

1. `docs/research/RESEARCH_FRAMEWORK.md`
2. `docs/research/TASK_MAP.md`
3. `PROJECT_STATUS.md`
4. `IMPLEMENTATION_PLAN.md` — read only the current Task and directly related frozen interfaces/dependencies
5. The upstream `docs/task_reports/<TaskID>.md` and `docs/handoffs/*.md` explicitly named by the current Task
6. Only then read the directly relevant source and test files

Do not read the whole execution manual unless the current prompt explicitly requires it.
Do not load every historical task report.
Do not rescan the entire repository merely to reconstruct project history.

## Authority

Use the following semantic authority order:

`RESEARCH_FRAMEWORK.md` > `IMPLEMENTATION_PLAN.md` > current Task Prompt

Use:

- `TASK_MAP.md` only for global navigation;
- `PROJECT_STATUS.md` only for current execution state;
- task reports/handoffs for already-completed facts, limitations, evidence and blocking gaps.

If `TASK_MAP.md` conflicts with `IMPLEMENTATION_PLAN.md`, treat the Implementation Plan as authoritative and report documentation drift.

If `PROJECT_STATUS.md` conflicts with actual code/tests, inspect the directly relevant evidence and report the conflict. Do not silently continue across the conflict.

## Bootstrap Check

Before implementing anything, determine and record internally or in the Task report:

- Current Task ID and name
- Current Module / Phase
- Why this Task exists
- Direct upstream artifacts actually consumed
- Stable output that must be left for downstream Tasks
- Direct downstream consumer(s)
- Frozen interfaces/semantics that must not be changed
- Any conditional Task status relevant to the current Task
- Any explicit blocking-gap handoff that must be consumed

If these cannot be determined from the repository sources above, stop implementation and report a handoff/documentation conflict rather than inventing project context.

## Blocking Gap Rule

When the current Task is a repair for a previously confirmed blocking gap:

1. Accept the prior handoff's confirmed problem as input.
2. Do not rerun the full capability review unless actual repository facts contradict the handoff.
3. Read the handoff fields:
   - Problem
   - Evidence
   - Impact
   - Minimum Required Capability
   - Non-goals
   - Relevant Files
   - Acceptance Requirements
4. Implement only the frozen repair boundary from `IMPLEMENTATION_PLAN.md` / Task Spec.
5. Close the gap with explicit regression evidence and remaining limitations.

## Scope Discipline

Never use this Skill as justification to:

- redesign the research framework;
- duplicate the full Task Map in the Skill;
- duplicate current project status in the Skill;
- infer future Task behavior from memory;
- implement later Tasks early;
- rebuild an already-completed capability review;
- expand a bounded repair into a general-purpose subsystem.

## End Condition

After bootstrap, execute only the current Task Prompt.

Before marking the Task complete, ensure its task report and `PROJECT_STATUS.md` leave enough stable information for the next fresh Codex conversation to bootstrap without relying on chat history.
