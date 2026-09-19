# Implementation Plan — P0-T3 frozen v1

本计划冻结接口、模块职责、Task dependency 和工程落点；不表示后续算法已经实现。研究目标服从 [RESEARCH_FRAMEWORK](docs/research/RESEARCH_FRAMEWORK.md)；导航见 [TASK_MAP](docs/research/TASK_MAP.md)，实际完成状态只见 [PROJECT_STATUS](PROJECT_STATUS.md)。冻结日期 2026-09-18；初始决定见 [P0-T3-001](docs/decisions/P0-T3-001-interface-freeze.md)。

## 1. 已确认基础与复用边界

依据 [P0-T1 report](docs/task_reports/P0-T1.md)、[repository overview](docs/architecture/repository_overview.md)、[P0-T2 report](docs/task_reports/P0-T2.md)、[capability map](docs/architecture/sfir_capability_map.md)；不重做扫描或 capability review。

生产入口 `scripts/s_seir/s_seir_pipeline.py:build_sseir`，最终投影 `s_seir_semantic_fact_adapter.py:build_function_level_semantic_fact_ir_payload` → `s_seir_semantic_fact_ir.py:SemanticFactIRBridge`。既有 schema `s-seir-semantic-fact-ir/v1` 保持。`scripts/legacy_yul/` 是运行依赖；`scripts/废弃_sfir_pipeline未使用/`、`scripts/ver 1.0/` 仅历史参考，不新增依赖。缺源码的 STORAGE-ADDR-EQ-001 旧模型不替代当前字典式 SFIR。

| Source key | 实际源码/字段 | 研究层消费限制 |
| --- | --- | --- |
| SFIR | `scripts/s_seir/s_seir_semantic_fact_ir.py:build_function`；function_id、declaration、semantic_nodes、fact_cfg、fact_ssa、semantic_edges、boundary_links、path_witnesses、diagnostics | function 级事实为输入，不能重建平行 CFG/IR |
| CFG | 同文件 `_build_fact_cfg/_graph_analysis/_fuse_linear_semantic_blocks/_node_order`；blocks、edges、control_dependencies、dominance | 结构边不是 feasibility；块内回退顺序、无出口 postdom 回退不是证明 |
| SSA | 同文件 `_build_fact_ssa/_bindings`；definitions、phis、bindings、node.fact_ssa.reads/writes、reaching_definitions_in/out | version 使用 serial；仅作 SFIR 内引用。phi incoming_versions 和 predecessor_blocks 不是逐边配对 |
| STORAGE | 同文件 `_storage_location_binding`；`s_seir_solidity_semantic_lifter.py:storage_location`；`s_seir_storage_layout.py`；`s_seir_overlay_builder.py` | location/access/state_variable/keys/member 可用；access 文本不能独立证明动态同址 |
| EFFECT | `s_seir_solidity_semantic_lifter.py:atomic_semantic`；`s_seir_semantic_fact_adapter.py:call_fact/overlay_fact` | 复用 call_kind/target/arguments/value、EventEmit、Return、Revert、Require/Assert；Yul gas 丢失须声明 |
| EXPR | lifter operator/operand_ssa/checked/constant_operands；`s_seir_yul_normalize.py`、`s_seir_opaque_preprocess.py` | 字符串/有限规则不构成 typed SMT AST；只消费有证据的类型/位宽 |
| RECOVERY | `s_seir_memory_ssa.py`、`s_seir_sink_resolver.py`、`scripts/legacy_yul/assembly_memory_ssa.py` | MemorySSA/SinkResolver 继续负责 memory/payload 恢复，不等于 persistent slicing |

以上 key 是后文 Existing SFIR source 的精确引用缩写。新增 research-layer 类型是引用这些事实的分析结果/摘要，不复制 SFIR node class、Storage IR、Call/Event IR；各 owner 优先局部扩展既有代码。

## 2. Module contracts

### Module 1 — Core Semantic Control Recovery

输入：同一 input fingerprint 的函数 SFIR、typed semantic operands、CFG/SSA/path witnesses/diagnostics。P1-T1 提供 SemanticAction；P1-T2～T5 负责 high-recall candidate control；P1-T6 只对 candidate edge 的局部区域做 symbolic/SMT；P1-T7 负责 Guard 规范化/比较和最终封装。

稳定输出 **Feasible Semantic Control Edge + Normalized Guard**，容器名 `Module1Result`：`function_ref, actions[], feasible_edges[], unresolved_edges[], rejected_edges[], guards[], evidence[], status`。三类边是按同一 candidate_id 分区的结果，禁止漏掉候选；只有证明 UNSAT 的边可进 rejected。SAT 是在记录的 assumptions/region/context 下的 witness，不能据不完整前缀自动标全路径 feasible；没有入口可达性或充分条件证明时留 unresolved，并记录局部 SAT。Guard 同时携带适用范围与前提。正常非 flattened 区域也要保留可证明的 action control，不能因 detector 未命中直接丢弃函数。

M1 不负责 state dependency、slicing、transition grouping。M2/M3 读取 unresolved records 并保守保留其潜在依赖/效果；只读取 feasible_edges 后声称 complete 属契约违规。

### Module 2 — Sink-Guided State Dependency Recovery

直接输入 `Module1Result` 的 control、normalized Guard 和诊断；另消费 SFIR FactSSA/RD/expressions/storage 与 P2-T1A 地址关系。通过 adapter/view 复用现有 Def-Use/SSA/RD，不重新控制恢复。P2-T2 是现有 RD 在 feasible-control 与 persistent-alias 边界上的局部扩展，不是第二套普通 RD 框架。

两层交付：P2-T7 的 `Module2StorageResult`（StateDependency，仅 StorageWrite）；P2-T8 的 `Module2Result`（同一 schema 的 SinkDependency 扩展）。共同字段 `function_ref, module1_ref, coverage, dependencies[], unresolved_dependencies[], evidence[], status`；coverage 枚举 `STORAGE_WRITE / MULTI_SINK`。最终稳定输出 **State / Sink Dependency Summary**。M2 不判定提交/回滚，也不恢复最终执行偏序；依赖边可供 M3 作 order evidence。

### Module 3 — Order-Aware State Transition Reconstruction

直接输入 `Module1Result` + `Module2Result`（其中保留 P2-T7 StorageWrite summaries）+ SFIR 的可审计 CFG/provenance。P3-T1 adapter 转成 SemanticEvent；T2 grouping；T3 partial order；T4 failure/commit/rollback；T5 canonicalization。不得重新运行 M1/M2。

稳定输出 **Canonical State Transition IR**；`Module3Result` 为 `function_ref, module1_ref, module2_ref, transitions[], unresolved_transitions[], evidence[], status`。P3-T6 evaluator 比较 whole transition，不能用事件集合相同掩盖 order/failure 差异。

## 3. Shared identity / Evidence / AnalysisStatus / serialization

以下公共字段是每项冻结研究 artifact 的组成部分，Interface Freeze Table 的每行均继承，不能通过省略表格列免除它们。

### 3.1 Identity 与跨样本比较

`ArtifactEnvelope = {schema, id, function_ref, input_fingerprint, producer, evidence_refs, status, payload, extensions}`。schema 格式 `erc20-research/<artifact-kebab-name>/v1`；producer 为 `{task, implementation_version, config_fingerprint}`。function_ref 引用编译输入指纹、合约声明身份、canonical signature；构造/fallback/receive 明确 entry kind；无稳定声明身份时不得仅用显示名冒充稳定。

`id = kind + ':' + SHA256(canonical_JSON(identity_basis))`。identity_basis 由表中字段组成；排除时间、机器路径、迭代序号和 evaluator 数据。优先复用稳定 declaration/semantic provenance，但现有 semantic_id、block_id、fssa version 仅作为 `SourceRef` 定位，不独立充当跨运行身份。source span 可用于同一编译输入内的 origin 定位，不能用于顺序证明。使用 operation role/operand field path 区分同一语句的不同语义操作；这不是偶然 list index。

同一输入/工具版本/配置，遍历重排不得改变 id。相同语义内容但不同 occurrence 不能被 hash 去重合成一次副作用。无法建立 occurrence identity 时 `identity_scope=RUN_LOCAL` + INSUFFICIENT_EVIDENCE，记录 source ref 和冲突，禁止借随机数/对象地址制造“stable identity”。

这些 provenance identity **不要求 clean/obfuscated 相同**。canonical comparison 使用 typed semantic payload 的参数角色（ABI position）、有 layout 证据的 state root/path、表达式和事件/偏序图映射，排除来源 id/源码路径/临时变量名/时间。对称图只可作确定性 canonical labeling 或返回 UNKNOWN，不用节点序号决定语义相等。没有 layout 证据不能按变量名字强配 storage root；比较投影保留状态与未知占位，不能将两个 unknown 判等价。

### 3.2 Evidence / Provenance Contract

`EvidenceRecord = {id, kind, claim, premises[], source_refs[], artifact_refs[], rule, scope, assumptions[], result, reason_code, producer, status}`。claim 为结构化 `{predicate, operands}`，rule 为版本化规则标识，reason_code 可供失败归因；可加 message，但不得只有调试字符串。

`SourceRef = {input_fingerprint, function_ref, source_kind, locator}`，source_kind 冻结为 `SEMANTIC_NODE / CFG_BLOCK / CFG_EDGE / DEFINITION / USE / STORAGE_ACCESS / RECOVERY_DIAGNOSTIC`；locator 用现有标识和字段路径定位可读取的原始 artifact。artifact_refs 指向 abstract-state、solver query/result、dependency edge、order constraint 等前序结果。每个结论需可解析的证据引用；无法追溯时 INSUFFICIENT_EVIDENCE，不编造 proof。

`SolverEvidence` 至少为 `{query_digest, query_artifact, theory, bit_widths, assumptions, region_ref, context_ref, backend, backend_version, timeout_ms, outcome, opaque_predicate_result, model_or_proof_ref, reason}`；模型或证明未提供时该字段为 null 并说明，不能伪称 backend 可出 proof。P1-T6 拥有 query/evidence；query 完整持久化，不能只有 digest。opaque_predicate_result 为 `{predicate_ref, classification, evidence_refs}`，classification=`ALWAYS_TRUE / ALWAYS_FALSE / NON_CONSTANT / UNKNOWN`，只在已记录 scope/assumptions 内解释。M1 及下游保留上游 diagnostics 原值和映射原因，fallback/opaque 不能消失。

### 3.3 AnalysisStatus Contract

不用单一 success 布尔值混合证明和运行完成：

- `proof`: `PROVEN / CANDIDATE / UNKNOWN`（声明相对于 scope/assumptions 是否被证明）。
- `completion`: `COMPLETE / PARTIAL / NOT_RUN`（声明范围的覆盖/收敛状态）。
- `solver`: `NOT_RUN / SAT / UNSAT / UNKNOWN / TIMEOUT / UNSUPPORTED`。
- `diagnostics[]`: 每项 `{code, reason, evidence_refs, affected_refs, scope}`，code 至少 `UNKNOWN / TIMEOUT / UNSUPPORTED / TRUNCATED / FALLBACK / OPAQUE / INSUFFICIENT_EVIDENCE / ERROR`。

各 artifact 都有 proof/completion/diagnostics；非 solver artifact 的 solver=NOT_RUN。具体关系枚举与 status 分开，例如 PROVEN 的结论可以是“这条边不可行”，不是“边存在”。unknown ≠ success；unsupported ≠ infeasible；resource limit ≠ fixed point；空 refs ≠ 无依赖；MAY/UNKNOWN alias ≠ NO_ALIAS。

依赖链保留所有适用诊断与 affected scope，不能以本阶段执行结束覆盖上游 PARTIAL。某些局部事实仍可 PROVEN，但包含 unresolved effect 的全函数摘要不能 COMPLETE。精化解除某诊断需新 evidence，保留 supersedes 引用，不能删除历史。异常记录 ERROR 并使相应执行失败；不得捕获后输出空成功结果。

### 3.4 Serialization / extension / schema ownership

研究 artifact 用 JSON UTF-8；object keys 字典序、无额外空白作 hash 输入；整数字面值用规范十进制字符串避免 256-bit 精度丢失，类型中区分 bit width/signedness；布尔/null 保持 JSON 类型。不适用字段用 null，未知字段用显式 unknown record/status，空集合只有在完成相关检查后才能表示“没有”。

无序集合按稳定 id/canonical payload 排列；真正有语义顺序的 operand 列表保留顺序，序列化事件排列不是 execution order。partial order 由显式 constraints 表达；evidence/provenance 可分 sidecar，但内容寻址、引用可解析。canonical comparison projection 独立于运行 envelope，记录 projection_version 和 derivation evidence。

每行 producer 是 schema 内容 owner；P0-T3 冻结 v1，P1-T1 负责实现 shared envelope/status/evidence/identity 校验，后续 artifact owner 复用该实现。只允许在 `extensions` 增加可忽略的非语义元数据且不改变 identity/comparison；任何必需字段、枚举、语义、identity 或 schema version 变更需 ADR 与受影响回归，不得自行 fork v1。

## 4. Interface Freeze Table

所有行 Frozen status=`FROZEN-v1`。共同 Required fields、Evidence/status、Serialization、Schema/version owner、Allowed extension rule 均继承 §3；Owner 列给出 Module 和 schema owner。下表是契约名，不承诺 Python class 名；数据可以保持现行 dict/view，不为更名复制 class。

| Artifact / Interface | Producer Task | Consumer Task(s) | Owner | Existing SFIR source / reuse mode | Identity basis | Required semantic fields（加 §3 envelope） | Evidence / status 特例 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| SemanticAction | P1-T1 | P1-T2, P1-T5, P1-T7, P3-T1 | M1 / P1-T1 | EFFECT, STORAGE / adapter-view | function + semantic occurrence provenance + action kind | kind, semantic_ref, operand_refs, control_anchor, replacement_refs | source node/anchor 必须可追溯；未识别 effect 为 unresolved |
| SemanticControlEdge | P1-T5; P1-T6 refinement; P1-T7 seal | P1-T6, P1-T7, P2-T1, P2-T2, P3-T2, P3-T3 | M1 / P1-T5 | CFG / new research-layer type | action endpoints or ENTRY/EXIT + region/context + path alternative provenance | from, to, candidate_id, guard_ref, context_ref, feasibility, region_ref | feasibility=FEASIBLE/INFEASIBLE/UNRESOLVED；solver 与前缀证据分开 |
| Guard | P1-T6; P1-T7 canonical seal | P1-T7, P2-T1A, P2-T2, P2-T4, P2-T6, P2-T7, P2-T8, P3-T2, P3-T4, P3-T5 | M1 / P1-T7 | CFG, EXPR, SSA / adapter-view | typed canonical predicate + symbol bindings + scope/assumptions | source_predicate_refs, expression, symbol_bindings, scope, assumptions, normalization_rules | normalization/equivalence evidence；未知不按字符串判等 |
| StorageAddressRelation | P2-T1A | P2-T2, P2-T4, P2-T6, P2-T8 | M2 / P2-T1A | STORAGE, SSA, CFG / new research-layer type | ordered access pair + access contexts + Guard assumptions | left_access_ref, right_access_ref, relation, guard_refs, reason, root_path_key_facts, unknown_provenance | SAME/DISTINCT/MAY_OVERLAP/INSUFFICIENT_EVIDENCE；详 §5 |
| Definition | P2-T1 | P2-T1A, P2-T2, P2-T3, P2-T4 | M2 / P2-T1 | SSA / adapter-view | binding declaration/facet + defining occurrence or entry role | sfir_definition_ref, binding_ref, defining_action_ref, role, type, origin | 现有 serial version 只作 ref；未知定义显式 |
| Use | P2-T1 | P2-T1A, P2-T2, P2-T3, P2-T4 | M2 / P2-T1 | SSA, EXPR / adapter-view | consuming occurrence + semantic operand field path | sfir_use_ref, operand_path, role, candidate_definition_refs, completeness | 环境/复合值 refs 缺失不能作无依赖 |
| ValueIdentity | P2-T3 | P2-T4, P2-T5 | M2 / P2-T3 | SSA / adapter-view | Definition identity or typed literal/input role; phi merge origin | definition_ref, kind, type, incoming_by_edge, input_role, unknown_alternatives | value 身份不等于 storage address；phi pairing 必须证明 |
| ValueFlow | P2-T4 | P2-T5, P2-T6, P2-T8, P3-T3 | M2 / P2-T4 | SSA, STORAGE, semantic_edges / new research-layer type | source + target + dependency kind + access/control context | source_ref, target_ref, dependency_kind, definition_ref, use_ref, value_ref, source_role, storage_relation_ref, guard_refs, control_refs | unknown source 是显式节点/边，不能丢掉 |
| CanonicalExpression | P2-T5 | P2-T6, P2-T7, P2-T8, P3-T5 | M2 / P2-T5 | EXPR, SSA / adapter-view | typed term + ValueIdentity bindings + arithmetic mode | term, type, arithmetic_mode, side_conditions, source_expression_refs, rewrite_steps | effectful 值引用一次；rewrite 需等价依据 |
| SemanticSlice | P2-T6 | P2-T7, P2-T8 | M2 / P2-T6 | ValueFlow + CFG / new research-layer type | sink + operand roles + control scope + reachable dependencies | sink_ref, value_flow_refs, guard_refs, source_refs, unresolved_frontier, closure_status | data+control；默认非破坏性 slice view |
| StateDependency | P2-T7 | P2-T8, P3-T1, P3-T5 | M2 / P2-T7 | STORAGE + SemanticSlice / adapter-view | sink occurrence + operand role + Guard scope | input_refs, old_state_reads, guard_refs, storage_access_refs, storage_relation_refs, update_expression_ref, sink_ref, slice_ref | 与 SinkDependency 共用 DependencySummary 核心 |
| SinkDependency | P2-T8 | P3-T1, P3-T2, P3-T4, P3-T5 | M2 / P2-T8 | EFFECT + StateDependency core / adapter-view | sink occurrence + operand role + Guard scope | DependencySummary core, sink_kind, operand_dependencies, call_kind, target, arguments, value, external_state_impact, failure_dependencies | 未知 external impact 显式；不是 callee summary |
| SemanticEvent | P3-T1 | P3-T2, P3-T5 | M3 / P3-T1 | SFIR StateRead + M1/M2 / adapter-view | source semantic occurrence + event role | kind, action_ref, dependency_refs, state_access_ref, guard_refs, effect_ref, replacement_refs | StateRead 无 action_ref 时有 state source；不重新提取 actions |
| OrderConstraint | P3-T3 | P3-T4, P3-T5, P3-T6 | M3 / P3-T3 | CFG + ValueFlow + M1/M2 / new research-layer type | before event + after event + co-execution scope | before, after, relation, guard_ref, scope, proof_kind, premise_refs | relation=BEFORE；必须有真实顺序依据，非 list/id/source |
| TransitionCandidate | P3-T2 | P3-T3, P3-T4, P3-T5 | M3 / P3-T2 | SemanticEvent + M1/M2 / new research-layer type | entry/context + event occurrence set + Guard alternatives | event_refs, guard_refs, entry_ref, exit_refs, grouping_evidence_refs, alternatives | 尚未承诺 commit 或完整 transition |
| StateTransitionIR | P3-T5 | P3-T6, P4-T1, P5-T4, P6-T3, P6-T4 | M3 / P3-T5 | M1/M2 + TransitionCandidate/Failure/Order / new research-layer summary | canonical scoped transition payload; provenance map separate | transition_id, inputs, guard_refs, state_reads, state_updates, external_effects, failure, order_constraints, unresolved_order, event_multiplicity | §7；Evidence/Status 不可 canonicalization 丢失 |

辅助契约也冻结，以免 Task 间存在未定义传递：

| Artifact | Producer | Consumers | Required payload / identity / evidence |
| --- | --- | --- | --- |
| FlattenedRegion | P1-T2 | P1-T3, P1-T4, P1-T5 | dispatcher_ref, region_cfg_refs, control_state_candidates, cases, entries, exits, detection_evidence；identity=CFG origin set+dispatcher occurrence；candidate status |
| AbstractDomainContract | P1-T3 | P1-T4 | domain_version, value_domain, context_policy, k, transfer_rules, join_rules, context_update, stabilization_policy；identity=规则/config digest；bottom/top/unknown 不混同 |
| AbstractPropagation | P1-T4 | P1-T5, P1-T6 | region_ref, domain_ref, node_context_states, convergence_evidence, resource_budget, termination；termination=FIXED_POINT/RESOURCE_LIMIT/UNSUPPORTED/ERROR |
| SolverEvidence | P1-T6 | P1-T7, P3-T6, P6-T4 | §3.2；identity=query_digest+backend/config+assumptions；SAT/UNSAT/UNKNOWN/TIMEOUT/UNSUPPORTED |
| DefUseIndex | P2-T1 | P2-T1A, P2-T2, P2-T4 | definitions, uses, def_to_uses, use_to_candidate_defs, unresolved_uses；identity=function+source references digest |
| StorageAccessView | P2-T1A | P2-T2, P2-T4, P2-T6, P2-T8 | §5 输入投影；identity=访问 occurrence+role；不是新 Storage IR |
| ReachingDefinitionSet | P2-T2 | P2-T3, P2-T4 | use_ref, candidate_definition_refs, path_guard_refs, storage_relation_refs, unknown_clobbers, completeness；identity=use+control context |
| ValueFlowGraph | P2-T4 | P2-T5, P2-T6, P2-T8 | nodes, value_flows, unresolved_sources, module1_ref；identity=function+分析输入 digest；不得称已有 semantic_edges 为完整图 |
| PartialOrder | P3-T3 | P3-T4, P3-T5, P3-T6 | event_refs, constraints, unresolved_pairs, scopes；pair status=INCOMPARABLE/UNKNOWN；identity=event set+scope |
| FailureSemantics | P3-T4 | P3-T5, P3-T6 | §7 failure；identity=transition candidate+exit/Guard alternative |
| Module1Result | P1-T7 | P2-T1, P2-T1A, P2-T2, P2-T4, P2-T6, P2-T7, P2-T8, P3-T1, P3-T2, P3-T3, P3-T5 | §2 M1；identity=function+analysis config+SFIR fingerprint；跨模块引用同一版本 |
| Module2StorageResult | P2-T7 | P2-T8, P3-T1 | §2 M2 coverage=STORAGE_WRITE；identity=function+M1/依赖输入 digest |
| Module2Result | P2-T8 | P3-T1, P3-T2, P3-T3, P3-T4, P3-T5 | §2 M2 coverage=MULTI_SINK；保留 storage summaries 的原 identity |
| Module3Result | P3-T5 | P3-T6, P4-T1, P5-T4 | §2 M3；identity=function+M1/M2/配置 digest |

辅助表所有行同样继承 §3 的 envelope/evidence/status/serialization；producer 拥有 schema，mode 为研究结果（StorageAccessView/DefUseIndex 是 adapter-view），SFIR 来源沿输入链可追溯。这里只冻结记录与职责；k 值、domain 格、solver backend、rewrite 集合是相应 Task 在约束内实现并验证的策略，不在 P0-T3 编造论文算法。

## 5. P2-T1A frozen minimum contract — REQUIRED

固定依赖 **P2-T1 → P2-T1A → P2-T2**。P2-T2 即使直接复用 P2-T1，也必须依赖并消费 P2-T1A，不能恢复默认跳过路径。

冻结查询签名（计划中的 Python API，当前不创建实现）：

```python
relate_storage_accesses(left: StorageAccessView, right: StorageAccessView,
                        context: StorageRelationContext) -> StorageAddressRelation
```

`StorageAccessView` 是 StateRead/StateWrite 及 unknown write/call clobber 的引用投影：`semantic_ref, access_kind, location_ref, root_ref, path_segments, layout_ref, key_uses, access_point, provenance_refs`。path_segments 保留 mapping/member/已恢复 slot 的层级；不按 access 字符串重建位置。key_uses 每项有 `use_ref, candidate_definition_refs, typed_value_ref, evidence_refs`，typed_value_ref 可为已有 SSA binding/typed literal；P2-T3 尚未执行时允许 null+unknown，不建立依赖循环。access_point 引用函数 CFG/Yul anchor 与定义使用时点，不能用 source position 代替先后关系。

`StorageRelationContext = {function_ref, module1_ref, guard_refs, feasible_edge_refs, unresolved_edge_refs, def_use_ref, assumptions, clobber_refs}`。provenance 缺失必须保留标志。后续 ValueIdentity 可通过同一查询输入增强 key evidence，但不改变四态协议、不要求重新实现 alias。

`relation` 冻结为：

| Enum | 语义 | 消费规则 |
| --- | --- | --- |
| SAME | 在 scope/Guard 假设内可证同一完整 storage 位置/区间 | 只有位置、执行覆盖及到达证据都充分时才允许 strong update；SAME 本身不证明某写已执行 |
| DISTINCT | 可证不相交的位置/区间 | 可以排除此对的 persistent dependence；需完整 root/layout/key 证据 |
| MAY_OVERLAP | 恢复的 root/path/key 已足以描述候选，但允许同址/重叠且未证明具体关系 | 保留可能依赖；weak update；不强覆盖旧定义 |
| INSUFFICIENT_EVIDENCE | 缺 root/path/layout/key/provenance 等必需依据，连候选关系都不完备 | 保留未知影响；不得当作 DISTINCT 或无依赖 |

最小覆盖：named state、single-level balances mapping、nested allowance mapping、已恢复 Solidity/inline Yul/Mixed storage path；root/path/layout；访问时点 key copy/redefinition；不同参数运行时可能相等；relevant Guard（例如 from==to/from!=to）；unknown storage/write；call clobber。相同参数名不证明跨写前后相同 key 值；不同名字不证明异址；packed layout 的部分重叠用 MAY_OVERLAP，缺字节范围用 INSUFFICIENT_EVIDENCE。

证据必须指出使用的 root/path/layout/key facts、访问时点 Definition/Use、Guard 假设、规则、reason 和 unknown_provenance。DISTINCT 的 root/slot 依据必须是 layout/命名空间证明；mapping 的 key 异值判别仅在已恢复 typed mapping 模型假设内有效，hash/地址模型假设必须写入 evidence，不能宣称求解了任意 keccak 碰撞。

未知写视为 potential clobber，不能只因不在 exact binding 中而忽略；delegatecall 以及无法排除重入的外部 call 保留未知 caller-state 影响。staticcall 的不写入结论也需 call kind 证据，返回值/外部读取仍未知。P2-T1A 提供关系/影响界限，P2-T2 负责传播/kill；P2-T4 连接值流；P2-T6 和 P2-T8 共用它保留 persistent dependency；P3 显式接收未知影响。

Non-goals：通用 Alias Analyzer、任意地址/keccak/slot 算术求解、全部 memory/pointer alias、完整跨合约 state alias/代理/重入模型、重设计 SFIR Storage IR。未知情况可返回显式不足，不能以扩大目标来追求全部 PASS。

## 6. Guard / Value Flow / Dependency contracts

### Guard

source representation 是 CFG edge guard、BranchCondition/Require/Assert、path witness 和 typed operands。canonical representation 是 typed predicate term：`{op, type, operands, literal, symbol_ref, arithmetic_mode}`；每个不适用字段可 null，unknown term 保留原始 refs/reason。symbol_ref 是声明/定义身份，不是显示名。P1-T6 负责最小 predicate term 与 SMT translation；P1-T7 拥有规范化；P2-T5 在同一 term vocabulary 上扩展值表达式，不能建立第二套 Guard。新增 term operator 必须有语义和版本决策。

P1-T6 首个 operator/type/lowering 实现子集与 refined `guard_ref` view 见 [P1-T6-001](docs/decisions/P1-T6-001-local-typed-smt.md)。沿用上述六字段与七字段 SemanticControlEdge；不增加 canonicalization/equivalence 语义。

规范化保留 bit width、signedness、checked/wrapping、转换、异常 side condition；不得把 EVM 位向量无条件化成数学整数。`compare_guards(left, right, assumptions) -> ComparisonResult`：outcome=`EQUIVALENT / DIFFERENT / UNKNOWN / TIMEOUT / UNSUPPORTED`，带 counterexample/proof/query refs 和 scope。同一已验证 canonical term 可作句法相同的充分证据；字符串不同不是语义不同证据。guard id 只是结构化身份，不代替逻辑等价；本 Task 不实现 SMT。

### Value Flow

`dependency_kind = DATA / PHI / STORAGE_VALUE / STORAGE_KEY / CONTROL / CALL_RESULT / UNKNOWN`；`source_role = INPUT / ENVIRONMENT / OLD_STATE / EXPRESSION / CONSTANT / UNKNOWN`。source/target 必须是可解析的 Definition/Use/ValueIdentity/semantic occurrence/source node 引用。INPUT 以 ABI 参数角色，ENVIRONMENT 明确 msg.sender/msg.value；compound operand 按 typed operand path 建来源或显式 unknown。

ValueFlow 记录访问位置关系与读出的 value 身份，不能混同。PHI 必须关联 feasible predecessor evidence，现有 incoming_versions 集合不能任意选择一个。P2-T2/3 复用并补齐必要配对；unknown predecessor 保留。没有边且 refs 为空时，只有完整性证明才能断言无依赖；否则保留 unresolved source。控制依赖来自 M1 normalized Guard；VFG 的存在不代表全部 source 已解析。

### State / Sink Dependency

共同 `DependencySummary` 核心：`sink_ref, input_refs, old_state_reads, guard_refs, storage_access_refs, storage_relation_refs, operand_dependencies, update_expression_ref, slice_ref, external_state_impact, failure_dependencies`，外加 §3 evidence/status。StateDependency 约束 sink_kind=StorageWrite，必须有位置、key、更新值与旧状态来源；SinkDependency 用同一核心覆盖 ExternalCall/EtherTransfer/Emit/Return/Revert。

`operand_dependencies` 以语义角色 target/arguments/value/event_arguments/return_values/revert_payload/storage_key/update_value 定位，不仅把全部输入放进无角色集合。`external_state_impact = {kind: NONE_PROVEN / MAY_CLOBBER / UNKNOWN, scope, evidence_refs}`；未知 target/value/args 显式记录。一个带 value 的 call 表示一个 ExternalCall effect 加其 value operand，不再合成第二次 EtherTransfer；send/transfer 则投影 EtherTransfer。Require/Assert 的失败路径可适配为 Revert action，需合成来源与失败 Guard，不能无条件再记一次 revert。

SemanticSlice 默认只生成 relevant view，不删除原事实；若 P2-T6 要 safe cleanup，必须证明去除项不影响 sink、Guard、failure 与 order，且保留 source evidence，不能因“看起来是 junk”删语义。

## 7. SemanticEvent / STIR / Order / Failure contract

SemanticAction 是 M1 的关键行为锚点；SemanticEvent 是 M3 将已有 action、StateRead 与 M2 dependency 组合的 view，不执行新的 action extraction。kind=`StateRead / StateWrite / ExternalCall / EtherTransfer / Emit / Revert / Return`。StateRead 可无 action_ref，但必须有 state_access_ref；不同运行次数不能因同形 payload 合并。

STIR 必须含 transition identity、Inputs、Guard、State Reads、State Updates、External Effects、Failure、Order Constraints、Evidence、Analysis Status（实际序列化字段见接口表）。state update 为 `{event_ref, location_ref, old_state_refs, expression_ref, guard_ref, commit_ref}`；effect 保留 call kind/target/args/value、event/return/revert operand 与未知状态影响。entry old state 不能与当前 load value 或写后 state version 混淆。

`OrderConstraint.relation=BEFORE` 表示在指定 Guard/共执行 scope 下的严格偏序。CFG 可达只说明可能路径，不自动证明所有执行 A 在 B 前；需要 path、dominance 或可证数据/状态/控制依赖的组合依据。无出口 postdom fallback、来源位置、semantic-id tie、list order 只能作为 evidence 的风险信息，不作最终 proof。闭包不得有未解释环；互斥分支不能强行排序。

PartialOrder.unresolved_pairs 中 `INCOMPARABLE` 表示已分析的适用模型无法建立强制先后（例如互斥或允许两种顺序，需证据），`UNKNOWN` 表示证据不足，二者不能混同。循环的动态 occurrence/repetition 尚不能有限精确表达时，使用 `event_multiplicity={kind:UNKNOWN, reason,...}` 和 unresolved transition；不能对静态事件自环伪造 DAG 或静默只算一次。无循环单次 occurrence 标 ONCE，已证有限展开可标 BOUNDED 并附界证据，不在本任务实现展开。

`FailureSemantics = {condition_guard_ref, exit_kind, revert_path_refs, success_path_refs, commit_policy, rollback_scope, affected_event_refs, call_failure_handling, evidence_refs, status}`。

- exit_kind=`SUCCESS / REVERT / UNKNOWN`；commit_policy=`COMMIT / ROLLBACK / UNKNOWN`；rollback_scope 引用当前执行 frame/transaction boundary，不猜测跨合约执行。
- 成功路径提交已执行写及效果；显式 revert 路径中前序写/日志是 attempted，标 ROLLBACK，不计入 committed result。成功仍不能把未执行分支效果提交。
- 低级 call 的失败返回布尔与 caller revert 区分；handled failure 可能继续 caller。无法恢复捕获/传播边界时 UNKNOWN，不能默认全事务成功或全回滚。
- 不要求完整代理/重入模型，无法证明的外部影响成为 explicit unknown effect；不据 event 出现就判事件最终提交。

Canonical STIR 序列化与 provenance 分离但可追溯，partial-order closure 或等价图比较不能受冗余传递边/列表顺序影响。`compare_transitions` 复用 ComparisonResult，比较 inputs/Guard/state/effects/failure/order 及覆盖；所有需要维度可判定后才可 EQUIVALENT，DIFFERENT 要有差异 witness，任一关键 unknown 不得推断相等。clean/obfuscated 的稳定 provenance id 不必相同，使用 §3.1 语义投影。

## 8. Gap Resolution Map

每行 evidence source 均指 P0-T2 capability map 同编号与其代码索引；E0–E3 指既有 `docs/task_reports/P0-T2_revision_evidence/capability_probes.json`、`sink_impacts.json`。这里消费已有结论，不重新评审能力。

| Gap | Current capability | Confirmed gap | Responsible Task | Frozen interface impact | Downstream consumer | Evidence source | Non-goal | Resolution |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| G1 | Fact CFG/Guard/path witnesses | structural ≠ feasible | P1-T2, P1-T3, P1-T4, P1-T5, P1-T6, P1-T7 | SemanticControlEdge 分区、Guard/solver/status | P2-T1, P2-T2, P2-T6, P3-T2 | capability map G1；CFG/EXPR | whole-program production symbolic；source-identical CFG | REUSE EXISTING CAPABILITY; ASSIGNED TO EXISTING PLANNED TASK |
| G2 | FactSSA definitions/reads/phis/RD | 环境/compound refs、definition identity、phi 配对不全 | P2-T1, P2-T2, P2-T3, P2-T4 | Definition/Use/ValueIdentity/unknown source；M1 typed operands 由 P1-T6 承担 | P2-T1A, P2-T5, P2-T6, P2-T8 | capability map G2；SSA；environment probes | 重新实现完整 SSA/dataflow | REUSE EXISTING CAPABILITY; ASSIGNED TO EXISTING PLANNED TASK |
| G3 | 已恢复 root/access/path 与有限 storage aliases | persistent relation/key access time/unknown write/call clobber | P2-T1A, P2-T2 | 四态 StorageAddressRelation + conservative kill/weak update | P2-T2, P2-T4, P2-T6, P2-T8, P3-T5 | capability map G3；STORAGE；E0–E3 | 通用 alias、任意地址算术、完整跨合约状态 | REUSE EXISTING CAPABILITY; P2-T1A MINIMUM SUPPLEMENTATION; EXPLICIT UNSUPPORTED / RESEARCH-SCOPE LIMITATION |
| G4 | semantic_edges/SSA/RD/control deps | 非完整 VFG 或 sink slice | P2-T4, P2-T6, P2-T7, P2-T8 | ValueFlow/SemanticSlice/统一 DependencySummary | P3-T1, P3-T3, P5-T3 | capability map G4；E3；SSA/RECOVERY | 第二套 CFG/SSA/RD/slicing engine | REUSE EXISTING CAPABILITY; ASSIGNED TO EXISTING PLANNED TASK |
| G5 | Call/ValueTransfer/Event/Return/Revert operands | 未知 external-state impact、call clobber、部分字段丢失 | P1-T1, P2-T1A, P2-T8, P3-T4, P3-T5 | effect operands、call_kind、unknown impact/failure | P3-T1, P3-T5, P5-T3, P5-T4 | capability map G5；EFFECT；E3 | 全跨合约/代理/重入；Yul gas 缺失不宣称完整 | REUSE EXISTING CAPABILITY; ASSIGNED TO EXISTING PLANNED TASK; EXPLICIT UNSUPPORTED / RESEARCH-SCOPE LIMITATION |
| G6 | CFG fusion/SSA/control evidence | source/id/list/postdom fallback 非 order proof | P3-T3, P3-T6 | partial OrderConstraint + incomparable/unknown | P3-T4, P3-T5, P5-T4, P6-T3 | capability map G6；CFG `_node_order`、Yul `_local_order` | total order/source order；通用循环模型 | REUSE EXISTING CAPABILITY; ASSIGNED TO EXISTING PLANNED TASK; EXPLICIT UNSUPPORTED / RESEARCH-SCOPE LIMITATION |
| G7 | State/effect/terminal/canonical replacement | 缺完整 transition/commit/rollback | P3-T1, P3-T2, P3-T4, P3-T5, P3-T6 | SemanticEvent/STIR/FailureSemantics；旧表示仅 evidence | P4-T1, P5-T4, P6-T3 | capability map G7；SFIR/EFFECT | 原始源码恢复；重复计效 | REUSE EXISTING CAPABILITY; ASSIGNED TO EXISTING PLANNED TASK |
| G8 | 分散 diagnostics/notes/query status | unknown/opaque/truncation/fallback 未统一传播 | P1-T1, P1-T4, P1-T6, P1-T7, P2-T4, P2-T8, P3-T5, P5-T1 | §3 AnalysisStatus/Evidence 全 artifact 继承 | P2-T1, P3-T1, P5-T5, P6-T4 | capability map G8；SFIR diagnostics/RECOVERY | 默默吞异常/把资源限制称 fixed point | REUSE EXISTING CAPABILITY; ASSIGNED TO EXISTING PLANNED TASK |

## 9. Research Reference Map

仓库中对 `docs/`、`docs_old/`、`skills/` 的定向材料检索仅找到导航/能力图中的方法名称与任务组织，没有足以支持这些指定机制的论文原文或算法材料。以下是本任务明确授权的借鉴边界，**不是论文机制已核实的主张**；不凭标题/记忆补全算法。source availability 均为 `source support insufficient`。相应实现 Task 若要声称复现某论文，必须先补原文、版本、页/节与实际借用机制，再记录证据；工程实现仍以这里冻结的输入输出约束为准。

| Reference ID | Task | reference work | borrowed mechanism | adaptation to SFIR / Solidity / Yul | explicitly not adopted | source availability |
| --- | --- | --- | --- | --- | --- | --- |
| R1 | P1-T2, P1-T3, P1-T4, P1-T5 | CFF Abstract Interpretation / k-switch | 任务指定：region detection、context abstraction、fixed point、候选边的阶段组织 | 使用 Fact CFG/typed values；保留资源边界；k 与 transfer 由相应 Task 验证 | 未核实论文格/transfer/收敛定理或参数；不照搬机器码 IR | source support insufficient；本 Task prompt + TASK_MAP 仅需求来源 |
| R2 | P1-T6 | Symbolic CFF / Local Symbolic Refinement | 候选边的局部 symbolic/SMT refinement | Guard/位宽语义、局部 assumptions 和五态 solver outcome | whole-program production exploration；未知论文特定策略 | source support insufficient；仅任务蓝图 |
| R3 | P1-T6 | JSimpo | 只借鉴“先结构缩小范围，再局部精化”的分析组织 | M1 T2–T5 缩小范围，T6 局部精化 | 不声称移植其算法、领域假设或论文实验 | source support insufficient；仅用户限定借鉴范围 |
| R4 | P2-T5 | Syntia | 只借鉴恢复语义等价表达式而非原始语法 | typed SFIR term rewrite 与 checked/wrapping 证据 | 不引入未证实 synthesis/search/sampling 机制 | source support insufficient；仅用户限定思想 |
| R5 | P2-T6, P2-T8 | semantic-driven / output-oriented relevance | 从语义输出/sink 回溯真正相关数据与控制 | StorageWrite 首版，Multi-Sink 共用 VFG/slicer/Guard | 通用源码简化；按语法相似度或无证据删除操作 | source support insufficient；未确定唯一论文来源 |

其余 Task 的 Research Reference 标为 N/A（工程集成/评估/工程契约，没有新的论文迁移 claim）。P6-T1/2 可引用 R1–R5 作为被消融的设计动机，不据此声称论文复现。

## 10. Directory / Ownership Plan 与 Oracle Isolation

下列新增落点是规划，只在对应 Task 创建所需文件；P0-T3 不批量创建空实现。既有目录不迁移。

| Directory / artifact class | Owner | Boundary |
| --- | --- | --- |
| `scripts/s_seir/s_seir_research_*.py` | P1/P2/P3 对应 Task（见 §12） | production analysis 建于现有 SFIR；不建平行 `src/` |
| `scripts/s_seir/s_seir_research_contracts.py` | P1-T1 shared contract；后续 artifact owner 局部加入 | §3 公共协议实现；不复制 SFIR classes |
| `scripts/s_seir/s_seir_research_*_tests.py`、`tests/fixtures/research/` | 对应实现 Task | 沿用当前直接运行测试；测试层才可读 oracle |
| `docs/architecture/schemas/` | 每个 artifact producer；shared=P1-T1 | 后续 JSON schema 与版本；本计划是当前规范，不建空 schema |
| `benchmarks/micro/` | P1-T7/P2-T7/P2-T8/P3-T6 各模块微样例；P4-T1 统一 manifest | 局部验收微样例不等于正式冻结 dataset；避免 P4↔P1–3 循环依赖 |
| `benchmarks/cases/`, `benchmarks/generation/`, `benchmarks/validation/` | P4-T2/P4-T3/P4-T4 | 输入/配对变换/验证与 recovery 分离 |
| `benchmarks/oracles/` | P4-T1 schema；P4-T3 内容；P4-T4 freeze | expected/frozen oracle；生产无访问权 |
| `benchmarks/schema/`, `benchmarks/manifests/` | P4-T1 schema；P4-T4 dataset owner | manifest/coverage/fingerprint；不是 recovery 配置 |
| `experiments/evaluators/` | P1-T7/P2-T7+T8/P3-T6；P5-T1 统一封装 | 可读两侧 recovery artifacts 和 oracle；不能回写 recovery |
| `experiments/runners/`, `experiments/schema/` | P5-T1～T4 | orchestration、预算/环境/版本；不得修改算法 |
| `experiments/aggregation/`, `experiments/ablations/`, `experiments/reproduction/` | P5-T5、P6-T1～T4 | 聚合/隔离实验变体/复现 |
| `results/recovery/<run>/` | M1=P1-T7、M2=P2-T7/T8、M3=P3-T5 | recovery artifact，保存输入/配置/evidence；无 expected |
| `results/evaluation/<run>/` | M1=P1-T7、M2=P2-T7/T8、M3=P3-T6 | Module raw evaluation，不混作 recovery artifact |
| `results/raw/<experiment>/<run>/` | P5-T2/T3/T4；消融 P6-T1/T2/T3 | immutable raw experiment records、失败/超时日志 |
| `results/aggregate/<run>/` | P5-T5 | 仅派生结果，追溯 raw refs/fingerprint |
| `docs/task_reports/`, `docs/decisions/` | 每个 Task/决策 owner | handoff/evidence/变更；不进入 recovery |

Recovery 允许输入为当前样本源码/编译依赖、compiler/frontend 配置、SFIR 与自己上游研究 artifacts。不得传 pair id→expected 的查询能力，也不能接受 oracle path/expected transition 参数。clean 与 obfuscated 使用隔离 invocation，各自只见自己的输入；clean 源码允许作为 clean 自己的输入，不能帮助 obfuscated recovery。pair manifest/oracle/另一侧 artifact 只在 evaluator/benchmark validation/test 可见。

依赖方向为 runner → recovery → SFIR；runner → evaluator → oracle/artifacts。production 不 import evaluator/benchmark/results reader；P1-T1 开始添加无 oracle import/path 的契约检查，P4-T1/P5-T1 增加隔离运行/访问拒绝测试，恶意注入 oracle 参数应拒绝。P0-T3 仅冻结边界，不声称已存在这些隔离测试。实验 runner 只选择受版本约束配置；消融模块不得偷偷改变生产算法。

## 11. Machine-readable results / Schema Ownership

所有记录继承 input/artifact fingerprint、evidence/status 和 schema/version 约束；artifact class 强制区分 `RECOVERY / EVALUATION / ORACLE / EXPERIMENT_RAW / AGGREGATE / MANIFEST`。不能让同名 JSON 在两个角色间复用。

| Artifact | Schema/version owner | Producer / consumer | Required payload |
| --- | --- | --- | --- |
| Module1 recovery raw | P1-T7 | M1 → M2/M3/evaluator | Module1Result、config/SFIR refs、诊断 |
| Module2 recovery raw | P2-T7（core）, P2-T8（extension） | M2 → M3/evaluator | Module2Result、coverage、M1 ref、dependency/evidence |
| Module3 recovery raw | P3-T5 | M3 → evaluator | Module3Result、M1/M2 refs、STIR/projection version |
| EvaluationResult / Module1Evaluation / Module2Evaluation / Module3Evaluation | P1-T7/P2-T7+T8/P3-T6 分别拥有模块指标；P5-T1 统一 envelope | 各 evaluator → P4-T1/P5 runners | case/side artifacts、comparison outcome、metrics、unknown/failure reason、oracle_ref（可 null）、coverage、budget/time |
| OracleRecord | P4-T1 schema；P4-T3/P4-T4 content/freeze | reviewed benchmark/test → evaluator/validation | expected canonical claims、人工审查 provenance、scope、version；不进入生产 |
| BenchmarkManifest | P4-T1；P4-T4 freeze | benchmark → evaluator/runner | case_id、pair refs、source/dependency hashes、entrypoints、representation、base_case、obfuscation profile/strength/seed、oracle refs、eligibility/unsupported reason |
| DatasetFingerprint | P4-T4 | manifest → P5/P6 | SHA256(canonical manifest + 按 logical path 排序的 source/dependency/oracle/profile/schema 内容 hashes)；不含绝对路径/mtime |
| ExperimentResult | P5-T1 schema；P5-T2/T3/T4 records | runners → P5-T5/P6 | run_id、experiment/module、case/side、dataset fingerprint、artifact/evaluator refs、git revision+dirty patch digest、tool/compiler/solver versions、config/seed/budget、duration、outcome/status/errors |
| AggregateResult | P5-T5 | raw → reports/P6 | raw record refs/hashes、dataset/schema versions、group keys、denominator、counts、coverage、unknown/timeout/unsupported/failure distribution、统计方法 |

ExperimentResult schema=`erc20-experiment-result/v1`，EvaluationResult=`erc20-evaluation-result/v1`，AggregateResult=`erc20-aggregate-result/v1`，BenchmarkManifest=`erc20-benchmark-manifest/v1`，OracleRecord=`erc20-oracle-record/v1`，DatasetFingerprint=`erc20-dataset-fingerprint/v1`。细分指标由模块 evaluator owner 实现并验证，任何改变指标定义/分母/准入/expected 规则需 ADR；结果 schema 统一不授权改变语义判定。

P4-T4 冻结样本集合/版本后，失败不能被删除。unsupported 与 timeout 单列但保留总分母和 coverage；若有预注册 eligible subset，同时报告全量与 subset 计数及原因。P5-T5 按 overall/category/representation/obfuscation/strength/base-case 聚合，不用缺失文件当作成功或零失败。

## 12. Task Dependency Map — 37 fixed + P2-T1A

Phase counts：P0=3、P1=7、P2=8 fixed + 1 REQUIRED supplement、P3=6、P4=4、P5=5、P6=4；fixed 合计 37，执行节点 38。下表列全部 direct dependencies；consumer 列为这些依赖的逆向追踪（间接依赖不重复列）。P2-T2 的 direct dependencies 必须含 P2-T1A。

P0-T1/T2 是既有事实，P0-T3 是当前架构任务。后续任务全部是计划，不因本表存在自动授权执行。M1→M2→M3 串联；模块局部 micro tests/evaluators 先行，P4 之后才冻结正式 dataset；P5 执行正式实验；P6 ablation 不逆向改变生产 pipeline。

| Task / Phase | Responsibility | Direct input | Stable output | Direct dependency | Downstream consumer |
| --- | --- | --- | --- | --- | --- |
| P0-T1 | 仓库扫描与 Baseline | Repository | Repository facts + baseline | 无 | P0-T2, P0-T3 |
| P0-T2 | SFIR Capability Map | P0-T1 仓库事实与 baseline | Capability map + G1–G8 evidence | P0-T1 | P0-T3 |
| P0-T3 | 接口冻结与 Implementation Plan | P0-T1 facts + P0-T2 gaps | Frozen framework + plan + contracts | P0-T1, P0-T2 | P1-T1 |
| P1-T1 | Semantic Action Identification | SFIR + P0-T3 Module 1 contract | SemanticAction | P0-T3 | P1-T2, P1-T5, P1-T7 |
| P1-T2 | Dispatcher / Flattened Region Detection | SFIR CFG + SemanticAction | FlattenedRegion | P1-T1 | P1-T3, P1-T4, P1-T5 |
| P1-T3 | k-switch Abstract Domain and Context | FlattenedRegion + SFIR typed operands | AbstractDomainContract | P1-T2 | P1-T4 |
| P1-T4 | Fixed-Point Propagation Engine | FlattenedRegion + AbstractDomainContract | AbstractPropagation | P1-T2, P1-T3 | P1-T5 |
| P1-T5 | Candidate Semantic Control Edge Reconstruction | SemanticAction + FlattenedRegion + AbstractPropagation | Candidate SemanticControlEdge | P1-T1, P1-T2, P1-T4 | P1-T6 |
| P1-T6 | Local Symbolic Execution + SMT | Candidate SemanticControlEdge + abstract context + SFIR | Refined SemanticControlEdge + Guard + SolverEvidence | P1-T5 | P1-T7 |
| P1-T7 | Guard Canonicalization + Module 1 Evaluation | Refined SemanticControlEdge + Guard + SemanticAction | Module1Result + Module1Evaluation | P1-T1, P1-T6 | P2-T1, P2-T1A, P2-T2, P2-T4, P2-T6, P2-T7, P2-T8, P3-T1, P3-T2, P3-T3, P3-T5, P4-T1, P5-T1, P5-T2 |
| P2-T1 | Def-Use Infrastructure | Module1Result + SFIR FactSSA/operands | Definition + Use + DefUseIndex | P1-T7 | P2-T1A, P2-T2, P2-T3, P2-T4 |
| P2-T1A | Minimal Storage Alias / Address Equivalence | Definition/Use + SFIR storage/layout + Module1Result | StorageAddressRelation | P2-T1, P1-T7 | P2-T2, P2-T4, P2-T6, P2-T8 |
| P2-T2 | Reaching Definitions | DefUseIndex + StorageAddressRelation + Module1Result + existing RD | ReachingDefinitionSet | P2-T1, P2-T1A, P1-T7 | P2-T3, P2-T4 |
| P2-T3 | SSA / Explicit Value Identity | Definition/Use + ReachingDefinitionSet + FactSSA | ValueIdentity | P2-T1, P2-T2 | P2-T4 |
| P2-T4 | Value Flow Graph | DefUseIndex + ReachingDefinitionSet + ValueIdentity + StorageAddressRelation + Module1Result | ValueFlowGraph | P2-T1, P2-T1A, P2-T2, P2-T3, P1-T7 | P2-T5, P2-T6, P2-T8 |
| P2-T5 | Expression Normalization | ValueFlowGraph + typed SFIR expressions | CanonicalExpression | P2-T4 | P2-T6, P2-T7, P2-T8 |
| P2-T6 | Semantic Sink + Backward Slicing | ValueFlowGraph + CanonicalExpression + Module1Result + StorageAddressRelation | SemanticSlice | P2-T4, P2-T5, P1-T7, P2-T1A | P2-T7, P2-T8 |
| P2-T7 | Contract State Dependency Lifting + Module 2 Evaluation | SemanticSlice + CanonicalExpression + Module1Result | StateDependency + Module2StorageResult + Module2Evaluation | P2-T6, P2-T5, P1-T7 | P2-T8, P3-T1, P5-T3 |
| P2-T8 | Multi-Sink Semantic Dependency Extension | ValueFlowGraph + expressions + slices + StateDependency + Module1Result + StorageAddressRelation | SinkDependency + Module2Result + Module2Evaluation | P2-T4, P2-T5, P2-T6, P2-T7, P1-T7, P2-T1A | P3-T1, P3-T2, P3-T3, P3-T4, P3-T5, P4-T1, P5-T1, P5-T3 |
| P3-T1 | Semantic Event Schema | Module1Result + Module2StorageResult + Module2Result + SFIR StateRead | SemanticEvent | P1-T7, P2-T7, P2-T8 | P3-T2, P3-T5 |
| P3-T2 | Transition Grouping | SemanticEvent + Module1Result + Module2Result | TransitionCandidate | P3-T1, P1-T7, P2-T8 | P3-T3, P3-T4, P3-T5 |
| P3-T3 | Partial Order Recovery | TransitionCandidate + feasible control + data/state/control evidence | OrderConstraint + PartialOrder | P3-T2, P1-T7, P2-T8 | P3-T4, P3-T5 |
| P3-T4 | Revert / Failure Semantics | TransitionCandidate + PartialOrder + failure dependencies | FailureSemantics | P3-T2, P3-T3, P2-T8 | P3-T5 |
| P3-T5 | Canonical State Transition IR | SemanticEvent + TransitionCandidate + PartialOrder + FailureSemantics + M1/M2 | StateTransitionIR + Module3Result | P3-T1, P3-T2, P3-T3, P3-T4, P1-T7, P2-T8 | P3-T6 |
| P3-T6 | Module 3 Evaluation + Order Benchmark | Module3Result + canonical M1/M2 outputs | Module3Evaluation + order micro cases | P3-T5 | P4-T1, P5-T1, P5-T4 |
| P4-T1 | Benchmark Schema + Micro Benchmark | 三个 Module canonical outputs/evaluators + micro cases | BenchmarkManifest + micro benchmark + oracle contract | P1-T7, P2-T8, P3-T6 | P4-T2, P4-T3, P4-T4 |
| P4-T2 | Controlled Obfuscation + Pair Generation | BenchmarkManifest + approved transformation profiles | Validated pairs + transformation metadata | P4-T1 | P4-T3, P4-T4 |
| P4-T3 | ERC-20-style Paired Benchmark | Benchmark schema + validated pairs | ERC-20 pairs + reviewed oracle | P4-T1, P4-T2 | P4-T4 |
| P4-T4 | Larger Contracts + Dataset Freeze | Micro/ERC-20 pairs + reviewed oracle + transformation metadata | Frozen dataset + manifest + fingerprint + coverage matrix | P4-T1, P4-T2, P4-T3 | P5-T1, P5-T2, P5-T3, P5-T4, P5-T5 |
| P5-T1 | Unified Result Schema + Evaluator Contract | 三个 Module evaluators + frozen dataset | ExperimentResult contract | P1-T7, P2-T8, P3-T6, P4-T4 | P5-T2, P5-T3, P5-T4 |
| P5-T2 | Experiment 1 Runner | Module1Evaluation + frozen dataset + ExperimentResult contract | Experiment1 raw results | P1-T7, P4-T4, P5-T1 | P5-T5, P6-T1 |
| P5-T3 | Experiment 2 Runner | Module2Evaluation + frozen dataset + ExperimentResult contract | Experiment2 raw results | P2-T7, P2-T8, P4-T4, P5-T1 | P5-T5, P6-T2 |
| P5-T4 | Experiment 3 Runner | Module3Evaluation + frozen dataset + ExperimentResult contract | Experiment3 raw results | P3-T6, P4-T4, P5-T1 | P5-T5, P6-T3 |
| P5-T5 | Aggregation + Export | Experiment1/2/3 raw results + frozen manifest | AggregateResult + tables + failure distribution | P5-T2, P5-T3, P5-T4, P4-T4 | P6-T1, P6-T2, P6-T3, P6-T4 |
| P6-T1 | Control Recovery Ablation | Experiment1 results + evaluator + frozen dataset + aggregate | Control ablation results | P5-T2, P5-T5 | P6-T4 |
| P6-T2 | Sink-Guided Ablation | Experiment2 results + evaluator + frozen dataset + aggregate | Sink-guided ablation results | P5-T3, P5-T5 | P6-T4 |
| P6-T3 | Order-Aware Ablation | Experiment3 results + evaluator + frozen dataset + aggregate | Order-aware ablation results | P5-T4, P5-T5 | P6-T4 |
| P6-T4 | Full Regression / Failure Attribution / Reproduction | 全部模块 + dataset + experiments + ablations | FINAL_EXPERIMENT_REPORT.md + REPRODUCE.md + regression package | P6-T1, P6-T2, P6-T3, P5-T5 | 项目研究报告 |

### 每个后续 Task 的工程、验收与禁止重做契约

下述每行与上表相同 Task 行合并读取，构成完整 task card：ID/Phase/Module、responsibility、direct input/dependency、stable output、producer/consumer、implementation/file owner、interface touched、Research Reference、reuse、forbidden work、acceptance ownership、known limitations。producer 始终是该行 Task；consumer 是上表逆依赖与接口表指定消费者。上游 handoff 只读取 direct dependencies 的报告和直接接口。

Phase 1/2/3 分别归 M1/M2/M3；Phase 4 归 Benchmark，Phase 5 归 Experiment，Phase 6 归 Reproduction。测试归相应 producer，artifact consumer 在自己的 Task 验收输入契约；共享路径逐 Task 扩展，不重复创建实现。所有 Task 需修改前 baseline、相关既有回归、针对性正反例及交接，不以测试数量替代语义验收。

#### P1-T1 — Semantic Action Identification

- Responsibility：从已提升 SFIR 统一识别 StorageWrite、ExternalCall、EtherTransfer、Emit、Revert、Return，保留稳定 occurrence、schema 与 evidence。

- Phase / Module / Producer：Phase 1 / Module 1 / P1-T1。

- Implementation / file owner：`scripts/s_seir/s_seir_research_actions.py`；由 P1-T1 拥有本次增量。

- Interface touched：SemanticAction；schema ownership 按 §4/§11。

- Research Reference：N/A（工程集成/验证，无论文迁移 claim），具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：SFIR Fact CFG / semantic nodes / expressions / provenance；direct dependency 的稳定输出。

- Work forbidden to redo：不复制 SFIR Call/Event/Storage IR。

- Acceptance ownership：P1-T1，测试/检查落点 `scripts/s_seir/s_seir_research_actions_tests.py`；必须验证：六种 action、稳定身份/遍历重排、替代去重、Require/Assert 失败投影、unknown effect、shared status/evidence 与 oracle 隔离边界。

- Relevant known limitations：未锚定语义与未知 operand 必须显式。

#### P1-T2 — Dispatcher / Flattened Region Detection

- Responsibility：依据函数/Yul Fact CFG 与 action anchors 识别 dispatcher、flattened region、control-state candidate、case blocks、entry/exit，并保留 detection evidence。

- Phase / Module / Producer：Phase 1 / Module 1 / P1-T2。

- Implementation / file owner：`scripts/s_seir/s_seir_research_regions.py`；由 P1-T2 拥有本次增量。

- Interface touched：FlattenedRegion；schema ownership 按 §4/§11。

- Research Reference：R1，具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：SFIR Fact CFG / semantic nodes / expressions / provenance；direct dependency 的稳定输出。

- Work forbidden to redo：不重做 SemanticAction。

- Acceptance ownership：P1-T2，测试/检查落点 `scripts/s_seir/s_seir_research_regions_tests.py`；必须验证：dispatcher/非 dispatcher 正反例、entry/exit/case evidence、unsupported control 显式保留。

- Relevant known limitations：无出口/不可约控制不能假定支持。

#### P1-T3 — k-switch Abstract Domain and Context

- Responsibility：定义 <Abstract State, Context>、abstract value、transfer、join、context update 与 stabilization semantics，交付可被传播引擎消费的域契约。

- Phase / Module / Producer：Phase 1 / Module 1 / P1-T3。

- Implementation / file owner：`scripts/s_seir/s_seir_research_abstract_domain.py`；由 P1-T3 拥有本次增量。

- Interface touched：AbstractDomainContract；schema ownership 按 §4/§11。

- Research Reference：R1，具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：SFIR Fact CFG / semantic nodes / expressions / provenance；direct dependency 的稳定输出。

- Work forbidden to redo：不重做 flattening detection；不做完整 fixed point。

- Acceptance ownership：P1-T3，测试/检查落点 `scripts/s_seir/s_seir_research_abstract_domain_tests.py`；必须验证：transfer/join/context update 的局部语义、bottom/top/unknown、类型位宽与 stabilization contract；不运行完整 fixed point。

- Relevant known limitations：类型/位宽缺失保留 UNKNOWN。

#### P1-T4 — Fixed-Point Propagation Engine

- Responsibility：按 P1-T3 域契约执行 context-sensitive worklist propagation，保存 node/context abstract states、收敛证据与资源/截断状态。

- Phase / Module / Producer：Phase 1 / Module 1 / P1-T4。

- Implementation / file owner：`scripts/s_seir/s_seir_research_propagation.py`；由 P1-T4 拥有本次增量。

- Interface touched：AbstractPropagation；schema ownership 按 §4/§11。

- Research Reference：R1，具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：SFIR Fact CFG / semantic nodes / expressions / provenance；direct dependency 的稳定输出。

- Work forbidden to redo：不重新定义 abstract state。

- Acceptance ownership：P1-T4，测试/检查落点 `scripts/s_seir/s_seir_research_propagation_tests.py`；必须验证：收敛/循环/资源截止分别验收；node/context evidence 可复现；RESOURCE_LIMIT 不等于 FIXED_POINT。

- Relevant known limitations：资源截止不得标为 fixed point。

#### P1-T5 — Candidate Semantic Control Edge Reconstruction

- Responsibility：组合 SemanticAction、FlattenedRegion 和 fixed-point states，重建高召回 candidate semantic control edges。

- Phase / Module / Producer：Phase 1 / Module 1 / P1-T5。

- Implementation / file owner：`scripts/s_seir/s_seir_research_control_edges.py`；由 P1-T5 拥有本次增量。

- Interface touched：SemanticControlEdge；schema ownership 按 §4/§11。

- Research Reference：R1，具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：SFIR Fact CFG / semantic nodes / expressions / provenance；direct dependency 的稳定输出。

- Work forbidden to redo：不重做 detector/domain；不执行 SMT。

- Acceptance ownership：P1-T5，测试/检查落点 `scripts/s_seir/s_seir_research_control_edges_tests.py`；必须验证：action endpoints 与候选证据闭合；已知路径高召回；无 SMT 调用；候选被截断时 PARTIAL。

- Relevant known limitations：高召回候选允许假边且保留 truncation。

#### P1-T6 — Local Symbolic Execution + SMT

- Responsibility：只精化候选边的局部 symbolic region，返回五态 solver outcome、Guard、opaque predicate result 和 solver evidence。

- Phase / Module / Producer：Phase 1 / Module 1 / P1-T6。

- Implementation / file owner：`scripts/s_seir/s_seir_research_local_refinement.py`；由 P1-T6 拥有本次增量。

- Interface touched：SemanticControlEdge; Guard; SolverEvidence；schema ownership 按 §4/§11。

- Research Reference：R2 + R3，具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：SFIR Fact CFG / semantic nodes / expressions / provenance；direct dependency 的稳定输出。

- Work forbidden to redo：不做 production whole-program symbolic execution。

- Acceptance ownership：P1-T6，测试/检查落点 `scripts/s_seir/s_seir_research_local_refinement_tests.py`；必须验证：SAT/UNSAT/UNKNOWN/TIMEOUT/UNSUPPORTED 五态、typed query/assumptions、局部 SAT 与全路径 scope 区分、超时不删边。

- Relevant known limitations：局部 SAT 的路径前提须保留；UNKNOWN/TIMEOUT 不删边。

#### P1-T7 — Guard Canonicalization + Module 1 Evaluation

- Responsibility：规范化 SemanticControlEdge/Guard，提供 Guard semantic comparison、Module 1 evaluator 与 micro benchmark，并封装稳定 M1 输出。

- Phase / Module / Producer：Phase 1 / Module 1 / P1-T7。

- Implementation / file owner：`scripts/s_seir/s_seir_research_guards.py; experiments/evaluators/module1.py`；由 P1-T7 拥有本次增量。

- Interface touched：Guard; Module1Result; EvaluationResult；schema ownership 按 §4/§11。

- Research Reference：N/A（工程集成/验证，无论文迁移 claim），具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：SFIR Fact CFG / semantic nodes / expressions / provenance；direct dependency 的稳定输出。

- Work forbidden to redo：不重做候选恢复或 solver engine。

- Acceptance ownership：P1-T7，测试/检查落点 `scripts/s_seir/s_seir_research_guards_tests.py`；必须验证：Guard 规范化/语义比较正反例；输入/遍历重排稳定；M1 三类 edge 分区；micro evaluator 不进入 recovery。

- Relevant known limitations：字符串不同不证明 Guard 不等价。

#### P2-T1 — Def-Use Infrastructure

- Responsibility：复用 FactSSA，建立稳定 Definition/Use identity、Def→Use 与 Use→candidate Definitions 查询，并审计输入来源完整性。

- Phase / Module / Producer：Phase 2 / Module 2 / P2-T1。

- Implementation / file owner：`scripts/s_seir/s_seir_research_def_use.py`；由 P2-T1 拥有本次增量。

- Interface touched：Definition; Use; DefUseIndex；schema ownership 按 §4/§11。

- Research Reference：N/A（工程集成/验证，无论文迁移 claim），具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：Module1Result / Guard、FactSSA / existing RD / storage / MemorySSA / SinkResolver；direct dependency 的结果。

- Work forbidden to redo：不重做 control recovery 或完整数据流框架。

- Acceptance ownership：P2-T1，测试/检查落点 `scripts/s_seir/s_seir_research_def_use_tests.py`；必须验证：现有 FactSSA 引用复用、Def↔Use 双向一致、msg.sender/msg.value/compound/unbound operand、missing refs 显式 unknown。

- Relevant known limitations：空 refs、环境值、compound operand 必须审计。

#### P2-T1A — Minimal Storage Alias / Address Equivalence

- Responsibility：在 ERC-20 最小范围对访问时点的 storage 地址关系提供保守四态查询与证据，供 persistent RD/VFG/slicing 共用。

- Phase / Module / Producer：Phase 2 / Module 2 / P2-T1A。

- Implementation / file owner：`scripts/s_seir/s_seir_research_storage_relation.py`；由 P2-T1A 拥有本次增量。

- Interface touched：StorageAccessView; StorageAddressRelation；schema ownership 按 §4/§11。

- Research Reference：N/A（工程集成/验证，无论文迁移 claim），具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：Module1Result / Guard、FactSSA / existing RD / storage / MemorySSA / SinkResolver；direct dependency 的结果。

- Work forbidden to redo：不做通用 alias/任意 slot 算术/新 Storage IR。

- Acceptance ownership：P2-T1A，测试/检查落点 `scripts/s_seir/s_seir_research_storage_relation_tests.py`；必须验证：E0–E3 缺口对应正反例；same/distinct/may/insufficient 四态；copy/redefinition/nested/Mixed/Guard/unknown write/call clobber；无通用 alias 扩张。

- Relevant known limitations：key copy/redefinition、可能同值参数、未知写及 call clobber。

#### P2-T2 — Reaching Definitions

- Responsibility：局部扩展现有 RD，使其在 M1 control/Guard 上传播并消费 P2-T1A 处理 persistent reads/writes、branch/join/loop/redefinition。

- Phase / Module / Producer：Phase 2 / Module 2 / P2-T2。

- Implementation / file owner：`scripts/s_seir/s_seir_research_reaching.py; scripts/s_seir/s_seir_semantic_fact_ir.py`；由 P2-T2 拥有本次增量。

- Interface touched：ReachingDefinitionSet；schema ownership 按 §4/§11。

- Research Reference：N/A（工程集成/验证，无论文迁移 claim），具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：Module1Result / Guard、FactSSA / existing RD / storage / MemorySSA / SinkResolver；direct dependency 的结果。

- Work forbidden to redo：不重做 P2-T1A 或普通 CFG/RD 框架。

- Acceptance ownership：P2-T2，测试/检查落点 `scripts/s_seir/s_seir_research_reaching_tests.py`；必须验证：feasible branch/join/loop/redefinition；UNSAT 不传播；MAY/unknown weak update；只有 SAME+覆盖证明可 strong kill；复用既有 RD。

- Relevant known limitations：仅已证 UNSAT 边可剔除；may/unknown 不强覆盖。

#### P2-T3 — SSA / Explicit Value Identity

- Responsibility：复用已有 SSA/definition records，补充稳定 ValueIdentity 与必要的 merge/phi 显式前驱来源。

- Phase / Module / Producer：Phase 2 / Module 2 / P2-T3。

- Implementation / file owner：`scripts/s_seir/s_seir_research_value_identity.py`；由 P2-T3 拥有本次增量。

- Interface touched：ValueIdentity；schema ownership 按 §4/§11。

- Research Reference：N/A（工程集成/验证，无论文迁移 claim），具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：Module1Result / Guard、FactSSA / existing RD / storage / MemorySSA / SinkResolver；direct dependency 的结果。

- Work forbidden to redo：不建立第二套 SSA framework。

- Acceptance ownership：P2-T3，测试/检查落点 `scripts/s_seir/s_seir_research_value_identity_tests.py`；必须验证：entry/def/phi identity 稳定；incoming_by_edge 有证据；无法配对保持 unknown；不建立第二套 SSA。

- Relevant known limitations：phi 不可按两个列表的相同下标配对。

#### P2-T4 — Value Flow Graph

- Responsibility：整合 Def-Use、RD、ValueIdentity、storage relation 与 M1 control，形成带来源/角色/证据的可切片 ValueFlowGraph。

- Phase / Module / Producer：Phase 2 / Module 2 / P2-T4。

- Implementation / file owner：`scripts/s_seir/s_seir_research_value_flow.py`；由 P2-T4 拥有本次增量。

- Interface touched：ValueFlow; ValueFlowGraph；schema ownership 按 §4/§11。

- Research Reference：N/A（工程集成/验证，无论文迁移 claim），具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：Module1Result / Guard、FactSSA / existing RD / storage / MemorySSA / SinkResolver；direct dependency 的结果。

- Work forbidden to redo：不重做 CFG/SSA/RD；不把 semantic_edges 宣称完整 VFG。

- Acceptance ownership：P2-T4，测试/检查落点 `scripts/s_seir/s_seir_research_value_flow_tests.py`；必须验证：从输入/旧状态经 load/store 到 sink 连通；key/value/control 角色区分；E3 对应六种 sink 来源与 unknown frontier。

- Relevant known limitations：未知 state/input 来源必须有显式边。

#### P2-T5 — Expression Normalization

- Responsibility：以 typed term 进行 copy/constant propagation、constant folding、algebraic simplification 和有证据的 expression rewriting。

- Phase / Module / Producer：Phase 2 / Module 2 / P2-T5。

- Implementation / file owner：`scripts/s_seir/s_seir_research_expressions.py`；由 P2-T5 拥有本次增量。

- Interface touched：CanonicalExpression；schema ownership 按 §4/§11。

- Research Reference：R4，具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：Module1Result / Guard、FactSSA / existing RD / storage / MemorySSA / SinkResolver；direct dependency 的结果。

- Work forbidden to redo：不恢复原始语法；不引入通用 synthesis engine。

- Acceptance ownership：P2-T5，测试/检查落点 `scripts/s_seir/s_seir_research_expressions_tests.py`；必须验证：copy/constant/folding/algebraic rewrite 的等价/非等价负例；checked/wrapping 与 side effects 不丢失。

- Relevant known limitations：checked arithmetic、溢出和副作用约束。

#### P2-T6 — Semantic Sink + Backward Slicing

- Responsibility：以 StorageWrite 为首版 sink 沿 data 与 M1 control/Guard backward slice，输出 relevant slice 和 unresolved frontier。

- Phase / Module / Producer：Phase 2 / Module 2 / P2-T6。

- Implementation / file owner：`scripts/s_seir/s_seir_research_slicing.py`；由 P2-T6 拥有本次增量。

- Interface touched：SemanticSlice；schema ownership 按 §4/§11。

- Research Reference：R5，具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：Module1Result / Guard、FactSSA / existing RD / storage / MemorySSA / SinkResolver；direct dependency 的结果。

- Work forbidden to redo：不建立另一套 Guard；不替代 MemorySSA/SinkResolver。

- Acceptance ownership：P2-T6，测试/检查落点 `scripts/s_seir/s_seir_research_slicing_tests.py`；必须验证：StorageWrite 的 value/path/key/state/control 依赖全部保留；非相关项仅 view 排除；unknown frontier 不称闭合。

- Relevant known limitations：StorageWrite 首版；data 与 control 均需闭合。

#### P2-T7 — Contract State Dependency Lifting + Module 2 Evaluation

- Responsibility：将已得 slice 提升为 inputs/old state/Guard/storage key/update/sink dependency，完成 StorageWrite 层 evaluator 与 raw results。

- Phase / Module / Producer：Phase 2 / Module 2 / P2-T7。

- Implementation / file owner：`scripts/s_seir/s_seir_research_dependencies.py; experiments/evaluators/module2.py`；由 P2-T7 拥有本次增量。

- Interface touched：StateDependency; Module2Result; EvaluationResult；schema ownership 按 §4/§11。

- Research Reference：N/A（工程集成/验证，无论文迁移 claim），具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：Module1Result / Guard、FactSSA / existing RD / storage / MemorySSA / SinkResolver；direct dependency 的结果。

- Work forbidden to redo：不重做 slicing 或 normalization。

- Acceptance ownership：P2-T7，测试/检查落点 `scripts/s_seir/s_seir_research_dependencies_tests.py`；必须验证：StateDependency inputs/old-state/Guard/update/sink 可追溯；StorageWrite micro evaluator 与 machine raw；oracle 隔离。

- Relevant known limitations：未知旧状态不得解释为常量/无依赖。

#### P2-T8 — Multi-Sink Semantic Dependency Extension

- Responsibility：复用同一 slicing/dependency core 扩展 ExternalCall、EtherTransfer、Emit、Return、Revert，保留 operand/persistent/failure dependencies。

- Phase / Module / Producer：Phase 2 / Module 2 / P2-T8。

- Implementation / file owner：`scripts/s_seir/s_seir_research_dependencies.py; experiments/evaluators/module2.py`；由 P2-T8 拥有本次增量。

- Interface touched：SinkDependency; Module2Result; EvaluationResult；schema ownership 按 §4/§11。

- Research Reference：R5，具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：Module1Result / Guard、FactSSA / existing RD / storage / MemorySSA / SinkResolver；direct dependency 的结果。

- Work forbidden to redo：不建立第二套 slicing engine。

- Acceptance ownership：P2-T8，测试/检查落点 `scripts/s_seir/s_seir_research_dependencies_tests.py`；必须验证：ExternalCall/EtherTransfer/Emit/Return/Revert 与 StorageWrite 共用引擎；operand roles/persistent/failure deps、未知调用影响与 coverage。

- Relevant known limitations：call kind/target/args/value、persistent/failure dependency 与未知影响。

#### P3-T1 — Semantic Event Schema

- Responsibility：适配 M1/M2 与 StateRead 为统一 SemanticEvent，提供序列化，保留原 occurrence 与替代关系。

- Phase / Module / Producer：Phase 3 / Module 3 / P3-T1。

- Implementation / file owner：`scripts/s_seir/s_seir_research_events.py`；由 P3-T1 拥有本次增量。

- Interface touched：SemanticEvent；schema ownership 按 §4/§11。

- Research Reference：N/A（工程集成/验证，无论文迁移 claim），具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：Module1Result + Module2Result、既有 CFG/SSA 证据；direct dependency 的事件/顺序/失败结果。

- Work forbidden to redo：不重做 Module 1/2 或重复计入旧 effects。

- Acceptance ownership：P3-T1，测试/检查落点 `scripts/s_seir/s_seir_research_events_tests.py`；必须验证：StateRead 和六类 action 的事件适配；无重复效果；保留 M1/M2 identity/evidence/status。

- Relevant known limitations：StateRead 不是新的 SemanticAction；替代表示只计效一次。

#### P3-T2 — Transition Grouping

- Responsibility：按 Guard、control/data/state dependency 和 event evidence 分组得到 TransitionCandidate 与分组证据。

- Phase / Module / Producer：Phase 3 / Module 3 / P3-T2。

- Implementation / file owner：`scripts/s_seir/s_seir_research_transitions.py`；由 P3-T2 拥有本次增量。

- Interface touched：TransitionCandidate；schema ownership 按 §4/§11。

- Research Reference：N/A（工程集成/验证，无论文迁移 claim），具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：Module1Result + Module2Result、既有 CFG/SSA 证据；direct dependency 的事件/顺序/失败结果。

- Work forbidden to redo：不按源码相邻关系代替 grouping 证据。

- Acceptance ownership：P3-T2，测试/检查落点 `scripts/s_seir/s_seir_research_transitions_tests.py`；必须验证：Guard/control/data/state 驱动分组；互斥分支与循环 candidate；不得按源码相邻或 id grouping。

- Relevant known limitations：循环/不确定分组保留 candidate。

#### P3-T3 — Partial Order Recovery

- Responsibility：在共执行 scope 中恢复可证明 A ≺ B 的偏序，显式保留 incomparable/unknown 与证明来源。

- Phase / Module / Producer：Phase 3 / Module 3 / P3-T3。

- Implementation / file owner：`scripts/s_seir/s_seir_research_order.py`；由 P3-T3 拥有本次增量。

- Interface touched：OrderConstraint; PartialOrder；schema ownership 按 §4/§11。

- Research Reference：N/A（工程集成/验证，无论文迁移 claim），具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：Module1Result + Module2Result、既有 CFG/SSA 证据；direct dependency 的事件/顺序/失败结果。

- Work forbidden to redo：不重建 CFG；不强制 total order。

- Acceptance ownership：P3-T3，测试/检查落点 `scripts/s_seir/s_seir_research_order_tests.py`；必须验证：偏序正确/反序负例；incomparable 与 unknown；无出口/loop 限制；重排 semantic ids/list 不改变已证 order。

- Relevant known limitations：source position/semantic id/list order 不可证明顺序。

#### P3-T4 — Revert / Failure Semantics

- Responsibility：将 revert/failure 条件、成功/失败路径、commit/rollback 与调用失败处理纳入转移语义。

- Phase / Module / Producer：Phase 3 / Module 3 / P3-T4。

- Implementation / file owner：`scripts/s_seir/s_seir_research_failure.py`；由 P3-T4 拥有本次增量。

- Interface touched：FailureSemantics；schema ownership 按 §4/§11。

- Research Reference：N/A（工程集成/验证，无论文迁移 claim），具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：Module1Result + Module2Result、既有 CFG/SSA 证据；direct dependency 的事件/顺序/失败结果。

- Work forbidden to redo：不做完整重入/代理/跨合约执行。

- Acceptance ownership：P3-T4，测试/检查落点 `scripts/s_seir/s_seir_research_failure_tests.py`；必须验证：写后 revert、emit 后 revert、success commit、handled low-level failure、unknown propagation；attempted/committed 分开。

- Relevant known limitations：低级 call false 不自动等于 caller revert。

#### P3-T5 — Canonical State Transition IR

- Responsibility：组合前序产物为 Guard/state/effect/failure/order 统一 Canonical STIR 与确定性序列化/比较投影。

- Phase / Module / Producer：Phase 3 / Module 3 / P3-T5。

- Implementation / file owner：`scripts/s_seir/s_seir_research_stir.py`；由 P3-T5 拥有本次增量。

- Interface touched：StateTransitionIR; Module3Result；schema ownership 按 §4/§11。

- Research Reference：N/A（工程集成/验证，无论文迁移 claim），具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：Module1Result + Module2Result、既有 CFG/SSA 证据；direct dependency 的事件/顺序/失败结果。

- Work forbidden to redo：不重做前两模块；不复制 SFIR。

- Acceptance ownership：P3-T5，测试/检查落点 `scripts/s_seir/s_seir_research_stir_tests.py`；必须验证：deterministic serialization、canonical projection、Guard/state/effect/failure/order 完整；clean/obfuscated provenance 不混入比较。

- Relevant known limitations：不完备摘要不能宣称 whole-transition equivalence。

#### P3-T6 — Module 3 Evaluation + Order Benchmark

- Responsibility：评估 whole-transition 与 order 恢复，加入相同行为但关键顺序不同的微型样例和负对照。

- Phase / Module / Producer：Phase 3 / Module 3 / P3-T6。

- Implementation / file owner：`experiments/evaluators/module3.py; benchmarks/micro/order/`；由 P3-T6 拥有本次增量。

- Interface touched：EvaluationResult; BenchmarkManifest；schema ownership 按 §4/§11。

- Research Reference：N/A（工程集成/验证，无论文迁移 claim），具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：Module1Result + Module2Result、既有 CFG/SSA 证据；direct dependency 的事件/顺序/失败结果。

- Work forbidden to redo：evaluator 不参与 recovery。

- Acceptance ownership：P3-T6，测试/检查落点 `tests/fixtures/research/ + 对应 experiments/benchmarks validation`；必须验证：whole-transition 比较及相同行为/不同关键顺序负对照；unknown 不判等价；micro order benchmark 和 raw schema。

- Relevant known limitations：相同行为不同关键顺序必须作为负对照。

#### P4-T1 — Benchmark Schema + Micro Benchmark

- Responsibility：统一 paired benchmark manifest/oracle/evidence schema，整合 micro cases 与 negative controls，冻结 oracle isolation 验收。

- Phase / Module / Producer：Phase 4 / Benchmark / P4-T1。

- Implementation / file owner：`benchmarks/schema/; benchmarks/micro/`；由 P4-T1 拥有本次增量。

- Interface touched：BenchmarkManifest; OracleRecord；schema ownership 按 §4/§11。

- Research Reference：N/A（工程集成/验证，无论文迁移 claim），具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：三个 Module canonical outputs/evaluators、已有 micro cases 与已冻结接口。

- Work forbidden to redo：不把 oracle 变成 recovery 输入。

- Acceptance ownership：P4-T1，测试/检查落点 `tests/fixtures/research/ + 对应 experiments/benchmarks validation`；必须验证：manifest/oracle schema validation、正反配对、两个独立 recovery invocation、oracle 访问拒绝/无泄露。

- Relevant known limitations：配对有效性与语义恢复准确性分开。

#### P4-T2 — Controlled Obfuscation + Pair Generation

- Responsibility：生成 CFF/dispatcher、opaque predicate、fake edge、temp splitting、copy chain、junk、expression decomposition 的可控配对，保存 profile/seed/log 并验证。

- Phase / Module / Producer：Phase 4 / Benchmark / P4-T2。

- Implementation / file owner：`benchmarks/generation/; benchmarks/validation/`；由 P4-T2 拥有本次增量。

- Interface touched：BenchmarkManifest; DatasetFingerprint；schema ownership 按 §4/§11。

- Research Reference：N/A（工程集成/验证，无论文迁移 claim），具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：三个 Module canonical outputs/evaluators、已有 micro cases 与已冻结接口。

- Work forbidden to redo：不修改 recovery algorithm。

- Acceptance ownership：P4-T2，测试/检查落点 `tests/fixtures/research/ + 对应 experiments/benchmarks validation`；必须验证：profile/seed 可复现、变换有效性 evidence、失败变换保留、不修改 oracle 迎合 recovery。

- Relevant known limitations：变换失败保留记录；seed/profile 可追踪。

#### P4-T3 — ERC-20-style Paired Benchmark

- Responsibility：构建 transfer/approve/transferFrom/mint/burn 的 ERC-20 风格配对与 reviewed oracle，标注 Solidity/Yul/Mixed 表示范围。

- Phase / Module / Producer：Phase 4 / Benchmark / P4-T3。

- Implementation / file owner：`benchmarks/cases/erc20/; benchmarks/oracles/`；由 P4-T3 拥有本次增量。

- Interface touched：BenchmarkManifest; OracleRecord；schema ownership 按 §4/§11。

- Research Reference：N/A（工程集成/验证，无论文迁移 claim），具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：三个 Module canonical outputs/evaluators、已有 micro cases 与已冻结接口。

- Work forbidden to redo：不使用 original source 帮助 obfuscated recovery。

- Acceptance ownership：P4-T3，测试/检查落点 `tests/fixtures/research/ + 对应 experiments/benchmarks validation`；必须验证：五类 ERC-20 操作、Solidity/inline Yul/Mixed coverage、人工审查 oracle、unsupported 表示不伪装成功。

- Relevant known limitations：transfer/approve/transferFrom/mint/burn；standalone Yul 显式 unsupported。

#### P4-T4 — Larger Contracts + Dataset Freeze

- Responsibility：加入较完整合约作为补充外部有效性样例，冻结正式 dataset manifest/fingerprint/coverage matrix。

- Phase / Module / Producer：Phase 4 / Benchmark / P4-T4。

- Implementation / file owner：`benchmarks/manifests/; benchmarks/validation/`；由 P4-T4 拥有本次增量。

- Interface touched：BenchmarkManifest; DatasetFingerprint；schema ownership 按 §4/§11。

- Research Reference：N/A（工程集成/验证，无论文迁移 claim），具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：三个 Module canonical outputs/evaluators、已有 micro cases 与已冻结接口。

- Work forbidden to redo：不因失败删除样例或扩展研究目标。

- Acceptance ownership：P4-T4，测试/检查落点 `tests/fixtures/research/ + 对应 experiments/benchmarks validation`；必须验证：manifest/fingerprint 可复算、覆盖矩阵、样例冻结与排除原因、larger contract scope 不扩大。

- Relevant known limitations：较完整合约仅补充外部有效性。

#### P5-T1 — Unified Result Schema + Evaluator Contract

- Responsibility：统一 Exp1/2/3 raw result 与 evaluator envelope，保留 environment/version/timeout/seed/dataset fingerprint 和未知结果。

- Phase / Module / Producer：Phase 5 / Experiment / P5-T1。

- Implementation / file owner：`experiments/schema/; experiments/evaluators/`；由 P5-T1 拥有本次增量。

- Interface touched：ExperimentResult; EvaluationResult；schema ownership 按 §4/§11。

- Research Reference：N/A（工程集成/验证，无论文迁移 claim），具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：三个 Module evaluators、P4 frozen dataset、schema 与既有 runner 公共设施。

- Work forbidden to redo：不改 recovery/benchmark 冻结语义。

- Acceptance ownership：P5-T1，测试/检查落点 `tests/fixtures/research/ + 对应 experiments/benchmarks validation`；必须验证：跨模块 raw→统一 schema 不丢 status/evidence；环境/配置/预算/fingerprint 必填；evaluator/oracle 隔离。

- Relevant known limitations：environment/versions/budget/seed/fingerprint 必须保存。

#### P5-T2 — Experiment 1 Runner

- Responsibility：调用稳定 M1 recovery/evaluator 在 frozen dataset 上执行 Experiment 1 并保存不可变 raw results。

- Phase / Module / Producer：Phase 5 / Experiment / P5-T2。

- Implementation / file owner：`experiments/runners/experiment1.py`；由 P5-T2 拥有本次增量。

- Interface touched：ExperimentResult；schema ownership 按 §4/§11。

- Research Reference：N/A（工程集成/验证，无论文迁移 claim），具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：三个 Module evaluators、P4 frozen dataset、schema 与既有 runner 公共设施。

- Work forbidden to redo：runner 不修改 recovery algorithm。

- Acceptance ownership：P5-T2，测试/检查落点 `tests/fixtures/research/ + 对应 experiments/benchmarks validation`；必须验证：Exp1 runner 可复现、逐样例日志/预算/错误完整、timeout/unsupported 保留、evaluator 版本一致。

- Relevant known limitations：timeout/unsupported 不从分母静默去除。

#### P5-T3 — Experiment 2 Runner

- Responsibility：调用稳定 M2 recovery/evaluator 执行 Experiment 2，分别报告 StorageWrite 与 Multi-Sink 依赖恢复。

- Phase / Module / Producer：Phase 5 / Experiment / P5-T3。

- Implementation / file owner：`experiments/runners/experiment2.py`；由 P5-T3 拥有本次增量。

- Interface touched：ExperimentResult；schema ownership 按 §4/§11。

- Research Reference：N/A（工程集成/验证，无论文迁移 claim），具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：三个 Module evaluators、P4 frozen dataset、schema 与既有 runner 公共设施。

- Work forbidden to redo：runner 不修改 recovery algorithm。

- Acceptance ownership：P5-T3，测试/检查落点 `tests/fixtures/research/ + 对应 experiments/benchmarks validation`；必须验证：Exp2 两层 coverage 与 alias/unknown failures 报告；schema/fingerprint/环境一致。

- Relevant known limitations：StorageWrite 与 Multi-Sink 分层报告。

#### P5-T4 — Experiment 3 Runner

- Responsibility：调用稳定 M3 recovery/evaluator 执行 Experiment 3，报告 transition/order/failure 的完整结果。

- Phase / Module / Producer：Phase 5 / Experiment / P5-T4。

- Implementation / file owner：`experiments/runners/experiment3.py`；由 P5-T4 拥有本次增量。

- Interface touched：ExperimentResult；schema ownership 按 §4/§11。

- Research Reference：N/A（工程集成/验证，无论文迁移 claim），具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：三个 Module evaluators、P4 frozen dataset、schema 与既有 runner 公共设施。

- Work forbidden to redo：runner 不修改 recovery algorithm。

- Acceptance ownership：P5-T4，测试/检查落点 `tests/fixtures/research/ + 对应 experiments/benchmarks validation`；必须验证：Exp3 五个语义维度与 order/failure 负例；raw 不丢 unknown；schema/fingerprint 一致。

- Relevant known limitations：Guard/state/effect/failure/order 任一未知不能宣称完整等价。

#### P5-T5 — Aggregation + Export

- Responsibility：从 raw records 按 overall/category/representation/obfuscation/strength/base-case 聚合并导出统计与 failure distribution。

- Phase / Module / Producer：Phase 5 / Experiment / P5-T5。

- Implementation / file owner：`experiments/aggregation/`；由 P5-T5 拥有本次增量。

- Interface touched：AggregateResult；schema ownership 按 §4/§11。

- Research Reference：N/A（工程集成/验证，无论文迁移 claim），具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：三个 Module evaluators、P4 frozen dataset、schema 与既有 runner 公共设施。

- Work forbidden to redo：不重跑或修改 recovery/oracle。

- Acceptance ownership：P5-T5，测试/检查落点 `tests/fixtures/research/ + 对应 experiments/benchmarks validation`；必须验证：raw 可追溯、总分母/各组计数可复算、缺文件报错、失败分类与所有维度聚合。

- Relevant known limitations：保留全部样例；overall/category/representation/profile/strength/base-case 分组。

#### P6-T1 — Control Recovery Ablation

- Responsibility：在隔离实验中比较 k-switch AI、whole-program symbolic baseline、k-switch+local SMT，并做 k sensitivity。

- Phase / Module / Producer：Phase 6 / Ablation / Reproduction / P6-T1。

- Implementation / file owner：`experiments/ablations/control.py`；由 P6-T1 拥有本次增量。

- Interface touched：ExperimentResult; AggregateResult；schema ownership 按 §4/§11。

- Research Reference：R1 + R2 + R3（仅设计动机），具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：正式 dataset/evaluator/raw/aggregate、现有模块与实验框架。

- Work forbidden to redo：whole-program symbolic 仅独立 baseline；不进入生产。

- Acceptance ownership：P6-T1，测试/检查落点 `tests/fixtures/research/ + 对应 experiments/benchmarks validation`；必须验证：AI-only/whole-program baseline/local SMT 同 dataset/evaluator/预算、k sensitivity、生产不变。

- Relevant known limitations：同预算比较 AI-only/local-SMT/whole-program baseline；k sensitivity。

#### P6-T2 — Sink-Guided Ablation

- Responsibility：消融 sink-guided slicing/semantic relevance，验证依赖恢复设计贡献，保持相同 dataset/evaluator/预算。

- Phase / Module / Producer：Phase 6 / Ablation / Reproduction / P6-T2。

- Implementation / file owner：`experiments/ablations/dependency.py`；由 P6-T2 拥有本次增量。

- Interface touched：ExperimentResult; AggregateResult；schema ownership 按 §4/§11。

- Research Reference：R5（仅设计动机），具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：正式 dataset/evaluator/raw/aggregate、现有模块与实验框架。

- Work forbidden to redo：不另建生产 slicing engine。

- Acceptance ownership：P6-T2，测试/检查落点 `tests/fixtures/research/ + 对应 experiments/benchmarks validation`；必须验证：sink-guided/relevance 消融只在实验配置中；同 dataset/evaluator/预算；unknown/失败保留。

- Relevant known limitations：semantic relevance 消融同 dataset/evaluator/budget。

#### P6-T3 — Order-Aware Ablation

- Responsibility：消融显式 partial order，验证其对区分状态转移的贡献，保持生产 STIR 契约。

- Phase / Module / Producer：Phase 6 / Ablation / Reproduction / P6-T3。

- Implementation / file owner：`experiments/ablations/order.py`；由 P6-T3 拥有本次增量。

- Interface touched：ExperimentResult; AggregateResult；schema ownership 按 §4/§11。

- Research Reference：N/A（工程集成/验证，无论文迁移 claim），具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：正式 dataset/evaluator/raw/aggregate、现有模块与实验框架。

- Work forbidden to redo：不改变生产 Order contract。

- Acceptance ownership：P6-T3，测试/检查落点 `tests/fixtures/research/ + 对应 experiments/benchmarks validation`；必须验证：有/无 order 实验在顺序负例有可归因差异；不改变 production STIR。

- Relevant known limitations：移除 order 只为实验变体，失败样例保留。

#### P6-T4 — Full Regression / Failure Attribution / Reproduction

- Responsibility：运行全项目必要回归，归因失败并构造最小 failing case，提供复现封装、最终报告与限制。

- Phase / Module / Producer：Phase 6 / Ablation / Reproduction / P6-T4。

- Implementation / file owner：`FINAL_EXPERIMENT_REPORT.md; REPRODUCE.md; experiments/reproduction/`；由 P6-T4 拥有本次增量。

- Interface touched：全部结果/复现契约；schema ownership 按 §4/§11。

- Research Reference：N/A（工程集成/验证，无论文迁移 claim），具体借用/不采用/source availability 见 §9。

- Upstream work that must be reused：正式 dataset/evaluator/raw/aggregate、现有模块与实验框架。

- Work forbidden to redo：不通过放宽判定制造复现通过。

- Acceptance ownership：P6-T4，测试/检查落点 `tests/fixtures/research/ + 对应 experiments/benchmarks validation`；必须验证：全部必要回归、最小 failing case、失败归因、独立复现步骤、最终报告与原始结果对应。

- Relevant known limitations：工具链未锁定时明确跨机器限制。


## 13. Change Control / Bootstrap / Stop Rule

冻结后修改 interface/schema/identity/Module contract/benchmark-oracle rule/Task dependency/research invariant，必须提交 `docs/decisions/`：Problem、Evidence、Decision、Alternatives、Impact、Affected Modules、Affected Tasks、Affected Experiments、Required Regression。同步框架（如需且经研究人员决定）、计划、TASK_MAP、schema/测试；不能以临时 task report 或文件名惯例取代决定。新增不改变语义的 extensions 也需记录所属 Task 与兼容性检查。

后续 Bootstrap：`docs/research/RESEARCH_FRAMEWORK.md → docs/research/TASK_MAP.md → PROJECT_STATUS.md → IMPLEMENTATION_PLAN.md（当前 Task 与直接接口）→ direct upstream task reports/handoffs → 相关源码/测试`，同时读取适用 AGENTS.md。不要每次全仓扫描、一次读全部历史或仅凭编号猜职责。

当前 P0-T3 只冻结文档/契约和检查它们。不得实现 SemanticAction extractor、dispatcher、k-switch/fixed point、SMT、Def-Use、P2-T1A、VFG、slicing、STIR，不建正式 benchmark、不跑正式实验、不改 oracle/既有 SFIR。完成验收、PROJECT_STATUS 与 task report 后停止；P1-T1 仅是下一可授权 Task。
