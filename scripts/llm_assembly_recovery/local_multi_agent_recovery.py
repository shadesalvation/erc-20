#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


Json = dict[str, Any]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def strip_code_fence(text: str) -> str:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json|solidity|sol)?\s*", "", value, count=1, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value, count=1)
    return value.strip()


def extract_json_object(text: str) -> Json:
    value = strip_code_fence(text)
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
        raise ValueError("top-level JSON is not an object")
    except json.JSONDecodeError:
        pass

    start = value.find("{")
    end = value.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("LLM response does not contain a JSON object")
    parsed = json.loads(value[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("extracted JSON is not an object")
    return parsed


def clean_solidity_response(text: str) -> str:
    value = strip_code_fence(text)
    return value.rstrip() + "\n"


def safe_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    name = re.sub(r"_+", "_", name).strip("_.")
    return name or "function"


def parse_src_range(src: str) -> tuple[int, int]:
    try:
        start_text, length_text, _file_id = str(src).split(":", 2)
        start = int(start_text)
        length = int(length_text)
        return start, start + length
    except (TypeError, ValueError):
        return 0, 0


def one_function_payload(full_payload: Json, function_item: Json) -> Json:
    out = {k: v for k, v in full_payload.items() if k != "functions"}
    out["functions"] = [function_item]
    return out


def normalize_agent1_json(raw_json: Json, original_payload: Json, function_item: Json) -> Json:
    """Accept either full compact JSON, a single-function compact JSON, or one function object."""
    if isinstance(raw_json.get("functions"), list) and raw_json["functions"]:
        return raw_json
    if "llm_projection_judgement" in raw_json and "function_id" in raw_json:
        out = {k: v for k, v in original_payload.items() if k != "functions"}
        out["functions"] = [raw_json]
        return out
    out = {k: v for k, v in original_payload.items() if k != "functions"}
    merged = dict(function_item)
    merged["llm_projection_judgement"] = raw_json
    out["functions"] = [merged]
    return out


def merge_function_replacements(source_text: str, replacements: list[tuple[int, int, str]]) -> str:
    result = source_text
    for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
        if start < 0 or end <= start or end > len(result):
            raise ValueError(f"Invalid replacement range: {start}:{end}")
        result = result[:start] + replacement.rstrip() + result[end:]
    return result


def mask_comments_and_strings(text: str) -> str:
    result = list(text)
    i = 0
    n = len(text)
    while i < n:
        if text.startswith("//", i):
            j = text.find("\n", i + 2)
            if j < 0:
                j = n
            for k in range(i, j):
                result[k] = " "
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                j = n - 2
            for k in range(i, min(j + 2, n)):
                result[k] = " "
            i = j + 2
        elif text[i] in {"'", '"'}:
            quote = text[i]
            result[i] = " "
            i += 1
            while i < n:
                result[i] = " "
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
        else:
            i += 1
    return "".join(result)


def mask_assembly_blocks(text: str) -> str:
    result = list(text)
    for match in re.finditer(r"\bassembly\b", text):
        i = match.end()
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text) or text[i] != "{":
            continue
        depth = 0
        j = i
        while j < len(text):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        for k in range(match.start(), min(j, len(text))):
            result[k] = " "
    return "".join(result)


def extracted_assembly_text(text: str) -> str:
    chunks: list[str] = []
    for match in re.finditer(r"\bassembly\b", text):
        i = match.end()
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text) or text[i] != "{":
            continue
        depth = 0
        j = i
        while j < len(text):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        chunks.append(text[match.start() : min(j, len(text))])
    return "\n".join(chunks)


def collect_yul_let_variables(function_item: Json) -> set[str]:
    names: set[str] = set()
    for block in function_item.get("assembly_blocks", []) if isinstance(function_item.get("assembly_blocks"), list) else []:
        text = str(block.get("text", ""))
        for name in re.findall(r"\blet\s+([A-Za-z_$][A-Za-z0-9_$]*)\b", text):
            names.add(name)
    return names


def validate_recovered_function(recovered_function: str, function_item: Json) -> list[str]:
    assembly_text = extracted_assembly_text(recovered_function)
    masked = mask_comments_and_strings(mask_assembly_blocks(recovered_function))
    issues: list[str] = []
    forbidden_calls = [
        "mload",
        "mstore",
        "sload",
        "sstore",
        "staticcall",
        "delegatecall",
        "callcode",
        "gas",
        "shl",
        "shr",
        "sar",
        "byte",
        "pop",
        "log0",
        "log1",
        "log2",
        "log3",
        "log4",
    ]
    for name in forbidden_calls:
        if re.search(rf"\b{name}\s*\(", masked):
            issues.append(f"forbidden_yul_builtin_outside_assembly:{name}")
    if re.search(r"\.[ \t]*slot\b", masked):
        issues.append("forbidden_storage_slot_selector_outside_assembly:.slot")
    if re.search(r"\badd\s*\(", masked):
        issues.append("suspicious_yul_add_outside_assembly:add")
    yul_let_names = collect_yul_let_variables(function_item)
    for name in sorted(yul_let_names):
        if re.search(rf"\b{name}\b", masked):
            issues.append(f"assembly_local_variable_escaped:{name}")
    if not re.search(r"\bfunction\s+", recovered_function):
        issues.append("missing_function_definition")
    has_preserved_sstore = bool(re.search(r"\bsstore\s*\(", assembly_text))
    has_preserved_log = bool(re.search(r"\blog[0-4]\s*\(", assembly_text))
    overlays = function_item.get("semantic_overlays", [])
    if isinstance(overlays, list):
        for overlay in overlays:
            if not isinstance(overlay, dict):
                continue
            kind = overlay.get("kind")
            attrs = overlay.get("attrs") if isinstance(overlay.get("attrs"), dict) else {}
            if has_preserved_sstore and kind in {"MappingWrite", "StateVariableWrite"}:
                access = attrs.get("access")
                if isinstance(access, str) and access and re.search(re.escape(access) + r"\s*=", masked):
                    issues.append(f"duplicate_high_level_write_with_preserved_sstore:{access}")
            if has_preserved_log and kind == "EventEmit":
                event = attrs.get("event")
                if isinstance(event, str) and event and event != "unknownEvent" and re.search(rf"\bemit\s+{re.escape(event)}\s*\(", masked):
                    issues.append(f"duplicate_high_level_emit_with_preserved_log:{event}")
    return issues


def normalize_code_for_compare(text: str) -> str:
    return re.sub(r"\s+", "", mask_comments_and_strings(text))


def has_recoverable_high_level_policy(function_item: Json) -> bool:
    judgement = function_item.get("llm_projection_judgement")
    if not isinstance(judgement, dict):
        return False
    if judgement.get("function_recoverability") == "fully_recoverable":
        return True
    for policy in judgement.get("overlay_projection_policies", []):
        if not isinstance(policy, dict):
            continue
        if policy.get("overlay_kind") == "ExpressionNormalization":
            continue
        if policy.get("exact_solidity_equivalent") is True and policy.get("output_kind") in {
            "solidity_statement",
            "solidity_expression",
            "solidity_block",
        }:
            return True
    return False


def validate_effective_recovery(recovered_function: str, original_function: str, augmented_function: Json) -> list[str]:
    if not has_recoverable_high_level_policy(augmented_function):
        return []
    if normalize_code_for_compare(recovered_function) == normalize_code_for_compare(original_function):
        return ["no_effective_recovery_for_recoverable_function"]
    return []


class LocalOpenAIClient:
    def __init__(self, base_url: str, model: str | None, api_key: str, timeout: int = 600) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def resolve_model(self) -> str:
        if self.model:
            return self.model
        request = urllib.request.Request(
            f"{self.base_url}/models",
            headers=self.headers(),
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(
                "No --model was provided and the local LLM /v1/models endpoint could not be queried. "
                "Pass --model explicitly."
            ) from exc
        models = payload.get("data", [])
        if not models:
            raise RuntimeError("The local LLM /v1/models endpoint returned no models. Pass --model explicitly.")
        model = models[0].get("id")
        if not model:
            raise RuntimeError("The first local LLM model has no id. Pass --model explicitly.")
        self.model = str(model)
        return self.model

    def headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int | None,
    ) -> str:
        model = self.resolve_model()
        body: Json = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        # Do not set max_tokens by default. The caller explicitly requested that
        # neither input nor output be truncated by this script.
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=self.headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Local LLM HTTP error {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not connect to local LLM at {self.base_url}: {exc}") from exc
        choices = payload.get("choices") or []
        if not choices:
            raise RuntimeError(f"Local LLM returned no choices: {payload}")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str):
            raise RuntimeError(f"Local LLM returned no message content: {payload}")
        return content


@dataclass
class Agent:
    name: str
    system_prompt: str
    client: LocalOpenAIClient
    temperature: float = 0.0
    max_tokens: int | None = None

    def run(self, user_prompt: str) -> str:
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return self.client.chat(messages, temperature=self.temperature, max_tokens=self.max_tokens)


RECOVERABILITY_SYSTEM = """You are Agent 1 in a two-agent Solidity inline assembly recovery pipeline.

Your only job is to analyze an S-SEIR compact assembly semantic model and add recoverability / projection judgement.

You must not rewrite Solidity source code.
You must not omit any function from the input.
You must not truncate content.
You must output strict JSON only. Do not wrap the JSON in markdown.

For each function and each semantic overlay, judge whether the semantics can be rendered as high-level Solidity-like code.
Use these enums:

recoverability:
- fully_recoverable
- partially_recoverable
- preserve_assembly

output_kind:
- solidity_statement
- solidity_expression
- solidity_block
- low_level_call_statement
- assembly_preserved
- semantic_comment
- unresolved

Add an object named llm_projection_judgement to each function. It must include:
- function_recoverability
- rationale
- assembly_block_decisions
- overlay_projection_policies
- recommended_recovery_plan
- preserve_assembly_reasons

Each overlay_projection_policies item must include:
- target_overlay
- overlay_kind
- exact_solidity_equivalent
- output_kind
- reason
- solidity_candidate
- risks

Important principles:
- MappingRead, MappingWrite, StateVariableRead, StateVariableWrite, and known EventEmit are usually recoverable if their attrs contain solidity_like or equivalent high-level facts.
- EventEmit is recoverable as emit only when attrs.event is a known event name and is not unknownEvent. If attrs.event is unknownEvent or null, mark it assembly_preserved or semantic_comment; do not infer an event name from topic similarity or Solidity declarations.
- RequireOverlay is recoverable only when its condition is expressible as valid Solidity high-level code.
- Never mark a RequireOverlay as exact Solidity if its condition or solidity_candidate contains raw Yul/EVM builtins such as staticcall(...), call(...), delegatecall(...), callcode(...), gas(), mload(...), mstore(...), sload(...), sstore(...), log0-log4(...), keccak256(memory_pointer, size), or raw memory pointer variables.
- If a RequireOverlay wraps a call/staticcall that is also represented by a PrecompileCall overlay, treat the RequireOverlay as covered_by_precompile_or_preserve. The Solidity candidate should either be an empty string, a semantic comment, or a valid Solidity check around the high-level precompile result. Do not output raw staticcall/call syntax.
- PrecompileCall may be recoverable when attrs.solidity_like is explicit and valid Solidity; otherwise prefer low_level_call_statement or assembly_preserved.
- ExpressionNormalization overlays are helper facts. They must not by themselves force source recovery. Do not mark ExpressionNormalization as exact Solidity when it contains or depends on mload, mstore, sload, sstore, .slot, memory pointer variables, assembly-local variables, or keccak256 over raw memory.
- MemoryWrite, MemoryRead, MemoryHash, and raw slot calculation are not source recovery statements. They are evidence for overlays only.
- If assembly_block_decisions marks a block as preserve_assembly, then overlays whose only purpose is ExpressionNormalization, MemoryWrite, MemoryRead, MemoryHash, or raw slot calculation inside that block must not be projected as Solidity statements.
- If a Solidity candidate depends on a Yul 'let' variable from the assembly block, mark it non-recoverable unless the candidate also declares and assigns a valid Solidity variable from high-level facts.
- If high-level overlays are available for MappingRead, MappingWrite, StateVariableRead, StateVariableWrite, RequireOverlay, or known EventEmit, prefer those overlays and ignore the low-level memory/slot construction overlays that merely explain how the high-level fact was derived.
- Do not recommend preserving an assembly block that still contains sstore/log/call/revert and also emitting the same high-level MappingWrite, StateVariableWrite, EventEmit, or RequireOverlay outside that assembly block. That duplicates side effects. Either recover the side effect and remove the low-level assembly side effect, or preserve the assembly side effect and do not emit the high-level duplicate.
- Raw low-level call, delegatecall, unknown calldata/memory, raw revert payload, unresolved path-conditioned storage, and unknown events may require preserving assembly or adding a semantic comment.
- Do not invent contract types, external ABI names, custom errors, event names, revert strings, or ABI types not supported by input facts.
- If exact Solidity equivalence is uncertain, set exact_solidity_equivalent=false and explain why.
"""


SOURCE_RECOVERY_SYSTEM = """You are Agent 2 in a two-agent Solidity inline assembly recovery pipeline.

Your job is to produce one recovered Solidity function body as source code.

Inputs:
1. The original full Solidity source file as context.
2. The target original function source.
3. Agent 1's augmented S-SEIR compact assembly semantic model for this target function only.

Rules:
- Output exactly one complete Solidity function definition for the target function only.
- Do not use markdown fences.
- Do not output explanations.
- Preserve the target function signature, visibility, modifiers, returns, and non-assembly Solidity logic.
- Do not output pragma, imports, contract declarations, state variables, other functions, or any text outside the target function.
- Replace only inline assembly regions that Agent 1 judges recoverable.
- If Agent 1 marks a region as preserve_assembly, keep that assembly source unchanged.
- If a region is partially recoverable, recover only the safe high-level parts and preserve unsafe low-level assembly when needed.
- Only use overlay_projection_policies with exact_solidity_equivalent=true and output_kind in {solidity_statement, solidity_expression, solidity_block} to generate high-level Solidity.
- Do not generate Solidity code from overlay_projection_policies whose output_kind is assembly_preserved, low_level_call_statement, semantic_comment, or unresolved.
- Do not generate Solidity code directly from ExpressionNormalization overlays. They are helper facts only. Use MappingRead, MappingWrite, StateVariableRead, StateVariableWrite, RequireOverlay, known EventEmit, and explicitly safe PrecompileCall overlays instead.
- Do not generate Solidity statements for low-level memory/slot construction. Drop mstore/mload/keccak256 raw-memory helper steps when their high-level MappingRead/MappingWrite/StateVariableRead/StateVariableWrite overlay already exists.
- If a partly recovered high-level statement would require reading a Yul 'let' variable outside assembly, do not emit that high-level statement. Keep the relevant assembly block unchanged.
- Do not duplicate side effects. If you keep an assembly block that still contains sstore, do not also emit the corresponding high-level assignment outside assembly. If you keep an assembly block that still contains log0-log4, do not also emit the corresponding high-level event outside assembly. If you keep an assembly block that still contains revert, do not also emit the corresponding require outside assembly.
- Do not invent unknown event names, custom errors, external contract interfaces, or ABI types.
- Use Solidity-like code that preserves the semantics described by S-SEIR overlays.
- Do not emit raw Yul/EVM builtins as Solidity expressions or statements outside an assembly block. Forbidden outside assembly: staticcall(...), call(...), delegatecall(...), callcode(...), gas(), mload(...), mstore(...), sload(...), sstore(...), log0-log4(...), and raw memory pointer operations.
- Also forbidden outside assembly: shl(...), shr(...), sar(...), byte(...), pop(...), add(...), and .slot.
- If Agent 1 provides both a PrecompileCall high-level candidate and a RequireOverlay around the original staticcall, use the high-level PrecompileCall candidate and do not also emit the raw staticcall require.
- Do not invent revert strings. Prefer bare require(condition) or preserve assembly unless the input explicitly provides a reason string.
- Unknown events must not be renamed to a known event. Preserve the corresponding assembly log or add a semantic comment if exact recovery is unsafe.
- Where exact high-level Solidity is unsafe, keep assembly or add a short Solidity comment before preserved assembly.
- Final self-check before answering: outside assembly blocks, the function must not contain mload, mstore, sload, sstore, staticcall, delegatecall, callcode, gas(), shl, shr, sar, byte, pop, log0-log4, add(...), .slot, or any Yul 'let' variable from the original assembly. If the self-check fails, return the original target function with unsafe assembly preserved.
- Return one complete compilable-looking Solidity function definition.
"""


REPAIR_SYSTEM = """You are Agent 3 in a Solidity inline assembly recovery pipeline.

Your job is to repair one recovered Solidity function that failed deterministic validation.

Inputs:
1. The original full Solidity source file as context.
2. The original target function.
3. The failed recovered target function.
4. Validation issues produced by a deterministic checker.
5. Agent 1's augmented S-SEIR compact semantic model for this target function only.

Output exactly one complete Solidity function definition for the target function only.
Do not use markdown fences.
Do not output explanations.

Repair rules:
- Fix every validation issue.
- Do not emit raw Yul/EVM builtins outside assembly: mload, mstore, sload, sstore, staticcall, call, delegatecall, callcode, gas, shl, shr, sar, byte, pop, log0-log4, add(...), or .slot.
- Do not reference Yul 'let' variables outside assembly.
- Do not duplicate side effects. If a preserved assembly block still contains sstore, do not also emit the equivalent high-level assignment outside that assembly. If preserved assembly still contains log0-log4, do not also emit the equivalent event outside that assembly.
- If a high-level MappingWrite/StateVariableWrite/EventEmit is used to replace an assembly side effect, remove or rewrite the preserved assembly so the original sstore/log side effect is not executed again.
- If you cannot safely remove the low-level assembly side effect, prefer preserving the original assembly and do not emit the high-level duplicate.
- If the only safe repair is to return the original target function unchanged, do so.
"""


def build_recoverability_prompt(semantic_text: str) -> str:
    return (
        "Analyze the following S-SEIR compact assembly semantic model. "
        "It contains exactly one function. Return the same one-function semantic model augmented with llm_projection_judgement.\n\n"
        "S-SEIR_COMPACT_JSON_BEGIN\n"
        f"{semantic_text}\n"
        "S-SEIR_COMPACT_JSON_END\n"
    )


def build_source_recovery_prompt(source_text: str, target_function_text: str, augmented_semantic_text: str) -> str:
    return (
        "Recover the inline assembly regions in the target Solidity function using Agent 1's augmented semantic model.\n"
        "Return only the recovered target function definition.\n\n"
        "ORIGINAL_SOLIDITY_SOURCE_BEGIN\n"
        f"{source_text}\n"
        "ORIGINAL_SOLIDITY_SOURCE_END\n\n"
        "TARGET_FUNCTION_SOURCE_BEGIN\n"
        f"{target_function_text}\n"
        "TARGET_FUNCTION_SOURCE_END\n\n"
        "AUGMENTED_S_SEIR_SEMANTIC_MODEL_BEGIN\n"
        f"{augmented_semantic_text}\n"
        "AUGMENTED_S_SEIR_SEMANTIC_MODEL_END\n"
    )


def build_repair_prompt(
    source_text: str,
    target_function_text: str,
    failed_function_text: str,
    validation_issues: list[str],
    augmented_semantic_text: str,
) -> str:
    return (
        "Repair the failed recovered Solidity function. Return only the repaired target function definition.\n\n"
        "ORIGINAL_SOLIDITY_SOURCE_BEGIN\n"
        f"{source_text}\n"
        "ORIGINAL_SOLIDITY_SOURCE_END\n\n"
        "ORIGINAL_TARGET_FUNCTION_BEGIN\n"
        f"{target_function_text}\n"
        "ORIGINAL_TARGET_FUNCTION_END\n\n"
        "FAILED_RECOVERED_FUNCTION_BEGIN\n"
        f"{failed_function_text}\n"
        "FAILED_RECOVERED_FUNCTION_END\n\n"
        "VALIDATION_ISSUES_BEGIN\n"
        f"{json.dumps(validation_issues, indent=2, ensure_ascii=False)}\n"
        "VALIDATION_ISSUES_END\n\n"
        "AUGMENTED_S_SEIR_SEMANTIC_MODEL_BEGIN\n"
        f"{augmented_semantic_text}\n"
        "AUGMENTED_S_SEIR_SEMANTIC_MODEL_END\n"
    )


def run_pipeline(args: argparse.Namespace) -> None:
    source_path = args.source.resolve()
    semantic_path = args.semantic_input.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "raw_agent_responses"
    raw_dir.mkdir(parents=True, exist_ok=True)
    validation_dir = output_dir / "validation_reports"
    validation_dir.mkdir(parents=True, exist_ok=True)

    source_text = read_text(source_path)
    semantic_text = read_text(semantic_path)
    semantic_payload = json.loads(semantic_text)
    functions = semantic_payload.get("functions")
    if not isinstance(functions, list) or not functions:
        raise ValueError(f"{semantic_path} does not contain a non-empty functions array")

    client = LocalOpenAIClient(
        base_url=args.base_url,
        model=args.model,
        api_key=args.api_key,
        timeout=args.timeout,
    )
    recoverability_agent = Agent(
        name="recoverability_agent",
        system_prompt=RECOVERABILITY_SYSTEM,
        client=client,
        temperature=args.agent1_temperature,
        max_tokens=args.agent1_max_tokens,
    )
    source_recovery_agent = Agent(
        name="source_recovery_agent",
        system_prompt=SOURCE_RECOVERY_SYSTEM,
        client=client,
        temperature=args.agent2_temperature,
        max_tokens=args.agent2_max_tokens,
    )
    repair_agent = Agent(
        name="repair_agent",
        system_prompt=REPAIR_SYSTEM,
        client=client,
        temperature=args.repair_temperature,
        max_tokens=args.repair_max_tokens,
    )

    augmented_functions: list[Json] = []
    replacements: list[tuple[int, int, str]] = []

    for index, function_item in enumerate(functions, start=1):
        signature = str(function_item.get("signature") or function_item.get("function_id") or f"function_{index}")
        function_name = safe_name(f"{index:02d}_{signature}")
        function_payload = one_function_payload(semantic_payload, function_item)
        function_semantic_text = json.dumps(function_payload, indent=2, ensure_ascii=False)
        function_source = function_item.get("function_source") if isinstance(function_item.get("function_source"), dict) else {}
        function_src = str(function_source.get("src", ""))
        function_text = str(function_source.get("text", ""))
        start, end = parse_src_range(function_src)
        if not function_text and end > start:
            function_text = source_text[start:end]
        if not function_text:
            raise ValueError(f"Function {signature} has no function_source.text and no valid src range")

        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Function {index}/{len(functions)} Agent 1 started: {signature}", file=sys.stderr)
        agent1_raw = recoverability_agent.run(build_recoverability_prompt(function_semantic_text))
        agent1_raw_path = raw_dir / f"{function_name}.agent1_recoverability.raw.txt"
        write_text(agent1_raw_path, agent1_raw)

        agent1_json = normalize_agent1_json(extract_json_object(agent1_raw), function_payload, function_item)
        if not agent1_json.get("functions"):
            raise ValueError(f"Agent 1 returned no function for {signature}")
        augmented_function = agent1_json["functions"][0]
        augmented_functions.append(augmented_function)
        augmented_function_text = json.dumps(one_function_payload(semantic_payload, augmented_function), indent=2, ensure_ascii=False)

        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Function {index}/{len(functions)} Agent 2 started: {signature}", file=sys.stderr)
        agent2_raw = source_recovery_agent.run(build_source_recovery_prompt(source_text, function_text, augmented_function_text))
        agent2_raw_path = raw_dir / f"{function_name}.agent2_source_recovery.raw.txt"
        write_text(agent2_raw_path, agent2_raw)

        recovered_function = clean_solidity_response(agent2_raw)
        validation_issues = validate_recovered_function(recovered_function, function_item)
        validation_issues.extend(validate_effective_recovery(recovered_function, function_text, augmented_function))
        repair_attempts: list[dict[str, Any]] = []
        for attempt in range(1, args.repair_attempts + 1):
            if not validation_issues:
                break
            print(
                f"Validation failed for {signature}; repair attempt {attempt}/{args.repair_attempts}. Issues: {', '.join(validation_issues)}",
                file=sys.stderr,
            )
            repair_raw = repair_agent.run(
                build_repair_prompt(
                    source_text,
                    function_text,
                    recovered_function,
                    validation_issues,
                    augmented_function_text,
                )
            )
            repair_raw_path = raw_dir / f"{function_name}.agent3_repair_attempt_{attempt}.raw.txt"
            write_text(repair_raw_path, repair_raw)
            candidate = clean_solidity_response(repair_raw)
            candidate_issues = validate_recovered_function(candidate, function_item)
            candidate_issues.extend(validate_effective_recovery(candidate, function_text, augmented_function))
            repair_attempts.append(
                {
                    "attempt": attempt,
                    "raw_response": str(repair_raw_path),
                    "issues_before": validation_issues,
                    "issues_after": candidate_issues,
                }
            )
            recovered_function = candidate
            validation_issues = candidate_issues
        recovered_function_path = output_dir / "recovered_functions" / f"{function_name}.sol"
        write_text(recovered_function_path, recovered_function)
        final_function = recovered_function
        fallback_used = False
        if validation_issues:
            fallback_used = True
            final_function = function_text.rstrip() + "\n"
            fallback_path = output_dir / "recovered_functions" / f"{function_name}.fallback_original.sol"
            write_text(fallback_path, final_function)
            print(
                f"Validation failed for {signature}; using original function. Issues: {', '.join(validation_issues)}",
                file=sys.stderr,
            )
        validation_report = {
            "function": signature,
            "recovered_function": str(recovered_function_path),
            "fallback_used": fallback_used,
            "issues": validation_issues,
            "repair_attempts": repair_attempts,
            "rule": "If recovered function leaks Yul-only builtins or assembly-local variables outside assembly, use the original target function for final merge.",
        }
        write_json(validation_dir / f"{function_name}.validation.json", validation_report)
        replacements.append((start, end, final_function))
        print(f"Wrote {recovered_function_path}", file=sys.stderr)

    augmented = {k: v for k, v in semantic_payload.items() if k != "functions"}
    augmented["functions"] = augmented_functions
    recoverability_output = args.recoverability_output or (output_dir / f"{source_path.stem}.assembly_recoverability.json")
    write_json(recoverability_output, augmented)
    print(f"Wrote {recoverability_output}", file=sys.stderr)

    recovered_source = merge_function_replacements(source_text, replacements)
    recovered_output = args.recovered_output or (output_dir / f"{source_path.stem}.recovered.sol")
    write_text(recovered_output, recovered_source)
    print(f"Wrote {recovered_output}", file=sys.stderr)
    print(f"Wrote raw responses to {raw_dir}", file=sys.stderr)
    print(f"Wrote validation reports to {validation_dir}", file=sys.stderr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a local two-agent LLM pipeline to judge and recover Solidity inline assembly from S-SEIR compact output."
    )
    parser.add_argument("--source", type=Path, required=True, help="Original Solidity source file.")
    parser.add_argument("--semantic-input", type=Path, required=True, help="S-SEIR compact JSON input.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory for all generated outputs.")
    parser.add_argument("--recoverability-output", type=Path, help="Agent 1 augmented semantic JSON output.")
    parser.add_argument("--recovered-output", type=Path, help="Agent 2 recovered Solidity source output.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/v1", help="OpenAI-compatible local LLM base URL.")
    parser.add_argument("--model", help="Model id. If omitted, the script queries /v1/models and uses the first model.")
    parser.add_argument("--api-key", default="", help="Optional API key for the local OpenAI-compatible server.")
    parser.add_argument("--timeout", type=int, default=600, help="HTTP timeout in seconds for each LLM request.")
    parser.add_argument("--agent1-temperature", type=float, default=0.0)
    parser.add_argument("--agent2-temperature", type=float, default=0.0)
    parser.add_argument("--repair-temperature", type=float, default=0.0)
    parser.add_argument("--agent1-max-tokens", type=int, help="Optional max_tokens for Agent 1. Unset means no script-side output cap.")
    parser.add_argument("--agent2-max-tokens", type=int, help="Optional max_tokens for Agent 2. Unset means no script-side output cap.")
    parser.add_argument("--repair-max-tokens", type=int, help="Optional max_tokens for repair agent. Unset means no script-side output cap.")
    parser.add_argument("--repair-attempts", type=int, default=2, help="How many repair attempts to run before falling back to the original function.")
    return parser.parse_args()


def main() -> None:
    run_pipeline(parse_args())


if __name__ == "__main__":
    main()
