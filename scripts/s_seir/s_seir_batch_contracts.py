#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import urllib.request
from dataclasses import asdict, is_dataclass
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sys

_SSEIR_ROOT = Path(__file__).resolve().parents[1]
for _sseir_path in (_SSEIR_ROOT / "legacy_yul", _SSEIR_ROOT / "s_seir"):
    _sseir_text = str(_sseir_path)
    if _sseir_text not in sys.path:
        sys.path.insert(0, _sseir_text)

from assembly_ast_cfg import compile_source_ast, discover_solc
from s_seir_llm_assembly_export import function_has_assembly
from s_seir_pipeline import build_sseir
from s_seir_solidity_like_export import write_solidity_like_text


Json = dict[str, Any]


@dataclass
class CompilePreparation:
    source: Path
    solc_bin: str
    include_paths: list[Path] = field(default_factory=list)
    remappings: list[str] = field(default_factory=list)
    installed_dependencies: list[Json] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> Json:
        return {
            "source": str(self.source),
            "solc_bin": self.solc_bin,
            "include_paths": [str(path) for path in self.include_paths],
            "remappings": self.remappings,
            "installed_dependencies": self.installed_dependencies,
            "notes": self.notes,
        }


def json_ready(value: Any) -> Any:
    if is_dataclass(value):
        return json_ready(asdict(value))
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    return value


def iter_ast(value: Any):
    if isinstance(value, dict):
        if isinstance(value.get("nodeType"), str):
            yield value
        for item in value.values():
            yield from iter_ast(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_ast(item)


def contract_infos(source: Path, solc_bin: str) -> dict[str, Json]:
    ast = compile_source_ast(source, solc_bin)
    infos: dict[str, Json] = {}
    for node in iter_ast(ast):
        if node.get("nodeType") != "ContractDefinition":
            continue
        name = str(node.get("name") or "")
        if not name:
            continue
        infos[name] = {
            "name": name,
            "contractKind": node.get("contractKind"),
            "abstract": bool(node.get("abstract", False)),
            "fullyImplemented": node.get("fullyImplemented"),
            "base_contracts": base_contract_names(node),
            "src": node.get("src"),
        }
    return infos


def base_contract_names(contract_node: Json) -> list[str]:
    names: list[str] = []
    for base in contract_node.get("baseContracts") or []:
        base_name = base.get("baseName") if isinstance(base, dict) else None
        name = None
        if isinstance(base_name, dict):
            name = base_name.get("name")
            if not name:
                name = base_name.get("namePath")
            if not name:
                type_string = (base_name.get("typeDescriptions") or {}).get("typeString")
                if isinstance(type_string, str) and type_string.startswith("contract "):
                    name = type_string.split()[-1]
        if name:
            names.append(str(name).split(".")[-1])
    return names


def selected_deployed_contracts(infos: dict[str, Json], include_abstract: bool = False) -> tuple[set[str], list[Json]]:
    selected: set[str] = set()
    skipped: list[Json] = []
    for name, info in infos.items():
        reason = None
        if info.get("contractKind") != "contract":
            reason = f"contractKind={info.get('contractKind')}"
        elif info.get("abstract") and not include_abstract:
            reason = "abstract_contract"
        if reason:
            skipped.append({**info, "skip_reason": reason})
            continue
        selected.add(name)
    return selected, skipped


def inherited_analysis_contracts(infos: dict[str, Json], selected: set[str]) -> set[str]:
    """Contracts whose implementation is part of a selected deployed contract."""

    out = set(selected)
    stack = list(selected)
    while stack:
        name = stack.pop()
        for base in infos.get(name, {}).get("base_contracts") or []:
            if base in out:
                continue
            out.add(base)
            stack.append(base)
    return out


def source_id(source: Path) -> str:
    parts = source.parts
    address = next((part for part in reversed(parts) if part.startswith("0x") and len(part) >= 10), None)
    if address:
        try:
            rel = source.relative_to(source.parents[len(parts) - parts.index(address) - 1])
        except Exception:
            rel = Path(source.name)
        suffix = "__".join(rel.with_suffix("").parts[1:])
        return address if not suffix else f"{address}__{suffix}"
    parent = source.parent.name
    return f"{parent}__{source.stem}" if parent else source.stem


def source_has_imports(source: Path) -> bool:
    try:
        text = source.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    return "\nimport " in text or text.lstrip().startswith("import ")


def import_paths(source: Path) -> list[str]:
    try:
        text = source.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    out = []
    pattern = re.compile(r"import\s+(?:[^\"']*?\s+from\s+)?[\"']([^\"']+)[\"']", re.S)
    for match in pattern.finditer(text):
        out.append(match.group(1))
    return out


def pragma_constraints(source: Path) -> list[str]:
    try:
        text = source.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    return [match.group(1).strip() for match in re.finditer(r"pragma\s+solidity\s+([^;]+);", text)]


def choose_solc_version(source: Path, fallback_solc: str) -> str | None:
    constraints = pragma_constraints(source)
    joined = " ".join(constraints)
    exacts = re.findall(r"(?:^|\s)=\s*(\d+\.\d+\.\d+)", joined)
    if exacts:
        return max(exacts, key=version_tuple)
    carets = re.findall(r"\^\s*(\d+\.\d+\.\d+)", joined)
    if carets:
        version = max(carets, key=version_tuple)
        # For wide ^0.8.x constraints, use the already configured compiler
        # unless the source explicitly requires a newer minor.
        if version.startswith("0.8."):
            try:
                patch = int(version.split(".")[2])
            except ValueError:
                patch = 0
            if patch <= 27:
                return None
        return version
    lowers = re.findall(r">=\s*(\d+\.\d+\.\d+)", joined)
    if lowers:
        version = max(lowers, key=version_tuple)
        if version.startswith("0.8."):
            try:
                patch = int(version.split(".")[2])
            except ValueError:
                patch = 0
            if patch > 27:
                return version
        return None
    return None


def version_tuple(version: str) -> tuple[int, int, int]:
    try:
        major, minor, patch = version.split(".")
        return int(major), int(minor), int(patch)
    except Exception:
        return 0, 0, 0


def available_solc(version: str) -> Path | None:
    candidates = [
        Path.home() / ".solc-select" / "artifacts" / f"solc-{version}" / f"solc-{version}",
        Path.home() / ".solcx" / f"solc-v{version}",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def ensure_solc_version(version: str, cache_dir: Path) -> Path:
    existing = available_solc(version)
    if existing:
        return existing
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"solc-{version}"
    if target.is_file():
        return target.resolve()
    list_url = "https://binaries.soliditylang.org/linux-amd64/list.json"
    list_request = urllib.request.Request(list_url, headers={"User-Agent": "s-seir-batch/1.0"})
    with urllib.request.urlopen(list_request, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8"))
    filename = None
    for build in data.get("builds", []):
        if build.get("version") == version:
            filename = build.get("path")
            break
    if not filename:
        raise RuntimeError(f"solc version {version} not found in Solidity binary list")
    bin_url = f"https://binaries.soliditylang.org/linux-amd64/{filename}"
    bin_request = urllib.request.Request(bin_url, headers={"User-Agent": "s-seir-batch/1.0"})
    tmp = target.with_suffix(".download")
    with urllib.request.urlopen(bin_request, timeout=120) as response, tmp.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    tmp.chmod(0o755)
    tmp.replace(target)
    return target.resolve()


DEPENDENCY_REPOS = {
    "@openzeppelin/contracts": "https://github.com/OpenZeppelin/openzeppelin-contracts.git",
    "@openzeppelin/contracts-upgradeable": "https://github.com/OpenZeppelin/openzeppelin-contracts-upgradeable.git",
    "solady": "https://github.com/Vectorized/solady.git",
}


def dependency_roots(imports: list[str]) -> set[str]:
    roots: set[str] = set()
    for item in imports:
        if item.startswith("@openzeppelin/contracts-upgradeable"):
            roots.add("@openzeppelin/contracts-upgradeable")
        elif item.startswith("@openzeppelin/contracts"):
            roots.add("@openzeppelin/contracts")
        elif item.startswith("solady/"):
            roots.add("solady")
    return roots


def install_dependency(root: str, deps_dir: Path) -> Json:
    repo = DEPENDENCY_REPOS[root]
    if root.startswith("@"):
        target = deps_dir / "node_modules" / Path(root)
    else:
        target = deps_dir / "node_modules" / root
    if target.exists():
        normalize_dependency_layout(root, target)
        return {"dependency": root, "path": str(target), "action": "reused"}
    target.parent.mkdir(parents=True, exist_ok=True)
    command = ["git", "clone", "--depth", "1", repo, str(target)]
    subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    normalize_dependency_layout(root, target)
    return {"dependency": root, "path": str(target), "action": "git_clone", "repo": repo}


def normalize_dependency_layout(root: str, target: Path) -> None:
    # GitHub repositories for OpenZeppelin keep package contents under
    # ./contracts, while Solidity imports expect the npm package layout:
    # @openzeppelin/contracts/token/...  not  @openzeppelin/contracts/contracts/token/...
    if not root.startswith("@openzeppelin/"):
        return
    package_root = target / "contracts"
    if not package_root.is_dir():
        return
    for child in package_root.iterdir():
        link = target / child.name
        if link.exists():
            continue
        try:
            link.symlink_to(child, target_is_directory=child.is_dir())
        except OSError:
            if child.is_dir():
                shutil.copytree(child, link, dirs_exist_ok=True)
            else:
                shutil.copy2(child, link)


def sample_root_for(source: Path) -> Path:
    for parent in [source.parent, *source.parents]:
        if parent.name.startswith("0x") and len(parent.name) >= 10:
            return parent
    return source.parent


def local_dependency_exists(sample_root: Path, root: str) -> bool:
    candidates: list[Path]
    if root == "@openzeppelin/contracts":
        candidates = [
            sample_root / "@openzeppelin" / "contracts",
            sample_root / "node_modules" / "@openzeppelin" / "contracts",
            sample_root / "npm" / "@openzeppelin" / "contracts",
        ]
    elif root == "@openzeppelin/contracts-upgradeable":
        candidates = [
            sample_root / "@openzeppelin" / "contracts-upgradeable",
            sample_root / "node_modules" / "@openzeppelin" / "contracts-upgradeable",
            sample_root / "npm" / "@openzeppelin" / "contracts-upgradeable",
        ]
    elif root == "solady":
        candidates = [
            sample_root / "solady",
            sample_root / "node_modules" / "solady",
        ]
    else:
        candidates = []
    return any(path.exists() for path in candidates)


def local_include_paths(source: Path, deps_dir: Path, imports: list[str]) -> list[Path]:
    sample_root = sample_root_for(source)
    roots = dependency_roots(imports)
    need_global_deps = bool(roots) and any(not local_dependency_exists(sample_root, root) for root in roots)
    paths = [
        source.parent,
        sample_root,
        sample_root / "contracts",
        sample_root / "project" / "contracts",
        sample_root / "node_modules",
        sample_root / "npm",
        sample_root / "lib",
        sample_root / "solady",
    ]
    if need_global_deps:
        paths.append(deps_dir / "node_modules")
    return unique_existing_paths(paths)


def unique_existing_paths(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            resolved = path.resolve()
        except Exception:
            continue
        if not resolved.exists():
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        out.append(resolved)
    return out


def dependency_remappings(deps_dir: Path) -> list[str]:
    # Keep this hook for future explicit remappings, but do not generate
    # absolute remappings for npm-style dependencies. With standard-json, some
    # solc versions turn absolute remap targets into absolute source names and
    # then fail to load them. The normalized node_modules layout plus
    # include/allow paths is more portable across compiler versions.
    return []


def apply_compile_env(include_paths: list[Path], remappings: list[str]) -> tuple[str | None, str | None]:
    previous_paths = os.environ.get("SSEIR_SOLC_INCLUDE_PATHS")
    previous_remappings = os.environ.get("SSEIR_SOLC_REMAPPINGS")
    os.environ["SSEIR_SOLC_INCLUDE_PATHS"] = os.pathsep.join(str(path) for path in include_paths)
    os.environ["SSEIR_SOLC_REMAPPINGS"] = os.pathsep.join(remappings)
    return previous_paths, previous_remappings


def restore_compile_env(previous: tuple[str | None, str | None]) -> None:
    previous_paths, previous_remappings = previous
    if previous_paths is None:
        os.environ.pop("SSEIR_SOLC_INCLUDE_PATHS", None)
    else:
        os.environ["SSEIR_SOLC_INCLUDE_PATHS"] = previous_paths
    if previous_remappings is None:
        os.environ.pop("SSEIR_SOLC_REMAPPINGS", None)
    else:
        os.environ["SSEIR_SOLC_REMAPPINGS"] = previous_remappings


def prepare_compile_environment(source: Path, args: argparse.Namespace, fallback_solc: str) -> CompilePreparation:
    requested_version = choose_solc_version(source, fallback_solc)
    notes: list[str] = []
    if requested_version:
        solc_path = ensure_solc_version(requested_version, args.solc_cache_dir)
        notes.append(f"selected_solc_from_pragma:{requested_version}")
    else:
        solc_path = Path(fallback_solc)
        notes.append("selected_default_solc")

    imports = import_paths(source)
    installed: list[Json] = []
    if not args.no_install_deps:
        for root in sorted(dependency_roots(imports)):
            try:
                installed.append(install_dependency(root, args.deps_dir))
            except Exception as exc:
                installed.append({
                    "dependency": root,
                    "action": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                })
    include_paths = local_include_paths(source, args.deps_dir, imports)
    remappings = dependency_remappings(args.deps_dir)
    return CompilePreparation(
        source=source,
        solc_bin=str(solc_path.resolve()),
        include_paths=include_paths,
        remappings=remappings,
        installed_dependencies=installed,
        notes=notes,
    )


DEPENDENCY_PARTS = {
    "@openzeppelin",
    "openzeppelin",
    "node_modules",
    "npm",
    "solady",
    "lib",
    "libs",
    "library",
    "libraries",
    "interfaces",
}

DEPENDENCY_FILENAMES = {
    "IERC20.sol",
    "IERC20Metadata.sol",
    "IERC165.sol",
    "IUniswapV2Factory.sol",
    "IUniswapV2Router.sol",
    "IUniswapV2Router01.sol",
    "IUniswapV2Router02.sol",
    "ISwapFactory.sol",
    "ISwapRouter.sol",
    "Address.sol",
    "Context.sol",
    "Ownable.sol",
    "OwnableRoles.sol",
    "SafeMath.sol",
    "SafeERC20.sol",
    "Strings.sol",
    "Math.sol",
}


def discover_sources(root: Path, pattern: str, sample_mode: bool = True, max_sources_per_sample: int = 1) -> list[Path]:
    if root.is_file():
        return [root]
    if sample_mode:
        sample_dirs = sorted(path for path in root.iterdir() if path.is_dir())
        if sample_dirs and all(path.name.startswith("0x") for path in sample_dirs[: min(8, len(sample_dirs))]):
            out: list[Path] = []
            for sample_dir in sample_dirs:
                out.extend(select_sample_sources(sample_dir, max_sources_per_sample))
            return out
    return sorted(path for path in root.glob(pattern) if path.is_file())


def select_sample_sources(sample_dir: Path, limit: int = 1) -> list[Path]:
    candidates = [path for path in sample_dir.rglob("*.sol") if path.is_file()]
    if not candidates:
        return []
    ranked = sorted(candidates, key=lambda path: source_candidate_score(sample_dir, path), reverse=True)
    selected = [path for path in ranked if source_candidate_score(sample_dir, path)[0] > -1000]
    return selected[: max(1, limit)]


def source_candidate_score(sample_dir: Path, source: Path) -> tuple[int, str]:
    rel = source.relative_to(sample_dir)
    parts = set(rel.parts[:-1])
    name = source.name
    stem = source.stem
    score = 0
    if any(part in DEPENDENCY_PARTS for part in parts):
        score -= 1000
    if name in DEPENDENCY_FILENAMES:
        score -= 800
    if stem.startswith("I") and len(stem) > 1 and stem[1].isupper():
        score -= 500
    if "interface" in stem.lower() or "factory" in stem.lower() or "router" in stem.lower():
        score -= 300
    if "lib" in stem.lower() or "library" in stem.lower():
        score -= 300
    if len(rel.parts) == 1:
        score += 500
    if len(rel.parts) >= 2 and rel.parts[0] == "contracts":
        score += 150
    if name == "Token.sol":
        score += 300
    if stem.lower() in {"token", "kof", "contract", "main"}:
        score += 100
    try:
        text = source.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        text = ""
    concrete_contracts, abstract_contracts = contract_implementation_counts(text)
    if concrete_contracts:
        score += 700 + 100 * concrete_contracts
    if abstract_contracts and not concrete_contracts:
        score -= 900
    if "assembly" in text:
        score += 250
    if " contract " in text or "\ncontract " in text:
        score += 100
    if " library " in text or "\nlibrary " in text:
        score -= 400
    if " interface " in text or "\ninterface " in text:
        score -= 400
    return score, str(rel)


def contract_implementation_counts(source_text: str) -> tuple[int, int]:
    masked = mask_comments_for_selection(source_text)
    concrete = 0
    abstract = 0
    for match in re.finditer(r"\b(?:(abstract)\s+)?contract\s+([A-Za-z_$][A-Za-z0-9_$]*)\b", masked):
        if match.group(1):
            abstract += 1
        else:
            concrete += 1
    return concrete, abstract


def mask_comments_for_selection(source_text: str) -> str:
    # Keep string contents; this is only a cheap source-selection heuristic.
    source_text = re.sub(r"/\*.*?\*/", " ", source_text, flags=re.S)
    source_text = re.sub(r"//[^\n]*", " ", source_text)
    return source_text


def function_dict(fn: Any) -> Json:
    data = fn.to_dict()
    function_source = getattr(fn, "_sseir_function_source", None)
    assembly_sources = getattr(fn, "_sseir_assembly_sources", None)
    if isinstance(function_source, dict):
        data["function_source"] = json_ready(function_source)
    if isinstance(assembly_sources, list):
        data["assembly_sources"] = json_ready(assembly_sources)
    return data


def batch_payload(
    schema: str,
    input_root: Path,
    output_dir: Path,
    sources: list[Json],
    failures: list[Json],
) -> Json:
    return {
        "schema": schema,
        "input_root": str(input_root),
        "output_dir": str(output_dir),
        "selection_rule": {
            "included_contracts": "ContractDefinition.contractKind == 'contract'",
            "excluded_contracts": ["library", "interface", "abstract contract unless --include-abstract"],
            "assembly_function_rule": "function.source_statements contains at least one yul statement",
        },
        "sources": sources,
        "failures": failures,
    }


def result_dir_for(source: Path, output_dir: Path) -> Path:
    return output_dir / safe_path_name(source_id(source))


def safe_path_name(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_.@+-]+", "_", value).strip("._")
    return sanitized or "source"


def copy_source_snapshot(source: Path, result_dir: Path) -> Json:
    snapshot_dir = result_dir / "source"
    sample_root = sample_root_for(source)
    copied: list[str] = []
    if sample_root.exists() and sample_root.is_dir():
        candidates = [
            path for path in sample_root.rglob("*.sol")
            if path.is_file() and not is_output_or_dependency_snapshot(path)
        ]
        base = sample_root
    else:
        candidates = [source]
        base = source.parent
    for item in candidates:
        try:
            rel = item.relative_to(base)
        except ValueError:
            rel = Path(item.name)
        target = snapshot_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        copied.append(str(rel))
    try:
        entry_source = source.relative_to(base)
    except ValueError:
        entry_source = Path(source.name)
    return {
        "entry_source": str(entry_source),
        "source_root": str(sample_root if sample_root.exists() else source.parent),
        "snapshot_dir": str(snapshot_dir),
        "copied_solidity_files": sorted(copied),
    }


def is_output_or_dependency_snapshot(path: Path) -> bool:
    parts = set(path.parts)
    return bool(parts & {"node_modules", ".git", "artifacts", "cache", "out", "build"})


def process_source(source: Path, args: argparse.Namespace, solc_bin: str, result_dir: Path) -> tuple[Json, Json, list[Any]]:
    sid = source_id(source)
    branch_source = result_dir / "branch_preprocessed" / f"{sid}.sol"
    branch_report = result_dir / "branch_preprocessed" / f"{sid}.txt"
    has_imports = source_has_imports(source)
    branch_preprocess_enabled = not args.no_branch_preprocess
    preparation = prepare_compile_environment(source, args, solc_bin)
    previous_env = apply_compile_env(preparation.include_paths, preparation.remappings)
    try:
        infos = contract_infos(source, preparation.solc_bin)
    except Exception:
        restore_compile_env(previous_env)
        raise

    selected, skipped = selected_deployed_contracts(infos, include_abstract=args.include_abstract)
    if args.contract:
        selected = {name for name in selected if name in set(args.contract)}
        for name, info in infos.items():
            if name not in selected and info not in skipped:
                skipped.append({**info, "skip_reason": "not_in_requested_contracts"})

    try:
        functions = build_sseir(
            source,
            solc_bin=preparation.solc_bin,
            slither_bin=args.slither_bin,
            workdir=args.workdir.resolve(),
            branch_preprocess=branch_preprocess_enabled,
            branch_preprocess_output=branch_source if branch_preprocess_enabled else None,
            branch_report_output=branch_report if branch_preprocess_enabled else None,
        )
        analysis_source = branch_source if branch_preprocess_enabled and branch_source.exists() else source
        analysis_infos = contract_infos(analysis_source, preparation.solc_bin)
    finally:
        restore_compile_env(previous_env)
    analysis_contracts = inherited_analysis_contracts(analysis_infos, selected)
    selected_functions = [fn for fn in functions if fn.contract in analysis_contracts]
    assembly_functions = [fn for fn in selected_functions if function_has_assembly(fn)]

    source_entry = {
        "source": str(source),
        "source_id": sid,
        "compile_preparation": preparation.to_dict(),
        "contracts": list(infos.values()),
        "analysis_contract_definitions": list(analysis_infos.values()),
        "selected_contracts": sorted(selected),
        "analysis_contracts": sorted(analysis_contracts),
        "inherited_analysis_contracts": sorted(analysis_contracts - selected),
        "skipped_contracts": skipped,
        "branch_preprocess": {
            "enabled": branch_preprocess_enabled,
            "disabled_reason": "disabled_by_flag" if args.no_branch_preprocess else None,
            "mode": "flattened_imports" if (branch_preprocess_enabled and has_imports) else ("single_source" if branch_preprocess_enabled else None),
            "source": str(branch_source) if branch_preprocess_enabled else None,
            "report": str(branch_report) if branch_preprocess_enabled else None,
        },
        "function_count": len(selected_functions),
        "assembly_function_count": len(assembly_functions),
        "functions": [function_dict(fn) for fn in selected_functions],
    }

    assembly_entry = {
        key: value
        for key, value in source_entry.items()
        if key != "functions"
    }
    assembly_entry["functions"] = [function_dict(fn) for fn in assembly_functions]
    return source_entry, assembly_entry, selected_functions


def write_result_dir(
    result_dir: Path,
    source: Path,
    full_entry: Json,
    assembly_entry: Json,
    selected_functions: list[Any],
) -> Json:
    result_dir.mkdir(parents=True, exist_ok=True)
    snapshot = copy_source_snapshot(source, result_dir)
    full_entry["source_snapshot"] = snapshot
    assembly_entry["source_snapshot"] = snapshot
    sseir_path = result_dir / "sseir.json"
    assembly_path = result_dir / "assembly_functions.json"
    solidity_like_path = result_dir / "solidity_like.txt"
    write_json(sseir_path, {
        "schema": "s-seir-source/v1",
        "source": str(source),
        "result_dir": str(result_dir),
        "source_entry": full_entry,
    })
    write_json(assembly_path, {
        "schema": "s-seir-source-assembly-functions/v1",
        "source": str(source),
        "result_dir": str(result_dir),
        "source_entry": assembly_entry,
    })
    write_solidity_like_text(selected_functions, solidity_like_path)
    return {
        "source": str(source),
        "source_id": full_entry.get("source_id"),
        "result_dir": str(result_dir),
        "sseir_output": str(sseir_path),
        "assembly_output": str(assembly_path),
        "solidity_like_output": str(solidity_like_path),
        "function_count": full_entry.get("function_count"),
        "assembly_function_count": full_entry.get("assembly_function_count"),
        "selected_contracts": full_entry.get("selected_contracts", []),
        "status": "ok",
    }


def write_failure_dir(result_dir: Path, source: Path, failure: Json) -> Json:
    result_dir.mkdir(parents=True, exist_ok=True)
    snapshot = copy_source_snapshot(source, result_dir)
    failure = {**failure, "source_snapshot": snapshot}
    failure_path = result_dir / "failure.json"
    write_json(failure_path, {
        "schema": "s-seir-source-failure/v1",
        "source": str(source),
        "result_dir": str(result_dir),
        "failure": failure,
    })
    return {
        "source": str(source),
        "source_id": source_id(source),
        "result_dir": str(result_dir),
        "failure_output": str(failure_path),
        "status": "failed",
        "error_type": failure.get("error_type"),
        "error": failure.get("error"),
    }


def write_json(path: Path, payload: Json) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_ready(payload), indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch S-SEIR analysis for verified deployed contract samples, excluding libraries/interfaces.",
    )
    parser.add_argument("input", type=Path, nargs="?", default=Path("TOKENS"), help="Solidity file or sample root directory.")
    parser.add_argument("--glob", default="*/Token.sol", help="Glob used when input is a directory. Default: */Token.sol")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/batch_sseir"))
    parser.add_argument("--sseir-output", type=Path, help="Deprecated. Use per-source result directories under --output-dir.")
    parser.add_argument("--assembly-output", type=Path, help="Deprecated. Use per-source result directories under --output-dir.")
    parser.add_argument("--write-aggregate", action="store_true", help="Also write legacy aggregate JSON outputs.")
    parser.add_argument("--contract", action="append", help="Only include this deployed contract name. Can be repeated.")
    parser.add_argument("--include-abstract", action="store_true", help="Include abstract contractKind=contract definitions.")
    parser.add_argument("--keep-sources-without-assembly", action="store_true")
    parser.add_argument("--flat-glob-mode", action="store_true", help="Do not group 0x sample directories; use --glob directly.")
    parser.add_argument("--max-sources-per-sample", type=int, default=1)
    parser.add_argument("--no-branch-preprocess", action="store_true")
    parser.add_argument("--solc-bin")
    parser.add_argument("--solc-cache-dir", type=Path, default=Path("outputs/sseir_solc_cache"))
    parser.add_argument("--deps-dir", type=Path, default=Path("outputs/sseir_deps"))
    parser.add_argument("--no-install-deps", action="store_true")
    parser.add_argument("--slither-bin")
    parser.add_argument("--workdir", type=Path, default=Path("."))
    parser.add_argument("--fail-fast", action="store_true")
    args = parser.parse_args()

    input_root = args.input.resolve()
    args.output_dir = args.output_dir.resolve()
    args.workdir = args.workdir.resolve()
    args.solc_cache_dir = args.solc_cache_dir.resolve()
    args.deps_dir = args.deps_dir.resolve()
    sseir_output = (args.sseir_output or (args.output_dir / "batch.sseir.json")).resolve()
    assembly_output = (args.assembly_output or (args.output_dir / "batch.assembly_functions.json")).resolve()
    solc_bin = args.solc_bin or discover_solc(None)

    sources = discover_sources(
        input_root,
        args.glob,
        sample_mode=not args.flat_glob_mode,
        max_sources_per_sample=args.max_sources_per_sample,
    )
    full_sources: list[Json] = []
    assembly_sources: list[Json] = []
    failures: list[Json] = []
    manifest_entries: list[Json] = []

    for index, source in enumerate(sources, start=1):
        print(f"[{index}/{len(sources)}] S-SEIR {source}")
        result_dir = result_dir_for(source, args.output_dir)
        try:
            full_entry, assembly_entry, selected_functions = process_source(source, args, solc_bin, result_dir)
            full_sources.append(full_entry)
            if assembly_entry["functions"] or args.keep_sources_without_assembly:
                assembly_sources.append(assembly_entry)
            manifest_entries.append(write_result_dir(result_dir, source, full_entry, assembly_entry, selected_functions))
        except Exception as exc:
            failure = {
                "source": str(source),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            failures.append(failure)
            manifest_entries.append(write_failure_dir(result_dir, source, failure))
            print(f"  ERROR {failure['error_type']}: {failure['error']}")
            if args.fail_fast:
                raise

    manifest_path = args.output_dir / "manifest.json"
    manifest = {
        "schema": "s-seir-batch-manifest/v1",
        "input_root": str(input_root),
        "output_dir": str(args.output_dir),
        "layout": {
            "per_source_directory": "<output_dir>/<source_id>/",
            "source_snapshot": "source/",
            "full_sseir": "sseir.json",
            "assembly_functions": "assembly_functions.json",
            "solidity_like": "solidity_like.txt",
            "failure": "failure.json",
        },
        "selection_rule": {
            "included_contracts": "ContractDefinition.contractKind == 'contract'",
            "excluded_contracts": ["library", "interface", "abstract contract unless --include-abstract"],
            "assembly_function_rule": "function.source_statements contains at least one yul statement",
        },
        "sources": manifest_entries,
        "summary": {
            "processed_sources": len(full_sources),
            "failed_sources": len(failures),
            "assembly_sources": sum(1 for item in manifest_entries if item.get("assembly_function_count", 0)),
        },
    }
    write_json(manifest_path, manifest)

    if args.write_aggregate or args.sseir_output or args.assembly_output:
        full_payload = batch_payload(
            "s-seir-batch/v1",
            input_root,
            args.output_dir,
            full_sources,
            failures,
        )
        assembly_payload = batch_payload(
            "s-seir-batch-assembly-functions/v1",
            input_root,
            args.output_dir,
            assembly_sources,
            failures,
        )
        write_json(sseir_output, full_payload)
        write_json(assembly_output, assembly_payload)
        print(f"Wrote aggregate {sseir_output}")
        print(f"Wrote aggregate {assembly_output}")

    print(f"Wrote {manifest_path}")
    print(f"Processed sources: {len(full_sources)} ok, {len(failures)} failed")
    print(f"Assembly sources: {sum(1 for item in manifest_entries if item.get('assembly_function_count', 0))}")


if __name__ == "__main__":
    main()
