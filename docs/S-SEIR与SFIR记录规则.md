# S-SEIR 与 SFIR 记录规则

## 1. 目的与适用范围

本文说明源码中的语义在两个层次中如何记录：

```Plain
Yul 源码
  -> S-SEIR：SourceStatement + Control + Effect + SemanticOverlay
  -> SFIR：完成的高级 semantic node + FactCFG + FactSSA

Solidity 源码
  -> Slither / SlithIR-SSA + SolidityAtomicOperationExtractor
  -> SFIR：Solidity semantic node + FactCFG + FactSSA
```

本文中的 JSON 是字段摘录，省略函数、合约、稳定 id 的重复字段；`asm_s_*`、`eff_*`、
`ov_*`、`sol_atom_*`、`sfir:*` 和 `fssa:*` 都由每次分析动态分配，不能把示例编号当作
跨运行的固定名称。

核心规则：

1. **S-SEIR 是 Yul 恢复层。** 它保留原始语句、CFG、Effect、MemorySSA/SinkResolver 证据和
   高级 overlay。
2. **SFIR 是下游语义层。** 它只接收 Solidity 原子语义，以及已经完成的 Yul 高级 overlay；
   不接收 Yul 低层 effect 或 MemorySSA 查询轨迹。
3. **记录的是语义，不是逐指令转写。** 已经证明的 `mapping[key]`、`array[index]`、
   `msg.sig`、`require(condition)` 等只保留高级表示；不能证明时保留 unresolved 或通用
   `ValueCompute`，不伪装为 Solidity 等价语义。
4. **顺序与条件由图证明。** block 内用 `operation_order`，跨 block 用 FactCFG；条件、
   bounds、Phi 和跨语言 def-use 均来自 CFG、dominance 或 SSA，而非源码行号猜测。

## 2. 通用字段与层次关系

### 2.1 S-SEIR 中的 Yul 记录

每个函数的标准 `FunctionSSEIR` 语义视图包含：

```Plain
source_statements  # 源码语句，Yul 使用 asm_s_*
control            # 融合后的 Solidity/Yul CFG
expr_roles         # 表达式的角色
effects            # Yul 低层副作用和语义终点
semantic_overlays  # 已恢复的高级语义
security_facts
analysis_facts
```

`effects` 允许保留 `StorageRead`、`StorageWrite`、`MemoryWrite`、`MemoryHash`、`Branch`、
`Revert`、`ValueDef` 等恢复证据；`semantic_overlays` 才是对“源码想做什么”的高级解释。
一个 overlay 通过 `effects` 与 `stmt_refs` 指回证据和源码。

```JSON
{
  "overlay_id": "ov_7",
  "kind": "MappingWrite",
  "effects": ["eff_11"],
  "stmt_refs": ["asm_s_4", "asm_s_5", "asm_s_6"],
  "attrs": {
    "access": "balances[to]",
    "state_variable": "balances",
    "keys": ["to"],
    "value": "amount"
  }
}
```

完成型 Yul overlay 在进入 SFIR 前还会携带安全的控制出处：

```JSON
{
  "semantic_anchor_cfg_node": "bb_asm1_n12",
  "semantic_evidence_cfg_nodes": ["bb_asm1_n12"]
}
```

这两个字段只记录 CFG block 身份；不能携带 `effect_id`、`path_states`、slot 计算或 memory
版本。

### 2.2 SFIR 中的统一记录

SFIR 的每个函数包含：

```Plain
fact_cfg          # 独立的事实 CFG
fact_ssa          # 最终语义变量的 reaching definitions / Phi
semantic_nodes    # Solidity 与 Yul 的规范高级操作
semantic_edges    # data / data_phi / bridge_input / bridge_output
boundary_links    # Yul -> Solidity 的跨边界输出
path_witnesses    # 按需记录的 guard 摘要
diagnostics
```

一条最终节点的公共形状为：

```JSON
{
  "semantic_id": "sfir:Token.transfer(address,uint256):yul_overlay:ov_7",
  "operation_id": "yul_overlay:ov_7",
  "kind": "StateWrite",
  "source_lang": "yul",
  "origin": "yul_sseir_semantic_overlay",
  "fact_role": "effect",
  "stmt_refs": ["asm_s_4"],
  "condition": "amount <= balances[from]",
  "lvalue": "balances[to]",
  "rvalue": "amount",
  "reads": ["amount"],
  "writes": ["balances[to]"],
  "semantic": {},
  "semantic_provenance": {},
  "placement": {},
  "fact_ssa": {"reads": [], "writes": []}
}
```

SFIR 中的 `source_lang` 只有 `solidity` 或 `yul`。典型 `origin` 分别为：

```Plain
solidity_atomic_operation
yul_sseir_semantic_overlay
```

## 3. Yul：状态变量与 mapping 写入

### 3.1 源码

```Solidity
mapping(address => uint256) balances;

function setBalance(address to, uint256 amount) external {
    assembly {
        mstore(0x00, to)
        mstore(0x20, balances.slot)
        let slot := keccak256(0x00, 0x40)
        sstore(slot, amount)
    }
}
```

### 3.2 在 S-SEIR 中的记录

首先保留 Yul 原语句：

```JSON
{
  "stmt_id": "asm_s_1",
  "lang": "yul",
  "text": "mstore(0x00, to)"
}
```

对应的 effect 是恢复证据，而不是最终源码重建语句：

```JSON
[
  {"effect_id": "eff_1", "kind": "MemoryWrite", "stmt_refs": ["asm_s_1"],
   "attrs": {"offset": "0x00", "value": "to"}},
  {"effect_id": "eff_2", "kind": "MemoryWrite", "stmt_refs": ["asm_s_2"],
   "attrs": {"offset": "0x20", "value": "balances.slot"}},
  {"effect_id": "eff_3", "kind": "MemoryHash", "stmt_refs": ["asm_s_3"],
   "attrs": {"ptr": "0x00", "size": "0x40", "value": "slot"}},
  {"effect_id": "eff_4", "kind": "StorageWrite", "stmt_refs": ["asm_s_4"],
   "attrs": {"slot": "slot", "value": "amount"}}
]
```

MemorySSA、storage layout 和 storage sink 的 def-use 共同证明该 hash 是 `balances[to]` 的
storage location。S-SEIR 因而记录两个高级 overlay：

```JSON
[
  {
    "overlay_id": "ov_1",
    "kind": "MappingSlot",
    "effects": ["eff_1", "eff_2", "eff_3"],
    "stmt_refs": ["asm_s_1", "asm_s_2", "asm_s_3"],
    "attrs": {
      "target": "slot",
      "access": "balances[to]",
      "state_variable": "balances",
      "keys": ["to"],
      "solidity_equivalent": true
    }
  },
  {
    "overlay_id": "ov_2",
    "kind": "MappingWrite",
    "effects": ["eff_4"],
    "stmt_refs": ["asm_s_4"],
    "attrs": {
      "access": "balances[to]",
      "state_variable": "balances",
      "keys": ["to"],
      "value": "amount",
      "semantic_anchor_cfg_node": "bb_asm1_n8",
      "semantic_evidence_cfg_nodes": ["bb_asm1_n8"]
    }
  }
]
```

这里 `mstore` 和 `keccak256` 没有被“丢失”：它们仍是 S-SEIR 证明 `MappingSlot` 的上游
证据；高级语义的规范终点是 `balances[to] = amount`。

### 3.3 在 SFIR 中的记录

`MappingSlot` 投影为 support node，`MappingWrite` 投影为 effect node：

```JSON
[
  {
    "kind": "StorageLocationResolve",
    "source_lang": "yul",
    "rvalue": "balances[to]",
    "reads": ["balances", "to"],
    "writes": [],
    "semantic": {
      "operation": "storage_location_resolve",
      "location": {
        "kind": "mapping",
        "access": "balances[to]",
        "state_variable": "balances",
        "keys": ["to"]
      },
      "resolution_status": "resolved"
    }
  },
  {
    "kind": "StateWrite",
    "source_lang": "yul",
    "lvalue": "balances[to]",
    "rvalue": "amount",
    "reads": ["amount"],
    "writes": ["balances[to]"],
    "semantic": {
      "operation": "state_write",
      "access": "balances[to]",
      "state_variable": "balances",
      "keys": ["to"],
      "location": {"kind": "mapping", "access": "balances[to]"},
      "value": "amount"
    }
  }
]
```

不会出现 `MemoryWrite`、`MemoryHash`、`StorageWrite(slot)` 或 `keccak256(0,64)` effect。
若 slot 恢复失败，SFIR 记录 `UnresolvedStorageLocation`，其 `rvalue` 为
`opaqueStorageLocation`；不会把任意 hash 声称为 mapping。

## 4. Yul：条件、空 revert 与 require

### 4.1 源码

```Solidity
bool paused;

function requireNotPaused() external view {
    assembly {
        let current := sload(paused.slot)
        if current { revert(0, 0) }
    }
}
```

### 4.2 在 S-SEIR 中的记录

S-SEIR 保留 Branch、StorageRead 和 Revert effect，并从空 revert 分支生成
`RequireOverlay`。`PredicateLifter` 使用同一 Branch 的 reaching StateRead 和 TypeEnv，
将 Yul 条件完成为高级条件：

```JSON
[
  {
    "kind": "Predicate",
    "attrs": {
      "predicate_id": "pred_1",
      "expression": "(current != 0)",
      "status": "resolved",
      "context": "condition",
      "semantic_model": "cfg_reaching_definition_predicate",
      "evaluation_model": "yul_ast_right_to_left_function_call_arguments"
    }
  },
  {
    "kind": "RequireOverlay",
    "attrs": {
      "condition": "(current == 0)",
      "on_fail": "revert",
      "guard_stmt_refs": ["asm_s_1"]
    }
  }
]
```

`RequireOverlay` 记录的是继续执行的条件，因此是失败分支 predicate 的逻辑否定。通用的
condition `ExpressionNormalization/EvaluationStep` 会从下游 canonical 输出中断开，避免
`sload(paused.slot)` 与 `current != 0` 同时作为两个条件节点出现。

无法将条件中的低层叶子提升时，Predicate 保守记录：

```JSON
{"expression": "opaquePredicate(pred_1)", "status": "unresolved"}
```

### 4.3 在 SFIR 中的记录

SFIR 使用两个层次记录：`StateRead` 是值语义，`Require` 是控制语义；FactCFG 的 guard
采用恢复后的 condition：

```JSON
{
  "semantic_nodes": [
    {
      "kind": "StateRead",
      "source_lang": "yul",
      "lvalue": "current",
      "rvalue": "paused",
      "semantic": {"operation": "state_read", "access": "paused"}
    },
    {
      "kind": "Require",
      "source_lang": "yul",
      "condition": "(current == 0)",
      "reads": ["current"],
      "semantic": {"condition": "(current == 0)", "on_fail": "revert"}
    }
  ],
  "fact_cfg": {
    "edges": [
      {"kind": "true", "guard": "(current == 0)", "to": "<continuation>"},
      {"kind": "false", "guard": "!((current == 0))", "to": "<revert>"}
    ]
  }
}
```

只有满足 `branch -> empty revert / continuation` 的直接 CFG 形状时才进行这种
`Require` 归一；复杂分支不会被错误压缩成 require。

## 5. Yul：calldata selector 与数组元素

### 5.1 源码

```Solidity
function dispatch(bytes4 selector, uint256[] calldata values, uint256 i) external pure {
    assembly {
        selector := shr(224, calldataload(0))
        if lt(i, values.length) {
            let value := calldataload(add(values.offset, add(0x20, mul(i, 0x20))))
        }
    }
}
```

### 5.2 在 S-SEIR 中的记录

两个模式分别完成：

```JSON
[
  {
    "kind": "CalldataSelectorRead",
    "attrs": {
      "target": "selector",
      "target_type": "bytes4",
      "source": "msg.data",
      "access": "msg.sig",
      "semantic_model": "calldata_selector_word_shift",
      "solidity_equivalent": true
    }
  },
  {
    "kind": "CalldataArrayElementRead",
    "attrs": {
      "target": "value",
      "array": "values",
      "index": "i",
      "access": "values[i]",
      "data_location": "calldata",
      "semantic_model": "typed_calldata_array_element_read",
      "bounds_proof": {
        "condition": "i < values.length",
        "controller": "bb_asm1_n4",
        "controlled_edge": "true",
        "proof": "cfg_dominating_bounds_edge_without_index_redefinition"
      }
    }
  }
]
```

selector 只匹配类型为 `bytes4` 的精确 `shr(224, calldataload(0))`。数组元素除了 ABI layout
外还必须证明 bounds 的 true edge 支配读取、非 true edge 不可到达读取，且 `i` 中途未被
重新定义。原因是越界 `calldataload` 返回零，而 Solidity `values[i]` 会 revert。

证明失败时，`CalldataArrayElementRead` 不生成，原 `CalldataWordRead` 保留；这保证不会把
不等价的 EVM 读取错误记为数组索引。

### 5.3 在 SFIR 中的记录

两个 completed overlay 均成为高级 `ValueCompute`：

```JSON
[
  {
    "kind": "ValueCompute",
    "source_lang": "yul",
    "lvalue": "selector",
    "rvalue": "msg.sig",
    "reads": ["msg.data"],
    "writes": ["selector"],
    "semantic": {
      "operation": "calldata_selector_read",
      "access": "msg.sig",
      "semantic_model": "calldata_selector_word_shift"
    }
  },
  {
    "kind": "ValueCompute",
    "source_lang": "yul",
    "lvalue": "value",
    "rvalue": "values[i]",
    "reads": ["values", "i"],
    "writes": ["value"],
    "semantic": {
      "operation": "calldata_array_element_read",
      "array": "values",
      "index": "i",
      "access": "values[i]",
      "bounds_proof": {
        "proof": "cfg_dominating_bounds_edge_without_index_redefinition"
      }
    }
  }
]
```

最终 SFIR 不保留 `shr`、`calldataload`、`mul(i,0x20)` 或 address arithmetic 作为这两个
操作的平行语义。

## 6. Yul：memory 数组与局部函数

### 6.1 源码

```Solidity
function choose(uint256[][] memory matrix, uint256 i, uint256 j)
    public pure returns (uint256 result)
{
    assembly {
        function twice(x) -> y { y := add(x, x) }
        if lt(i, mload(matrix)) {
            // 这个 mload 的参数是 outer-element 的地址；加载结果 row 是内层数组的指针。
            let row := mload(add(matrix, add(0x20, mul(i, 0x20))))
            // mload(row) 的参数是 row 指向的数组对象地址；加载结果是 row.length。
            if lt(j, mload(row)) {
                let elementAddress := add(row, add(0x20, mul(j, 0x20)))
                // 最后的 mload 才从 elementAddress 所指向的 word 读取 uint256 元素值。
                let value := mload(elementAddress)
                result := twice(value)
            }
        }
    }
}
```

### 6.2 在 S-SEIR 中的记录

数组读取不按字符串切分。对每个 `mload(address)`，算法先将它的**参数** `address` 当作
待解析的内存地址，而不是直接当作数组元素值；然后沿唯一、支配该 load 的 ValueDef def-use
链匹配：

```Plain
mul(index, 0x20)
  -> add(0x20, <stride>)
  -> add(array_base, <offset>)
  -> <element_address>
  -> mload(<element_address>) = <loaded_word>
```

当 TypeEnv 确认 memory array base，且每一维都有对应 `lt(index, length)` 真边 bounds
proof 时，才把“地址处加载出的 word”解释为数组元素。对于 `uint256[][]`，第一维元素的
word 是内层动态数组的 memory pointer，因此 `row` 是 `matrix[i]` 的 typed alias；
`mload(row)` 读取的是该内层数组 length，`mload(elementAddress)` 才读取 `matrix[i][j]`
的 `uint256` 值。固定点迭代可依次记录：

```JSON
[
  {
    "kind": "MemoryArrayElementRead",
    "attrs": {
      "target": "row",
      "array": "matrix",
      "index": "i",
      "access": "matrix[i]",
      "element_type": "uint256[] memory",
      "pattern_model": "memory_array_read_def_use_word_stride",
      "bounds_proof": {"controlled_edge": "true"}
    }
  },
  {
    "kind": "MemoryArrayElementRead",
    "attrs": {
      "target": "value",
      "array": "matrix[i]",
      "index": "j",
      "access": "matrix[i][j]",
      "bounds_proof": {"controlled_edge": "true"}
    }
  },
  {
    "kind": "YulLocalFunctionDefinition",
    "attrs": {
      "name": "twice",
      "parameters": ["x"],
      "returns": ["y"],
      "semantic_model": "yul_local_function_cfg",
      "semantic_cfg": {"blocks": [], "edges": []}
    }
  },
  {
    "kind": "YulLocalFunctionCall",
    "attrs": {
      "target": "result",
      "function": "twice",
      "arguments": ["value"],
      "definition_overlay": "ov_yul_local_def_1"
    }
  }
]
```

`row` 不是第一维元素的普通数值：它是从第一维 element-address 处 load 出来的内层数组
指针，并成为下一维的 typed alias。`elementAddress` 也只是地址计算结果；只有
`mload(elementAddress)` 的 loaded word 才对应 `matrix[i][j]`。因此该恢复来自 fixed point
的 def-use 证明，而不是从源码文本猜测。局部函数定义有独立 `semantic_cfg`；不会将 callee
的控制流内联到 caller。

### 6.3 在 SFIR 中的记录

```JSON
[
  {
    "kind": "ValueCompute",
    "source_lang": "yul",
    "lvalue": "row",
    "rvalue": "matrix[i]",
    "semantic": {
      "operation": "memory_array_element_read",
      "array": "matrix",
      "index": "i",
      "access": "matrix[i]",
      "pattern_model": "memory_array_read_def_use_word_stride"
    }
  },
  {
    "kind": "ValueCompute",
    "source_lang": "yul",
    "lvalue": "value",
    "rvalue": "matrix[i][j]",
    "semantic": {"operation": "memory_array_element_read"}
  },
  {
    "kind": "LocalFunctionDefinition",
    "source_lang": "yul",
    "lvalue": "twice",
    "semantic": {"semantic_cfg": {"blocks": [], "edges": []}},
    "placement": {"status": "nested_definition"}
  },
  {
    "kind": "InternalCall",
    "source_lang": "yul",
    "lvalue": "result",
    "rvalue": "twice(value)",
    "reads": ["value"],
    "writes": ["result"],
    "semantic": {"operation": "yul_local_function_call", "function": "twice"}
  }
]
```

最终节点不包含 `row/elementAddress` 的 pointer-address 推导、`mload`、`add` 或 `mul` 的
恢复过程；它们保留在 S-SEIR 证据。这里 `rvalue=matrix[i]` 表示 `row` 已被证明为该内层
数组对象的高级别 pointer alias，而 `rvalue=matrix[i][j]` 表示最终 load word 的值。局部
函数定义不是 caller FactCFG 中的执行节点，调用节点才按调用点顺序进入 CFG。

## 7. Solidity：mapping 更新

### 7.1 源码

```Solidity
mapping(address => uint256) balances;

function credit(address to, uint256 amount) external {
    balances[to] += amount;
}
```

### 7.2 在 SFIR 中的记录

Solidity 不经过 S-SEIR Yul effect/overlay 层。SlithIR-SSA 先产生 reference、隐式 state
read、二元计算和 state write 的 `sol_atom`；SFIR 逐原子记录其语义顺序：

```JSON
[
  {
    "kind": "StorageLocationResolve",
    "source_lang": "solidity",
    "origin": "solidity_atomic_operation",
    "lvalue": "REF_balances_to_1",
    "rvalue": "balances[to]",
    "reads": ["balances", "to"],
    "writes": ["REF_balances_to_1"],
    "semantic": {
      "operation": "storage_location_resolve",
      "location": {
        "kind": "mapping",
        "access": "balances[to]",
        "state_variable": "balances",
        "keys": ["to"]
      }
    }
  },
  {
    "kind": "StateRead",
    "source_lang": "solidity",
    "lvalue": "TMP_balance_1",
    "rvalue": "balances[to]",
    "reads": ["balances[to]"],
    "writes": ["TMP_balance_1"],
    "semantic": {"operation": "state_read", "access": "balances[to]"}
  },
  {
    "kind": "ValueCompute",
    "source_lang": "solidity",
    "lvalue": "TMP_sum_1",
    "rvalue": "TMP_balance_1 + amount",
    "reads": ["TMP_balance_1", "amount"],
    "writes": ["TMP_sum_1"],
    "semantic": {"operation": "value_compute", "operator": "+"}
  },
  {
    "kind": "StateWrite",
    "source_lang": "solidity",
    "lvalue": "balances[to]",
    "rvalue": "TMP_sum_1",
    "reads": ["TMP_sum_1"],
    "writes": ["balances[to]"],
    "semantic": {"operation": "state_write", "access": "balances[to]"}
  }
]
```

中间 SSA 名称可以不同；其语义要求是读取、计算、写入的 CFG 顺序和 def-use 不变。常量
`amount` 这类 entry binding 在 FactSSA 中有入口版本，不会被虚构成一次临时定义。

## 8. Solidity：条件与分支合流

### 8.1 源码

```Solidity
mapping(address => uint256) balances;

function choose(bool flag, address a, address b, uint256 amount) external {
    address target = flag ? a : b;
    balances[target] = amount;
}
```

### 8.2 在 SFIR 中的记录

条件是 CFG terminator 和 `BranchCondition`，不是将两条路径展开为两个 state write：

```JSON
{
  "fact_cfg": {
    "blocks": [
      {
        "block_id": "fcfg:...:bb_sol_n4",
        "terminator": {"kind": "Branch", "condition": "flag"},
        "semantic_ids": ["sfir:...:sol_atom_1"]
      }
    ],
    "edges": [
      {"from": "fcfg:...:bb_sol_n4", "to": "<then>", "kind": "true", "guard": "flag"},
      {"from": "fcfg:...:bb_sol_n4", "to": "<else>", "kind": "false", "guard": "!(flag)"}
    ]
  },
  "semantic_nodes": [
    {
      "kind": "BranchCondition",
      "source_lang": "solidity",
      "rvalue": "flag",
      "reads": ["flag"],
      "semantic": {"operation": "branch_condition", "predicate": "flag"}
    },
    {
      "kind": "ValuePhi",
      "source_lang": "solidity",
      "lvalue": "target_3",
      "rvalue": ["a_1", "b_1"],
      "semantic": {"operation": "phi", "inputs": ["a_1", "b_1"]}
    },
    {
      "kind": "StateWrite",
      "source_lang": "solidity",
      "lvalue": "balances[target]",
      "rvalue": "amount",
      "condition": null
    }
  ]
}
```

FactSSA 在合流 block 为 `target` 产生自己的 Phi version：

```JSON
{
  "version": "fssa:decl:<target>:value:phi:1",
  "binding_id": "decl:<target>:value",
  "definition_kind": "phi",
  "incoming_versions": ["fssa:decl:<a>:value:v1", "fssa:decl:<b>:value:v2"],
  "predecessor_blocks": ["<then>", "<else>"]
}
```

下游只需要一个 `balances[target] = amount`，其 `target` 的 FactSSA version 已说明该值由
两个分支合流而来；不需要也不应在语义节点层复制两个等价写入。

## 9. Solidity：嵌套 mapping 与 struct 成员

### 9.1 源码

```Solidity
struct Account {
    uint256 credit;
    bool enabled;
}

mapping(address => Account) accounts;

function enableAndCredit(address owner, uint256 delta) external {
    accounts[owner].enabled = true;
    accounts[owner].credit += delta;
}
```

### 9.2 在 SFIR 中的记录

`Index` 和 `Member` 的 SlithIR 原子操作先建立 typed reference chain，不能在刚遇到
`accounts[owner]` 时就把它误写为一次读或写。真正被后续赋值/计算消费时，SFIR 分别记录
location、隐式 StateRead、计算和 StateWrite：

```JSON
[
  {
    "kind": "StorageLocationResolve",
    "source_lang": "solidity",
    "lvalue": "REF_account_1",
    "rvalue": "accounts[owner]",
    "reads": ["accounts", "owner"],
    "semantic": {
      "operation": "storage_location_resolve",
      "location": {
        "kind": "mapping",
        "access": "accounts[owner]",
        "state_variable": "accounts",
        "keys": ["owner"]
      }
    }
  },
  {
    "kind": "StorageLocationResolve",
    "source_lang": "solidity",
    "lvalue": "REF_credit_1",
    "rvalue": "accounts[owner].credit",
    "reads": ["REF_account_1"],
    "semantic": {
      "operation": "storage_location_resolve",
      "location": {
        "kind": "storage_member",
        "access": "accounts[owner].credit",
        "state_variable": "accounts",
        "keys": ["owner"],
        "member": "credit"
      }
    }
  },
  {
    "kind": "StateWrite",
    "source_lang": "solidity",
    "lvalue": "accounts[owner].enabled",
    "rvalue": "true",
    "writes": ["accounts[owner].enabled"],
    "semantic": {
      "operation": "state_write",
      "access": "accounts[owner].enabled",
      "state_variable": "accounts",
      "keys": ["owner"],
      "location": {"kind": "storage_member", "member": "enabled"},
      "value": "true"
    }
  },
  {
    "kind": "StateRead",
    "source_lang": "solidity",
    "lvalue": "TMP_credit_1",
    "rvalue": "accounts[owner].credit",
    "reads": ["accounts[owner].credit"],
    "writes": ["TMP_credit_1"]
  },
  {
    "kind": "ValueCompute",
    "source_lang": "solidity",
    "lvalue": "TMP_credit_sum_1",
    "rvalue": "TMP_credit_1 + delta",
    "reads": ["TMP_credit_1", "delta"],
    "writes": ["TMP_credit_sum_1"]
  },
  {
    "kind": "StateWrite",
    "source_lang": "solidity",
    "lvalue": "accounts[owner].credit",
    "rvalue": "TMP_credit_sum_1",
    "writes": ["accounts[owner].credit"]
  }
]
```

其中 `REF_*`、`TMP_*` 是 Slither SSA 临时名，可能随编译器/Slither 版本变化；稳定语义是
`semantic.location.access`、`state_variable`、`keys`、`member`，以及由 FactCFG 保持的读取—
计算—写入顺序。`enabled` 的直接赋值不产生旧值 StateRead；`credit += delta` 则必须先读旧值。

## 10. Solidity：require、事件、内部调用、外部调用与返回

### 10.1 源码

```Solidity
interface IPing {
    function ping(uint256 value) external returns (uint256);
}

contract Relay {
    event Forwarded(address indexed caller, uint256 value);

    function twice(uint256 value) internal pure returns (uint256) {
        return value * 2;
    }

    function forward(IPing peer, uint256 amount) external returns (uint256 result) {
        require(amount != 0, "zero amount");
        uint256 doubled = twice(amount);
        result = peer.ping(doubled);
        emit Forwarded(msg.sender, result);
        return result;
    }
}
```

### 10.2 在 SFIR 中的记录

SFIR 将条件计算、控制终点、调用、事件和返回分别记录；不会把整个函数压缩成一条
`forward(...)` 事实：

```JSON
[
  {
    "kind": "ValueCompute",
    "source_lang": "solidity",
    "lvalue": "TMP_nonzero_1",
    "rvalue": "amount != 0",
    "reads": ["amount"],
    "writes": ["TMP_nonzero_1"],
    "semantic": {"operation": "value_compute", "operator": "!="}
  },
  {
    "kind": "Require",
    "source_lang": "solidity",
    "condition": "amount != 0",
    "reads": ["TMP_nonzero_1"],
    "semantic": {
      "operation": "require",
      "function": "require",
      "guard": "amount != 0",
      "arguments": ["TMP_nonzero_1", "zero amount"]
    }
  },
  {
    "kind": "InternalCall",
    "source_lang": "solidity",
    "lvalue": "doubled_1",
    "rvalue": "twice(amount)",
    "reads": ["amount"],
    "writes": ["doubled_1"],
    "semantic": {
      "operation": "internal_call",
      "function": "twice",
      "arguments": ["amount"]
    }
  },
  {
    "kind": "ExternalCall",
    "source_lang": "solidity",
    "lvalue": "result_1",
    "rvalue": "peer.ping(doubled_1)",
    "reads": ["peer", "doubled_1"],
    "writes": ["result_1"],
    "semantic": {
      "operation": "external_call",
      "function": "ping",
      "target": "peer",
      "arguments": ["doubled_1"]
    }
  },
  {
    "kind": "EventEmit",
    "source_lang": "solidity",
    "reads": ["msg.sender", "result_1"],
    "semantic": {
      "operation": "event_emit",
      "event": "Forwarded",
      "arguments": ["msg.sender", "result_1"]
    }
  },
  {
    "kind": "Return",
    "source_lang": "solidity",
    "rvalue": "result_1",
    "reads": ["result_1"],
    "semantic": {"operation": "return", "values": ["result_1"]}
  }
]
```

`Require` 的 `fact_role` 为 `control`，调用、事件、返回的 `fact_role` 为 `effect`。调用的
函数名、目标和参数保存在 `semantic`；若 ABI 或调用目标在 Slither 输出中并不完整，SFIR
保留已有 `LowLevelCall`/`AtomicOperation` 信息，不把它臆造为 `peer.ping(...)`。

`require` 所在 block 还在 FactCFG 中保留失败控制流；简化显示时可呈现为：

```Plain
require(amount != 0);
```

但其后续 `InternalCall -> ExternalCall -> EventEmit -> Return` 的实际顺序仍由同一 block 的
`semantic_ids` 和 FactSSA def-use 决定，而不是由该展示文本决定。

## 11. Solidity 与 Yul：跨语言 def-use

### 11.1 源码

```Solidity
uint256 stored;

function addStored(uint256 amount) external returns (uint256 result) {
    uint256 localValue = amount + 1;
    assembly {
        result := add(localValue, sload(stored.slot))
    }
}
```

### 11.2 在 SFIR 中的记录

Solidity 的 `localValue = amount + 1` 先定义一个 FactSSA version。Yul 的已恢复
`StateRead(stored)` 和 `ValueCompute(result = localValue + stored)` 读取该 version，故记录：

```JSON
{
  "semantic_edges": [
    {
      "from": "sfir:...:sol_atom_localValue",
      "to": "sfir:...:yul_overlay_expression",
      "kind": "bridge_input",
      "value_ref": "fssa:decl:<localValue>:value:v4"
    }
  ]
}
```

Yul 对命名返回值 `result` 的高级定义被后续 Solidity 读取时，记录相反方向：

```JSON
{
  "boundary_links": [
    {
      "kind": "bridge_output",
      "from": "sfir:...:yul_overlay_expression",
      "to": "sfir:...:sol_atom_return",
      "definition_version": "fssa:decl:<result>:value:v7"
    }
  ]
}
```

该桥接仅使用最终 FactSSA definition；不把 Slither 的临时 SSA 名称或 S-SEIR effect
path-state 作为跨语言身份。

## 12. 记录中的控制块与人类可读版本

`fact_cfg.blocks[*].semantic_ids` 是最终节点在该 block 内的执行顺序。为减少没有语义的
`goto` block，SFIR 仅执行三类保守变换：无 guard 的一进一出 Yul relay 收缩、同语言唯一
直线语义块融合、以及零语义 `YulNode/Fallthrough` transport 收缩。被收缩内容记录在：

```Plain
contracted_blocks / contracted_edge_ids
collapsed_transport / collapsed_entry_transport
boundary_transitions / entry_boundary_transitions
fused_source_blocks / linear_fusion
```

这些字段保留边界审计信息；branch、switch、loop back、merge、entry/exit、return/revert/stop
和任何带 guard 的边都不会因展示需要被收缩。

因此 C-like 输出中的：

```C
if (flag) goto B1; else goto B2;
```

表达的是 FactCFG 的真实控制关系；已经证明可折叠的纯 transport 不再单独显示，而会以
`collapsed transport` 注释或 edge provenance 表示。

## 13. 审阅规则

审阅某段源码的记录时，应依次检查：

1. `stmt_refs` 是否能回到对应 Solidity/Yul 源语句；
2. 对 Yul，S-SEIR overlay 是否真正覆盖该段源码所表达的高级语义；
3. 对 Yul，SFIR 是否只保留 overlay 的高级结果、没有泄漏 effect/memory/slot transport；
4. 对 Solidity，SFIR 原子操作是否保持读取、计算、写入的顺序，且隐式 storage read 已
   正确物化；
5. 条件是否由 FactCFG edge/terminator 表示，值合流是否由 FactSSA Phi 表示；
6. 同一高级操作是否只保留一个 canonical node；
7. 无法证明时，是否显示 unresolved/opaque/generic expression，而非错误的高级恢复。

这些规则的目标不是使记录外观逐行贴近源码，而是确保后续解混淆能够从 SFIR 可靠地恢复
“条件、顺序和操作”三项语义。
