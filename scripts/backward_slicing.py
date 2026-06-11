# backward_slicing.py
# Part 1: backward slicing 基础数据结构与标签定义

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
import json
import argparse
import re


# ============================================================
# 1. backward slicing 的种子节点类型
# ============================================================

SEED_OP_TYPES: Set[str] = {
    "SSTORE",
    "SLOAD",
    "REVERT",
    "STATICCALL",
    "LOG3",
}


# ============================================================
# 2. backward tracing 中可能被纳入的依赖节点类型
# ============================================================

DEPENDENCY_OP_TYPES: Set[str] = {
    "MSTORE",
    "MLOAD",
    "KECCAK256",
    "IF",
    "STATE_READ",
    "RETURN",
}


# ============================================================
# 3. backward slice 内部边类型
# ============================================================

SLICE_EDGE_TYPES: Set[str] = {
    "DATA_DEPENDENCY",
    "CONTROL_DEPENDENCY",
    "SLOT_CONSTRUCTION",
    "VALUE_CONSTRUCTION",
    "CALL_CONTEXT",
}


# ============================================================
# 4. 状态变量角色
#    对应前一步 JSON 中的 state_variable_roles
# ============================================================

@dataclass
class StateVariableRole:
    name: str
    slot_index_guess: Optional[int]
    solidity_type: str
    semantic_role: str
    confidence: str
    evidence: List[str] = field(default_factory=list)


# ============================================================
# 5. 原始节点
#    对应前一步 JSON 中的 raw_nodes
# ============================================================

@dataclass
class RawNode:
    entrypoint: str
    function: str
    function_name: str
    node_id: int
    node_type: str

    expression: str
    irs: List[str]
    text: str

    state_variables_read: List[str]
    state_variables_written: List[str]
    variables_read: List[str]
    variables_written: List[str]

    source_mapping: str


# ============================================================
# 6. 锚点候选
#    对应前一步 JSON 中的 anchor_candidates
# ============================================================

@dataclass
class AnchorCandidate:
    entrypoint: str
    function: str
    function_name: str
    node_id: int
    node_type: str

    op_type: str
    candidate_security_type: str
    relevance_label: str
    confidence: str
    needs_human_review: bool

    evidence: List[str]
    statement: str
    expression: str
    irs: List[str]

    state_variables_read: List[str]
    state_variables_written: List[str]
    variables_read: List[str]
    variables_written: List[str]

    source_mapping: str


# ============================================================
# 7. backward slice 中的一个依赖节点
# ============================================================

@dataclass
class SliceDependencyNode:
    function: str
    function_name: str
    node_id: int
    node_type: str

    op_type: str
    dependency_role: str

    expression: str
    statement: str
    irs: List[str]

    variables_read: List[str]
    variables_written: List[str]
    state_variables_read: List[str]
    state_variables_written: List[str]

    source_mapping: str
    reason: str


# ============================================================
# 8. backward slice 中的一条依赖边
# ============================================================

@dataclass
class SliceDependencyEdge:
    edge_type: str

    from_function: str
    from_node_id: int

    to_function: str
    to_node_id: int

    variable: Optional[str] = None
    reason: str = ""


# ============================================================
# 9. 一个核心锚点对应的一条 backward slice 记录
# ============================================================

@dataclass
class BackwardSliceRecord:
    slice_id: str
    entrypoint: str

    seed_function: str
    seed_function_name: str
    seed_node_id: int
    seed_op_type: str
    seed_candidate_security_type: str
    seed_statement: str
    seed_source_mapping: str

    used_variables: List[str] = field(default_factory=list)

    data_dependency_nodes: List[SliceDependencyNode] = field(default_factory=list)
    control_dependency_nodes: List[SliceDependencyNode] = field(default_factory=list)
    dependency_edges: List[SliceDependencyEdge] = field(default_factory=list)

    unresolved_variables: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


# ============================================================
# 10. 一个 entrypoint 的 backward slicing 工作区
# ============================================================

@dataclass
class BackwardSlicingWorkspace:
    source: str
    contract: str
    entrypoint: str

    state_variable_roles: List[StateVariableRole]
    reachable_functions: List[str]

    raw_nodes: List[RawNode]
    anchor_candidates: List[AnchorCandidate]

    raw_node_index: Dict[Tuple[str, int], RawNode] = field(default_factory=dict)
    variable_definition_index: Dict[str, List[RawNode]] = field(default_factory=dict)

    backward_slices: List[BackwardSliceRecord] = field(default_factory=list)
    # ============================================================
# Part 2: 读取 anchor JSON，并构建 backward slicing 工作区
# ============================================================

def load_json(path: str | Path) -> Dict[str, Any]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def parse_state_variable_roles(contract_obj: Dict[str, Any]) -> List[StateVariableRole]:
    roles: List[StateVariableRole] = []

    for item in contract_obj.get("state_variable_roles", []):
        roles.append(
            StateVariableRole(
                name=item.get("name", ""),
                slot_index_guess=item.get("slot_index_guess"),
                solidity_type=item.get("type", ""),
                semantic_role=item.get("semantic_role", "UNKNOWN_STATE"),
                confidence=item.get("confidence", "LOW"),
                evidence=item.get("evidence", []),
            )
        )

    return roles


def parse_raw_nodes(entrypoint: str, entry_report: Dict[str, Any]) -> List[RawNode]:
    raw_nodes: List[RawNode] = []

    for item in entry_report.get("raw_nodes", []):
        raw_nodes.append(
            RawNode(
                entrypoint=entrypoint,
                function=item.get("function", ""),
                function_name=item.get("function_name", ""),
                node_id=int(item.get("node_id", -1)),
                node_type=item.get("node_type", ""),
                expression=item.get("expression", ""),
                irs=item.get("irs", []),
                text=item.get("text", ""),
                state_variables_read=item.get("state_variables_read", []),
                state_variables_written=item.get("state_variables_written", []),
                variables_read=item.get("variables_read", []),
                variables_written=item.get("variables_written", []),
                source_mapping=item.get("source_mapping", ""),
            )
        )

    return raw_nodes


def parse_anchor_candidates(
    entrypoint: str,
    entry_report: Dict[str, Any],
) -> List[AnchorCandidate]:
    anchors: List[AnchorCandidate] = []

    for item in entry_report.get("anchor_candidates", []):
        anchors.append(
            AnchorCandidate(
                entrypoint=entrypoint,
                function=item.get("function", ""),
                function_name=item.get("function_name", ""),
                node_id=int(item.get("node_id", -1)),
                node_type=item.get("node_type", ""),
                op_type=item.get("op_type", ""),
                candidate_security_type=item.get("candidate_security_type", ""),
                relevance_label=item.get("relevance_label", ""),
                confidence=item.get("confidence", "LOW"),
                needs_human_review=bool(item.get("needs_human_review", True)),
                evidence=item.get("evidence", []),
                statement=item.get("statement", ""),
                expression=item.get("expression", ""),
                irs=item.get("irs", []),
                state_variables_read=item.get("state_variables_read", []),
                state_variables_written=item.get("state_variables_written", []),
                variables_read=item.get("variables_read", []),
                variables_written=item.get("variables_written", []),
                source_mapping=item.get("source_mapping", ""),
            )
        )

    return anchors


def build_raw_node_index(raw_nodes: List[RawNode]) -> Dict[Tuple[str, int], RawNode]:
    """
    建立节点索引：
        (function, node_id) -> RawNode

    注意：
        node_id 在不同 function 中可能重复，所以 key 必须包含 function。
    """
    index: Dict[Tuple[str, int], RawNode] = {}

    for node in raw_nodes:
        key = (node.function, node.node_id)
        index[key] = node

    return index


def build_variable_definition_index(raw_nodes: List[RawNode]) -> Dict[str, List[RawNode]]:
    """
    建立变量定义索引：
        variable_name -> [RawNode, RawNode, ...]

    作用：
        后续从 seed anchor 中看到 variables_read 后，
        可以反查这些变量由哪些节点定义。

    例子：
        seed:
            sstore(XCUz, qVPd - _amount)

        variables_read:
            XCUz__transfer_asm_0
            qVPd__transfer_asm_0
            _amount

        通过这个索引可以找到：
            XCUz__transfer_asm_0 <- keccak256(...)
            qVPd__transfer_asm_0 <- sload(XCUz)
    """
    index: Dict[str, List[RawNode]] = {}

    for node in raw_nodes:
        for var in node.variables_written:
            index.setdefault(var, []).append(node)

    return index


def select_contract_obj(data: Dict[str, Any], contract_name: Optional[str] = None) -> Dict[str, Any]:
    contracts = data.get("contracts", [])

    if not contracts:
        raise ValueError("No contracts found in input JSON.")

    if contract_name is None:
        return contracts[0]

    for contract in contracts:
        if contract.get("contract") == contract_name:
            return contract

    raise ValueError(f"Contract not found: {contract_name}")


def select_entry_report(
    contract_obj: Dict[str, Any],
    entrypoint: str,
) -> Dict[str, Any]:
    for report in contract_obj.get("entrypoint_reports", []):
        if report.get("entrypoint") == entrypoint:
            return report

    available = [
        report.get("entrypoint")
        for report in contract_obj.get("entrypoint_reports", [])
    ]

    raise ValueError(
        f"Entrypoint not found: {entrypoint}. "
        f"Available entrypoints: {available}"
    )


def build_workspace_from_json(
    input_json_path: str | Path,
    entrypoint: str,
    contract_name: Optional[str] = None,
) -> BackwardSlicingWorkspace:
    """
    从前一步 anchor JSON 构建一个 entrypoint 的 backward slicing 工作区。

    输入：
        input_json_path:
            前一步脚本输出的 anchors_v2.json

        entrypoint:
            要分析的 ERC-20 入口函数，例如：
                transfer(address,uint256)
                transferFrom(address,address,uint256)
                approve(address,uint256)

        contract_name:
            可选。若一个文件中有多个 contract，用它指定目标合约。

    输出：
        BackwardSlicingWorkspace
    """
    data = load_json(input_json_path)
    source = data.get("source", "")

    contract_obj = select_contract_obj(data, contract_name=contract_name)
    contract = contract_obj.get("contract", "")

    state_variable_roles = parse_state_variable_roles(contract_obj)

    entry_report = select_entry_report(contract_obj, entrypoint)
    reachable_functions = entry_report.get("reachable_functions", [])

    raw_nodes = parse_raw_nodes(entrypoint, entry_report)
    anchor_candidates = parse_anchor_candidates(entrypoint, entry_report)

    raw_node_index = build_raw_node_index(raw_nodes)
    variable_definition_index = build_variable_definition_index(raw_nodes)

    workspace = BackwardSlicingWorkspace(
        source=source,
        contract=contract,
        entrypoint=entrypoint,
        state_variable_roles=state_variable_roles,
        reachable_functions=reachable_functions,
        raw_nodes=raw_nodes,
        anchor_candidates=anchor_candidates,
        raw_node_index=raw_node_index,
        variable_definition_index=variable_definition_index,
    )

    return workspace

# ============================================================
# Part 3: 选择 backward slicing 的 seed anchor
# ============================================================

def is_seed_anchor(anchor: AnchorCandidate) -> bool:
    """
    判断一个 anchor_candidate 是否应作为 backward slicing 的起点。

    当前规则：
        1. op_type 属于 SEED_OP_TYPES；
        2. relevance_label 是 CORE_ANCHOR；
        3. 忽略空 statement 的节点，例如 ENDIF。
    """
    if anchor.op_type not in SEED_OP_TYPES:
        return False

    if anchor.relevance_label != "CORE_ANCHOR":
        return False

    if not anchor.statement.strip():
        return False

    return True


def make_slice_id(anchor: AnchorCandidate) -> str:
    """
    为每个 seed anchor 生成稳定的 slice_id。

    形式：
        entrypoint__function_name__op_type__node_id

    例子：
        transfer__transfer__SSTORE__39
        transfer__transfer__STATICCALL__25
        transfer__spendAllowance__SSTORE__50

    注意：
        这里会把函数名中的特殊字符做简单清洗，方便作为 JSON 字段或文件名使用。
    """
    entry = anchor.entrypoint.split("(")[0]
    func = anchor.function_name
    op = anchor.op_type
    node = anchor.node_id

    safe_entry = entry.replace("_", "")
    safe_func = func.replace("_", "")

    return f"{safe_entry}__{safe_func}__{op}__node{node}"


def collect_seed_variables(anchor: AnchorCandidate) -> List[str]:
    """
    收集 seed anchor 直接使用的变量。

    对 backward slicing 来说，seed 使用了哪些变量，
    就决定了第一轮要向后追踪哪些变量的定义。

    主要来源：
        anchor.variables_read
        anchor.state_variables_read

    注意：
        state_variables_read 也纳入 used_variables，
        因为 assembly 中某些状态变量读取会直接影响控制逻辑。
    """
    variables: List[str] = []

    for var in anchor.variables_read:
        if var not in variables:
            variables.append(var)

    for var in anchor.state_variables_read:
        if var not in variables:
            variables.append(var)

    return variables


def create_initial_slice_record(anchor: AnchorCandidate) -> BackwardSliceRecord:
    """
    根据一个 seed anchor 创建初始 BackwardSliceRecord。

    此时只记录：
        seed 是谁；
        seed 读取了哪些变量；
        暂时还不填 data_dependency_nodes / control_dependency_nodes。
    """
    used_variables = collect_seed_variables(anchor)

    record = BackwardSliceRecord(
        slice_id=make_slice_id(anchor),
        entrypoint=anchor.entrypoint,

        seed_function=anchor.function,
        seed_function_name=anchor.function_name,
        seed_node_id=anchor.node_id,
        seed_op_type=anchor.op_type,
        seed_candidate_security_type=anchor.candidate_security_type,
        seed_statement=anchor.statement,
        seed_source_mapping=anchor.source_mapping,

        used_variables=used_variables,
        data_dependency_nodes=[],
        control_dependency_nodes=[],
        dependency_edges=[],
        unresolved_variables=[],
        notes=[],
    )

    return record


def select_seed_anchors(workspace: BackwardSlicingWorkspace) -> List[AnchorCandidate]:
    """
    从 workspace.anchor_candidates 中筛选 backward slicing 起点。
    """
    seeds: List[AnchorCandidate] = []

    for anchor in workspace.anchor_candidates:
        if is_seed_anchor(anchor):
            seeds.append(anchor)

    return seeds


def initialize_backward_slices(workspace: BackwardSlicingWorkspace) -> List[BackwardSliceRecord]:
    """
    为 workspace 中的所有 seed anchors 创建初始 backward slice records。

    注意：
        这里还没有开始追踪依赖，只是初始化。
    """
    seeds = select_seed_anchors(workspace)

    records: List[BackwardSliceRecord] = []

    for seed in seeds:
        record = create_initial_slice_record(seed)
        records.append(record)

    workspace.backward_slices = records
    return records


def print_seed_summary(workspace: BackwardSlicingWorkspace) -> None:
    """
    打印 seed anchors 摘要，方便人工检查脚本选出来的 slicing 起点是否合理。
    """
    seeds = select_seed_anchors(workspace)

    print("=" * 80)
    print(f"[Seed Summary]")
    print(f"source     : {workspace.source}")
    print(f"contract   : {workspace.contract}")
    print(f"entrypoint : {workspace.entrypoint}")
    print(f"seed count : {len(seeds)}")
    print("=" * 80)

    for idx, seed in enumerate(seeds, start=1):
        print(f"\n[{idx}] {seed.op_type} | {seed.candidate_security_type}")
        print(f"    function       : {seed.function}")
        print(f"    node_id        : {seed.node_id}")
        print(f"    relevance      : {seed.relevance_label}")
        print(f"    confidence     : {seed.confidence}")
        print(f"    source_mapping : {seed.source_mapping}")
        print(f"    variables_read : {seed.variables_read}")
        print(f"    statement      : {seed.statement[:300]}")
        
# ============================================================
# Temporary Runner: 只测试 Part 1-3 的结果
# ============================================================

# def dump_initial_slice_records(
#     records: List[BackwardSliceRecord],
#     output_path: str | Path,
# ) -> None:
#     """
#     将前三步生成的初始 BackwardSliceRecord 导出为 JSON。

#     注意：
#         这里导出的还不是完整 backward slicing 结果。
#         目前只有：
#             seed anchor 是谁；
#             seed 用了哪些变量；
#             seed 的基本信息。

#         还没有：
#             data_dependency_nodes
#             control_dependency_nodes
#             dependency_edges
#     """
#     output_path = Path(output_path)
#     output_path.parent.mkdir(parents=True, exist_ok=True)

#     data = [asdict(record) for record in records]

#     with output_path.open("w", encoding="utf-8") as f:
#         json.dump(data, f, ensure_ascii=False, indent=2)

#     print(f"[OK] initial slice records saved to: {output_path}")


# def main() -> None:
#     parser = argparse.ArgumentParser(
#         description="Initialize backward slicing seeds from ERC-20 anchor JSON."
#     )

#     parser.add_argument(
#         "input_json",
#         help="前一步 anchor extractor 输出的 JSON 文件，例如 outputs/anchors_v2.json",
#     )

#     parser.add_argument(
#         "--entrypoint",
#         default="transfer(address,uint256)",
#         help=(
#             "要分析的 ERC-20 入口函数。"
#             "例如 transfer(address,uint256), "
#             "transferFrom(address,address,uint256), "
#             "approve(address,uint256)"
#         ),
#     )

#     parser.add_argument(
#         "--contract",
#         default=None,
#         help="目标合约名。如果 JSON 里只有一个合约，可以不填。",
#     )

#     parser.add_argument(
#         "-o",
#         "--output",
#         default="outputs/initial_backward_slices.json",
#         help="前三步初始化结果输出路径。",
#     )

#     args = parser.parse_args()

#     workspace = build_workspace_from_json(
#         input_json_path=args.input_json,
#         entrypoint=args.entrypoint,
#         contract_name=args.contract,
#     )

#     print_seed_summary(workspace)

#     records = initialize_backward_slices(workspace)

#     dump_initial_slice_records(records, args.output)


# if __name__ == "__main__":
#     main()

# ============================================================
# Part 4: 从 seed variables 出发追踪数据依赖
# ============================================================

# ============================================================
# Part 4 revised: 数据依赖追踪修复版
# ============================================================

def node_text_of_raw(node: RawNode) -> str:
    if node.text:
        return node.text

    parts: List[str] = []
    if node.expression:
        parts.append(node.expression)
    if node.irs:
        parts.extend(node.irs)

    return " ".join(parts)


def infer_op_type_from_raw_node(node: RawNode) -> str:
    text = node_text_of_raw(node).lower()
    node_type = node.node_type.lower()

    if "sstore" in text:
        return "SSTORE"
    if "sload" in text:
        return "SLOAD"
    if "staticcall" in text:
        return "STATICCALL"
    if "delegatecall" in text:
        return "DELEGATECALL"
    if ".call" in text or " call(" in text:
        return "CALL"
    if "log3" in text:
        return "LOG3"
    if "revert" in text:
        return "REVERT"
    if "keccak256" in text:
        return "KECCAK256"
    if "mstore" in text:
        return "MSTORE"
    if "mload" in text:
        return "MLOAD"
    if "condition" in text or "nodetype.if" in node_type:
        return "IF"
    if "return" in text or "nodetype.return" in node_type:
        return "RETURN"

    if node.state_variables_read:
        return "STATE_READ"
    if node.state_variables_written:
        return "STATE_WRITE"

    # 普通赋值 / 算术，例如:
    # zfPf = CCkU
    # zfPf = zfPf - CCkU
    if ":=" in text or " = " in text:
        return "ASSIGNMENT"

    return "UNKNOWN"


def classify_dependency_role_by_ops(
    definition_op: str,
    consumer_op: str,
    variable: str,
) -> Tuple[str, str]:
    """
    根据 definition node 和 consumer node 的 op_type 判断依赖角色。

    definition_op:
        定义变量的节点类型

    consumer_op:
        使用变量的节点类型

    variable:
        被追踪的变量名
    """
    # keccak256 结果被 SLOAD/SSTORE 使用，通常是 storage slot
    if definition_op == "KECCAK256" and consumer_op in {"SLOAD", "SSTORE"}:
        return "SLOT_CONSTRUCTION", "SLOT_CONSTRUCTION"

    # SLOAD 的结果被 SSTORE/REVERT/IF 使用，通常是 value 或条件构造
    if definition_op == "SLOAD":
        return "VALUE_CONSTRUCTION", "VALUE_CONSTRUCTION"

    # mload/mstore 通常和 memory buffer / calldata / event data 有关
    if definition_op in {"MLOAD", "MSTORE"}:
        return "MEMORY_DEPENDENCY", "DATA_DEPENDENCY"

    # 普通赋值/算术一般是 value construction
    if definition_op == "ASSIGNMENT":
        return "VALUE_CONSTRUCTION", "VALUE_CONSTRUCTION"

    # 状态变量读取
    if definition_op == "STATE_READ":
        return "DATA_DEPENDENCY", "DATA_DEPENDENCY"

    # IF 节点按理应进入 control dependency；
    # 这里保守放到 CONTROL_DEPENDENCY。
    if definition_op == "IF":
        return "CONTROL_DEPENDENCY", "CONTROL_DEPENDENCY"

    return "DATA_DEPENDENCY", "DATA_DEPENDENCY"


def raw_node_to_dependency_node(
    variable: str,
    raw_node: RawNode,
    consumer_function: str,
    consumer_node_id: int,
    dependency_role: str,
) -> SliceDependencyNode:
    op_type = infer_op_type_from_raw_node(raw_node)

    reason = (
        f"Variable '{variable}' used by consumer node "
        f"{consumer_function}#{consumer_node_id} is defined or prepared here."
    )

    return SliceDependencyNode(
        function=raw_node.function,
        function_name=raw_node.function_name,
        node_id=raw_node.node_id,
        node_type=raw_node.node_type,
        op_type=op_type,
        dependency_role=dependency_role,
        expression=raw_node.expression,
        statement=node_text_of_raw(raw_node),
        irs=raw_node.irs,
        variables_read=raw_node.variables_read,
        variables_written=raw_node.variables_written,
        state_variables_read=raw_node.state_variables_read,
        state_variables_written=raw_node.state_variables_written,
        source_mapping=raw_node.source_mapping,
        reason=reason,
    )


def make_dependency_edge(
    edge_type: str,
    variable: str,
    from_node: RawNode,
    to_function: str,
    to_node_id: int,
) -> SliceDependencyEdge:
    return SliceDependencyEdge(
        edge_type=edge_type,
        from_function=from_node.function,
        from_node_id=from_node.node_id,
        to_function=to_function,
        to_node_id=to_node_id,
        variable=variable,
        reason=(
            f"Consumer node reads variable '{variable}', "
            f"which is written or prepared by node "
            f"{from_node.function}#{from_node.node_id}."
        ),
    )


def add_dependency_node_once(
    record: BackwardSliceRecord,
    dep_node: SliceDependencyNode,
) -> None:
    existing_keys = {
        (node.function, node.node_id, node.dependency_role)
        for node in record.data_dependency_nodes
    }

    key = (dep_node.function, dep_node.node_id, dep_node.dependency_role)

    if key not in existing_keys:
        record.data_dependency_nodes.append(dep_node)


def add_dependency_edge_once(
    record: BackwardSliceRecord,
    edge: SliceDependencyEdge,
) -> None:
    existing_keys = {
        (
            e.edge_type,
            e.from_function,
            e.from_node_id,
            e.to_function,
            e.to_node_id,
            e.variable,
        )
        for e in record.dependency_edges
    }

    key = (
        edge.edge_type,
        edge.from_function,
        edge.from_node_id,
        edge.to_function,
        edge.to_node_id,
        edge.variable,
    )

    if key not in existing_keys:
        record.dependency_edges.append(edge)


def get_state_variable_names(workspace: BackwardSlicingWorkspace) -> Set[str]:
    return {role.name for role in workspace.state_variable_roles}


def classify_unresolved_variable(
    workspace: BackwardSlicingWorkspace,
    record: BackwardSliceRecord,
    variable: str,
) -> None:
    """
    对找不到定义节点的变量做分类。

    典型情况：
        _amount / _from / _to:
            函数参数

        qBQC / sSgI:
            状态变量

        其他:
            unknown
    """
    if variable not in record.unresolved_variables:
        record.unresolved_variables.append(variable)

    state_names = get_state_variable_names(workspace)

    if variable in state_names:
        if hasattr(record, "unresolved_state_variables"):
            if variable not in record.unresolved_state_variables:
                record.unresolved_state_variables.append(variable)
        return

    if variable.startswith("_"):
        if hasattr(record, "unresolved_parameters"):
            if variable not in record.unresolved_parameters:
                record.unresolved_parameters.append(variable)
        return

    if hasattr(record, "unresolved_unknowns"):
        if variable not in record.unresolved_unknowns:
            record.unresolved_unknowns.append(variable)


def find_all_definition_nodes(
    workspace: BackwardSlicingWorkspace,
    variable: str,
) -> List[RawNode]:
    return workspace.variable_definition_index.get(variable, [])


def filter_definitions_before_consumer(
    definitions: List[RawNode],
    consumer_function: str,
    consumer_node_id: int,
) -> List[RawNode]:
    """
    只保留同一函数中、位于 consumer 节点之前的定义。

    这是对第四阶段原始问题的第一层修复：
        避免 node19 使用 Jfwv 时，把 node32 这种后续定义也加入。
    """
    result: List[RawNode] = []

    for node in definitions:
        if node.function != consumer_function:
            continue

        if node.node_id < consumer_node_id:
            result.append(node)

    return result


def choose_reaching_definitions(
    definitions: List[RawNode],
    keep_all_previous_defs: bool = False,
) -> List[RawNode]:
    """
    从候选定义中选择 reaching definitions。

    当前提供两个模式：

    1. keep_all_previous_defs = True
       保留所有前置定义。
       优点：高召回。
       缺点：可能保留过多。

    2. keep_all_previous_defs = False
       只保留最近的前置定义。
       优点：结果更干净。
       缺点：遇到分支时可能漏掉另一路定义。

    当前建议默认 False。
    """
    if not definitions:
        return []

    sorted_defs = sorted(definitions, key=lambda n: n.node_id)

    if keep_all_previous_defs:
        return sorted_defs

    return [sorted_defs[-1]]


def find_reaching_definition_nodes(
    workspace: BackwardSlicingWorkspace,
    variable: str,
    consumer_function: str,
    consumer_node_id: int,
    keep_all_previous_defs: bool = False,
) -> List[RawNode]:
    """
    查找 variable 在 consumer 节点处的 reaching definition 候选。

    修复点：
        原来直接返回所有同名变量定义；
        现在先过滤掉 consumer 之后的定义；
        然后默认只保留最近的前置定义。
    """
    all_defs = find_all_definition_nodes(workspace, variable)

    previous_defs = filter_definitions_before_consumer(
        definitions=all_defs,
        consumer_function=consumer_function,
        consumer_node_id=consumer_node_id,
    )

    return choose_reaching_definitions(
        definitions=previous_defs,
        keep_all_previous_defs=keep_all_previous_defs,
    )


def add_definition_dependency(
    workspace: BackwardSlicingWorkspace,
    record: BackwardSliceRecord,
    variable: str,
    definition_node: RawNode,
    consumer_function: str,
    consumer_node_id: int,
    consumer_op_type: str,
) -> None:
    """
    把一个 definition node 加入当前 slice，并建立：
        definition node -> consumer node
    的依赖边。
    """
    definition_op = infer_op_type_from_raw_node(definition_node)

    dependency_role, edge_type = classify_dependency_role_by_ops(
        definition_op=definition_op,
        consumer_op=consumer_op_type,
        variable=variable,
    )

    dep_node = raw_node_to_dependency_node(
        variable=variable,
        raw_node=definition_node,
        consumer_function=consumer_function,
        consumer_node_id=consumer_node_id,
        dependency_role=dependency_role,
    )
    add_dependency_node_once(record, dep_node)

    edge = make_dependency_edge(
        edge_type=edge_type,
        variable=variable,
        from_node=definition_node,
        to_function=consumer_function,
        to_node_id=consumer_node_id,
    )
    add_dependency_edge_once(record, edge)
# ============================================================
# Part 4 fix: 精简 memory dependency，按 offset 选择最近 mstore
# ============================================================

def extract_mstore_target_offset(
    node: RawNode,
    memory_var: str,
) -> Optional[int]:
    """
    尝试判断一个 mstore 节点写入 memory_var 的哪个 offset。

    目标识别这几类形式：

        mstore(bbwz, X)
            -> offset 0

        mstore(bbwz + 32, X)
            -> offset 32

    当前前一步 JSON 中 expression 一般长这样：

        mstore(uint256,uint256)(bbwz__transfer_asm_0,_from)

        mstore(uint256,uint256)(bbwz__transfer_asm_0 + 32,0)

    返回：
        0
        32
        None
    """
    text = node.expression or node_text_of_raw(node)

    if "mstore" not in text.lower():
        return None

    # 抽取 mstore(...) 的第一个参数
    # 例如：
    #   mstore(uint256,uint256)(bbwz__transfer_asm_0 + 32,0)
    # 捕获：
    #   bbwz__transfer_asm_0 + 32
    match = re.search(r"mstore\([^)]*\)\(([^,]+),", text)

    if not match:
        return None

    target = match.group(1).strip()

    if memory_var not in target:
        return None

    normalized = target.replace(" ", "")

    if normalized == memory_var:
        return 0

    if normalized == f"{memory_var}+32":
        return 32

    # 兼容 add(memory_var, 32) 形式，虽然你当前 JSON 里多数是 + 32
    if f"add({memory_var},32)" in normalized:
        return 32

    return None


def infer_required_memory_offsets_for_consumer(
    consumer_node: RawNode,
) -> Set[int]:
    """
    根据 consumer 节点类型，判断它需要哪些 memory offset 的最近写入。

    规则：

    1. keccak256(bbwz, 64)
       读取 64 bytes，也就是：
           offset 0
           offset 32

    2. staticcall(..., bbwz, 20, ...)
       当前只追踪 input memory。
       该合约中 input length 是 20，所以只需要：
           offset 0

    3. log3(bbwz, 32, ...)
       event data 长度 32，所以只需要：
           offset 0

    注意：
        这是启发式，不是完整 memory model。
    """
    op_type = infer_op_type_from_raw_node(consumer_node)
    text = node_text_of_raw(consumer_node).lower()

    if op_type == "KECCAK256":
        # 你的当前 slot 构造都是 keccak256(bbwz, 64)
        if "64" in text:
            return {0, 32}
        return {0}

    if op_type == "STATICCALL":
        return {0}

    if op_type == "LOG3":
        return {0}

    return set()


def find_recent_mstore_nodes_for_memory_consumer(
    workspace: BackwardSlicingWorkspace,
    memory_var: str,
    consumer_node: RawNode,
) -> List[RawNode]:
    """
    为某个 memory consumer 找最近的 mstore 依赖。

    和旧版不同：
        旧版：收集 consumer 前所有 mstore(bbwz, ...)
        新版：按 offset 只保留最近一次写入

    例子 1：
        XCUz = keccak256(bbwz, 64)

        需要：
            最近 mstore(bbwz, _from)
            最近 mstore(bbwz + 32, 0)

    例子 2：
        iLho = keccak256(bbwz, 64)

        需要：
            最近 mstore(bbwz, _to)
            最近 mstore(bbwz + 32, 0)

        不再保留更早的：
            mstore(bbwz, _from)
            mstore(bbwz, caller << 96)

    例子 3：
        log3(bbwz, 32, ...)

        需要：
            最近 mstore(bbwz, _amount)
    """
    required_offsets = infer_required_memory_offsets_for_consumer(consumer_node)

    if not required_offsets:
        return []

    latest_by_offset: Dict[int, RawNode] = {}

    for node in workspace.raw_nodes:
        if node.function != consumer_node.function:
            continue

        if node.node_id >= consumer_node.node_id:
            continue

        offset = extract_mstore_target_offset(
            node=node,
            memory_var=memory_var,
        )

        if offset is None:
            continue

        if offset not in required_offsets:
            continue

        previous = latest_by_offset.get(offset)

        if previous is None or node.node_id > previous.node_id:
            latest_by_offset[offset] = node

    return [
        latest_by_offset[offset]
        for offset in sorted(latest_by_offset.keys())
    ]

def find_memory_write_nodes_before_consumer(
    workspace: BackwardSlicingWorkspace,
    memory_var: str,
    consumer_function: str,
    consumer_node_id: int,
) -> List[RawNode]:
    """
    启发式 memory dependency 追踪。

    目标：
        如果 consumer 是 keccak256/staticcall/log3，
        并且它读取了 bbwz__xxx 这样的 memory buffer，
        则向前找同一函数中使用该 buffer 的 mstore 节点。

    这用于补强之前的问题：
        keccak256(bbwz, 64) 只追踪到 bbwz = mload(0x40)，
        但没有追踪到 mstore(bbwz, _from), mstore(bbwz+32, 0)。

    注意：
        这不是精确内存模型，只是保守启发式。
    """
    candidates: List[RawNode] = []

    for node in workspace.raw_nodes:
        if node.function != consumer_function:
            continue

        if node.node_id >= consumer_node_id:
            continue

        text = node_text_of_raw(node).lower()

        if "mstore" not in text:
            continue

        # 只要 mstore 文本或变量读取中出现 memory_var，就认为相关
        if memory_var in node_text_of_raw(node) or memory_var in node.variables_read:
            candidates.append(node)

    return sorted(candidates, key=lambda n: n.node_id)


def get_raw_node_by_function_and_id(
    workspace: BackwardSlicingWorkspace,
    function: str,
    node_id: int,
) -> Optional[RawNode]:
    return workspace.raw_node_index.get((function, node_id))

def add_memory_dependencies_for_node(
    workspace: BackwardSlicingWorkspace,
    record: BackwardSliceRecord,
    consumer_node: RawNode,
) -> List[RawNode]:
    """
    对 keccak256 / staticcall / log3 这类 memory consumer，
    补充最近的 mstore 依赖。

    返回：
        实际加入的 mstore nodes

    为什么要返回？
        后续递归追踪时，还需要继续追踪这些 mstore 节点读取的变量。
        例如：
            mstore(bbwz, _amount)
        需要把 _amount 继续加入 unresolved parameter。
    """
    consumer_op = infer_op_type_from_raw_node(consumer_node)

    if consumer_op not in {"KECCAK256", "STATICCALL", "LOG3"}:
        return []

    added_nodes: List[RawNode] = []

    for var in consumer_node.variables_read:
        if "bbwz" not in var and "mem" not in var.lower():
            continue

        mstore_nodes = find_recent_mstore_nodes_for_memory_consumer(
            workspace=workspace,
            memory_var=var,
            consumer_node=consumer_node,
        )

        for mstore_node in mstore_nodes:
            dep_node = raw_node_to_dependency_node(
                variable=var,
                raw_node=mstore_node,
                consumer_function=consumer_node.function,
                consumer_node_id=consumer_node.node_id,
                dependency_role="MEMORY_DEPENDENCY",
            )
            add_dependency_node_once(record, dep_node)

            edge = make_dependency_edge(
                edge_type="DATA_DEPENDENCY",
                variable=var,
                from_node=mstore_node,
                to_function=consumer_node.function,
                to_node_id=consumer_node.node_id,
            )
            add_dependency_edge_once(record, edge)

            added_nodes.append(mstore_node)

    return added_nodes

def trace_variable_definitions_recursive(
    workspace: BackwardSlicingWorkspace,
    record: BackwardSliceRecord,
    max_depth: int = 3,
    keep_all_previous_defs: bool = False,
    enable_memory_heuristic: bool = True,
) -> None:
    """
    修复版递归追踪。

    当前修复重点：

    1. 普通变量：
       只追踪 consumer 前最近的 reaching definition。

    2. memory buffer：
       对 keccak256/staticcall/log3，
       只补充当前 consumer 所需 offset 的最近 mstore。

    3. memory dependency 加入后，
       会继续追踪 mstore 中读取的变量。
       例如：
           mstore(bbwz, _amount)
       会继续把 _amount 加入 unresolved parameter。

    4. dependency edge 指向真实 consumer node，
       不全部指向 seed。
    """
    worklist: List[Tuple[str, str, int, str, int]] = []
    visited: Set[Tuple[str, str, int]] = set()

    for variable in record.used_variables:
        worklist.append(
            (
                variable,
                record.seed_function,
                record.seed_node_id,
                record.seed_op_type,
                0,
            )
        )

    # 如果 seed 自身是 memory consumer，先补最近 mstore 依赖
    seed_raw_node = get_raw_node_by_function_and_id(
        workspace,
        record.seed_function,
        record.seed_node_id,
    )

    if enable_memory_heuristic and seed_raw_node is not None:
        memory_nodes = add_memory_dependencies_for_node(
            workspace=workspace,
            record=record,
            consumer_node=seed_raw_node,
        )

        # 继续追踪这些 mstore 节点读取的变量
        for memory_node in memory_nodes:
            memory_op = infer_op_type_from_raw_node(memory_node)

            for next_var in memory_node.variables_read:
                worklist.append(
                    (
                        next_var,
                        memory_node.function,
                        memory_node.node_id,
                        memory_op,
                        1,
                    )
                )

            for next_state_var in memory_node.state_variables_read:
                worklist.append(
                    (
                        next_state_var,
                        memory_node.function,
                        memory_node.node_id,
                        memory_op,
                        1,
                    )
                )

    while worklist:
        variable, consumer_function, consumer_node_id, consumer_op_type, depth = worklist.pop(0)

        visit_key = (variable, consumer_function, consumer_node_id)
        if visit_key in visited:
            continue

        visited.add(visit_key)

        if depth > max_depth:
            continue

        reaching_defs = find_reaching_definition_nodes(
            workspace=workspace,
            variable=variable,
            consumer_function=consumer_function,
            consumer_node_id=consumer_node_id,
            keep_all_previous_defs=keep_all_previous_defs,
        )

        if not reaching_defs:
            classify_unresolved_variable(
                workspace=workspace,
                record=record,
                variable=variable,
            )
            continue

        for definition_node in reaching_defs:
            add_definition_dependency(
                workspace=workspace,
                record=record,
                variable=variable,
                definition_node=definition_node,
                consumer_function=consumer_function,
                consumer_node_id=consumer_node_id,
                consumer_op_type=consumer_op_type,
            )

            definition_op = infer_op_type_from_raw_node(definition_node)

            if enable_memory_heuristic:
                memory_nodes = add_memory_dependencies_for_node(
                    workspace=workspace,
                    record=record,
                    consumer_node=definition_node,
                )

                for memory_node in memory_nodes:
                    memory_op = infer_op_type_from_raw_node(memory_node)

                    for next_var in memory_node.variables_read:
                        worklist.append(
                            (
                                next_var,
                                memory_node.function,
                                memory_node.node_id,
                                memory_op,
                                depth + 1,
                            )
                        )

                    for next_state_var in memory_node.state_variables_read:
                        worklist.append(
                            (
                                next_state_var,
                                memory_node.function,
                                memory_node.node_id,
                                memory_op,
                                depth + 1,
                            )
                        )

            if depth + 1 <= max_depth:
                for next_var in definition_node.variables_read:
                    worklist.append(
                        (
                            next_var,
                            definition_node.function,
                            definition_node.node_id,
                            definition_op,
                            depth + 1,
                        )
                    )

                for next_state_var in definition_node.state_variables_read:
                    worklist.append(
                        (
                            next_state_var,
                            definition_node.function,
                            definition_node.node_id,
                            definition_op,
                            depth + 1,
                        )
                    )    

def populate_data_dependencies(
    workspace: BackwardSlicingWorkspace,
    recursive: bool = True,
    max_depth: int = 3,
    keep_all_previous_defs: bool = False,
    enable_memory_heuristic: bool = True,
) -> None:
    """
    对 workspace 中所有 backward_slices 填充数据依赖。

    参数说明：

    recursive:
        是否递归追踪。

    max_depth:
        最大递归深度。

    keep_all_previous_defs:
        True:
            保留 consumer 之前的所有定义。
            更保守，但 slice 更大。

        False:
            只保留最近的前置定义。
            更干净，是当前推荐默认值。

    enable_memory_heuristic:
        是否启用 mstore memory 依赖补强。
    """
    if not workspace.backward_slices:
        initialize_backward_slices(workspace)

    for record in workspace.backward_slices:
        trace_variable_definitions_recursive(
            workspace=workspace,
            record=record,
            max_depth=max_depth,
            keep_all_previous_defs=keep_all_previous_defs,
            enable_memory_heuristic=enable_memory_heuristic,
        )
# ============================================================
# Temporary Runner v2: 测试 Part 1-4 的结果
# ============================================================

def dump_backward_slice_records(
    records: List[BackwardSliceRecord],
    output_path: str | Path,
) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    data = [asdict(record) for record in records]

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[OK] backward slice records saved to: {output_path}")


def print_data_dependency_summary(workspace: BackwardSlicingWorkspace) -> None:
    """
    简单打印第四段追踪后的结果。
    重点看：
      1. seed anchor 是什么；
      2. used_variables 有哪些；
      3. 找到了哪些 data_dependency_nodes；
      4. 哪些变量 unresolved。
    """
    print("\n" + "=" * 100)
    print("[Data Dependency Summary]")
    print(f"source     : {workspace.source}")
    print(f"contract   : {workspace.contract}")
    print(f"entrypoint : {workspace.entrypoint}")
    print(f"slice count: {len(workspace.backward_slices)}")
    print("=" * 100)

    for idx, record in enumerate(workspace.backward_slices, start=1):
        print(f"\n[{idx}] {record.seed_op_type} | {record.seed_candidate_security_type}")
        print(f"    slice_id   : {record.slice_id}")
        print(f"    function   : {record.seed_function}")
        print(f"    node_id    : {record.seed_node_id}")
        print(f"    source     : {record.seed_source_mapping}")
        print(f"    seed       : {record.seed_statement[:220]}")

        print(f"    used_vars  : {record.used_variables}")

        print(f"    data deps  : {len(record.data_dependency_nodes)}")
        for dep in record.data_dependency_nodes[:8]:
            print(
                f"      - {dep.op_type} | {dep.dependency_role} | "
                f"{dep.function}#{dep.node_id}"
            )
            print(f"        {dep.statement[:220]}")

        if len(record.data_dependency_nodes) > 8:
            print(f"      ... {len(record.data_dependency_nodes) - 8} more dependency nodes")

        print(f"    edges      : {len(record.dependency_edges)}")
        for edge in record.dependency_edges[:8]:
            print(
                f"      - {edge.edge_type}: "
                f"{edge.from_function}#{edge.from_node_id} "
                f"-> {edge.to_function}#{edge.to_node_id} "
                f"via {edge.variable}"
            )

        if len(record.dependency_edges) > 8:
            print(f"      ... {len(record.dependency_edges) - 8} more edges")

        print(f"    unresolved: {record.unresolved_variables}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run backward data-dependency tracing from ERC-20 anchor JSON."
    )

    parser.add_argument(
        "input_json",
        help="前一步 anchor extractor 输出的 JSON，例如 outputs/anchors_v2.json",
    )

    parser.add_argument(
        "--entrypoint",
        default="transfer(address,uint256)",
        help=(
            "要分析的 ERC-20 入口函数，例如："
            "transfer(address,uint256), "
            "transferFrom(address,address,uint256), "
            "approve(address,uint256)"
        ),
    )

    parser.add_argument(
        "--contract",
        default=None,
        help="目标合约名。如果 JSON 里只有一个合约，可以不填。",
    )

    parser.add_argument(
        "--max-depth",
        type=int,
        default=3,
        help="变量定义递归追踪深度，默认 3。",
    )

    parser.add_argument(
        "-o",
        "--output",
        default="outputs/backward_slices_part4.json",
        help="第四段结果输出路径。",
    )

    args = parser.parse_args()

    workspace = build_workspace_from_json(
        input_json_path=args.input_json,
        entrypoint=args.entrypoint,
        contract_name=args.contract,
    )

    # 第三段：初始化 seed anchors
    initialize_backward_slices(workspace)

    # 第四段：追踪 seed 使用变量的定义
    populate_data_dependencies(
        workspace=workspace,
        recursive=True,
        max_depth=args.max_depth,
    )

    # 打印摘要
    print_data_dependency_summary(workspace)

    # 导出完整 JSON
    dump_backward_slice_records(
        records=workspace.backward_slices,
        output_path=args.output,
    )


if __name__ == "__main__":
    main()