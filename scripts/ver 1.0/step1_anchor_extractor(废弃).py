from slither import Slither
from pathlib import Path
import json

CORE_ENTRYPOINTS = {
    "transfer(address,uint256)",
    "transferFrom(address,address,uint256)",
    "approve(address,uint256)",
}

SECURITY_KEYWORDS = {
    "fee", "tax", "blacklist", "whitelist",
    "pair", "router", "controller",
    "owner", "admin", "tradingOpen",
    "maxTx", "maxWallet", "exclude", "excluded"
}

LOW_LEVEL_KEYWORDS = {
    "assembly", "sload", "sstore", "mstore",
    "calldataload", "delegatecall", "staticcall"
}


def name_of(x):
    return getattr(x, "name", str(x))


def full_name_of(x):
    return getattr(x, "full_name", getattr(x, "name", str(x)))


def to_list(x):
    if x is None:
        return []
    return list(x)


def node_text(node):
    expr = str(node.expression) if getattr(node, "expression", None) else ""
    irs = " ".join(str(ir) for ir in to_list(getattr(node, "irs", [])))
    return f"{expr} {irs}".strip()


def variable_names(xs):
    return sorted({name_of(x) for x in xs})


def collect_reachable_functions(entry_func):
    visited = set()
    stack = [entry_func]
    result = []

    while stack:
        f = stack.pop()
        key = getattr(f, "canonical_name", full_name_of(f))

        if key in visited:
            continue

        visited.add(key)
        result.append(f)

        for callee in to_list(getattr(f, "internal_calls", [])):
            if hasattr(callee, "nodes") and getattr(callee, "is_implemented", False):
                stack.append(callee)

        for modifier in to_list(getattr(f, "modifiers", [])):
            if hasattr(modifier, "nodes"):
                stack.append(modifier)

    return result


def detect_anchors(node, function):
    anchors = []
    text = node_text(node)
    lowered = text.lower()

    state_written = to_list(getattr(node, "state_variables_written", []))
    state_read = to_list(getattr(node, "state_variables_read", []))
    vars_read = to_list(getattr(node, "variables_read", []))
    vars_written = to_list(getattr(node, "variables_written", []))

    base = {
        "function": full_name_of(function),
        "node_id": getattr(node, "node_id", None),
        "node_type": str(getattr(node, "type", "")),
        "statement": text,
        "state_variables_read": variable_names(state_read),
        "state_variables_written": variable_names(state_written),
        "variables_read": variable_names(vars_read),
        "variables_written": variable_names(vars_written),
        "source_mapping": str(getattr(node, "source_mapping", "")),
    }

    # 1. 状态写入
    if state_written:
        item = dict(base)
        item["type"] = "STATE_WRITE"
        item["reason"] = "persistent state update; may affect balance, allowance, policy state, or supply"
        anchors.append(item)

    # 2. require / revert / assert / if
    if (
        "require" in lowered
        or "revert" in lowered
        or "assert" in lowered
        or "if" in str(getattr(node, "type", "")).lower()
    ):
        item = dict(base)
        item["type"] = "CONDITION_OR_REVERT"
        item["reason"] = "condition may affect transfer success, fee logic, access control, or external call behavior"
        anchors.append(item)

    # 3. ERC-20 事件
    if "transfer" in text or "approval" in text:
        # 注意：这里第一版会有误报，比如函数名 transfer。
        # 后续可以改成基于 SlithIR EventCall 精确识别。
        if "emit" in lowered or "eventcall" in lowered or "event" in lowered:
            item = dict(base)
            item["type"] = "ERC20_EVENT"
            item["reason"] = "Transfer/Approval event is security-relevant for ERC-20 audit visibility"
            anchors.append(item)

    # 4. 外部调用 / 低层调用
    high_level_calls = to_list(getattr(node, "high_level_calls", []))
    low_level_calls = to_list(getattr(node, "low_level_calls", []))

    if high_level_calls or low_level_calls or ".call" in lowered or "delegatecall" in lowered or "staticcall" in lowered:
        item = dict(base)
        item["type"] = "EXTERNAL_OR_LOW_LEVEL_CALL"
        item["high_level_calls"] = [str(c) for c in high_level_calls]
        item["low_level_calls"] = [str(c) for c in low_level_calls]
        item["reason"] = "external or low-level call may affect transfer success, routing, controller checks, or value movement"
        anchors.append(item)

    # 5. assembly / sload / sstore
    if any(k in lowered for k in LOW_LEVEL_KEYWORDS):
        item = dict(base)
        item["type"] = "ASSEMBLY_OR_LOW_LEVEL_SEMANTICS"
        item["reason"] = "assembly or low-level operation may bypass normal Solidity-level analysis"
        anchors.append(item)

    # 6. ERC-20 策略变量读写：fee/tax/blacklist/pair/router/controller 等
    matched = []
    for v in variable_names(state_read + state_written + vars_read + vars_written):
        for k in SECURITY_KEYWORDS:
            if k.lower() in v.lower():
                matched.append(v)

    for k in SECURITY_KEYWORDS:
        if k.lower() in lowered:
            matched.append(k)

    if matched:
        item = dict(base)
        item["type"] = "ERC20_POLICY_RELATED"
        item["matched_terms"] = sorted(set(matched))
        item["reason"] = "possible fee/tax/blacklist/router/controller/owner-related logic"
        anchors.append(item)

    return anchors


def main(sol_file, output_file):
    slither = Slither(sol_file)

    report = {
        "source": sol_file,
        "contracts": []
    }

    for contract in slither.contracts_derived:
        contract_report = {
            "contract": contract.name,
            "entrypoints": []
        }

        for function in contract.functions_declared:
            if not getattr(function, "is_implemented", False):
                continue

            if function.full_name not in CORE_ENTRYPOINTS:
                continue

            reachable_functions = collect_reachable_functions(function)

            entry_report = {
                "entrypoint": function.full_name,
                "reachable_functions": [full_name_of(f) for f in reachable_functions],
                "anchors": []
            }

            for rf in reachable_functions:
                for node in to_list(getattr(rf, "nodes", [])):
                    entry_report["anchors"].extend(detect_anchors(node, rf))

            contract_report["entrypoints"].append(entry_report)

        report["contracts"].append(contract_report)

    Path(output_file).parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"[OK] saved to {output_file}")


if __name__ == "__main__":
    main(
        sol_file="TOKENS/0x0068e979c72bbb31373ea8cb47eaefb44978566e/Token.sol",
        output_file="outputs/step1_security_anchors.json"
    )