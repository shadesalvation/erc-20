# SFIR Capability Map — P0-T2

日期：2026-09-18（按修订任务要求增量完善）。范围：当前工作树的 Solidity / inline Yul → SFIR，服务 ERC-20 核心持久状态语义；不实现缺失功能，不冻结 P0-T3 计划。

结论：现有 SFIR 可复用，已有函数级 Fact CFG、FactSSA、到达定义、语义节点与数据/控制关系。**统一节点类型不等于统一地址等价分析**：标准 mapping 和部分 storage alias 已能恢复，但当前 storage identity 不足以无条件支撑 ERC-20 状态依赖，尤其是两个地址参数可能相等、key 重定义、未知 slot 和调用后的状态影响。无需据此重设计整套 IR。

## 依据、层次与判定口径

上游已核对：[AGENTS](../../AGENTS.md)、[PROJECT_STATUS](../../PROJECT_STATUS.md)、[P0-T1 报告及授权补充](../task_reports/P0-T1.md)、[repository overview](repository_overview.md)、[P0-T1 ADR](../decisions/P0-T1-001-baseline-semantic-contracts.md)。主线入口和测试清单仍与实际代码一致；本轮补记 overview 中后增的 Task Map、本地 skills 与独立 review/修复记录，未改变主线结构。P0-T1 首次失败记录是历史，最新交接为 26/26 PASS；原 P0-T2 baseline 为 26/26 PASS；本轮增量 baseline 仍为 26/26 PASS，见 [revision baseline/results.json](../task_reports/P0-T2_revision_evidence/baseline/results.json)。旧结果保留为历史。

主入口仍是 `scripts/s_seir/s_seir_pipeline.py:build_sseir`；最终入口为 `s_seir_semantic_fact_adapter.py:build_function_level_semantic_fact_ir_payload` → `SemanticFactIRBridge`，schema=`s-seir-semantic-fact-ir/v1`。`scripts/legacy_yul/` 是运行依赖；归档 `scripts/废弃_sfir_pipeline未使用/` 不是本表实现依据。

“已有”表示具备明确表示和代码/回归依据，不承诺覆盖所有输入。“部分”说明能表示已恢复情况，但仍有缺失或覆盖边界。“缺失”仅指下述研究所需能力未由当前生产路径提供。新增列中的“需补/待冻结”是缺口判断，不是实现方案或 Task 排期。

区分三个层次：恢复层（AST/SlithIR、Yul CFG、MemorySSA、effects、SinkResolver）；兼容 fact 投影；最终 SFIR（`fact_cfg`、`fact_ssa`、`semantic_nodes`、`semantic_edges`、`path_witnesses`、`diagnostics`）。不能把恢复层的字段直接宣称为最终 SFIR 能力。最终 SFIR 只消费完成的 Yul overlays；未知操作可能成为 `UnmodeledSSeirOverlay`，并不意味着每条原始 opcode 都有精确语义。

代码索引（路径相对仓库根；表格引用代号加实际函数名，便于复核）：

| 代号 | 当前实现 |
| --- | --- |
| IR | [s_seir_semantic_fact_ir.py](../../scripts/s_seir/s_seir_semantic_fact_ir.py)：`SemanticFactIRBridge` |
| SOL | [s_seir_solidity_semantic_lifter.py](../../scripts/s_seir/s_seir_solidity_semantic_lifter.py)：`SoliditySemanticLifter` |
| ATOM | [s_seir_solidity_atomic_ops.py](../../scripts/s_seir/s_seir_solidity_atomic_ops.py)：`SolidityAtomicOperationExtractor` |
| YUL | [s_seir_yul_semantic_lifter.py](../../scripts/s_seir/s_seir_yul_semantic_lifter.py)：`YulSemanticLifter` |
| ADAPT | [s_seir_semantic_fact_adapter.py](../../scripts/s_seir/s_seir_semantic_fact_adapter.py)：`SSeirFactAdapter` |
| OVER | [s_seir_overlay_builder.py](../../scripts/s_seir/s_seir_overlay_builder.py)：`SemanticOverlayBuilder` |
| CTRL | [s_seir_control_builder.py](../../scripts/s_seir/s_seir_control_builder.py)：`ControlBuilder` |
| MEM | [s_seir_memory_ssa.py](../../scripts/s_seir/s_seir_memory_ssa.py)；[assembly_memory_ssa.py](../../scripts/legacy_yul/assembly_memory_ssa.py) |
| SINK | [s_seir_sink_resolver.py](../../scripts/s_seir/s_seir_sink_resolver.py)：`SinkResolver` |
| EXPR | [s_seir_yul_normalize.py](../../scripts/s_seir/s_seir_yul_normalize.py)、[assembly_arithmetic_compare_ir.py](../../scripts/legacy_yul/assembly_arithmetic_compare_ir.py) |
| LAYOUT | [s_seir_storage_layout.py](../../scripts/s_seir/s_seir_storage_layout.py)、[s_seir_type_env.py](../../scripts/s_seir/s_seir_type_env.py) |

## 语义表示支持/缺失矩阵

| 后续需求 | 当前支持程度 | 对应代码 | 缺口 | 是否需要新增 |
| --- | --- | --- | --- | --- |
| Basic Block | 已有：独立函数 Fact CFG block、semantic_ids、前驱/后继、terminator | IR `_build_fact_cfg`、`build_function` | 空 transport/entry/join 可被收缩，不保留 source-identical block；未锚定事实有诊断 | 不需要新 block IR |
| CFG Edge | 已有：from/to/kind/guard；终止边清理、跨语言 provenance | IR `_build_fact_cfg`、`_transport_edge`、`_refresh_fact_cfg_analysis` | 边是结构可达关系，不是经求解证明 feasible 的边 | Module 1 需补 feasibility |
| Branch / Condition | 已有结构与符号条件：BranchCondition、Require、Assert、真假/默认/case Guard | SOL `atomic_fact_kind/atomic_semantic`；IR `_normalize_require_guards`、`_normalize_branch_conditions`、`_edge_guard`；`s_seir_predicate_lifter.py` | normalized display predicate 不等于完整逻辑规范式/可满足性证明 | Module 1 需补，保留现有表示 |
| Variable Definition | 已有：FactSSA definitions，owner_semantic_id，entry/semantic_definition/phi；声明身份或 name_scoped | IR `_bindings`、`_build_fact_ssa`；SOL `atomic_assignment` | 未锚定或无法绑定的值不保证定义完整；name fallback 不等于声明级唯一身份 | 局部补齐需求待 P0-T3 |
| Variable Use | 已有：节点 reads、fact_ssa.reads、value_ref 数据边 | IR `_build_fact_ssa`、`_binding_for_value`；SOL `atomic_ssa_reads`；ADAPT `rough_reads` | 复合文本/环境操作数不一定有 SSA 引用；非标识符可跳过；不能将所有 reads 当完整依赖图 | Module 2 需补完整性边界 |
| Expression | 部分：ValueAssign/ValueCompute/TypeConversion/ValuePhi，operator、operand_ssa、rvalue、checked、symbolic 文本 | SOL `atomic_semantic`；YUL `_compose_value_expressions`；EXPR `normalize_expr` | 没有统一可求解的 typed symbolic AST/位宽逻辑语义；字符串规范化不是 SMT | Module 1/3 所需局部语义待补 |
| Storage Load | 已有已恢复情况：StateRead + semantic.location + reads/writes | SOL `atomic_semantic/storage_location`；ADAPT `overlay_fact`；IR `_storage_location_binding` | 未恢复路径不具有精确 persistent identity；alias-sensitive 依赖不完整 | Module 2 需补 alias/unknown 边界 |
| Storage Store | 已有已恢复情况：StateWrite + location/value + FactSSA 写版本 | 同上；SOL `atomic_assignment`；OVER `storage_overlays` | 对不同 access 不建 may-alias kill/update；没有完整 call clobber/rollback state model | Module 2/3 需补相关分析 |
| Yul sload / sstore | 部分：恢复层 StorageRead/StorageWrite → overlays → 高层 StateRead/StateWrite | `s_seir_effect_lifter.py`；OVER `storage_overlays`；ADAPT `is_resolved_storage_location`；YUL `_sanitize_lower_yul_boundary` | 原始 opcode 不是最终 SFIR 节点契约；未知 slot/残留低层表达式为 opaque/unmodeled；独立 Yul object 入口缺失 | ERC-20 范围内需补的模式由 P0-T3 定，不开通用 frontend |
| External Call | 已有调用事实：Solidity ExternalCall/LowLevelCall；Yul ExternalCall；target/arguments/value，Solidity 另保留 gas | SOL `atomic_semantic`；ADAPT `call_fact/overlay_fact`；IR `_add_call_output_relations` | Yul call_fact 未投影 gas；调用记录/返回关系不是外部合约状态摘要、重入模型或跨函数状态依赖求解 | 表示复用；Module 2/3 边界待补 |
| DelegateCall | 已有种类区分：Solidity LowLevelCall 的 call_kind；Yul DelegateCallOverlay → ExternalCall 的 call_kind | SOL `atomic_semantic`；ADAPT `call_fact`；`legacy_yul/assembly_external_call_ir.py` | 不要求最终 kind 名为 DelegateCall；未建被调用代码在 caller storage 上的效果摘要 | 需明确 unknown effect；不扩展通用代理分析 |
| StaticCall | 已有种类区分：LowLevelCall/call_kind 或 StaticCallOverlay → ExternalCall | 同上；SINK `MEMORY_RANGE_BY_KIND` | 记录 staticcall 不等于证明目标行为/返回值；没有通用 callee summary | 表示无需另建；摘要边界待冻结 |
| Ether Transfer | 已有：send/transfer → ValueTransferCall(target,value,method)，call 的 call_value，SelfDestruct 单列 | SOL `ATOMIC_KIND_MAP/atomic_semantic`；ADAPT `call_fact` | 未见完整余额状态更新及 send/transfer/call 失败事务语义统一建模 | 若作为 External Effect 消费，Module 3 需限定语义 |
| Event | 已有：EventEmit，event/arguments，Yul log 恢复与 path candidate 合并 | SOL `atomic_semantic`；ADAPT `overlay_fact`；YUL `_canonicalize_path_conditioned_events`；`legacy_yul/assembly_event_ir.py` | 未知 topic/payload 不保证解码；回滚后的提交事件集合不是现有状态转移产物 | 表示复用；Module 3 提交语义待补 |
| Return | 已有：Return values；Yul return overlays；Fact CFG terminal | SOL `atomic_semantic`；ADAPT `overlay_fact`；IR `_is_terminal_terminator` | payload 未恢复则 opaque；返回值关系不是跨调用全程序求解 | 表示复用 |
| Revert | 已有：Revert/Require/Assert、自定义 error/payload、失败分支终止 | SOL `atomic_fact_kind`；ADAPT `overlay_fact`；IR `_normalize_require_guards/_is_terminal_terminator` | 终止 CFG 不等于将前序 writes/events 回滚成事务最终状态 | Module 3 需补 failure/commit 区分 |
| Calldata | 部分：参数声明、offset/length facet、CalldataWord/Selector/ArrayElement → 高层读取 | IR `_bindings`；ADAPT `overlay_fact`；`s_seir_calldata_array_lifter.py` | 数组提升依赖 CFG bounds 证明；缺证明保留 word read/candidate；没有任意 ABI 完整解码 | 复用当前模式，缺口限定于实际研究输入 |
| Memory | 部分：恢复层 MemorySSA 值/内存版本、byte slice、路径查询；最终为 array/struct/ABI/allocation 等高层语义 | MEM；SINK；ADAPT `overlay_fact`；YUL `_drop_completed_call_buffer_temporaries` | 最终 SFIR 不是原始 memory SSA；未知范围、循环 widening、跨块不安全内存保守失精；不能据此宣称 storage alias 完成 | 保持 MEM/SINK 职责；局部需求待冻结 |
| msg.sender | 部分：SlithIR 操作数/路径文本；caller() → msg.sender | SOL `atomic_ssa_reads/atomic_semantic`；EXPR；`assembly_arithmetic_compare_ir.py:41` | 无专门环境 entry binding，最终 `_IDENTIFIER` 不接纳该成员名；FactSSA 不产生环境 read ref | Module 2 输入来源需补或显式消费语义操作数 |
| msg.value | 部分：SlithIR 操作数、call value；callvalue() → msg.value | 同上；`assembly_arithmetic_compare_ir.py:42` | 同 msg.sender；不可将缺 SSA 引用当无依赖 | 同上 |

上述已有表示分别由当前测试的 atomic/slithir/integration、adapter、IR、storage/revert、endpoint、memory 等回归覆盖；具体测试起点见末表。六项隔离探针进一步验证 exact storage binding、alias/key-version 缺口与环境值边界，完整输入/输出在本轮重跑的 [capability_probes.json](../task_reports/P0-T2_revision_evidence/capability_probes.json)。探针通过表示观察可复现，不表示缺口已经修复，也不替代端到端 Solidity/Yul 覆盖。

## 七项基础分析核对

| 后续需求 | 当前支持程度 | 对应代码 | 缺口 | 是否需要新增 |
| --- | --- | --- | --- | --- |
| 1. Def-Use | 已有 final SSA refs 及 data/data_phi/bridge_input/bridge_output；call_output/precompile_output 是单独语义关系 | IR `_build_fact_ssa`、`_add_call_output_relations`；CTRL 的 SlithIR serializer；MEM ValueDefinition | 不是完整 sink slicing；Phi 数据边只投射直接 incoming owner，递归依赖须读 phis；环境与复合表达式可能无 ref | Module 2 需补消费/完整性分析，不重建全部 def-use |
| 2. Reaching Definitions | 已有函数 CFG fixed point，reaching_definitions_in/out，按 block-local semantic_ids 更新 | IR `_build_fact_ssa` | 基于绑定身份，不是 alias-aware storage reaching definitions；未用 Module 1 feasible edge/Guard refinement；调用不自动失效所有可能受影响位置 | Module 2 需补 |
| 3. SSA / value identity | 已有 SlithIR SSA、Yul MemorySSA 和最终 FactSSA；声明 facets、entry versions、Phi | IR `_bindings/_base_name/_build_fact_ssa`；MEM `new_value_definition` | 三者有边界，不能混用版本；fallback name_scoped、storage key 文本不保证语义身份；Phi 集合与 predecessor 列表不是显式逐边配对表 | 复用并限定，必要局部补齐待冻结 |
| 4. symbolic expression | 已有符号字符串、operator/operand 字段、路径谓词、局部代数规范化 | SOL `atomic_semantic`；EXPR；`s_seir_opaque_preprocess.py:fold_opaque_condition/equivalent_expr` | 没有通用 symbolic executor/SMT backend、UNKNOWN/TIMEOUT 协议；不把有限 opaque pattern 视作 k-switch AI | Module 1 需补研究能力 |
| 5. branch / loop / join | 已有真假/case/default、loop-condition/body/exit/back 结构；dominance、内部 postdominance → control_dependencies；汇合可由 predecessors/Phi 表示 | CTRL；IR `_terminator_view/_graph_analysis/_normalize_branch_conditions`；`s_seir_semantic_fact_ir_tests.py:test_loop_header_projects_complementary_body_and_exit_guards/test_fact_cfg_erases_empty_join_and_rebuilds_phi_at_successor` | join 不保证保留独立节点；无完整 SCC/不可约循环/无出口 CFG 正确性覆盖；postdom 无出口时使用列表末节点 fallback；结构 loop 不等于语义 dispatcher 恢复 | Module 1 需补，不能依节点编号判序 |
| 6. storage path / mapping key | 已有 location.kind/access/state_variable/keys/member（Solidity）；Yul 恢复 base slot + key、嵌套 keys、path candidates | SOL `storage_location`；ATOM reference_defs；OVER `mapping_slot_expr_from_key_base/byte_slice_mapping_slot_expr`；LAYOUT | keys 主要是表达式文本，最终 binding 未纳入 key SSA/Guard 等价；未知 manual slot 没有精确 location binding | Module 2 需补地址身份/未知边界 |
| 7. Solidity/Yul storage 统一提升 | 部分：已恢复读写进入同一 StateRead/StateWrite + location，最终以相同 access 匹配 binding | SOL `storage_location`；ADAPT `storage_location/is_resolved_storage_location`；IR `_storage_location_binding` | Yul manual/unresolved 和部分 storage reference 不能升为具名 state root；两侧 location.kind 对索引/成员粒度不完全相同；未实现跨表示通用地址等价 | 共用 schema 足够作为基础；等价缺口需补 |

## Alias / Storage Address Equivalence：明确结论

**不足以作为 ERC-20 风格状态依赖的完整 alias 分析。** 已支持的标准场景可以继续复用，但不能默认不同 `access` 不相交，也不能默认同一 key 文本在不同时点指向相同地址。

| 后续需求 | 当前支持程度 | 对应代码与验证 | 缺口 | 是否需要新增 |
| --- | --- | --- | --- | --- |
| memory 指针别名与字节重叠 | 已有 base+constant offset、值定义追踪、同 base 区间交集 | MEM `linear_aliases_for_text/first_alias_overlap`；legacy `AddressAlias/linear_aliases`；memory_byte_axis_tests | 符号 base 不同不证明区间不相交；这是 memory，不能套作 storage alias | 不新建通用 memory alias；保持现有职责 |
| Solidity storage local 别名 | 已有精确引用传播，Phi 的候选位置一致时合并 | ATOM storage_alias 分支（224 行起）；`s_seir_slither_semantic_integration_tests.py` 的 accounts[msg.sender].balance | 不同候选不构成通用 may-alias 集合；不同 mapping key 的相等性未证明 | Module 2 需补需要的关系 |
| Yul slot/hash 追踪 | 已有版本化 hash/slot consumer 激活、标准/嵌套 mapping、byte slice、路径候选、部分 packed/manual constant 运算 | OVER `storage_overlays`、`direct_state_slot_alias`、`constant_expr_slot_info`、`eval_uint256_constant_expr`、`path_conditioned_nested_mapping_slot_expr`；storage/revert、endpoint 回归 | 有限模式不等于任意 keccak preimage/256-bit 地址算术等价；不证明 may/no-alias | ERC-20 必要模式缺口待 P0-T3 冻结 |
| final storage 精确身份 | 已有 `@storage:{access}`；binding_id 包含 state root + access | IR `_storage_location_binding`（1858 行起）；IR tests `test_recovered_storage_location_has_an_exact_fact_ssa_binding`；probe same_path | 只用恢复的 access 文本；keys/member 保存为字段，不参与 key-version 等价求解 | 需补消费限制或局部身份能力 |
| 不同 spelling、相同 key 值 | 缺失最终桥接等价证明 | probe `different_key_spelling`：alias=owner 后 balances[alias] 与 balances[owner] 仍为不同 binding | 可能漏掉 write→read 依赖；上游部分展开能消除拼写差异，但不能保证全部情况 | Module 2 需补 |
| 相同 spelling、不同 key 版本 | 缺失最终桥接 key-version 区分 | probe `redefined_key_same_spelling`：写 balances[owner]，owner=other，再读相同文本，仍引用前一 storage write | 不同动态位置可能被错误合并；这是隔离 bridge 输入证明，未宣称每种前端都会输出该输入 | Module 2/3 需补身份约束 |
| ERC-20 from/to、owner/spender 可能相等 | 无统一 must-alias/may-alias/no-alias 查询和 Guard 条件化结果 | IR storage binding 和 reaching fixed point 不查询 key 相等性；OVER 路径候选去重不等于 alias solver | transfer 的 from==to 会改变读写依赖；不能将不同参数名当不相交地址 | Module 2 关键缺口，Module 3 受传递影响 |
| unknown storage / 外部调用影响 | 有显式 unknown/opaque，但无完整 alias-aware clobber/kill | OVER unknown_storage_slot；ADAPT resolved 检查；YUL sanitize；IR `_build_fact_ssa` | 未知写、delegatecall、潜在重入后仍不能据精确 binding 图判定全体状态依赖完备 | Module 2/3 需有保守边界，非本 Task 实现 |

`test_location_aliases_merge_by_high_semantic_identity` 验证的是同一已恢复高层 location 的去重，不能当作任意 storage address equivalence 证明。`storage[unknown expression]` 的 Solidity-like 展示同样不是具名持久状态恢复成功。

P0-T1 所称“常量 initializer 不求值”指 SOL 新增的 `constant_operands` 声明元数据；恢复层 OVER 早已有有限 `eval_uint256_constant_expr` 用于 slot。两者层次不同，不构成交接冲突，也不能合并宣称具备通用常量求值。

## 五类后续分析能力：支持/缺失矩阵

以下按分析能力命名，不依赖未来 Task 编号。这里的“最小补足”仅列现有证据尚不能满足的要求；不定义新 schema/API、算法方案、排期或必须新增的模块。

| 后续需求 | 当前支持程度 | 对应代码 | 缺口 | 是否需要新增 |
| --- | --- | --- | --- | --- |
| Feasible Control / Guard | 部分：branch/loop/join/switch、真假/case/default Guard、dominance/control_dependencies、symbolic path_witnesses 已有；并有有限 predicate/opaque normalization | IR `_build_fact_cfg/_normalize_branch_conditions/_switch_edge_replacements/_graph_analysis/_path_witnesses`；CTRL；EXPR；`s_seir_opaque_preprocess.py:fold_opaque_condition` | 结构边/符号 witness 不等于 feasible control；没有 k-switch AI 或局部 symbolic execution + SMT；复杂循环/postdom 与规范化表达式的位宽语义有边界 | 需补 G1；复用已有 CFG/Guard，不能推定已具备可满足性证明 |
| Definition Flow | 部分：声明/name-scoped binding、entry/semantic definition/Phi、FactSSA reads/writes、reaching_in/out 和跨语言 def-use 边 | IR `_bindings/_binding_for_value/_build_fact_ssa`；ATOM storage reference propagation | 非标识符/环境值可能无 ref；Phi 的输入版本集合不等于逐 feasible predecessor 的完整配对；persistent RD 无 key alias/访问时点/未知写失效关系 | 需补 G2/G3；不重建已有普通值 RD |
| Value Flow | 部分：SSA/value identity、data/data_phi/bridge_input/bridge_output、call_output、StateRead/StateWrite 与 storage path/keys 可作为图的原料 | IR `_build_fact_ssa/_add_call_output_relations/_storage_location_binding`；SOL `storage_location`；OVER `mapping_slot_expr_from_key_base` | 存在 semantic edge 图，但没有覆盖全部 state/input/source/alias/Guard 的统一可切片 Value Flow Graph；location 的文本 identity 不等于动态状态位置关系 | 需补 G2/G3/G4；不能把图字段存在判为 VFG 完整 |
| Sink Dependency | 部分：StorageWrite value/path/key，ExternalCall target/arguments/value、EtherTransfer target/value、Event arguments、Return values、Revert operands/Guard 已有表示；可解析已恢复 memory payload | SOL `atomic_semantic`；ADAPT `call_fact/overlay_fact`；IR `_build_fact_ssa/_graph_analysis`；SINK `MEMORY_RANGE_BY_KIND/resolve_effect` | 未有以 StorageWrite 为主、同时沿数据和控制依赖的完整 backward slicing；多 sink 的 persistent state 来源也未闭合；SinkResolver 是 memory 参数解析，不是该 slicer | 需补 G3/G4/G5；unknown operand/state 来源不能直接丢弃 |
| Transition Reconstruction | 部分：StateRead/Write、call/event/return/revert、终止边、CFG partial order、块内语义顺序、canonical replacement 已有 | IR `build_function/_fuse_linear_semantic_blocks/_normalize_require_guards`；YUL `_select_canonical_high_semantics/_local_order`；SOL/ADAPT | 没有完整 Guard+初始状态+更新+外部效果+失败/回滚的 transition summary；块内 source-position/ID tie 回退不是执行顺序证明；没有 Canonical STIR/冻结比较规则 | 需补 G3/G5/G6/G7；不能将原始 effect 与替代语义重复计入 |

### Alias 缺口影响哪些分析

四类分析**均受影响**。下表明确区分已观察的 SFIR 行为与对尚未实现的下游分析的影响推论。没有把未来 VFG/slicer 未运行说成已有测试失败。

| 受影响分析能力 | 已观察的代码/最小输入证据 | 对分析的影响 | 最小补足边界 |
| --- | --- | --- | --- |
| Reaching Definitions 中同一持久位置的 definition/use 传播 | IR `_storage_location_binding` 按 `@storage:{access}` 建 binding，`_build_fact_ssa` 只传播/覆盖同 binding。E1 中 copy-equivalent 两个 access 不共享写版本；E2 中 key 重定义后同文本仍共享 | 相同地址不同文本可能漏传播；相同文本不同访问时点可能误传播/错误覆盖。现有普通变量 fixed point 不能独自解决 persistent address 关系 | G3：在访问时点区分 key 定义与路径证据；不确定同址关系不能按异址删边，也不能无证明强覆盖；复用现有 RD，不在盘点中实现 StateSSA |
| Value Flow Graph 中 Storage Load/Store 的位置关联 | E1 的 state read 没有先前同址 write 的 `data` 边；IR `_build_fact_ssa` 依 storage binding 生成边，而 `_add_call_output_relations` 只补调用输出等语义关系 | 直接使用现有 semantic_edges 会缺少该状态值来源；text identity 合并也可能产生假状态边；无法承诺完整 state/input/source flow | G2/G3/G4：区分读取出的 value identity 与位置 identity，保留 load/store 地址关系依据及未知来源，不能仅按 access 字符串连边 |
| StorageWrite backward slicing 中 path/key/state dependency | E3 StorageWrite：`alias=owner; balances[owner]=other; loaded=balances[alias]; balances[other]=loaded`；当前有 read→sink 数据边但无 prior_write→read 边 | 沿现有数据边回溯可保留 loaded 和 key copy，却漏掉贡献持久状态值的前序写；对 path/key 的追踪与值的追踪不能互相替代 | G3/G4：第一版只需 StorageWrite 主路径的 value、storage path、mapping key、state source 与 feasible control/Guard 来源；不扩展为全程序切片器 |
| Multi-Sink persistent-state dependency recovery | E3 ExternalCall/ValueTransferCall/EventEmit/Return/Revert 五种消费 loaded 的合成输入均重现上述断点；SOL `atomic_semantic` 保留相应操作数，SINK 未提供 persistent RD | 五种 sink 的普通 operand 边存在，并不意味着其 persistent state 来源恢复完整；调用目标/金额/事件参数/返回值/失败参数若来自该 load，都可能受同一缺口影响 | G3/G4/G5：沿用同一 persistent state 关系和依赖证据，至少保留已恢复 operand 与 Guard 来源；外部调用效果不明要显式保留，不开展通用 callee/重入/代理合约分析 |

最小证据（均为**现行 `SemanticFactIRBridge` 的隔离输入**，不是未知 `LocationNode` 模型，也不声称每个前端都会生成完全相同输入）：

- E0：`balances[owner]` 写后读，相同恢复路径共享 FactSSA storage version，证明有限 exact-path 支持。
- E1：`alias = owner; balances[owner] = other; loaded = balances[alias]`。两个 access binding 不同，read 使用 entry storage version，没有 prior write→read 边。证明 final bridge 不消除这类 key copy；**不推断上游 direct copy/copy chain 一律失败**，ATOM 某些引用/表达式已能展开。
- E2：`balances[owner] = …; owner = other; loaded = balances[owner]`。key 的值定义改变，但相同 access 文本仍引用旧 storage write version。若初始 owner 与 other 不等，则不能据文本证明同址；这展示表示缺口，不声称现有四态 relation 返回了错误结果（当前无该查询接口）。
- E3：在 E1 的 loaded 后接六种 sink（StorageWrite + 五种其它 sink），实际均有 read→sink 边，均缺 prior write→read 边。只检查现有输出边，不新增生产 VFG、slicer 或 oracle。

[E0/E1/E2 完整结果](../task_reports/P0-T2_revision_evidence/capability_probes.json) 复用原六项探针；[E3 输入、SFIR 与观察结果](../task_reports/P0-T2_revision_evidence/sink_impacts.json) 由 [probe_sink_impacts.py](../task_reports/P0-T2_revision_evidence/probe_sink_impacts.py) 生成。PASS 指“缺口观察被成功复现”，不是目标依赖恢复能力通过。

### 最小补足边界与受影响实现（交 P0-T3 决策）

| 缺口 | 受影响分析能力 / 现行实现 | 在 ERC-20 研究范围内至少需要补足的证据 | 本次边界 |
| --- | --- | --- | --- |
| G1：结构控制不等于 feasible control | Feasible Control / Guard；CTRL、IR graph/Guard、EXPR | 对核心行为的候选 branch/loop/join/switch 边能说明可行性和 Guard 来源；参与局部判断的表达式需有类型/位宽语义与明确未解决状态 | 不冻结 solver/API、不实现 k-switch 或全程序 symbolic execution；补足路径由 P0-T3 定 |
| G2：Definition/use 与输入来源不完整 | Definition Flow、Value Flow；IR bindings/FactSSA、SOL/YUL operand 投影 | 保留声明/值定义/使用点及 Phi 来源；msg.sender/msg.value、复合操作数和不可绑定值要有来源或明确 unknown，不能因 refs 为空当作无依赖 | 不强制所有环境输入成为某种新 SSA 节点；具体接入方案未冻结 |
| G3：persistent address relation 不完整 | Definition Flow、Value Flow、Sink Dependency、Transition Reconstruction；IR storage binding、ATOM、OVER、LAYOUT | ERC-20 的具名状态、balances 单层/allowance 嵌套 mapping 及现有 Mixed 恢复路径，应能依据 root/path/layout、访问时点的 key 定义、已有相关 Guard 判断或保守保留地址关系；相同 representation 不是跨时点同址充分条件，文本不同也不是异址证明；未知 slot/缺 provenance/多候选不得静默当异址 | 最低语义区分“可证同址、可证异址、可能重叠、证据不足”及依据；这里只是需求边界，**不冻结枚举/API**。不解任意 keccak/slot 算术，不改 Location interning，不实现通用 alias analyzer/另一套 IR |
| G4：semantic edges 尚未形成完整可切片依赖 | Value Flow、Sink Dependency；IR edges/reaching/phis/control deps、SINK | 首先能保留 StorageWrite 的 value/path/key/state + 输入来源，并复用 feasible control/Guard；多 sink 共用这些依赖依据；Phi/unknown 不能任选定义，memory range query 不能替代状态切片 | 不实现 VFG/slicer，不把此处必要证据冻结为接口；复用现有 RD/SSA/MEM/SINK |
| G5：外部效果及未知状态影响边界 | Sink Dependency、Transition Reconstruction；SOL/ADAPT call/event/return/revert、IR call links | 已有 call kind、目标/参数/转账值、事件/返回/失败 operand 及状态来源应保留；无法证明外部调用/未知写不影响状态时，不得声称相关位置的依赖完备；Yul gas 未投影等字段损失须显式限定 | 不要求通用跨合约执行、代理/重入求解；未知影响可作为显式限制，是否纳入后续实验由 P0-T3 决策 |
| G6：部分顺序只有回退依据 | Transition Reconstruction；IR `_node_order/_fuse_linear_semantic_blocks`、YUL `_local_order` | 关键读写/调用/失败之间的顺序应来自 CFG、SSA、支配/依赖关系；无证据时保留未确定偏序，不用 source position/semantic_id 补成 total order | 不修改本次已有排序，不要求恢复 source-identical CFG |
| G7：没有完整状态转移/提交语义 | Transition Reconstruction；IR 终止边、SOL/ADAPT 效果、YUL canonical replacement | 将 StateRead/Write、External Effect、Failure、Guard 和可证 partial order 汇总，区分成功提交与 revert rollback；clean/obfuscated 使用同一 canonical representation 比较，替代表示不重复计效 | 不在此创建 STIR/schema/evaluator/benchmark，也不以源码相似度替代语义 |
| G8：unknown/unsupported 诊断分散 | 五类分析能力；IR `_binding_for_value/_validate`、CTRL notes、MEM 截断/widening、YUL sanitize | 下游需要辨认事实/来源是否完整，保留 unknown/opaque/truncated/fallback 的理由；若以后使用 solver，要显式记录 UNKNOWN/TIMEOUT，不能解释为成功或不可达 | 当前无 solver 测试；盘点只记录已有状态传播限制，不顺手统一诊断接口 |

这些是能力盘点的正确缺口结论，**不阻止 P0-T2 完成**；完成意味着证据、影响和边界已清楚，而不是上述能力已实现。当前 SFIR 与独立 `STORAGE-ADDR-EQ-001` 所指定缺源码模型的边界保持：本表不对缺失的 LocationNode/ValueRecord 实现作正反能力断言。

## 三个研究 Module 的输入能力逐项映射

| 后续需求 | 当前支持程度 | 对应代码 | 缺口 | 是否需要新增 |
| --- | --- | --- | --- | --- |
| M1：候选控制结构、branch/loop/join | 已有函数/Yul CFG、dominance、control deps | CTRL、IR `_graph_analysis`；legacy `assembly_ast_cfg.py` | 无 k-switch Abstract Interpretation 恢复产物 | 需补研究能力 |
| M1：Guard 的定义/使用/路径来源 | 已有 SSA、predicate、path_witnesses(status=symbolic) | IR `_build_fact_ssa/_path_witnesses`；predicate lifter | symbolic witness 不证明路径 feasible，复杂 operand refs 不全 | 需局部补齐 |
| M1：局部 Symbolic Execution + SMT refinement | 缺失；仅有 pattern 规范化/opaque preprocessing | EXPR；`s_seir_opaque_preprocess.py` | 无 solver 集成、可满足性结果与 UNKNOWN/TIMEOUT 的已验协议 | 需新增有限研究能力，不进行全程序探索 |
| M2：Storage Write sink + 持久状态/输入识别 | 已有 StateWrite、StateRead、location、参数/环境表达式 | SOL/ADAPT、IR `_bindings` | unknown location 与环境来源不是统一可切片 identity | 需补边界 |
| M2：数据与控制 backward slicing | 有 edges/phis/reaching/control_dependencies 可复用 | IR `_build_fact_ssa/_graph_analysis` | 当前生产链无完整 sink-guided slicer；历史 ver 1.0 不算现行能力；SINK 实际是 memory range 参数解析，不是该 slicer | 需新增消费分析 |
| M2：storage alias、读写覆盖、关键依赖保留 | 部分 exact location 与 slot 恢复 | ATOM、OVER、IR | 见 alias 表；不能忽略可能同址的 from/to、key-version 或 unknown writes | 必须限定/补齐 |
| M2：复用 M1 feasible control 与 Guard | 目前只可复用结构 CFG/符号 Guard | IR `fact_cfg/path_witnesses` | 尚无 M1 feasible-control 产物及其下游消费 | 待 P0-T3 冻结连接需求 |
| M3：Execution Order partial order | 有 CFG 边、块内 semantic_ids，linear fusion 以唯一 CFG 前后继证明；跨函数 links 不内联重复 effects | IR `build_function/_fuse_linear_semantic_blocks/_direct_call_links`；YUL canonical replacement | YUL `_local_order` 使用 source_statements 位置；IR `_node_order` 同序 tie 依 semantic_id。不能将这些回退视为 CFG/SSA 已证明的执行顺序 | 需明确顺序证据边界；不以源码顺序/ID 替代分析 |
| M3：State Update + External Effect + Failure | 已有节点及终止边、call output 关系 | SOL/ADAPT、IR `_normalize_require_guards/_add_call_output_relations` | 没有以入口状态、更新、提交/回滚为完整语义的 transition summary | 需补研究层重建 |
| M3：Canonical representation 比较 clean/obfuscated | 已有 SFIR 节点规范化与替代表示去重 | YUL `_select_canonical_high_semantics`、IR `model_boundary` | SFIR canonical node 去重不等于 Canonical STIR / 语义等价 evaluator；没有冻结 paired oracle/benchmark | 后续冻结，不改变现有 SFIR |

## P0-T1 检查起点、回归与限制逐项回收

全部主线脚本位于 `scripts/s_seir/`；每一入口的完整命令/退出码/日志见 `P0-T2_revision_evidence/baseline` 和 `final` 的 results.json。分组只用于说明能力证据，不改变原 25 个主线 + 1 个归档入口的 baseline 范围。

| P0-T1 existing module / tests 起点 | 本次核对结果及边界 |
| --- | --- |
| `s_seir_control_builder_tests.py`；CTRL/solc/Slither frontend | canonical function、UTF-8 source span、stack-too-deep fallback；仅 Solidity/inline Yul 入口。独立 Yul object 未支持结论保持 |
| `s_seir_solidity_atomic_ops_tests.py`、`s_seir_solidity_slithir_tests.py`、`s_seir_slither_semantic_integration_tests.py` | 原 SSA StateWrite、常量、真假 Guard、anchor 冲突修复仍通过；storage alias、calls/modifiers/inheritance/special entries 已表示，不升级为跨函数状态求解 |
| `s_seir_semantic_fact_adapter_tests.py`、`s_seir_semantic_fact_bridge_tests.py`、`s_seir_semantic_fact_ir_tests.py`、`s_seir_semantic_fact_render_tests.py` | 区分兼容 facts 和 final SFIR；CFG、SSA、unknown、替代去重、渲染回归通过；文本渲染不作等价 oracle |
| `s_seir_function_memory_bridge_tests.py`、`s_seir_memory_byte_axis_tests.py`、`s_seir_memory_query_status_tests.py`、`s_seir_sink_resolver_tests.py` | 跨 assembly memory、byte slices、query_status/unknown 和 sink 参数解析；MemorySSA/SinkResolver 职责保持，不能宣称 storage equivalence |
| `s_seir_memory_array_tests.py`、`s_seir_memory_array_local_function_tests.py`、`s_seir_calldata_array_lifter_tests.py` | array/struct/local Yul function、calldata bounds 模式；未证明的数组读取保留 candidate/word read |
| `s_seir_condition_storage_revert_tests.py`、`s_seir_unknown_storage_like_tests.py` | nested sload、mapping/嵌套 keys、slot version/path 候选、empty revert、未知 slot 展示；unknown 展示不算精确状态身份 |
| `s_seir_endpoint_tracing_tests.py`、`s_seir_event_topic_memory_tests.py` | call/returndata/event/hash、slot/packed alias 的有限模式，非通用 ABI 或 address solver |
| `s_seir_address_zero_check_tests.py`、`s_seir_predicate_lifter_tests.py`、`s_seir_opaque_preprocess_tests.py` | 零地址/分支/loop predicate、有限 opaque identity 规则；不是 M1 完整恢复或 SMT |
| `s_seir_sseir_overlay_surface_tests.py`、`s_seir_solidity_like_condition_tests.py`、`s_seir_inspect_functions_tests.py` | final surface/opaque/条件展示/检查工具；没有以源码文本相似度评价语义 |
| 归档 `semantic_ir.tests` | 5/5 unittest、1 入口通过；只维持 P0-T1 phase 回归，不用旧 IR 推断现行能力 |
| P0-T1 unsupported / known issues | 独立 Yul、依赖锁/CI、未冻结 benchmark、常量元数据求值边界全部保持；已有 fallback/unknown/opaque 不视为成功恢复 |

源码检查另有边界：MemorySSA `MAX_PATH_STATES=256`、`MAX_LOOP_JOIN_STATES=8`，有 unknown(loop-path-join/loop-carried-memory)；ControlBuilder 有 Slither timeout/fallback notes。最终 IR 不原样导出全部 control notes，且 `_binding_for_value` 对不可绑定值可返回 None，不能宣称所有异常/缺失均进入 final diagnostics。当前没有 SMT；本次没有新吞异常逻辑，也没有虚构 UNKNOWN/TIMEOUT 测试。完整错误传播一致性尚未被证明。

无冻结接口/语义/实验规则修改，无 ADR；无 benchmark/evaluator 实验。当前结果支持局部扩展现有基础设施，缺口安排与接口冻结留给 P0-T3。

当前盘点按五类能力和上述 G1–G8 边界交接。`docs/research/TASK_MAP.md` 现已存在，但导航中的未来任务安排不是本次验收依据；P0-T3 尚未冻结的新接口/实验契约不在本 Task 修改。稳定 SFIR v1 的表示限制（例如 access binding、块内 order 回退）已记录代码证据，交 P0-T3 决策。本次不要求先修补这些限制才完成盘点。
