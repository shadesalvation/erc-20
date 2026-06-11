# scripts/step1_anchor_extractor.py

from slither import Slither
import json
from pathlib import Path

ERC20_ENTRYPOINTS = {
    "transfer(address,uint256)",
    "transferFrom(address,address,uint256)",
    "approve(address,uint256)",
    "balanceOf(address)",
    "allowance(address,address)",
    "totalSupply()",
}

CORE_ENTRYPOINTS = {
    "transfer(address,uint256)",
    "transferFrom(address,address,uint256)",
    "approve(address,uint256)",
}


def safe_name(x):
    return getattr(x, "name", str(x))


def function_summary(function):
    return {
        "name": function.name,
        "full_name": function.full_name,
        "visibility": function.visibility,
        "modifiers": [m.name for m in function.modifiers],
        "state_variables_read": [v.name for v in function.state_variables_read],
        "state_variables_written": [v.name for v in function.state_variables_written],
        "internal_calls": [safe_name(c) for c in function.internal_calls],
        "external_calls": [str(c) for c in function.high_level_calls],
    }


def main(sol_file: str, output_file: str):
    slither = Slither(sol_file)

    report = {
        "source": sol_file,
        "contracts": []
    }

    for contract in slither.contracts_derived:
        contract_report = {
            "contract": contract.name,
            "erc20_entrypoints": [],
            "core_entrypoints": []
        }

        for function in contract.functions_declared:
            if not function.is_implemented:
                continue

            if function.full_name in ERC20_ENTRYPOINTS:
                item = function_summary(function)
                contract_report["erc20_entrypoints"].append(item)

                if function.full_name in CORE_ENTRYPOINTS:
                    contract_report["core_entrypoints"].append(item)

        report["contracts"].append(contract_report)

    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"[OK] report saved to {output_file}")


if __name__ == "__main__":
    main(
        sol_file="TOKENS/0x0068e979c72bbb31373ea8cb47eaefb44978566e/Token.sol",
        output_file="outputs/step1_entrypoints.json"
    )