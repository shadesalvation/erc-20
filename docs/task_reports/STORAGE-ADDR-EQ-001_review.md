# STORAGE-ADDR-EQ-001 — Storage Address Equivalence Review

- Problem ID: `STORAGE-ADDR-EQ-001`
- Review Status: **PARTIAL**
- 日期：2026-09-18
- 范围：P0-T2 的独立单问题审查；未重新执行完整 capability review，未实现修复。

## 审查对象核对与结论

**指定实现未在当前工作树中提供，不能完成源码级审查。** `scripts/semantic_ir/` 当前仅剩 `__pycache__/*.pyc`，`model.py`、`builder.py`、`tests.py`、`exporter.py`、`__init__.py`、README 均在 Git 状态中标为删除。源文件搜索未找到 `LocationNode`、`ExpressionArena`、`ValueRecord`、`_location()`、`build_with_bindings()` 或 `value_aliases`；仅在归档中找到另一种 `SemanticFunction`。

现行 `scripts/s_seir/s_seir_semantic_fact_ir.py` 使用字典形式的 FactSSA/semantic.location，不是本次描述的 LocationNode/ValueRecord 实现。归档 `scripts/废弃_sfir_pipeline未使用/semantic_ir/` 自身标注废弃，使用 `SemanticLocation`、`SemanticOperation`、`_location_for_fact()`；不能替代指定版本。没有恢复已删除源码、反编译缓存、将 Git HEAD 或归档冒充当前实现。

因此本报告能确认工作树与指定审查对象不一致，以及少量直接相关的现行 SFIR 证据；**不能把“目标源码不可用”记成“目标实现不支持某能力”，也不能沿用前次 FactSSA 探针推断本次 direct copy/copy chain 的失败。**

证据目录：[STORAGE-ADDR-EQ-001_evidence](STORAGE-ADDR-EQ-001_evidence/results.json)。其中记录目标目录文件清单、源码搜索、测试命令和原始错误。

## Analysis Target

审查目标只包括同一 `SemanticFunction`、同一次函数执行内，具有 persistent-storage `location` 的 `StateRead`、`StateWrite`、`Delete` 及实现中等价指令。

不包含 Memory、Calldata、transient storage、通用 pointer/address alias、跨 transaction、跨独立 invocation 或跨 contract execution。由于目标指令模型缺失，`Delete` 的实际表示以及其他等价 persistent-storage instruction 的完整集合为 **当前证据不足**，不能宣称已枚举。

## Storage Address Semantics

本次采用用户指定的 **恢复后的 logical persistent-storage location** 口径。在已证明 compatible 的同一 mapping base/path 下，若有效 context 证明 key 不等，应判 `NO_ALIAS`；不额外要求 keccak 的数学无碰撞证明。

对 `RawStorageLocation`、fixed slot 和直接物理 slot，只允许依据已有 slot/layout 信息判定。当前目标模型不可读，不能确认这些信息是否存在、是否规范化、是否足以证明 base/path compatibility。未新增 layout recovery、packed overlap、bit-range overlap 或 hash collision proof。

## Relation Semantics

下表是审查口径，不是已实现 API：

| Relation | 本次判定含义 | 指定实现当前表达/判定能力 |
| --- | --- | --- |
| MUST_ALIAS | 对指定两个 access point 及有效 context，足以证明必然访问同一 logical persistent location | 当前证据不足 |
| NO_ALIAS | 在相同作用域下足以证明不可能同址 | 当前证据不足 |
| MAY_ALIAS | 必要结构、layout、key、候选及 provenance 完整且处于分析能力范围，完整候选关系中同址与异址均可能 | 当前证据不足 |
| UNKNOWN | 必要信息不完整、unsupported 或分析能力不足，不能形成可靠 relation | 当前证据不足 |

无法确认目标实现是否区分 MAY_ALIAS/UNKNOWN，是否将任一种误作 NO_ALIAS。也没有证据确认其会将“representation 不相同”判为 NO_ALIAS。不得从 lack of interning equality 推导地址不等。

## Existing Capability

支持程度只使用“已支持 / 部分支持 / 不支持 / 当前证据不足”。除明确列为旁证的现行 SFIR 行外，以下均指用户指定的 Semantic IR。

| 必查能力 | 当前支持程度 | 核对结果与证据边界 |
| --- | --- | --- |
| LocationNode persistent location；base/keys/member/slot/access 的含义 | 当前证据不足 | 无目标 model 源码，不能将现行 location 字典字段等同于该模型 |
| `_location()` 创建、identity、复用以及 access 参与 identity | 当前证据不足 | 未找到该方法，无法确认 identity 元组/哈希内容 |
| 文本不同但语义相同导致不同 LocationNode | 当前证据不足 | 无目标 interning 实现与相应输出；未预设失败 |
| ExpressionArena key expression 表示/复用 | 当前证据不足 | 仅有名为 expressions 的缓存文件，不构成可核验当前源码 |
| build_with_bindings 临时量/support binding 消除 | 当前证据不足 | 未找到目标方法；不能判断替换范围、递归或循环处理 |
| direct copy | 当前证据不足 | 目标 ValueRecord/expression builder 不可用；未判不支持 |
| copy chain | 当前证据不足 | 同上；不能以旧 FactSSA 的 access 字符串探针代替 |
| SemanticFunction.values / ValueRecord 的 definition、RD、Phi、unresolved merge、value identity | 当前证据不足 | 归档同名 SemanticFunction 无此 values 模型，不是指定实现 |
| existing value identity 用于 storage key | 当前证据不足 | 目标 key-expression 与 ValueRecord 接口均缺失 |
| value_aliases 语义、范围及边界 | 当前证据不足 | 当前 Python 源码无该符号 |
| storage instruction condition / guard_conditions / local context | 当前证据不足 | 兼容 SFIR facts 有条件字段，但目标 instruction 模型与投影不可核验 |
| condition/guard 参与 storage address comparison | 当前证据不足 | 不能由条件字段存在推断参与地址比较 |
| 统一 storage semantic comparison API | 当前证据不足 | 指定生产目录无源码，不能对所述实现证明 API 存在或不存在 |
| normalized base/path/slot/layout compatibility | 当前证据不足 | 无目标 normalizer/comparison consumer；不凭 base/变量名差异判不同 |
| 旁证：现行 SFIR 恢复路径的精确 binding | 部分支持 | `_storage_location_binding` 要求 StateRead/StateWrite 和 access/state_variable，以 `@storage:{access}` 查找 binding；不是 LocationNode 地址关系 API |
| 旁证：兼容 facts 的 condition/guard_conditions | 已支持 | `s_seir_semantic_fact_bridge.py` 保存已有 Guard；仅证明 facts 层有条件，不能证明目标 instruction 比较已使用条件 |

## Confirmed Gap

**已确认的 review 缺口是目标源码/测试/输出证据缺失；不是已确认的目标算法 failure。** 以下算法问题目前无法裁决：

| Required gap check | 当前结论 |
| --- | --- |
| 是否缺少统一 Storage Address Relation | 当前证据不足，不能仅凭目标目录删除判断所描述版本没有 API |
| exact identity 与 access-point runtime equivalence 是否明确区分 | 当前证据不足 |
| 是否有统一判断 persistent access 语义相同的方法 | 当前证据不足 |
| key relation 是否可在指定 access point 查询 | 当前证据不足 |
| ambiguous definition / unsupported key / missing provenance 如何表示 | 当前证据不足 |
| 已有 context 是否进入地址关系判断 | 当前证据不足 |
| MAY_ALIAS 与 UNKNOWN 是否区分、是否错误降为 NO_ALIAS | 当前证据不足 |
| 相同 LocationNode/representation 是否被当作不同 access point 同址的充分条件 | 当前证据不足；没有定位到目标 consumer，不能宣称存在该错误 |

现行 SFIR 的 exact storage binding 确实按 access 建立，且函数内部不读取 condition/guard；但这只定位其 binding 行为。它不返回上述四类 relation，也不能证明本次指定 Semantic IR 的 equality consumer 会做同样判断。

## Semantic Cases To Check

以下输入是审查用例规格，不是新实现或声称已执行的目标测试。由于目标入口不可导入，不新增模拟替代 IR 的 reproducer。

| Case / input | Relevant target IR | 当前结果 | 能确认/不能确认的范围 |
| --- | --- | --- | --- |
| A：balances[msg.sender] vs 同式，stable exact access | 两个 use site 的 Location/key evidence | 当前证据不足 | 现行 SFIR 有相同已恢复路径共享 binding 的测试；不是目标 MUST_ALIAS 结果 |
| B：k=msg.sender；balances[k] vs balances[msg.sender] | build_with_bindings、ValueRecord、key expr | 当前证据不足 | 未确认 direct copy 成功或失败，不登记 confirmed failure |
| C：k1=msg.sender；k2=k1；balances[k2] vs balances[msg.sender] | copy chain/provenance | 当前证据不足 | 未确认 chain 递归消除范围 |
| D：balances[k] 的两个 access 之间 k 重定义 | 两个 instruction_id 对应的 key definition | 当前证据不足 | 无法确认是否只凭 access/Location/name 返回 MUST_ALIAS |
| E0：compatible base，x/y 无等式信息 | 完整 key/candidate/context | 当前证据不足 | 完整时可讨论 MAY_ALIAS；不完整应 UNKNOWN，不能预判当前结果 |
| E1：有效 x==y | 与当前两个 key 相关的 context | 当前证据不足 | 未确认 context-sensitive 同址识别 |
| E2：有效 x!=y | 同上 | 当前证据不足 | review 口径应 NO_ALIAS，未确认实现能力；无碰撞证明附加要求 |
| E3：仅有与 key/location 无关的 inequality | context 绑定关系 | 当前证据不足 | 未找到目标 consumer，不能确认存在错误使用 |
| F：已有可靠信息能证明 distinct persistent storage | normalized path/slot/layout 证据 | 当前证据不足 | 不凭 base 文本、变量名或 coarse representation 不同判 NO_ALIAS |
| G：unsupported key / missing provenance | unsupported/unresolved 标记 | 当前证据不足 | 不能确认如何表示，以及与完整候选的不确定性是否区分 |
| H1：完整 left/right candidates、多 pairing 结果 | candidate sets 与 feasible pairing | 当前证据不足 | 未确认是否遍历全部组合以及结果合并规则 |
| H2：候选不全/provenance 不完整 | unresolved merge / unknown | 当前证据不足 | 未确认是否保留不确定性；没有“任选 definition”的已证实反例 |

## Access-point Sensitivity

指定模型的 `instruction_id` 或等价稳定 use-site ID、key 在指定 use site 的 ValueRecord 关联、已有 reaching definitions 查询方式，均为 **当前证据不足**。

现行 SFIR 有 `semantic_id` 和节点 `fact_ssa` 引用；直接相关测试使用这些字段断言绑定。这仅证明不同现行模型有标识和引用，不足以推导目标模型支持 access-point-sensitive storage key relation。本次未新增 RD、query framework 或 SSA。

## Location Identity Boundary

需区分 exact representation interning 与 semantic runtime equality，但目标 `_location()` 缺失，不能确认它是否仅负责 stable representation/dedup，是否包含 access，以及文本差异怎样影响 identity。

同样无法确认生产 semantic equality 是否只要求 access 完全相同。相同 LocationNode 不自动构成本报告对 runtime 同址的证明；不同 LocationNode 也不自动证明异址。目标是否违反这一边界仍未核验。

## Context Boundary

已读的兼容事实桥接代码可保存 `condition` 和 `guard_conditions`，不是目标 Semantic IR instruction 的最终模型。目标保存哪些 local context、这些字段是否进入地址比较，均为 **当前证据不足**。

未新增 terminator 向下传播、CFG-wide path condition、control dependency、symbolic execution 或 SMT；也未重新审查这些其他能力。

## State-derived Key

以下两项均为 **当前证据不足**：

1. 两个 key 已引用同一稳定 ValueRecord 时，目标是否识别相同 value。
2. 两个独立 StateRead definition 是否被错误地仅凭源变量、高层 state 名称、Location 或 access 相同合并为相同 runtime value。

缺少 ValueRecord 和 storage key comparison consumer，不能将现行 FactSSA entry/storage binding 等同于 StateRead 值等价证据。未新增 StateSSA、storage versioning 或 state RD。

## Confirmed Evidence

| ID | input / relevant IR | current behavior | 能证明的结论 | implementation / test |
| --- | --- | --- | --- | --- |
| E1 | 当前工作树 `scripts/semantic_ir/` 与 Git status | 仅有六个 pyc 文件；源码标 D | 指定源码不可核验；不等于算法不支持 | [results.json](STORAGE-ADDR-EQ-001_evidence/results.json)、git_status_before.txt |
| E2 | 对 scripts Python 源码搜索指定类/方法/value_aliases | 仅发现归档 SemanticFunction | 未定位到题述实现；没有把全仓库所有 equality 都审查一遍 | [target_symbol_search.log](STORAGE-ADDR-EQ-001_evidence/target_symbol_search.log)，精确命令在 results.json |
| E3 | `.venv/bin/python -B -m unittest -v scripts.semantic_ir.tests` | exit 1，ModuleNotFoundError | 目标测试不可执行；1 个 loader error，不是一个语义用例失败 | [target_tests.log](STORAGE-ADDR-EQ-001_evidence/target_tests.log) |
| E4 | 现行 SFIR 两个节点：StateRead/StateWrite，location 为 mapping/balances[owner]/keys=[owner] | 两者 FactSSA storage binding 相同；具备版本引用 | 仅证明现行恢复路径 exact binding，不能证明 A–H 的目标 relation | [IR tests:636](../../scripts/s_seir/s_seir_semantic_fact_ir_tests.py#L636)、[IR:1858](../../scripts/s_seir/s_seir_semantic_fact_ir.py#L1858)；本次相关入口 27/27 PASS |
| E5 | 归档 SemanticFunction/SemanticLocation/source_location | `_location_for_fact` 对 JSON 表示去重；不是 `_location` 或 LocationNode | 排除误用旧同名类型为目标证据 | [归档 model](../../scripts/废弃_sfir_pipeline未使用/semantic_ir/model.py)、[归档 builder](../../scripts/废弃_sfir_pipeline未使用/semantic_ir/builder.py)；未执行归档实验 |
| E6 | 兼容 facts Guard 字段 | `_append_guard` 保存 guard_conditions 与 condition；部分路径也直接赋 guard_conditions | 仅证明现行兼容 fact 保存 context，不证明目标比较消费它 | [bridge:660](../../scripts/s_seir/s_seir_semantic_fact_bridge.py#L660)、[bridge:726](../../scripts/s_seir/s_seir_semantic_fact_bridge.py#L726) |

E4 相关现行测试命令：`.venv/bin/python -B scripts/s_seir/s_seir_semantic_fact_ir_tests.py`，exit 0，27 tests OK。没有执行完整 capability baseline，没有新增 reproducer；重新构造另一套模型无法弥补目标源文件缺失。

## Relevant Equality Sites

| 位置 | equality 类别 | 本次确认的边界 |
| --- | --- | --- |
| 指定 `scripts/semantic_ir/` | 无可读生产源码 | 无法列出目标 exact/semantic equality sites，亦无法确认“不存在 production semantic-equality consumer” |
| 现行 SFIR `_storage_location_binding` | access-keyed exact binding identity | 直接使用恢复的 access，保留 keys/member；不是两个 use site 的四态地址 relation 查询 |
| 归档 `_location_for_fact` | JSON exact representation equality | 对 source_location 序列化结果判等，仅用于辨明旧对象；不能作为现行 LocationNode interning 证据 |
| 兼容 facts `_append_guard` 等 | context 保存，不是 equality site | 条件存在不代表比较已使用 |

只作上述定位，没有全仓库 alias/equality 穷举，也没有把缺失目标替换为其他生产模块的完整审查。

## Unsupported / Evidence-insufficient

**Evidence-insufficient：有，不能写 None。** 未确认的 required items 包括：目标指令完整 scope；LocationNode 各字段及 `_location` identity；ExpressionArena/build_with_bindings；direct copy/copy chain；ValueRecord/RD/Phi/unresolved merge/value_aliases；mapping base/path compatibility；统一 relation API/consumer；A–H 全部目标行为；access-point sensitivity；context 参与；state-derived key；MAY_ALIAS/UNKNOWN 区分及是否错误判 NO_ALIAS。

这些均为“当前证据不足”，不是“已证实不支持”。Review 的 logical storage semantics、relation terminology 与排除范围已明确；它们是本次用户给定口径，不是冻结生产接口变更。

## 修改、验收与交接

只新增本报告及证据目录，向 P0-T2 报告登记固定问题 ID，并在 PROJECT_STATUS 记录本次独立审查状态。生产源码和既有测试未变，未修改 oracle，无 ADR、无修复、无新增算法或下一 Task。

| 验收 | 结果 |
| --- | --- |
| 指定 scope、logical semantics、relation terminology 明确 | PASS |
| 确认证据与版本边界，未假定 direct copy 等 failure | PASS |
| Existing Capability / Confirmed Gap 的目标行为完整确认 | FAIL：目标源码缺失 |
| A–H、access-point、identity/context/state-derived key、目标 equality sites 完整确认 | FAIL：当前证据不足 |
| required evidence-insufficient 明确列出 | PASS |
| 目标 baseline | FAIL：测试模块不可导入，原始错误已保存 |
| 直接相关现行 SFIR 回归 | PASS：27/27；不替代目标测试 |
| 生产逻辑未修改、范围核对 | PASS：见 evidence/verification.json |

继续本问题需要提供包含所述 `LocationNode`、`ExpressionArena`、`ValueRecord`、`build_with_bindings()` 的正确工作树或源码路径及对应测试。取得前不恢复旧文件、不推定接口、不实现修复。**Review Status: PARTIAL。完成此次记录后停止。**
