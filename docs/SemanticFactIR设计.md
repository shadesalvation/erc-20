# Semantic Fact IR：函数级桥接与记录规则

本文定义当前下游模型的唯一名称：`Semantic Fact IR`（SFIR）。它直接作为解混淆的起点；不再把 `Semantic Fact` 与另一层 `Semantic IR` 作为两个下游模型。

## 1. 边界

SFIR 的 `fact_cfg` 是**事实记录用 CFG**，而不是 S-SEIR 为恢复 Yul 所维护的 CFG。后者继续保留在上游，拥有 Yul 控制恢复、MemorySSA、SinkResolver 与低层 Effect；SFIR 只投影其已经完成的高级语义与可审计的语义级出处。两者不得相互修改，也不得以同一 block id 混用。

```text
Solidity AST / Slither SSA ─┐
                            ├─ 高级语义节点 + 语义出处 ─► SFIR bridge ─► Fact CFG + FactSSA
S-SEIR completed overlays ──┘                                      │
                                                                   └─ 解混淆输入

S-SEIR recovery CFG / MemorySSA / SinkResolver / Effect ── 上游证据，不进入 SFIR
```

Yul 输入的起点只能是已完成的 S-SEIR 高级 overlay。assembly boundary summary 只提供 Fact CFG 的结构出处，不能合成新的语义定义。`EffectNode`、`effect_id`、`effect_kind`、`path_states`、`slot_effect` 及由它们复制出的路径实例均不得出现在 SFIR 的 Yul 节点中。

## 2. Fact CFG

每个函数独立构建一份 Fact CFG；block id 为 `fcfg:<function-id>:<upstream-block-id>`，因此必然与恢复 CFG 区分。CFG 记录：

- block 的语言类别、源 block 出处、语句引用、终结符的高层视图；
- 边和 guard；
- 入口、RPO、支配关系与控制依赖；
- block 内按 `operation_order` 排列的 `semantic_ids`。

它是共享的规范图，而不是每条完整执行路径的一份复制。`path_witnesses` 仅保存节点所需的 guard 和控制边，按需生成具体路径见证，避免 Solidity–Yul–Solidity 片段发生笛卡尔积展开。

## 3. 语义节点与放置

Solidity 节点来自 `SolidityAtomicOperationExtractor` 的原子操作投影。Yul 节点来自 `YulSemanticLifter` 对 overlay 的投影，其 `semantic_provenance` 至少记录：

- `semantic_source`：overlay id 与种类；
- `evidence_cfg_nodes`：语义级可审计 CFG 出处；
- `anchor_cfg_node`：唯一时的锚定 block；
- `control_conditions`：高级条件。

bridge 只接受显式锚点或唯一的语义证据 block；不能唯一放置时记录 `unanchored_semantic_node`，不按文本顺序猜测位置。

## 4. FactSSA 与 Solidity/Yul 边界

SFIR 不复用 Slither 的 SSA 名称作为跨语言身份。每个变量以 Solidity AST declaration id 作为主身份：`decl:<id>:value`；若不能取得 declaration id，才使用函数作用域内的 `name:<name>:value` 并标记降级。`x.slot`、`x.offset` 等 Yul 可见成员是同一声明的 facet，例如 `decl:<id>:storage_slot`。

`fact_ssa.definitions` 生成新版本 `fssa:<binding>:vN`。参数、命名返回值、状态变量及其 storage facet 在入口定义；每个语义写入产生定义。命名返回值是普通函数级变量：Yul 对它的赋值就是该 Yul 高级语义节点的正常定义，不能由 assembly boundary 的 `external_writes` 再虚构一次定义。

数据流采用 CFG RPO 上的 reaching-definition 固定点：合流 block 对同一 binding 的多个输入产生一个 `FactPhi`，其结果版本继续流向后继。节点的 `fact_ssa.reads/writes` 只引用最终版本；原 Slither/S-SEIR SSA 仅可保留在上游审计出处中，不能作为下游绑定。

bridge 关系分两类：

- `semantic_edges(kind=bridge_input)`：Solidity 语义定义被 Yul 高级语义读取；
- `boundary_links(kind=bridge_output)`：Yul 高级语义节点写出的普通 FactSSA 版本被后续 Solidity 节点读取。

后者以实际 Yul 高级语义定义为源，故既保持“Yul 高级语义起点”的边界，也不会把命名返回值变成特殊变量。

## 5. 检测与拒绝条件

构建后强制检查：

1. 已锚定节点恰好属于一个 Fact CFG block；
2. 所有 FactSSA 定义版本唯一，Phi 的输入为该 CFG 的达到定义；
3. semantic edge 两端均是本函数的语义节点；
4. 任意 Yul 节点递归发现低层 effect transport 即报告并拒绝该节点；
5. 无到达定义的读取记录 `unresolved_fact_ssa_read`，不擅自猜测版本。
6. `Return`、`Revert`、`Stop` 在 Fact CFG 中没有出边；上游恢复 CFG 为边界分析保留的结构边不能穿透到 SFIR。

这些检查对应编译器中的 CFG 完整性、def-use/SSA 合流与数据流固定点验证；对跨语言绑定也采用同样的入口/出口契约检查。

## 6. 入口与产物

`scripts/s_seir/s_seir_pipeline.py` 默认输出 `outputs/semantic_fact_ir.json`。可用：

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python scripts/s_seir/s_seir_pipeline.py <contract.sol> \
  --semantic-fact-ir-output outputs/semantic_fact_ir.json \
  --semantic-fact-ir-text-output outputs/semantic_fact_ir.txt
```

`--semantic-facts-output`、`--semantic-ir-output` 和 `--semantic-ir-text-output` 保留为兼容别名，但都指向同一 SFIR 产物。旧的 path-cloning `SemanticFactBridge` 保留在独立模块中供既有调用者使用，不是 pipeline 的 SFIR 路径。

针对 bridge 的最小回归在 `scripts/s_seir/s_seir_semantic_fact_ir_tests.py`：覆盖 Solidity→Yul、Yul exit→Solidity，以及分支合流的 FactPhi。
